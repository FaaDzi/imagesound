#!/usr/bin/env python3
"""
backend_diagnostic.py — READ-ONLY MusicGen health and resource report.

Diagnoses the "PC hangs during generation" issue by measuring model footprint,
VRAM usage, and system RAM headroom.

Run from the project root with the project venv:
    .venv/Scripts/python backend_diagnostic.py

Dependencies (all already in the project venv except psutil):
    .venv/Scripts/pip install psutil
"""

import sys
import time

# ── psutil ───────────────────────────────────────────────────────────────────
try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

# ── torch ────────────────────────────────────────────────────────────────────
try:
    import torch
except ImportError:
    sys.exit(
        "ERROR: torch not importable. "
        "Make sure you are running inside the project .venv:\n"
        "    .venv/Scripts/python backend_diagnostic.py"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gb(n: int) -> str:
    return f"{n / 1024**3:.2f} GB"

def _section(title: str = "") -> None:
    width = 64
    if title:
        pad = max(0, (width - len(title) - 2) // 2)
        print(f"\n{'─' * pad} {title} {'─' * pad}")
    else:
        print("─" * width)

def _ram() -> dict:
    if not _PSUTIL:
        return {}
    vm = psutil.virtual_memory()
    return {"total": vm.total, "used": vm.used, "available": vm.available}

def _vram() -> dict:
    if not torch.cuda.is_available():
        return {}
    free, total = torch.cuda.mem_get_info(0)
    return {
        "allocated": torch.cuda.memory_allocated(0),
        "total": total,
        "free": free,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Device check
# ─────────────────────────────────────────────────────────────────────────────

_section("1. DEVICE CHECK")

cuda_ok = torch.cuda.is_available()
print(f"  torch.cuda.is_available() : {cuda_ok}")
print(f"  torch version             : {torch.__version__}")

if cuda_ok:
    gpu_name  = torch.cuda.get_device_name(0)
    gpu_props = torch.cuda.get_device_properties(0)
    total_vram = gpu_props.total_memory
    print(f"  GPU name                  : {gpu_name}")
    print(f"  Total VRAM                : {_gb(total_vram)}")
    print(f"  CUDA version              : {torch.version.cuda}")
    print(f"  cuDNN version             : {torch.backends.cudnn.version()}")
else:
    print()
    print("  !! CUDA UNAVAILABLE — MusicGen will run on CPU.")
    print("  !! CPU generation is 10-30× slower and loads all weights into system RAM,")
    print("  !! which is the most common cause of whole-PC hangs / swap storms.")


# ─────────────────────────────────────────────────────────────────────────────
# 2. System resources (before model load)
# ─────────────────────────────────────────────────────────────────────────────

_section("2. SYSTEM RESOURCES  (before model load)")

if not _PSUTIL:
    print("  System RAM  : psutil not installed — run:")
    print("                .venv/Scripts/pip install psutil")
    print("                then re-run this script.")
else:
    r0 = _ram()
    print(f"  System RAM total      : {_gb(r0['total'])}")
    print(f"  System RAM used       : {_gb(r0['used'])}")
    print(f"  System RAM available  : {_gb(r0['available'])}")

if cuda_ok:
    v0 = _vram()
    print(f"  VRAM allocated (pre)  : {_gb(v0['allocated'])}")
    print(f"  VRAM free (pre)       : {_gb(v0['free'])}")
    print(f"  VRAM total            : {_gb(v0['total'])}")
else:
    print("  VRAM                  : N/A (no CUDA device)")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Model load test  (facebook/musicgen-medium, same as the backend)
# ─────────────────────────────────────────────────────────────────────────────

_section("3. MODEL LOAD  (facebook/musicgen-medium)")
print("  Loading model — first run downloads ~1.5 GB of weights, subsequent runs use cache.")
print("  Please wait ...")

if cuda_ok:
    torch.cuda.reset_peak_memory_stats(0)

try:
    from audiocraft.models import MusicGen
except ImportError:
    sys.exit(
        "ERROR: audiocraft not importable. "
        "Make sure you are using the project .venv."
    )

t_load_start = time.perf_counter()
model = MusicGen.get_pretrained("facebook/musicgen-medium")
t_load = time.perf_counter() - t_load_start

r1 = _ram()
v1 = _vram()

print(f"  Load time             : {t_load:.1f}s")

# Confirm which device the model landed on
try:
    model_device = str(next(model.lm.parameters()).device)
except Exception:
    model_device = "unknown (could not inspect parameters)"
print(f"  Model device          : {model_device}")

if _PSUTIL:
    ram_delta = r1["used"] - r0["used"]
    print(f"  RAM before            : {_gb(r0['used'])}")
    print(f"  RAM after             : {_gb(r1['used'])}")
    print(f"  RAM delta (model)     : {_gb(ram_delta)}")
    print(f"  RAM available after   : {_gb(r1['available'])}")

if cuda_ok:
    vram_delta = v1["allocated"] - v0["allocated"]
    peak_load  = torch.cuda.max_memory_allocated(0)
    print(f"  VRAM allocated (post) : {_gb(v1['allocated'])}")
    print(f"  VRAM delta (model)    : {_gb(vram_delta)}")
    print(f"  VRAM free after load  : {_gb(v1['free'])}")
    print(f"  Peak VRAM so far      : {_gb(peak_load)}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Generation test  (5-second clip, text prompt)
# ─────────────────────────────────────────────────────────────────────────────

_section("4. GENERATION TEST  (5-second clip)")

TEST_PROMPT   = "ambient synth pads, slow, soft piano, relaxing"
TEST_DURATION = 5
print(f"  Prompt   : {TEST_PROMPT!r}")
print(f"  Duration : {TEST_DURATION}s")
print("  Generating ...")

if cuda_ok:
    torch.cuda.reset_peak_memory_stats(0)

model.set_generation_params(duration=TEST_DURATION)

t_gen_start = time.perf_counter()
wavs = model.generate([TEST_PROMPT])
t_gen = time.perf_counter() - t_gen_start

r2 = _ram()
v2 = _vram()
peak_vram_gen = torch.cuda.max_memory_allocated(0) if cuda_ok else 0

print(f"  Generation time             : {t_gen:.1f}s  ({t_gen / TEST_DURATION:.2f}x real-time)")
print(f"  Output shape                : {list(wavs[0].shape)}  (sample_rate={model.sample_rate})")

if cuda_ok:
    print(f"  Peak VRAM during generation : {_gb(peak_vram_gen)}")
    print(f"  VRAM free after generation  : {_gb(v2['free'])}")

if _PSUTIL:
    print(f"  RAM available after gen     : {_gb(r2['available'])}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Summary verdict
# ─────────────────────────────────────────────────────────────────────────────

_section("5. SUMMARY VERDICT")

# Device
if cuda_ok:
    print(f"  Device          : GPU — {gpu_name}")
else:
    print("  Device          : CPU  !! THIS IS A PROBLEM")
    print("                    MusicGen on CPU is the #1 cause of full-PC hangs.")
    print("                    Check your CUDA / PyTorch installation.")

# RAM footprint
if _PSUTIL:
    model_ram = r1["used"] - r0["used"]
    headroom  = r1["available"]
    print(f"  Model RAM use   : {_gb(model_ram)}")
    print(f"  RAM headroom    : {_gb(headroom)} available after model load")
    if headroom < 2 * 1024**3:
        print("  !! CRITICAL: < 2 GB free after load — OS will swap aggressively,")
        print("               which explains whole-PC freezes. Reduce model size or add RAM.")
    elif headroom < 4 * 1024**3:
        print("  !! WARNING: < 4 GB free after load — generation may trigger light swapping.")
    else:
        print("     RAM headroom looks adequate for generation.")
else:
    print("  RAM footprint   : (install psutil for RAM measurements)")

# VRAM footprint
if cuda_ok:
    model_vram = v1["allocated"] - v0["allocated"]
    print(f"  Model VRAM      : {_gb(model_vram)} of {_gb(total_vram)} total")
    print(f"  Peak VRAM (gen) : {_gb(peak_vram_gen)}")
    print(f"  VRAM free now   : {_gb(v2['free'])}")
    usage_pct = (total_vram - v2["free"]) / total_vram * 100
    print(f"  VRAM used %     : {usage_pct:.0f}%")
    if v2["free"] < 512 * 1024**2:
        print("  !! CRITICAL: < 512 MB VRAM free — next generation will likely OOM.")
        print("               Switch to musicgen-small or upgrade GPU.")
    elif v2["free"] < 1 * 1024**3:
        print("  !! WARNING: < 1 GB VRAM free — running close to the limit.")
    else:
        print("     VRAM headroom looks adequate.")
else:
    print("  VRAM            : N/A (CPU-only)")

_section()
print("  Diagnostic complete.")
_section()
