"""
mem_diag.py — Memory-leak diagnostic for ImageSound / MusicGen.

Generates N short clips back-to-back, force-unloads the model after each one,
and records RAM / VRAM / RSS / Python-object-count after every event.  The
trend table at the end shows which resource leaks and whether it is tied to
the load/unload cycle.

Run from the project root:
    python mem_diag.py                        # 10 cycles, 5 s clips
    python mem_diag.py --cycles 20 --duration 5
    python mem_diag.py --help

Output: live console  +  appended log file (mem_diag.log by default)

READ-ONLY — does not modify any backend code or pipeline logic.
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

# Force UTF-8 output on Windows where the default console encoding (cp1252) can't
# handle box-drawing characters, em-dashes, etc. used in the diagnostic output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Project root on sys.path ──────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── psutil ────────────────────────────────────────────────────────────────────
try:
    import psutil
except ImportError:
    sys.exit("psutil not found — install it:  pip install psutil")

_PROC = psutil.Process()


# ─────────────────────────────────────────────────────────────────────────────
# Measurement helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sys_ram_mb() -> float:
    """Total system RAM currently in use (all processes), MB."""
    return psutil.virtual_memory().used / 1024**2


def _rss_mb() -> float:
    """This process's RSS (resident set size), MB."""
    return _PROC.memory_info().rss / 1024**2


def _vram_alloc_mb() -> float:
    """CUDA: bytes currently held by live tensors, MB.  0 if CUDA unavailable."""
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.memory_allocated(0) / 1024**2
    except Exception:
        pass
    return 0.0


def _vram_rsvd_mb() -> float:
    """CUDA: bytes reserved by PyTorch caching allocator, MB.  0 if CUDA unavailable."""
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.memory_reserved(0) / 1024**2
    except Exception:
        pass
    return 0.0


def _gc_collect_and_count() -> int:
    """Force a full GC sweep, return count of tracked Python objects."""
    gc.collect()
    gc.collect()
    gc.collect()
    return len(gc.get_objects())


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Snap:
    label:    str
    cycle:    int      # 0 = pre-cycle (baseline / post-import)
    ts:       str      # HH:MM:SS
    sys_ram:  float    # MB
    rss:      float    # MB
    valloc:   float    # MB — VRAM allocated (live tensors)
    vrsvd:    float    # MB — VRAM reserved  (allocator cache)
    objs:     int      # Python objects tracked by GC


def take_snap(label: str, cycle: int) -> Snap:
    """Collect all metrics into a Snap.  Forces a GC sweep first."""
    return Snap(
        label=label,
        cycle=cycle,
        ts=datetime.now().strftime("%H:%M:%S"),
        sys_ram=_sys_ram_mb(),
        rss=_rss_mb(),
        valloc=_vram_alloc_mb(),
        vrsvd=_vram_rsvd_mb(),
        objs=_gc_collect_and_count(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Force-unload helper (bypasses idle timer — diagnostic only)
# ─────────────────────────────────────────────────────────────────────────────

def force_unload(manager) -> None:
    """
    Immediately unload the MusicGen model and release CUDA cache.
    Mirrors what unload_if_idle() does, without the idle-time check.
    Accesses private attrs — acceptable for a read-only diagnostic script.
    """
    if manager._model is None:
        return
    del manager._model
    manager._model = None
    gc.collect()
    gc.collect()
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fm(v: float, w: int = 9) -> str:
    """Float column, right-justified."""
    return f"{v:>{w},.1f}"


def _fd(v: float, w: int = 10) -> str:
    """Signed float delta, right-justified."""
    return f"{v:>+{w},.1f}"


def _id(v: int, w: int = 9) -> str:
    """Signed int delta, right-justified."""
    return f"{v:>+{w},d}"


HEADER = (
    "  {:>4}  {:<16}  {:>8}  "
    "{:>9}  {:>9}  {:>9}  {:>9}  "
    "{:>10}  {:>10}  {:>10}"
).format(
    "CYC", "EVENT", "TIME",
    "SYS_RAM", "RSS", "VALLOC", "VRSVD",
    "D_RSS", "D_VRSVD", "D_OBJS",
)

SEP = "  " + "-" * (len(HEADER) - 2)


def fmt_row(s: Snap, base: Snap) -> str:
    return (
        f"  {s.cycle:>4}  {s.label:<16}  {s.ts:>8}  "
        f"{_fm(s.sys_ram)}  {_fm(s.rss)}  {_fm(s.valloc)}  {_fm(s.vrsvd)}  "
        f"{_fd(s.rss   - base.rss)}  "
        f"{_fd(s.vrsvd - base.vrsvd)}  "
        f"{_id(s.objs  - base.objs)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Trend summary table
# ─────────────────────────────────────────────────────────────────────────────

def trend_table(all_snaps: list[Snap], base: Snap) -> list[str]:
    unloads = [s for s in all_snaps if s.label == "post-unload"]
    if not unloads:
        return ["  (no unload snapshots to trend)"]

    # Pick representative cycles: first, ~25%, ~50%, ~75%, last
    n = len(unloads)
    pick_indices = sorted({0, n // 4, n // 2, 3 * n // 4, n - 1})
    picks = [unloads[i] for i in pick_indices]

    # Deduplicate while preserving order
    seen: set[int] = set()
    picks = [p for p in picks if not (id(p) in seen or seen.add(id(p)))]  # type: ignore[func-returns-value]

    col_headers = ["BASE"] + [f"UNL-{s.cycle}" for s in picks]
    cw = max(10, max(len(h) for h in col_headers))

    lines: list[str] = []
    lines.append("")
    lines.append("  TREND SUMMARY — post-unload snapshots vs baseline")
    lines.append("  A steadily rising column = leak in the load/unload cycle.")
    lines.append("  Values that return near BASE after each unload = healthy.")
    lines.append("")

    hdr = f"  {'METRIC':<22}"
    for h in col_headers:
        hdr += f"  {h:>{cw}}"
    hdr += f"  {'D_TOTAL':>{cw}}  VERDICT"
    lines.append(hdr)
    lines.append("  " + "-" * (len(hdr) - 2))

    Getter = Callable[[Snap], float]
    metrics: list[tuple[str, Getter, float, bool]] = [
        # (display name,  getter,                 leak threshold MB/objs, use int fmt)
        ("SYS RAM (MB)",  lambda s: s.sys_ram,   50.0,   False),
        ("RSS (MB)",      lambda s: s.rss,        50.0,   False),
        ("VRAM alloc (MB)", lambda s: s.valloc,   10.0,   False),
        ("VRAM rsvd  (MB)", lambda s: s.vrsvd,    10.0,   False),
        ("Python objects", lambda s: float(s.objs), 5000.0, True),
    ]

    for name, getter, thresh, use_int in metrics:
        col_vals = [getter(base)] + [getter(p) for p in picks]
        delta_total = getter(unloads[-1]) - getter(base)
        verdict = "LEAK  <===" if abs(delta_total) > thresh else "clean"

        row = f"  {name:<22}"
        for v in col_vals:
            if use_int:
                row += f"  {int(v):>{cw},}"
            else:
                row += f"  {v:>{cw},.1f}"

        dt_str = (f"{int(delta_total):>+{cw},}" if use_int
                  else f"{delta_total:>+{cw},.1f}")
        row += f"  {dt_str}  {verdict}"
        lines.append(row)

    lines.append("")
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Per-metric mini-plot (ASCII spark line across cycles)
# ─────────────────────────────────────────────────────────────────────────────

def spark_plot(all_snaps: list[Snap], base: Snap) -> list[str]:
    """
    For each metric, print D-from-baseline for every post-unload snapshot as
    a simple bar chart.  Makes cumulative drift immediately visible.
    """
    unloads = [s for s in all_snaps if s.label == "post-unload"]
    if not unloads:
        return []

    lines: list[str] = []
    lines.append("")
    lines.append("  CYCLE-BY-CYCLE D FROM BASELINE  (post-unload only)")
    lines.append("  Each bar = delta after that unload.  Growing bars = leak.")
    lines.append("")

    def spark(values: list[float], unit: str, scale_factor: float = 1.0) -> str:
        CHARS = " .,-+*#@"
        if not values:
            return ""
        scaled = [v * scale_factor for v in values]
        vmax = max(abs(v) for v in scaled) or 1.0
        bars = ""
        for v in scaled:
            idx = int(abs(v) / vmax * (len(CHARS) - 1))
            bars += CHARS[idx]
        return bars

    metrics_sp: list[tuple[str, Callable[[Snap], float], str]] = [
        ("RSS D",        lambda s: s.rss   - base.rss,     "MB"),
        ("VRAM rsvd D",  lambda s: s.vrsvd - base.vrsvd,   "MB"),
        ("Python obj D", lambda s: float(s.objs - base.objs), ""),
    ]

    for name, getter, unit in metrics_sp:
        vals = [getter(s) for s in unloads]
        bar_str = spark(vals, unit)
        last_v = vals[-1] if vals else 0.0
        last_s = f"{last_v:+,.1f}{unit}" if unit else f"{int(last_v):+,}"
        lines.append(f"  {name:<18}  [{bar_str}]  last={last_s}")

    lines.append("")
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Memory-leak diagnostic: generate N songs and measure memory trend.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--cycles",   type=int, default=10,
                    help="Number of generate+unload cycles (default: 10)")
    ap.add_argument("--duration", type=int, default=5,
                    help="Clip length in seconds — shorter = faster (default: 5)")
    ap.add_argument("--prompt",   default="dark ambient drone, low frequency, slow, minimal",
                    help="Text prompt for all generations")
    ap.add_argument("--log",      default="mem_diag.log",
                    help="Append results to this file (default: mem_diag.log)")
    ap.add_argument("--no-unload", action="store_true",
                    help="Skip force-unload between cycles — isolates generation-only leak")
    args = ap.parse_args()

    log_path = ROOT / args.log
    pending: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        pending.append(line)

    def flush() -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            for ln in pending:
                f.write(ln + "\n")
        pending.clear()

    # ── Header ────────────────────────────────────────────────────────────────
    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    unload_mode = "NO-UNLOAD (generation-only)" if args.no_unload else "FORCE-UNLOAD after each cycle"
    emit("=" * 90)
    emit(f"  MEMLEAK DIAGNOSTIC — ImageSound / MusicGen")
    emit(f"  {run_ts}")
    emit(f"  cycles={args.cycles}  duration={args.duration}s  mode={unload_mode}")
    emit(f"  log={log_path}")
    emit("=" * 90)

    # ── Baseline — capture BEFORE any heavy imports ───────────────────────────
    emit()
    emit("  [baseline] Capturing before any imports ...")
    base = take_snap("BASELINE", 0)
    emit(f"  SYS_RAM={base.sys_ram:,.0f} MB  RSS={base.rss:,.0f} MB  "
         f"VRAM_alloc={base.valloc:,.0f} MB  VRAM_rsvd={base.vrsvd:,.0f} MB  "
         f"Python_objs={base.objs:,}")

    # ── Import pipeline (torch, audiocraft, etc.) ─────────────────────────────
    emit()
    emit("  [import] Loading pipeline.generate_song (torch + audiocraft) ...")
    t0 = time.perf_counter()
    from pipeline.generate_song import generate_song_from_text, get_model_manager  # noqa: PLC0415
    t_import = time.perf_counter() - t0
    emit(f"  Import done in {t_import:.1f}s")

    post_import = take_snap("post-import", 0)

    # ── GPU info ──────────────────────────────────────────────────────────────
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name  = torch.cuda.get_device_name(0)
            gpu_total = torch.cuda.get_device_properties(0).total_memory / 1024**2
            emit(f"  GPU: {gpu_name}  ({gpu_total:,.0f} MB total VRAM)")
        else:
            emit("  GPU: CUDA unavailable — VRAM columns will be 0")
    except Exception:
        emit("  GPU: torch not importable — VRAM columns will be 0")

    # ── Table header ──────────────────────────────────────────────────────────
    emit()
    emit("  All MB values.  D columns are vs BASELINE.")
    emit("  SYS_RAM = total system RAM in use.  RSS = this process only.")
    emit("  VALLOC = VRAM held by live tensors.  VRSVD = VRAM reserved by allocator.")
    emit()
    emit(HEADER)
    emit(SEP)
    emit(fmt_row(base, base))
    emit(fmt_row(post_import, base))
    emit(SEP)
    flush()

    manager = get_model_manager()
    all_snaps: list[Snap] = [base, post_import]

    # ── Generation + unload cycles ────────────────────────────────────────────
    for cycle in range(1, args.cycles + 1):
        emit(f"\n  >> CYCLE {cycle}/{args.cycles}")

        t_start = time.perf_counter()
        try:
            wav_path = generate_song_from_text(args.prompt, duration=args.duration)
        except Exception as exc:
            emit(f"  !!! ERROR on cycle {cycle}: {exc}")
            break
        t_gen = time.perf_counter() - t_start
        emit(f"  generation: {t_gen:.1f}s")

        # Delete WAV immediately — disk accumulation would bias RSS measurements
        try:
            wav_path.unlink()
        except Exception:
            pass

        s_gen = take_snap("post-generate", cycle)
        all_snaps.append(s_gen)
        emit(fmt_row(s_gen, base))

        if not args.no_unload:
            force_unload(manager)
            s_unl = take_snap("post-unload", cycle)
            all_snaps.append(s_unl)
            emit(fmt_row(s_unl, base))

        emit(SEP)
        flush()

    # ── Trend table ───────────────────────────────────────────────────────────
    for line in trend_table(all_snaps, base):
        emit(line)

    for line in spark_plot(all_snaps, base):
        emit(line)

    # ── Interpretation guide ──────────────────────────────────────────────────
    emit("  INTERPRETATION GUIDE")
    emit("  -----------------------------------------------------------------")
    emit("  RSS grows, never returns to base         → Python/C-ext heap leak")
    emit("  VRSVD non-zero after unload              → PyTorch allocator not flushed")
    emit("  VRSVD non-zero AND grows each cycle      → CUDA allocator fragmentation")
    emit("  Python objs grow, VRSVD clean            → Python-side object retention")
    emit("  All metrics return near base after unload → unload is clean; leak elsewhere")
    emit("  SYS_RAM grows but RSS doesn't            → another process or page cache")
    emit()
    emit("  RE-RUN with --no-unload to check whether leak is in generation itself")
    emit("  (i.e., leaks even without unload cycle) vs only in load/unload path.")
    emit()

    emit("=" * 90)
    emit(f"  Log appended to: {log_path}")
    emit("=" * 90)
    flush()


if __name__ == "__main__":
    main()
