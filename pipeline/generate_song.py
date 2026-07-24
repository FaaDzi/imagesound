"""
generate_song.py — Phase E (standalone, no FastAPI / database / endpoints)
Combines Phase D (Gemini image→prompt) + Phase C (MusicGen text→audio).

Public API
----------
  generate_song_from_image(image_path, duration=8) -> Path
  generate_song_from_text(prompt, duration=8)      -> Path

MusicGen loads lazily on first generation call and unloads immediately after
each generation finishes, freeing VRAM rather than staying resident.
"""

import gc
import io
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

# .env is at the project root — one level above pipeline/
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

from google import genai
from google.genai import types
from PIL import Image

_GEMINI_MODEL = "gemini-2.5-flash"

log = logging.getLogger(__name__)

# Constructed lazily on first real use (see _get_gemini_client) rather than at
# import time, so importing this module — e.g. for tests/tooling that only
# need the MusicGen side — doesn't require GEMINI_API_KEY to already be set.
_gemini_client: "genai.Client | None" = None


def _get_gemini_client() -> "genai.Client":
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(f"GEMINI_API_KEY not found. Looked in: {_ENV_PATH}")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


_GIF_MUSIC_DIRECTION_INSTRUCTION = """\
You are a music director who translates animated images into music.

These images are frames sampled from an animated GIF in sequence (first, middle, and last frames).
Your task: write one concise music prompt for an AI music model that captures BOTH the scene AND its energy.

Consider the animation as a whole:
- Rapid or dramatic changes across frames → energetic, fast, high-tempo music
- Slow or subtle changes → calm, flowing, minimal music
- Frames look nearly identical → treat as a still image; focus on content mood

Rules:
- Output ONLY the music prompt — no preamble, no explanation, nothing else.
- 1-2 sentences maximum, ideally under 25 words.
- Use musical language only: genre, mood, instruments, tempo, texture, energy.
- Do NOT describe what you see visually (no "frames show", "the GIF depicts", etc.).
- Choose 2-4 well-defined instruments only; name one clear focal/lead element.
- Keep adjectives minimal and purposeful — one vivid descriptor beats three vague ones.
"""

_MUSIC_DIRECTION_INSTRUCTION = """\
You are a music director who translates the emotional feeling of images into music.

Your task: look at this image and write a concise music prompt for an AI music model.

Rules:
- Output ONLY the music prompt — no preamble, no explanation, nothing else.
- 1-2 sentences maximum, ideally under 25 words.
- Use musical language only: genre, mood, instruments, tempo, texture, energy.
- Do NOT describe what you see (no "a photo of", "the image shows", etc.).
- Think: if this image were a film scene, what music would a composer write?
- Choose 2-4 well-defined instruments only. Too many simultaneous layers muddy the generated audio — favor clarity over grandeur.
- Name one clear focal element (the lead instrument or melody line) that carries the piece. The result must have a defined center, not a flat wash of equal layers.
- Keep adjectives minimal and purposeful — one vivid descriptor beats three vague ones.

Examples of good output (few instruments, one named lead, concise):
  "Solo cello lead over sparse low strings, slow and brooding, rising tension."
  "Fingerpicked acoustic guitar melody, light tambourine, upbeat indie folk, warm and bright."
  "Lead synth pad melody, subtle bass drone, slow reverb, melancholic and spacious."

Examples of what to AVOID (too many layers, no clear focal element):
  "Orchestral strings, piano, synth pads, brass swells, choir — epic and cinematic."
  "Guitar, bass, drums, violin, flute, and ambient textures create a lush soundscape."
"""

# ── MusicGen model manager ────────────────────────────────────────────────────
# Lazy-loads on first generation request, auto-unloads after idle timeout.
# Thread-safe: a lock is held for the entire duration of each generation so
# the idle checker cannot unload while work is in flight.


class _GenerationContext:
    """Context manager returned by _ModelManager.generation_context()."""

    __slots__ = ("_m", "_model_name", "_cb")

    def __init__(self, manager: "_ModelManager", model_name: str, on_model_ready):
        self._m = manager
        self._model_name = model_name
        self._cb = on_model_ready

    def __enter__(self):
        log.info("[gen-ctx] Acquiring generation lock (model=%s)...", self._model_name)
        self._m._lock.acquire()
        log.info("[gen-ctx] Lock acquired.")
        try:
            # Load (or reload) if the model is absent or a different variant is requested.
            if self._m._model is None or self._m._model_name != self._model_name:
                self._m._load(self._model_name)
            else:
                print(f"  [MusicGen] Reusing loaded facebook/musicgen-{self._model_name} — no reload", flush=True)
            if self._cb is not None:
                self._cb()
            return self._m._model
        except BaseException:
            log.info("[gen-ctx] Exception during model setup — releasing lock.")
            self._m._lock.release()
            raise

    def __exit__(self, *_):
        log.info("[gen-ctx] Releasing generation lock.")
        self._m._lock.release()


class _ModelManager:
    """Singleton manager for the MusicGen model."""

    def __init__(self):
        self._model = None
        self._model_name: str | None = None
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def generation_context(self, model_name: str = "medium", on_model_ready=None) -> _GenerationContext:
        """Return a context manager that loads the model if needed, holds the
        lock for the entire generation, then releases it on exit."""
        return _GenerationContext(self, model_name, on_model_ready)

    def _load(self, model_name: str) -> None:
        """Load (or reload) the named model variant. Must be called with _lock held."""
        import torch
        from audiocraft.models import MusicGen

        hf_name = f"facebook/musicgen-{model_name}"

        # Unload the currently resident model if it's a different variant.
        if self._model is not None and self._model_name != model_name:
            log.info("[model-load] Unloading existing model (%s) before loading %s...", self._model_name, model_name)
            del self._model
            self._model = None
            self._model_name = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            log.info("[model-load] Previous model unloaded.")

        log.info("[model-load] Loading %s...", hf_name)
        print(f"  [MusicGen] Loading {hf_name} ...")
        if torch.cuda.is_available():
            gpu  = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
            print(f"  [MusicGen] Device: GPU — {gpu} ({vram:.1f} GB VRAM)")
        else:
            print("  [MusicGen] Device: CPU (CUDA unavailable — generation will be slow)")

        t0 = time.perf_counter()
        self._model = MusicGen.get_pretrained(hf_name)
        self._model_name = model_name
        elapsed = time.perf_counter() - t0
        log.info("[model-load] %s loaded in %.1fs.", hf_name, elapsed)
        print(f"  [MusicGen] Ready in {elapsed:.1f}s")

    def force_unload(self) -> None:
        """Unconditionally unload the model. Called by the worker right after
        every generation finishes — trades the ~10-20s reload cost on the next
        job for never holding multi-GB VRAM once a song is done.

        Blocks briefly for the lock; the caller just released it via
        generation_context's __exit__, so it should be free immediately.
        """
        log.info("[unload] Acquiring lock...")
        with self._lock:
            if self._model is None:
                log.info("[unload] Model not loaded — nothing to unload.")
                return

            import torch

            vram_before = 0
            if torch.cuda.is_available():
                vram_before = torch.cuda.memory_reserved(0)
                log.info("[unload] VRAM reserved before unload: %.2f GB.", vram_before / 1024 ** 3)

            unloaded_name = self._model_name
            del self._model
            self._model = None
            self._model_name = None
            gc.collect()
            gc.collect()

            vram_freed_str = ""
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                freed = vram_before - torch.cuda.memory_reserved(0)
                vram_freed_str = f" (freed ~{freed / 1024 ** 3:.2f} GB VRAM)"

            print(f"  [MusicGen] facebook/musicgen-{unloaded_name} unloaded{vram_freed_str}", flush=True)
            log.info("[unload] Unload sequence complete.")


_model_manager = _ModelManager()


def get_model_manager() -> _ModelManager:
    """Return the process-wide model manager (used by the worker to force-unload after each job)."""
    return _model_manager


# ── Output folder (created at import time) ────────────────────────────────────

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


# ── Chunked generation ───────────────────────────────────────────────────────
# When requested duration exceeds _CHUNK_SEC, audio is produced in consecutive
# chunks: chunk 1 is generated from the text prompt; each subsequent chunk is
# generated via MusicGen's continuation API, conditioned on the tail of the
# previous chunk so the music flows naturally across the seam.  Chunks are kept
# in memory (no temp files) and stitched with a short crossfade before saving.

_CHUNK_SEC              = 30   # MusicGen single-pass ceiling (seconds)
_CONTINUATION_PROMPT_SEC = 6   # tail audio fed as continuation context per chunk
_CROSSFADE_MS           = 100  # crossfade length at chunk seams (milliseconds)


def _wrap_progress_callback(on_progress, cumulative: float, weight: float, expected_new_frames: int):
    """Build a (generated, total) -> None callback for musicgen.set_custom_progress_callback
    that maps ONE call's local progress into the caller's overall on_progress(fraction).

    Deliberately ignores audiocraft's own `total` argument: MusicGen._generate_tokens sets
    it to int(duration * frame_rate) for the FULL requested duration of that call, which for
    continuation chunks includes the primed prompt-tail (_CONTINUATION_PROMPT_SEC) — the
    numerator only counts NEW steps, so the raw ratio would asymptote well under 1.0 instead
    of reaching it. `expected_new_frames` (precomputed by the caller from the actual new-audio
    duration for this call) is the correct denominator.
    """
    def _cb(generated_tokens: int, _total_gen_len: int) -> None:
        if expected_new_frames <= 0:
            return
        local_frac = max(0.0, min(1.0, generated_tokens / expected_new_frames))
        on_progress(max(0.0, min(1.0, cumulative + local_frac * weight)))
    return _cb


def _crossfade_join(a: "torch.Tensor", b: "torch.Tensor", fade_samples: int) -> "torch.Tensor":
    """Linear crossfade between [C, T_a] and [C, T_b]. Returns [C, T_a + T_b - fade_samples]."""
    import torch

    fade_samples = min(fade_samples, a.shape[-1], b.shape[-1])
    if fade_samples <= 0:
        return torch.cat([a, b], dim=-1)

    fade_out = torch.linspace(1.0, 0.0, fade_samples, device=a.device)
    fade_in  = torch.linspace(0.0, 1.0, fade_samples, device=b.device)
    seam     = a[:, -fade_samples:] * fade_out + b[:, :fade_samples] * fade_in
    return torch.cat([a[:, :-fade_samples], seam, b[:, fade_samples:]], dim=-1)


def _generate_chunked(
    musicgen,
    prompts: "list[str]",
    duration: int,
    sample_rate: int,
    melody_wav: "torch.Tensor | None" = None,
    melody_sr: "int | None" = None,
    on_progress=None,
) -> "torch.Tensor":
    """
    Generate audio longer than _CHUNK_SEC by chaining MusicGen calls.

    Chunk 1 is produced from the text prompt alone (or, if melody_wav is given,
    from the text prompt + reference-audio melody via generate_with_chroma).

    When melody_wav is given, every subsequent chunk is ALSO melody-conditioned
    via generate_with_chroma, fed the matching _CHUNK_SEC-sized slice of
    melody_wav (chunk 2 <- seconds 30-60, chunk 3 <- seconds 60-90, ...) —
    each such chunk is a fresh generate_with_chroma call, not a continuation,
    since generate_continuation has no chroma input. Once melody_wav runs out
    (it's shorter than the requested duration), remaining chunks fall back to
    generate_continuation, conditioned on the tail of the previous chunk so
    the model continues naturally from where it left off.

    Without melody_wav, every chunk after the first uses generate_continuation
    the same way. All chunks are held in memory (no temp files) and stitched
    into a single tensor before returning.

    prompts: one string per chunk (1-indexed).  If shorter than the number of
    chunks produced, the last prompt is repeated.  Pass [single_prompt] for the
    original single-prompt behaviour.

    on_progress: optional callable(fraction: float) reporting overall 0.0-1.0
    progress across ALL chunks, weighted by each chunk's share of `duration`.

    Returns a [C, T] CPU tensor.
    """
    import torch

    def _chunk_prompt(idx: int) -> str:
        """1-indexed. Clamps to last entry if list is shorter than chunk count."""
        return prompts[min(idx - 1, len(prompts) - 1)]

    def _gpu_stats() -> str:
        if not torch.cuda.is_available():
            return "CUDA unavailable — CPU only"
        alloc  = torch.cuda.memory_allocated(0)  / 1024 ** 3
        reserv = torch.cuda.memory_reserved(0)   / 1024 ** 3
        return f"GPU alloc={alloc:.2f} GB  reserved={reserv:.2f} GB"

    prompt_tail_samples = int(_CONTINUATION_PROMPT_SEC * sample_rate)
    crossfade_samples   = int(_CROSSFADE_MS * sample_rate / 1000)
    target_samples      = int(duration * sample_rate)

    # Wall-clock reference for the entire chunked generation session.
    t_wall = time.perf_counter()

    def _W() -> str:
        """Cumulative elapsed from the start of _generate_chunked."""
        return f"+{time.perf_counter() - t_wall:.1f}s"

    print(f"  [chunked] Starting: target={duration}s ({target_samples} samples), "
          f"prompts={len(prompts)}, tail={_CONTINUATION_PROMPT_SEC}s, "
          f"crossfade={_CROSSFADE_MS}ms  threads={threading.active_count()}", flush=True)
    print(f"  [chunked] GPU at start: {_gpu_stats()}", flush=True)

    # --- Chunk 1: normal text-conditioned generation ---
    p1 = _chunk_prompt(1)
    print(f"  [chunked] Chunk 1 [{_W()}]: set_generation_params({_CHUNK_SEC}s)...", flush=True)
    musicgen.set_generation_params(duration=_CHUNK_SEC)
    _chunk1_kind = "generate_with_chroma" if melody_wav is not None else "generate"
    print(f"  [chunked] Chunk 1 [{_W()}]: {_chunk1_kind}() STARTING...", flush=True)
    print(f"  [chunked]   GPU before generate: {_gpu_stats()}", flush=True)

    weight_1 = min(duration, _CHUNK_SEC) / duration
    cumulative = 0.0
    if on_progress is not None:
        expected_new_frames_1 = int(min(duration, _CHUNK_SEC) * musicgen.frame_rate)
        musicgen.set_custom_progress_callback(
            _wrap_progress_callback(on_progress, cumulative, weight_1, expected_new_frames_1)
        )

    t_c1 = time.perf_counter()
    if melody_wav is not None:
        # Slice to this chunk's own window (0 - _CHUNK_SEC) — melody_wav may span
        # the whole song now, and chunk 1 should only see its matching segment,
        # same as chunks 2..N below.
        chunk1_melody_seg = melody_wav[:, :_CHUNK_SEC * melody_sr]
        raw = musicgen.generate_with_chroma([p1], chunk1_melody_seg, melody_sr, progress=(on_progress is not None))  # [1, C, T]
    else:
        raw = musicgen.generate([p1], progress=(on_progress is not None))   # [1, C, T]
    t_c1_elapsed = time.perf_counter() - t_c1
    cumulative = weight_1
    chunks = [raw[0].cpu()]            # [C, T]  — move off GPU before freeing
    del raw
    accumulated_samples = chunks[0].shape[-1]
    print(f"  [chunked] Chunk 1 [{_W()}]: generate() DONE in {t_c1_elapsed:.1f}s "
          f"-- shape={chunks[0].shape} "
          f"({accumulated_samples/sample_rate:.1f}s of {duration}s accumulated)", flush=True)
    print(f"  [chunked]   GPU after generate + del raw: {_gpu_stats()}", flush=True)

    # Flush allocator cache that chunk 1 left behind.  Without this the stale
    # cached tensors eat into the VRAM budget for chunk 2's generation call,
    # causing memory pressure (fragmentation / slower allocation) on tight cards.
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        print(f"  [chunked]   GPU after empty_cache (pre-chunk-2): {_gpu_stats()}", flush=True)

    chunk_idx = 2
    melody_total_samples = melody_wav.shape[-1] if melody_wav is not None else 0

    # --- Chunks 2..N ---
    #
    # If melody_wav still has audio covering this chunk's time window, generate
    # it fresh via generate_with_chroma conditioned on that slice (same as
    # chunk 1) — no continuation tail involved, so the full call's output is
    # new audio. Once melody_wav runs out, fall back to generate_continuation:
    # it returns D seconds of audio TOTAL including the prompt tail fed in, so
    # after stripping the prompt each call contributes only
    # (D - _CONTINUATION_PROMPT_SEC) seconds of new audio — we request
    # (needed + _CONTINUATION_PROMPT_SEC) per call so the stripped output
    # equals exactly what we need, capped at _CHUNK_SEC.
    # Accumulated samples (not the naive remaining counter) drives the loop so
    # we never exit early or produce short audio.

    while accumulated_samples < target_samples:
        remaining_samples = target_samples - accumulated_samples
        remaining_sec     = remaining_samples / sample_rate
        p = _chunk_prompt(chunk_idx)

        melody_seg = None
        if melody_wav is not None:
            seg_start = (chunk_idx - 1) * _CHUNK_SEC * melody_sr
            if seg_start < melody_total_samples:
                melody_seg = melody_wav[:, seg_start:seg_start + _CHUNK_SEC * melody_sr]

        if melody_seg is not None:
            # --- Melody-conditioned chunk: fresh generate_with_chroma call ---
            this_sec = min(remaining_sec, _CHUNK_SEC)
            print(f"  [chunked] Chunk {chunk_idx} [{_W()}]: melody segment "
                  f"{melody_seg.shape[-1]/melody_sr:.1f}s, set_generation_params({this_sec:.1f}s)...", flush=True)
            musicgen.set_generation_params(duration=this_sec)

            weight_i = this_sec / duration
            if on_progress is not None:
                expected_new_frames_i = int(this_sec * musicgen.frame_rate)
                musicgen.set_custom_progress_callback(
                    _wrap_progress_callback(on_progress, cumulative, weight_i, expected_new_frames_i)
                )

            print(f"  [chunked] Chunk {chunk_idx} [{_W()}]: generate_with_chroma({this_sec:.1f}s) STARTING...", flush=True)
            print(f"  [chunked]   GPU before generate_with_chroma: {_gpu_stats()}", flush=True)
            t_cx = time.perf_counter()
            raw_seg      = musicgen.generate_with_chroma([p], melody_seg, melody_sr, progress=(on_progress is not None))
            t_cx_elapsed = time.perf_counter() - t_cx
            cumulative  += weight_i
            print(f"  [chunked] Chunk {chunk_idx} [{_W()}]: generate_with_chroma DONE in {t_cx_elapsed:.1f}s "
                  f"-- raw shape={raw_seg.shape}", flush=True)

            new_part = raw_seg[0].cpu()
            del raw_seg

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                print(f"  [chunked]   GPU after del + empty_cache: {_gpu_stats()}", flush=True)
        else:
            # --- Continuation chunk: tail of previous chunk as audio prompt ---
            # Request enough to cover the gap + prompt overhead, but never
            # exceed the model's max generation window.
            this_sec = min(remaining_sec + _CONTINUATION_PROMPT_SEC, _CHUNK_SEC)

            # Time the prep (tail extraction) separately from generation itself.
            t_prep = time.perf_counter()
            tail = chunks[-1][:, -prompt_tail_samples:].unsqueeze(0)  # [1, C, tail] on CPU
            t_prep_elapsed = time.perf_counter() - t_prep

            print(f"  [chunked] Chunk {chunk_idx} [{_W()}]: STEP 1 — prep done in {t_prep_elapsed*1000:.0f}ms "
                  f"(need {remaining_sec:.1f}s more)", flush=True)
            print(f"  [chunked]   tail: device={tail.device}, shape={tail.shape}, sample_rate={sample_rate}", flush=True)
            print(f"  [chunked]   GPU before set_generation_params: {_gpu_stats()}", flush=True)

            print(f"  [chunked] Chunk {chunk_idx} [{_W()}]: STEP 2 — set_generation_params({this_sec:.1f}s)...", flush=True)
            musicgen.set_generation_params(duration=this_sec)

            weight_i = (this_sec - _CONTINUATION_PROMPT_SEC) / duration
            if on_progress is not None:
                expected_new_frames_i = int(max(this_sec - _CONTINUATION_PROMPT_SEC, 0) * musicgen.frame_rate)
                musicgen.set_custom_progress_callback(
                    _wrap_progress_callback(on_progress, cumulative, weight_i, expected_new_frames_i)
                )

            print(f"  [chunked] Chunk {chunk_idx} [{_W()}]: STEP 3 — generate_continuation({this_sec:.1f}s) STARTING...", flush=True)
            print(f"  [chunked]   GPU before generate_continuation: {_gpu_stats()}", flush=True)
            t_cx = time.perf_counter()
            cont       = musicgen.generate_continuation(tail, sample_rate, descriptions=[p], progress=(on_progress is not None))
            t_cx_elapsed = time.perf_counter() - t_cx
            cumulative += weight_i
            print(f"  [chunked] Chunk {chunk_idx} [{_W()}]: STEP 3 — generate_continuation RETURNED in {t_cx_elapsed:.1f}s "
                  f"-- raw shape={cont.shape}", flush=True)
            print(f"  [chunked]   GPU after generate_continuation: {_gpu_stats()}", flush=True)

            cont_audio = cont[0].cpu()
            del cont

            # Release chunk N's GPU cache before the next iteration starts.
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                print(f"  [chunked]   GPU after del cont + empty_cache: {_gpu_stats()}", flush=True)

            # Strip the prompt audio that was prepended to the output.
            if cont_audio.shape[-1] > prompt_tail_samples:
                new_part = cont_audio[:, prompt_tail_samples:]
            else:
                new_part = cont_audio

        # Trim to exactly what's needed on the last chunk (model may overshoot
        # by a few samples due to integer rounding).
        if accumulated_samples + new_part.shape[-1] > target_samples:
            new_part = new_part[:, :target_samples - accumulated_samples]

        accumulated_samples += new_part.shape[-1]
        print(f"  [chunked] Chunk {chunk_idx} [{_W()}]: DONE -- "
              f"new_part={new_part.shape[-1]/sample_rate:.1f}s, "
              f"total={accumulated_samples/sample_rate:.1f}s / {duration}s", flush=True)
        chunks.append(new_part)
        chunk_idx += 1

    if on_progress is not None:
        # Weight estimates + final-chunk sample trimming can leave cumulative
        # slightly off 1.0 in either direction — snap to exactly done.
        on_progress(1.0)
        musicgen.set_custom_progress_callback(None)

    print(f"  [chunked] Stitching {len(chunks)} chunks (crossfade={_CROSSFADE_MS}ms)... [{_W()}]", flush=True)
    t_stitch = time.perf_counter()

    result = chunks[0]
    for nxt in chunks[1:]:
        result = _crossfade_join(result, nxt, crossfade_samples)

    final_sec = result.shape[-1] / sample_rate
    print(f"  [chunked] Stitch DONE in {time.perf_counter()-t_stitch:.1f}s [{_W()}] -- "
          f"final={final_sec:.1f}s shape={result.shape}", flush=True)
    return result


# ── Arc resolution ───────────────────────────────────────────────────────────

def _resolve_arc(
    base: str,
    arc_preset: str,
    duration: int,
    arc_segments: "list[int] | None" = None,
) -> "str | list[str]":
    """Return a single prompt for short songs or a per-chunk list for long songs.

    Priority: arc_segments (custom drag values) > arc_preset (named shape) > steady.
    Lazy imports keep this module runnable as __main__ without the package path.
    """
    import math
    if duration <= _CHUNK_SEC:
        return base
    num_chunks = math.ceil(duration / _CHUNK_SEC)
    if arc_segments:
        from pipeline.arc_presets import build_arc_prompts_from_segments
        segs = list(arc_segments)
        if len(segs) < num_chunks:
            segs += [segs[-1]] * (num_chunks - len(segs))
        return build_arc_prompts_from_segments(base, segs[:num_chunks])
    if arc_preset == "steady":
        return base
    from pipeline.arc_presets import build_arc_prompts
    return build_arc_prompts(base, arc_preset, num_chunks)


# ── Private steps ─────────────────────────────────────────────────────────────

def _gif_to_prompt(image_path: Path) -> tuple[str, float]:
    """GIF path: sample up to 3 frames (first/middle/last), ONE Gemini call. Returns (prompt, elapsed)."""
    img = Image.open(image_path)
    n = getattr(img, "n_frames", 1)

    if n == 1:
        indices = [0]
    elif n == 2:
        indices = [0, n - 1]
    else:
        mid = (n - 1) // 2
        indices = [0, mid, n - 1]

    contents: list = []
    for idx in indices:
        img.seek(idx)
        frame = img.convert("RGB")
        buf = io.BytesIO()
        frame.save(buf, format="PNG")
        contents.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"))
    contents.append(_GIF_MUSIC_DIRECTION_INSTRUCTION)

    log.info("[gif] Sending %d frame(s) to Gemini in a single call (n_frames=%d).", len(indices), n)
    t0 = time.perf_counter()
    response = _get_gemini_client().models.generate_content(
        model=_GEMINI_MODEL,
        contents=contents,
    )
    return response.text.strip(), time.perf_counter() - t0


def _image_to_prompt(image_path: Path) -> tuple[str, float]:
    """Gemini step: image → MusicGen-ready text prompt. Returns (prompt, seconds)."""
    if image_path.suffix.lower() == ".gif":
        return _gif_to_prompt(image_path)

    img = Image.open(image_path)
    buf = io.BytesIO()
    fmt = (img.format or "PNG").upper()
    if fmt not in ("JPEG", "PNG", "WEBP"):
        fmt = "PNG"
    img.save(buf, format=fmt)
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt]

    t0 = time.perf_counter()
    response = _get_gemini_client().models.generate_content(
        model=_GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=buf.getvalue(), mime_type=mime),
            _MUSIC_DIRECTION_INSTRUCTION,
        ],
    )
    return response.text.strip(), time.perf_counter() - t0


def _prompt_to_wav(
    prompt: "str | list[str]",
    duration: int,
    stem: str,
    model: str = "medium",
    on_model_ready=None,
    melody_wav: "torch.Tensor | None" = None,
    melody_sr: "int | None" = None,
    filter_mode: str = "filtered",
    on_progress=None,
) -> tuple[Path, float]:
    """MusicGen step: text prompt(s) → WAV file. Returns (wav_path, seconds).

    prompt may be a single string (all chunks use the same prompt) or a list of
    strings (chunk N uses prompt[N-1], last entry repeated if list is short).

    melody_wav / melody_sr: optional reference audio for melody-conditioned
    generation (facebook/musicgen-melody only). Works on both the short path
    and the chunked path — for chunked generation, each chunk is conditioned
    on the matching slice of melody_wav (see _generate_chunked), so the
    reference's influence spans the whole song, falling back to plain
    continuation for any chunks beyond the reference's own length.

    filter_mode: "filtered" (default) runs the output through
    pipeline.postfilter.apply_postfilter before saving, to reduce EnCodec
    artifacts; "raw" saves MusicGen's output untouched.

    on_progress: optional callable(fraction: float) reporting overall 0.0-1.0
    generation progress, driven by audiocraft's real per-token callback.
    """
    import torch
    import torchaudio

    out_path    = OUTPUT_DIR / f"{stem}.wav"
    prompt_list = [prompt] if isinstance(prompt, str) else prompt

    # Log the VRAM and thread baseline BEFORE acquiring the model lock.
    # If these numbers grow song-over-song, there is a cross-generation resource leak.
    if torch.cuda.is_available():
        _bl_alloc = torch.cuda.memory_allocated(0) / 1024 ** 3
        _bl_resv  = torch.cuda.memory_reserved(0)  / 1024 ** 3
        log.info(
            "[gen] PRE-LOCK baseline (stem=%s): VRAM alloc=%.2f GB, reserved=%.2f GB, "
            "threads=%d — if these grow each song there is a leak",
            stem, _bl_alloc, _bl_resv, threading.active_count(),
        )
        print(f"  [gen] PRE-LOCK baseline: VRAM alloc={_bl_alloc:.2f} GB, "
              f"reserved={_bl_resv:.2f} GB, threads={threading.active_count()}", flush=True)

    with _model_manager.generation_context(model_name=model, on_model_ready=on_model_ready) as musicgen:
        sr = musicgen.sample_rate
        print(f"  [gen] Model ready — sample_rate={sr}, duration={duration}s, "
              f"path={'short' if duration <= _CHUNK_SEC else 'chunked'}, "
              f"precision=fp32", flush=True)
        t0 = time.perf_counter()

        if duration <= _CHUNK_SEC:
            # Short path: single MusicGen pass
            if on_progress is not None:
                expected_new_frames = int(duration * musicgen.frame_rate)
                musicgen.set_custom_progress_callback(
                    _wrap_progress_callback(on_progress, 0.0, 1.0, expected_new_frames)
                )
            if melody_wav is not None:
                print(f"  [gen] Short path: generate_with_chroma({duration}s) starting…", flush=True)
                musicgen.set_generation_params(duration=duration)
                wavs = musicgen.generate_with_chroma([prompt_list[0]], melody_wav, melody_sr, progress=(on_progress is not None))
            else:
                print(f"  [gen] Short path: generate({duration}s) starting…", flush=True)
                musicgen.set_generation_params(duration=duration)
                wavs = musicgen.generate([prompt_list[0]], progress=(on_progress is not None))
            final_audio = wavs[0].cpu()
            del wavs
            if on_progress is not None:
                # Weight/frame-count estimates can leave the last callback shy of (or
                # slightly past) 1.0 — snap to exactly done.
                on_progress(1.0)
                musicgen.set_custom_progress_callback(None)
            print(f"  [gen] Short path: done in {time.perf_counter()-t0:.1f}s", flush=True)
            if torch.cuda.is_available():
                _a = torch.cuda.memory_allocated(0) / 1024 ** 3
                _r = torch.cuda.memory_reserved(0)  / 1024 ** 3
                log.info("[gen] post-generate VRAM (lock held): alloc=%.2f GB, reserved=%.2f GB (stem=%s)", _a, _r, stem)
        else:
            # Long path: multi-chunk generation with continuation stitching.
            # melody_wav (if given) conditions chunk 1 only; later chunks continue
            # from the previous chunk's tail exactly as in the non-melody case.
            final_audio = _generate_chunked(
                musicgen, prompt_list, duration, sr,
                melody_wav=melody_wav, melody_sr=melody_sr,
                on_progress=on_progress,
            )

        musicgen_time = time.perf_counter() - t0

        if filter_mode == "filtered":
            print(f"  [gen] Applying postfilter (artifact reduction)…", flush=True)
            t_filter = time.perf_counter()
            from pipeline.postfilter import apply_postfilter
            final_audio = apply_postfilter(final_audio, sr)
            print(f"  [gen] Postfilter done in {time.perf_counter()-t_filter:.2f}s", flush=True)

        # Save inside the lock so GPU tensors are freed before we release.
        print(f"  [gen] Saving WAV to {out_path.name}…", flush=True)
        torchaudio.save(str(out_path), final_audio, sr)
        print(f"  [gen] Saved.", flush=True)
        del final_audio

    # `with ... as musicgen` does not scope musicgen to the block — it's still
    # a live local here, holding the model's tensors alive via refcount even
    # after _ModelManager.force_unload() clears its own reference. Drop it
    # first so the model's refcount actually reaches zero before unloading.
    del musicgen
    _model_manager.force_unload()
    if torch.cuda.is_available():
        _a = torch.cuda.memory_allocated(0) / 1024 ** 3
        _r = torch.cuda.memory_reserved(0)  / 1024 ** 3
        log.info("[gen] post-unload VRAM: alloc=%.2f GB, reserved=%.2f GB (stem=%s)", _a, _r, stem)
    return out_path, musicgen_time


# ── Public API ────────────────────────────────────────────────────────────────

def generate_song_from_image(image_path: "str | Path", duration: int = 8, model: str = "medium", on_model_ready=None, arc_preset: str = "steady", arc_segments: "list[int] | None" = None, filter_mode: str = "filtered", on_progress=None) -> "tuple[Path, str]":
    """
    Full pipeline: image -> Gemini prompt -> MusicGen WAV.
    Returns (wav_path, prompt_used).
    on_model_ready: optional callable invoked once the model is loaded and
    locked, right before generation starts (used by the worker to update
    job status from loading_model -> processing).
    arc_preset / arc_segments: shape energy across chunks for long songs (>30s).
    arc_segments takes priority over arc_preset when provided.
    filter_mode: "filtered" (default) or "raw" — see _prompt_to_wav.
    on_progress: optional callable(fraction: float) — see _prompt_to_wav.
    """
    image_path = Path(image_path)
    job_id     = uuid.uuid4().hex[:8]

    _banner(f"image -> song  [{job_id}]")
    print(f"  Image   : {image_path.name}")
    print(f"  Duration: {duration}s")
    print(f"  Model   : facebook/musicgen-{model}")
    arc_info = f"custom ({len(arc_segments)} segments)" if arc_segments else arc_preset
    print(f"  Arc     : {arc_info}\n")

    # Step 1 — Gemini
    print(f"  Step 1 — Gemini ({_GEMINI_MODEL})")
    prompt, t_gemini = _image_to_prompt(image_path)
    print(f"  Prompt  : {prompt}")
    print(f"  Time    : {t_gemini:.1f}s\n")

    # Step 2 — MusicGen (arc resolved after Gemini so it wraps the actual prompt)
    print(f"  Step 2 — MusicGen (facebook/musicgen-{model})")
    effective = _resolve_arc(prompt, arc_preset, duration, arc_segments)
    wav_path, t_music = _prompt_to_wav(effective, duration, f"image_{job_id}", model=model, on_model_ready=on_model_ready, filter_mode=filter_mode, on_progress=on_progress)
    print(f"  Saved   : {wav_path.name}")
    print(f"  Time    : {t_music:.1f}s  ({t_music / duration:.2f}x real-time)\n")

    _summary(t_gemini, t_music)
    return wav_path, prompt


def describe_image(image_path: "str | Path") -> str:
    """Gemini only: image -> MusicGen-ready prompt text. Does NOT generate audio."""
    prompt, _ = _image_to_prompt(Path(image_path))
    return prompt


def generate_song_from_text(prompt: str, duration: int = 8, model: str = "medium", on_model_ready=None, arc_preset: str = "steady", arc_segments: "list[int] | None" = None, filter_mode: str = "filtered", on_progress=None) -> Path:
    """
    Text-only path: user prompt → MusicGen WAV. Skips Gemini entirely.
    Returns the path to the saved WAV.
    on_model_ready: optional callable (same contract as generate_song_from_image).
    arc_preset / arc_segments: shape energy across chunks for long songs (>30s).
    arc_segments takes priority over arc_preset when provided.
    filter_mode: "filtered" (default) or "raw" — see _prompt_to_wav.
    on_progress: optional callable(fraction: float) — see _prompt_to_wav.
    """
    job_id = uuid.uuid4().hex[:8]

    _banner(f"text -> song  [{job_id}]")
    print(f"  Prompt  : {prompt}")
    print(f"  Duration: {duration}s")
    print(f"  Model   : facebook/musicgen-{model}")
    arc_info = f"custom ({len(arc_segments)} segments)" if arc_segments else arc_preset
    print(f"  Arc     : {arc_info}\n")

    print(f"  Step 1 — MusicGen (facebook/musicgen-{model})")
    effective = _resolve_arc(prompt, arc_preset, duration, arc_segments)
    wav_path, t_music = _prompt_to_wav(effective, duration, f"text_{job_id}", model=model, on_model_ready=on_model_ready, filter_mode=filter_mode, on_progress=on_progress)
    print(f"  Saved   : {wav_path.name}")
    print(f"  Time    : {t_music:.1f}s  ({t_music / duration:.2f}x real-time)")
    _banner("done")

    return wav_path


def generate_song_from_audio_melody(
    melody_wav_path: "str | Path",
    prompt: str,
    duration: int = 15,
    on_model_ready=None,
    arc_preset: str = "steady",
    arc_segments: "list[int] | None" = None,
    filter_mode: str = "filtered",
    on_progress=None,
) -> Path:
    """
    Melody-conditioned path: reference audio + text prompt -> MusicGen WAV,
    using facebook/musicgen-melody's generate_with_chroma(). No Gemini step —
    the prompt is always supplied by the caller.

    Melody conditioning spans the whole song: each _CHUNK_SEC-sized output
    chunk is conditioned on the matching slice of the reference audio (chunk 1
    <- reference 0-30s, chunk 2 <- reference 30-60s, etc). If the reference is
    shorter than the requested duration, chunks beyond its length fall back to
    the same continuation/crossfade stitching used by the text/image paths,
    following the prompt (and arc shaping) rather than the reference directly.

    Model is hardcoded to "melody" — generate_with_chroma raises a hard error
    on any other checkpoint, so there is no safe value to accept as a parameter.

    arc_preset / arc_segments: shape energy across chunks for long songs
    (>_CHUNK_SEC), same contract as generate_song_from_image/_text.
    filter_mode: "filtered" (default) or "raw" — see _prompt_to_wav.
    on_progress: optional callable(fraction: float) — see _prompt_to_wav.

    Returns the path to the saved WAV (same contract as generate_song_from_text).
    """
    import torchaudio

    melody_wav_path = Path(melody_wav_path)
    job_id = uuid.uuid4().hex[:8]

    _banner(f"melody -> song  [{job_id}]")
    print(f"  Melody  : {melody_wav_path.name}")
    print(f"  Prompt  : {prompt}")
    print(f"  Duration: {duration}s")
    print(f"  Model   : facebook/musicgen-melody\n")

    wav, sr = torchaudio.load(str(melody_wav_path))  # [C, T]
    # Only need melody up to the requested output length — trim any excess up
    # front. generate_with_chroma is still called once per _CHUNK_SEC-sized
    # output chunk (see _generate_chunked), each fed the matching slice of the
    # remaining audio, so the reference's influence spans the whole song
    # rather than just its first _CHUNK_SEC seconds.
    max_samples = int(duration * sr)
    if wav.shape[-1] > max_samples:
        print(f"  [melody] Reference trimmed to requested duration "
              f"({wav.shape[-1]} -> {max_samples} samples)", flush=True)
        wav = wav[:, :max_samples]

    print(f"  Step 1 — MusicGen (facebook/musicgen-melody)")
    effective = _resolve_arc(prompt, arc_preset, duration, arc_segments)
    wav_path, t_music = _prompt_to_wav(
        effective, duration, f"melody_{job_id}", model="melody",
        on_model_ready=on_model_ready, melody_wav=wav, melody_sr=sr,
        filter_mode=filter_mode, on_progress=on_progress,
    )
    print(f"  Saved   : {wav_path.name}")
    print(f"  Time    : {t_music:.1f}s  ({t_music / duration:.2f}x real-time)")
    _banner("done")

    return wav_path


def generate_song_arc(
    prompts: "list[str]",
    duration: int = 120,
    model: str = "medium",
) -> Path:
    """
    Step 2 experiment: chunked generation with a distinct prompt per chunk.
    Not a user-facing API — call directly or via pipeline/test_arc.py.

    prompts: one string per chunk; len should equal ceil(duration / _CHUNK_SEC).
             Shorter lists are fine — the last entry is repeated for extra chunks.
    Returns the path to the saved WAV.
    """
    job_id = uuid.uuid4().hex[:8]

    _banner(f"arc -> song  [{job_id}]")
    print(f"  Prompts ({len(prompts)}):")
    for i, p in enumerate(prompts, 1):
        print(f"    Chunk {i}: {p}")
    print(f"  Duration: {duration}s")
    print(f"  Model   : facebook/musicgen-{model}\n")

    print(f"  Step 1 — MusicGen (facebook/musicgen-{model})")
    wav_path, t_music = _prompt_to_wav(prompts, duration, f"arc_{job_id}", model=model)
    print(f"  Saved   : {wav_path.name}")
    print(f"  Time    : {t_music:.1f}s  ({t_music / duration:.2f}x real-time)")
    _banner("done")

    return wav_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _banner(label: str) -> None:
    print(f"\n{'=' * 64}")
    print(f"  {label}")
    print(f"{'=' * 64}")


def _summary(t_gemini: float, t_music: float) -> None:
    total = t_gemini + t_music
    print(f"  Total   : {total:.1f}s  "
          f"(Gemini {t_gemini:.1f}s + MusicGen {t_music:.1f}s)")
    _banner("done")


# ── Test harness ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("\nPhase E — Pipeline end-to-end test")
    print("Generates two songs using ONE MusicGen model load.\n")

    # Resolve test image — prefer Phase D sample images, accept CLI override
    if len(sys.argv) > 1:
        test_image = Path(sys.argv[1])
        if not test_image.exists():
            raise SystemExit(f"Image not found: {test_image}")
    else:
        _sample_dir = Path(__file__).parent.parent / "gemini_test" / "sample_images"
        test_image  = next(
            (p for name in ("moody_night.png", "energetic_chaos.png", "calm_landscape.png")
             if (p := _sample_dir / name).exists()),
            None,
        )
        if test_image is None:
            raise SystemExit(
                "No sample images found.\n"
                "Run  python gemini_test/image_to_prompt.py  first to generate them,\n"
                "or pass an image path:  python pipeline/generate_song.py my_photo.jpg"
            )
        print(f"Using sample image: {test_image.name}")
        print("(Pass your own image path as an argument for better prompts.)\n")

    t_wall = time.perf_counter()

    # --- Run 1: image -> song (loads MusicGen here, first time) ---
    wav1, _prompt1 = generate_song_from_image(test_image, duration=8)

    # --- Run 2: text → song (MusicGen must NOT reload) ---
    wav2 = generate_song_from_text(
        "melancholic lo-fi hip hop, mellow Rhodes piano, vinyl crackle, 70 bpm, nostalgic and warm",
        duration=8,
    )

    wall_time = time.perf_counter() - t_wall

    print("\n" + "=" * 64)
    print("  FINAL SUMMARY")
    print("-" * 64)
    print(f"  Song 1 (image->song) : output/{wav1.name}")
    print(f"  Song 2 (text->song)  : output/{wav2.name}")
    print(f"  Total wall time     : {wall_time:.1f}s for both songs")
    print(f"  MusicGen            : loaded once, reused for song 2")
    print("=" * 64 + "\n")
