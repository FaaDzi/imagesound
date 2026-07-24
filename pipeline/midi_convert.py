"""
Multi-track audio-to-MIDI conversion.

Pipeline
--------
1. Demucs htdemucs_6s separates the source WAV into stems (drums / bass /
   other / vocals / guitar / piano) entirely on CPU — keeps the GPU free for
   MusicGen.
2. Each non-silent stem is transcribed by Basic Pitch (shared ONNX model,
   loaded once and reused across all stems and conversions).
3. Per-stem note lists are assembled into a single multi-track .mid file with
   General-MIDI instrument assignments.
4. A sonified preview WAV is written from the combined MIDI for in-browser
   playback.

Memory discipline
-----------------
- Basic Pitch: loaded once, held as a module-level singleton (same pattern as
  the MusicGen model manager).
- Demucs: loaded per conversion, then immediately del'd + gc.collect()'d after
  separation so it doesn't linger while Basic Pitch transcribes each stem.
- Stem WAV temp files live in a TemporaryDirectory and are auto-deleted after
  all transcription is done.
"""

import gc
import logging
import tempfile
import threading
import wave as _wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


# ── Basic Pitch singleton ──────────────────────────────────────────────────────

_bp_model = None
_bp_lock = threading.Lock()


def _get_bp_model():
    """Return the cached Basic Pitch ONNX model, loading it on first call."""
    global _bp_model
    if _bp_model is None:
        with _bp_lock:
            if _bp_model is None:
                log.info("[midi] Loading Basic Pitch ONNX model…")
                from basic_pitch.inference import Model
                from basic_pitch import ICASSP_2022_MODEL_PATH
                _bp_model = Model(ICASSP_2022_MODEL_PATH)
                log.info("[midi] Basic Pitch model ready.")
    return _bp_model


# ── General-MIDI instrument map per stem ──────────────────────────────────────

# The order here matches htdemucs_6s' model.sources — drums first, then the others.
_STEM_ORDER = ["drums", "bass", "other", "vocals", "guitar", "piano"]

_STEM_GM: dict[str, dict] = {
    "drums":  {"program": 0,  "is_drum": True,  "name": "Drums"},
    "bass":   {"program": 32, "is_drum": False, "name": "Bass"},      # Acoustic Bass
    "other":  {"program": 0,  "is_drum": False, "name": "Other"},     # Acoustic Grand Piano
    "vocals": {"program": 52, "is_drum": False, "name": "Vocals"},    # Choir Aahs
    "guitar": {"program": 27, "is_drum": False, "name": "Guitar"},    # Electric Guitar (clean)
    "piano":  {"program": 0,  "is_drum": False, "name": "Piano"},     # Acoustic Grand Piano
}

_SILENCE_DB = -50.0   # stems whose RMS is below this are skipped


# ── Demucs separation ──────────────────────────────────────────────────────────

def _separate_stems(wav_path: Path, tmp_dir: Path) -> dict[str, Path]:
    """
    Run Demucs htdemucs separation on CPU and write each non-silent stem
    as a 22050 Hz mono WAV into tmp_dir.

    The Demucs model is del'd and gc.collect()'d before this function returns
    so it doesn't compete with MusicGen or Basic Pitch for memory.

    Returns {stem_name: Path} — only contains stems above the silence threshold.
    """
    import torch
    import torchaudio
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    log.info("[demucs] Loading htdemucs_6s model (CPU only)…")
    demucs = get_model("htdemucs_6s")
    demucs.eval()
    # Never move to CUDA — keeping separation on CPU prevents VRAM contention.

    target_sr   = demucs.samplerate          # 44100 for htdemucs
    stem_names  = list(demucs.sources)       # ["drums", "bass", "other", "vocals"]

    wav, sr = torchaudio.load(str(wav_path))  # [C, T]
    log.info("[demucs] Source: %d ch, %d Hz, %.1f s",
             wav.shape[0], sr, wav.shape[1] / sr)

    # htdemucs requires exactly 2 channels
    if wav.shape[0] == 1:
        wav = wav.expand(2, -1).contiguous()
    elif wav.shape[0] > 2:
        wav = wav[:2]

    if sr != target_sr:
        wav = torchaudio.functional.resample(wav, sr, target_sr)
        sr = target_sr

    # Standard demucs normalisation
    ref        = wav.mean(0)
    mean, std  = ref.mean(), ref.std() + 1e-8
    wav_norm   = ((wav - mean) / std).unsqueeze(0)   # [1, 2, T]

    log.info("[demucs] Separating stems on CPU (30–120 s for long songs)…")
    with torch.no_grad():
        sources = apply_model(demucs, wav_norm, device="cpu", num_workers=0)
    # sources: [1, n_stems, 2, T]
    sources = sources * std + mean      # denormalise

    # Release Demucs immediately — several hundred MB
    del demucs, wav, wav_norm, ref
    gc.collect()
    log.info("[demucs] Separation done. Model released.")

    bp_sr = 22050
    stem_paths: dict[str, Path] = {}

    for i, name in enumerate(stem_names):
        stem_stereo = sources[0, i]             # [2, T]
        stem_mono   = stem_stereo.mean(0)       # [T]

        rms = float(stem_mono.pow(2).mean().sqrt())
        db  = 20.0 * np.log10(rms + 1e-12)
        if db < _SILENCE_DB:
            log.info("[demucs] '%s' silent (%.1f dB) — skipping", name, db)
            continue

        # Resample to 22050 Hz for Basic Pitch
        stem_bp = (
            torchaudio.functional.resample(stem_mono.unsqueeze(0), sr, bp_sr)
            .squeeze(0).numpy()
        )
        arr = (np.clip(stem_bp, -1.0, 1.0) * 32767).astype(np.int16)

        out = tmp_dir / f"stem_{name}.wav"
        with _wave.open(str(out), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(bp_sr)
            wf.writeframes(arr.tobytes())

        log.info("[demucs] '%s': %.1f dB → %s (%d samples)",
                 name, db, out.name, len(arr))
        stem_paths[name] = out

    del sources
    gc.collect()
    return stem_paths


# ── Transcription and assembly ─────────────────────────────────────────────────

def _transcribe_stem(stem_path: Path, bp_model) -> list:
    """Run Basic Pitch on one stem WAV. Returns the notes list (may be empty)."""
    from basic_pitch.inference import predict
    _, midi_data, _ = predict(str(stem_path), bp_model)
    if not midi_data.instruments:
        return []
    return list(midi_data.instruments[0].notes)


def _build_multitrack_midi(stem_notes: dict[str, list]):
    """
    Assemble a pretty_midi.PrettyMIDI from per-stem note lists.
    Stems with no notes are silently excluded.
    GM instrument fallback: unrecognised stem names get program 0, non-drum.
    """
    import pretty_midi

    midi = pretty_midi.PrettyMIDI()
    for name in _STEM_ORDER:
        notes = stem_notes.get(name)
        if not notes:
            continue
        gm   = _STEM_GM.get(name, {"program": 0, "is_drum": False, "name": name.capitalize()})
        inst = pretty_midi.Instrument(
            program=gm["program"],
            is_drum=gm["is_drum"],
            name=gm["name"],
        )
        inst.notes = notes
        midi.instruments.append(inst)
        log.info("[midi] Track '%s': %d notes (program=%d, drum=%s)",
                 name, len(notes), gm["program"], gm["is_drum"])
    return midi


# ── Preview WAV ────────────────────────────────────────────────────────────────

def _write_preview_wav(midi_data, preview_path: Path) -> None:
    """Synthesize midi_data to a mono 22050 Hz WAV for in-browser preview.

    Uses pretty_midi's built-in sine-wave synthesizer — no FluidSynth needed.
    Writes a standard PCM WAV via stdlib so there are no extra dependencies.
    """
    audio = midi_data.synthesize(fs=22050, wave=np.sin)
    arr   = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with _wave.open(str(preview_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(arr.tobytes())
    log.info("[midi] Preview WAV: %s (%d samples)", preview_path.name, len(arr))


# ── Public entry point ─────────────────────────────────────────────────────────

def convert_to_midi(wav_path: Path, out_path: Path) -> int:
    """
    Convert a WAV to a multi-track MIDI + sonified preview WAV.

    wav_path  – clean source WAV (avoid reverb-processed audio; reverb muddies BP)
    out_path  – destination .mid file (parent directory created if needed)

    Returns the total note count across all tracks.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("[midi] Multi-track conversion: %s → %s", wav_path.name, out_path.name)

    with tempfile.TemporaryDirectory(prefix="imgsnd_stems_") as tmp:
        tmp_dir = Path(tmp)

        # 1. Separate into stems — Demucs model released before this returns
        stem_paths = _separate_stems(wav_path, tmp_dir)

        if not stem_paths:
            # Degenerate case: every stem was below the silence threshold.
            # Fall back to transcribing the whole file as a single "other" track.
            log.warning("[midi] All stems silent — falling back to whole-file transcription")
            stem_paths = {"other": wav_path}

        # 2. Transcribe each stem with the shared Basic Pitch model
        bp_model = _get_bp_model()
        stem_notes: dict[str, list] = {}
        for name, path in stem_paths.items():
            log.info("[midi] Transcribing '%s' stem…", name)
            notes = _transcribe_stem(path, bp_model)
            if notes:
                stem_notes[name] = notes
            else:
                log.info("[midi] '%s' produced 0 notes — track omitted", name)

    # TemporaryDirectory exits here: all stem WAV files are deleted.

    # 3. Assemble multi-track MIDI
    if not stem_notes:
        log.warning("[midi] No notes produced — writing empty MIDI")
    midi_data = _build_multitrack_midi(stem_notes)

    # 4. Write MIDI file
    midi_data.write(str(out_path))

    # 5. Write preview WAV alongside the MIDI
    preview_path = out_path.with_name(out_path.stem + "_preview.wav")
    try:
        _write_preview_wav(midi_data, preview_path)
    except Exception:
        log.exception("[midi] Sonification failed — preview unavailable, MIDI still saved")

    note_count = sum(len(n) for n in stem_notes.values())
    log.info("[midi] Done — %d notes across %d track(s): %s",
             note_count, len(stem_notes), list(stem_notes))
    return note_count
