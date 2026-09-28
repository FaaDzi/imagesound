"""
Multi-track audio-to-MIDI conversion.

Pipeline
--------
1. Demucs htdemucs_6s separates the source WAV into stems (drums / bass /
   other / vocals / guitar / piano) entirely on CPU — keeps the GPU free for
   whichever generation model is running.
2. Each non-silent stem is transcribed by Basic Pitch (shared ONNX model,
   loaded once and reused across all stems and conversions).
3. Per-stem note lists are assembled into a single multi-track .mid file with
   General-MIDI instrument assignments.
4. A sonified preview WAV is written from the combined MIDI for in-browser
   playback.

Memory discipline
-----------------
- Basic Pitch: loaded once, held as a module-level singleton (same pattern as
  the rest of the app's singletons).
- Demucs: loaded per conversion, then immediately del'd + gc.collect()'d after
  separation so it doesn't linger while Basic Pitch transcribes each stem.
- Stem WAV temp files live in a TemporaryDirectory and are auto-deleted after
  all transcription is done.
"""

import gc
import logging
import os
import tempfile
import threading
import wave as _wave
from pathlib import Path

import numpy as np

# Demucs pulls its pretrained checkpoints down on first use and would otherwise
# cache them under the user profile on the system drive. Current versions fetch
# from the Hugging Face Hub (HF_HOME) while older ones use torch.hub
# (TORCH_HOME), so both are pointed into the project. Set at import time,
# before torch/huggingface_hub read them.
_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TORCH_HOME", str(_ROOT / ".torch-cache"))
os.environ.setdefault("HF_HOME", str(_ROOT / ".hf-cache"))

# Separated stems are written to scratch before transcription and can run to
# hundreds of MB on a long song. Keep them in the project rather than %TEMP%
# on the system drive (pipeline/runner.py does the same for model workers).
_SCRATCH = Path(__file__).resolve().parents[1] / "pipeline" / "output" / ".work"

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

# One clearly distinct instrument per stem, so several tracks playing at once
# stay tellable apart by ear.
#
# This was briefly all-piano, on the reasoning that a transcription is read
# rather than performed and a uniform timbre hides nothing. Listening to it
# disproved that: three piano tracks at once are *harder* to follow than three
# different instruments, because there is no cue for which line is which. A
# sustained pad is still the wrong choice (Choir Aahs on vocals smeared exactly
# the detail worth hearing), so every patch here has a clear attack and a
# distinct register or colour.
_STEM_GM: dict[str, dict] = {
    "drums":  {"program": 0,  "is_drum": True,  "name": "Drums"},
    "bass":   {"program": 33, "is_drum": False, "name": "Bass"},      # Electric Bass (finger)
    "other":  {"program": 0,  "is_drum": False, "name": "Other"},     # Acoustic Grand Piano
    "vocals": {"program": 40, "is_drum": False, "name": "Vocals"},    # Violin -- a melody that sings
    "guitar": {"program": 27, "is_drum": False, "name": "Guitar"},    # Electric Guitar (clean)
    "piano":  {"program": 0,  "is_drum": False, "name": "Piano"},     # Acoustic Grand Piano
}

_SILENCE_DB = -50.0   # stems whose RMS is below this are skipped

# A stem this far below the loudest one is separation leakage, not a part.
# htdemucs_6s always emits all six stems whether or not the song contains the
# instrument, so a phonk track with no piano in it still gets a piano stem --
# bleed from everything else, which Basic Pitch then transcribes into confident
# notes. On one measured track that was 604 piano and 940 guitar notes out of
# 3996, from stems 26 dB and 11 dB down.
#
# 20 dB rather than something tighter because the evidence only supports
# removing the obvious cases. Measured on three tracks (see scripts/amt_eval.py),
# dropping stems past this point trades one metric against the other instead of
# improving both, and on one track it deleted the entire harmony.
_LEAKAGE_DB = 20.0


# ── Demucs separation ──────────────────────────────────────────────────────────

def _separate_stems(wav_path: Path, tmp_dir: Path, on_fraction=None) -> dict[str, Path]:
    """
    Run Demucs htdemucs separation on CPU and write each non-silent stem
    as a 22050 Hz mono WAV into tmp_dir.

    The Demucs model is del'd and gc.collect()'d before this function returns
    so it doesn't compete with Basic Pitch for memory.

    Returns {stem_name: Path} — only contains stems above the silence threshold.

    `on_fraction(f)` is called with 0..1 as Demucs works through the song --
    this is most of a conversion's wall time, so it is what makes a progress
    bar move at all.
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
    # Demucs reports each chunk's start offset as it finishes it (and which
    # model of the bag it is on); that over the song length is how far along
    # separation is. Callbacks must never break the separation itself.
    total_len = wav_norm.shape[-1]

    def _demucs_cb(d: dict) -> None:
        if on_fraction is None or d.get("state") != "end":
            return
        try:
            models = max(1, int(d.get("models", 1)))
            within = min(1.0, d.get("segment_offset", 0) / max(1, total_len))
            on_fraction((d.get("model_idx_in_bag", 0) + within) / models)
        except Exception:
            pass

    if on_fraction is not None:
        on_fraction(0.0)  # model is loaded: separation proper starts now
    with torch.no_grad():
        sources = apply_model(demucs, wav_norm, device="cpu", num_workers=0,
                              callback=_demucs_cb)
    # sources: [1, n_stems, 2, T]
    sources = sources * std + mean      # denormalise

    # Release Demucs immediately — several hundred MB
    del demucs, wav, wav_norm, ref
    gc.collect()
    log.info("[demucs] Separation done. Model released.")

    bp_sr = 22050
    stem_paths: dict[str, Path] = {}

    # Levels first, because whether a stem is worth transcribing is relative to
    # the others: -40 dB is a quiet part in a quiet mix and pure bleed in a loud
    # one, and the absolute floor alone cannot tell those apart.
    monos = [sources[0, i].mean(0) for i in range(len(stem_names))]
    levels = [20.0 * np.log10(float(m.pow(2).mean().sqrt()) + 1e-12) for m in monos]
    loudest = max(levels, default=0.0)
    # The loudest stem can be the drums, and then a quiet mix would lose every
    # pitched part to the gate and transcribe to percussion only. Whichever
    # pitched stem is loudest is always kept, however far down it sits.
    pitched = [(levels[i], n) for i, n in enumerate(stem_names) if n != "drums"]
    keep_anyway = max(pitched)[1] if pitched else None

    for i, name in enumerate(stem_names):
        stem_mono, db = monos[i], levels[i]

        if db < _SILENCE_DB:
            log.info("[demucs] '%s' silent (%.1f dB) — skipping", name, db)
            continue
        if db < loudest - _LEAKAGE_DB and name != keep_anyway:
            log.info("[demucs] '%s' leakage (%.1f dB, %.1f below loudest) — skipping",
                     name, db, loudest - db)
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


# General MIDI percussion keys, the subset worth distinguishing here.
_GM_KICK, _GM_SNARE, _GM_HAT = 36, 38, 42

# How much a band has to spike, as a fraction of that band's own loudest
# transients, before a hit is credited to it. Swept on the benchmark described
# in _transcribe_drums: accuracy is flat within a percent across 0.25-0.40
# (94.3 / 95.0 / 95.1 / 94.7) and falls off outside it, so this sits in the
# middle of a plateau rather than on a value that happened to score well.
_DRUM_FLUX_MIN = 0.32


def _transcribe_drums(stem_path: Path) -> list:
    """Transcribe the drum stem by onset detection rather than pitch tracking.

    Basic Pitch estimates *pitch*, which percussion does not really have — run
    on a drum stem it returns a handful of arbitrary notes that carry no
    rhythm. Detecting onsets and classifying each hit by which frequency band
    carries it gives a real groove and real General MIDI drum keys instead.

    The split is deliberately coarse (kick / snare / hat). Finer labels —
    toms, ride vs crash — are not reliably separable this way, and guessing
    them would just move the inaccuracy somewhere less obvious.

    Each hit is labelled from how much each band *grew*, not how loud it is.
    The earlier version scored a band's energy at the onset against that band's
    average across the whole stem, which fails completely on exactly the music
    this app generates: a sustained 808 holds the low band far above its own
    average all the time, so a kick attack can never beat the baseline. On a
    synthetic benchmark with known labels that lost **every kick in all eight
    sustained-bass cases** (0 of 31, 0 of 63, 0 of 47) and it is why a phonk
    track transcribed to 15 kicks against 357 snares.

    Positive spectral flux fixes it, because a drone contributes energy but
    almost no flux — it only grows at its own attacks. Measured over 16 cases
    (4 patterns x dry/808 x 2 tempos), mean label accuracy 63.5% -> 95.1%.

    Bands are tried low-to-high rather than taking the largest, because a snare
    has real high-frequency content: on argmax, a track whose hats are quiet or
    absent has its snares labelled hats (0 of 32 correct before, 32 of 32 now).
    """
    import librosa
    import pretty_midi

    y, sr = librosa.load(str(stem_path), sr=None, mono=True)
    if y.size == 0:
        return []

    hop = 256
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr,
                                        hop_length=hop, backtrack=True)
    if len(frames) == 0:
        return []
    times = librosa.frames_to_time(frames, sr=sr, hop_length=hop)

    n_fft = 1024
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    # Ordered low to high; the gaps between them are deliberate, so a hit has
    # to sit clearly inside a band rather than straddling two.
    bands = [
        (_GM_KICK,  freqs < 150),
        (_GM_SNARE, (freqs >= 200) & (freqs < 2500)),
        (_GM_HAT,   freqs >= 5000),
    ]
    flux = np.diff(S, axis=1, prepend=S[:, :1]).clip(min=0)
    band_flux = {p: flux[m].sum(axis=0) for p, m in bands}
    # Scale each band by its own loudest transients rather than its mean, so
    # "did this band spike" is judged against real hits and not against a drone.
    scale = {p: float(np.percentile(v, 99.5)) or 1e-9 for p, v in band_flux.items()}
    peak = float(onset_env.max()) or 1.0

    notes = []
    for t, frame in zip(times, frames):
        # One frame early: onset_detect backtracks to the foot of the rise, and
        # the growth being measured happens just after that.
        lo = max(0, int(frame) - 1)
        pitch = _GM_HAT
        for p, _ in bands:
            if float(band_flux[p][lo:lo + 4].max(initial=0.0)) / scale[p] >= _DRUM_FLUX_MIN:
                pitch = p
                break
        strength = float(onset_env[min(int(frame), len(onset_env) - 1)]) / peak
        notes.append(pretty_midi.Note(
            velocity=int(max(40, min(127, 60 + strength * 67))),
            pitch=pitch, start=float(t), end=float(t) + 0.08,
        ))

    kinds = {p: sum(1 for n in notes if n.pitch == p) for p, _ in bands}
    log.info("[midi] drums: %d hits (kick=%d snare=%d hat=%d)", len(notes),
             kinds[_GM_KICK], kinds[_GM_SNARE], kinds[_GM_HAT])
    return notes


def _analyse_rhythm(stem_path: Path) -> "tuple[float, int, bool]":
    """Return (bpm, beats_per_bar, measured) for a stem. `measured` is False
    when both trackers failed and bpm is only the 120 placeholder.

    Prefers Beat This, which tracks downbeats and so can report a real time
    signature; librosa's beat tracker gives a tempo but no meter, so the
    fallback has to assume 4/4. See pipeline/beat_track.py for why that runs
    out of process.
    """
    from pipeline import beat_track

    result = beat_track.analyse(stem_path)
    if result and 30.0 < result["bpm"] < 300.0:
        return round(float(result["bpm"]), 1), int(result["beats_per_bar"]), True
    bpm = _estimate_tempo(stem_path)
    return (bpm or 120.0), 4, bpm is not None


def _estimate_tempo(stem_path: Path) -> "float | None":
    """Beat-track a stem to get the song's tempo.

    Done here, on real audio, rather than downstream from the finished MIDI:
    once notes have been transcribed and quantized, the timing evidence a beat
    tracker needs is already degraded. The drum stem is the best cue, so
    callers pass that when separation produced one. The result is written into
    the MIDI header, so anything reading the file afterwards gets it for free.
    """
    import librosa

    try:
        y, sr = librosa.load(str(stem_path), sr=None, mono=True)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(np.atleast_1d(tempo)[0])
    except Exception:  # noqa: BLE001 -- never fail a conversion over the tempo
        log.exception("[midi] Tempo estimation failed")
        return None
    if not 30.0 < bpm < 300.0:
        return None
    log.info("[midi] Estimated tempo: %.1f BPM (from %s)", bpm, stem_path.name)
    return round(bpm, 1)


# How many notes each stem is allowed to sound at once.
#
# Basic Pitch transcribes pitch, and on a bass stem it reliably reports the
# fundamental *and* its overtones as separate simultaneous notes: measured on a
# 180s track, the bass track peaked at 5 voices and used 29 distinct pitches for
# what is played as a single line. The result reads as dense in a DAW and skews
# chord detection downstream, because the phantom notes are real pitches.
#
# Note length is NOT the problem, which is worth recording since it is the
# obvious thing to reach for: 0% of pitched notes in that file were under 80ms
# and none overlapped a repeat of the same pitch. Basic Pitch's own 127ms
# minimum already handles that.
_MAX_VOICES = {
    "bass": 1,      # a bass line is one note at a time, by definition
    "vocals": 1,    # likewise a sung line
    "guitar": 3,
    "piano": 4,
    "other": 4,
}
_DEFAULT_MAX_VOICES = 4


def _thin_polyphony(notes: list, max_voices: int, prefer_low: bool) -> list:
    """Drop notes so no more than `max_voices` sound at once.

    Walks the note starts in time order, keeping a set of notes still sounding.
    When the limit is exceeded the least-wanted note is dropped: for a bass or a
    vocal line that is the highest pitch (overtones sit above the fundamental);
    elsewhere it is the quietest, which keeps the voicing a listener would hear.
    """
    if max_voices <= 0 or not notes:
        return notes

    kept: list = []
    sounding: list = []          # notes kept so far that are still ringing
    for note in sorted(notes, key=lambda n: (n.start, n.pitch)):
        sounding = [s for s in sounding if s.end > note.start + 1e-6]
        if len(sounding) < max_voices:
            kept.append(note)
            sounding.append(note)
            continue
        # Pick the note to lose -- the incoming one counts as a candidate, so a
        # low note arriving under a stack of overtones still wins its place.
        pool = [*sounding, note]
        loser = max(pool, key=lambda n: n.pitch) if prefer_low \
            else min(pool, key=lambda n: n.velocity)
        if loser is note:
            continue
        sounding.remove(loser)
        kept.remove(loser)
        kept.append(note)
        sounding.append(note)
    return sorted(kept, key=lambda n: n.start)


# Written into a MIDI whose tempo header was measured from the audio. Readers
# (strudel_convert) otherwise can't tell a measured 120 BPM from pretty_midi's
# default 120, and used to treat every 120 as "unset": a synth-pop song the beat
# tracker put at 120 came out at 77 BPM, every note on the wrong grid.
TEMPO_MEASURED_MARK = "imagesound:tempo-measured"


def _build_multitrack_midi(stem_notes: dict[str, list], tempo: float = 120.0,
                           beats_per_bar: int = 4, tempo_measured: bool = False):
    """
    Assemble a pretty_midi.PrettyMIDI from per-stem note lists.
    Stems with no notes are silently excluded.
    GM instrument fallback: unrecognised stem names get program 0, non-drum.
    """
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    if tempo_measured:
        midi.text_events.append(pretty_midi.Text(TEMPO_MEASURED_MARK, 0.0))
    # Written so anything reading the file later -- a DAW, or our own Strudel
    # export -- gets the meter without having to re-analyse the audio.
    midi.time_signature_changes.append(
        pretty_midi.TimeSignature(numerator=beats_per_bar, denominator=4, time=0.0)
    )
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
        # Drums are already one hit per onset from our own detector; only the
        # pitch-tracked stems carry phantom overtone voices.
        if gm["is_drum"]:
            inst.notes = notes
        else:
            limit = _MAX_VOICES.get(name, _DEFAULT_MAX_VOICES)
            before = len(notes)
            inst.notes = _thin_polyphony(notes, limit, prefer_low=(limit == 1))
            if len(inst.notes) != before:
                log.info("[midi] Track '%s': %d -> %d notes (max %d voices)",
                         name, before, len(inst.notes), limit)
        midi.instruments.append(inst)
        log.info("[midi] Track '%s': %d notes (program=%d, drum=%s)",
                 name, len(inst.notes), gm["program"], gm["is_drum"])
    return midi


# ── Preview WAV ────────────────────────────────────────────────────────────────

def _piano_wave(x):
    """A harmonic stack instead of a bare sine, for the preview.

    The GM program written into the file (Acoustic Grand Piano on every pitched
    track) only reaches a DAW or an external player; pretty_midi's synthesizer
    ignores programs entirely and renders whatever `wave` is given. A pure sine
    has no harmonics at all, which makes octave errors and doubled overtones --
    the exact mistakes worth hearing in a transcription -- almost impossible to
    pick out, and chords turn to mush. These four partials at roughly 1/n
    amplitude are the cheapest thing that reads as a struck note.
    """
    return (np.sin(x) + 0.5 * np.sin(2 * x)
            + 0.25 * np.sin(3 * x) + 0.12 * np.sin(4 * x)) / 1.87


def _saw_wave(x):
    """Bright, many-harmonic tone — a bowed/lead colour that cuts through."""
    return sum(np.sin(k * x) / k for k in range(1, 9)) / 2.72


def _round_wave(x):
    """Fundamental-dominant tone for the bass, so it stays under everything."""
    return (np.sin(x) + 0.3 * np.sin(2 * x)) / 1.3


def _pluck_wave(x):
    """Odd-harmonic tone, hollower than the piano — reads as a plucked string."""
    return (np.sin(x) + 0.4 * np.sin(3 * x) + 0.2 * np.sin(5 * x)) / 1.6


# The GM programs written into the file only reach a DAW; pretty_midi's
# synthesizer ignores them entirely and renders whatever `wave` it is handed.
# To make the preview's parts distinguishable they have to differ in waveshape,
# so each track is synthesized separately with its own and the results mixed.
_PREVIEW_WAVES = {
    "Bass":   _round_wave,
    "Guitar": _pluck_wave,
    "Vocals": _saw_wave,
    "Piano":  _piano_wave,
    "Other":  _piano_wave,
}

_PREVIEW_FS = 22050

# Per-part loudness target before mixing (RMS, full scale).
_PREVIEW_TARGET_RMS = 0.12


def _synth_drums(inst, fs: int, n_samples: int):
    """Drums as noise bursts, because pretty_midi renders them as silence.

    `Instrument.synthesize` returns zeros for any is_drum track, so every
    preview so far has been pitched parts only -- the drums were missing
    entirely rather than merely quiet. These are crude one-shots, but a
    transcription preview needs the rhythm audible to be worth checking.
    """
    out = np.zeros(n_samples)
    rng = np.random.default_rng(0)
    # (decay rate, low-pass strength) per drum key; lower key = deeper sound.
    shapes = {_GM_KICK: (28.0, 0.92), _GM_SNARE: (45.0, 0.45), _GM_HAT: (140.0, 0.0)}
    for n in inst.notes:
        decay, smooth = shapes.get(n.pitch, (90.0, 0.2))
        length = int(fs * min(0.35, max(0.04, 3.0 / decay)))
        t = np.arange(length) / fs
        sig = rng.standard_normal(length)
        if smooth > 0:                      # one-pole low-pass -> body, not hiss
            for _ in range(int(smooth * 8) + 1):
                sig = np.convolve(sig, [1 - smooth, smooth], mode="same")
        if n.pitch == _GM_KICK:             # add a pitched thump under the click
            sig = sig * 0.3 + np.sin(2 * np.pi * (55 * np.exp(-t * 18) + 42) * t)
        sig *= np.exp(-t * decay) * (n.velocity / 127.0)
        i = int(n.start * fs)
        e = min(n_samples, i + length)
        if e > i:
            out[i:e] += sig[:e - i]
    return out


def _write_preview_wav(midi_data, preview_path: Path,
                       only: "str | None" = None) -> None:
    """Synthesize midi_data to a mono 22050 Hz WAV for in-browser preview.

    `only` renders a single track by name, which is what the solo buttons use:
    three parts at once is the right default for judging the whole thing, and
    hopeless for checking whether one line is right.

    No FluidSynth and no soundfont — stdlib `wave` plus pretty_midi.
    """
    fs = _PREVIEW_FS
    tracks = [i for i in midi_data.instruments if only is None or i.name == only]
    if not tracks:
        raise ValueError(f"no track named {only!r}")

    end = max((i.get_end_time() for i in tracks), default=0.0)
    n_samples = int(fs * (end + 1.0)) or fs
    mix = np.zeros(n_samples)
    for inst in tracks:
        if inst.is_drum:
            part = _synth_drums(inst, fs, n_samples)
        else:
            part = inst.synthesize(fs=fs, wave=_PREVIEW_WAVES.get(inst.name, _piano_wave))
            part = part[:n_samples]
            if part.size < n_samples:
                part = np.pad(part, (0, n_samples - part.size))
        # Level each part before mixing, in two steps.
        #
        # First to unit peak, which removes an arbitrary scale: pretty_midi sums
        # overlapping notes without normalising, so a dense track comes back at
        # RMS 30 and a sparse one near 1, and any absolute target applied to
        # that is meaningless (it pinned two parts at the clamp and left the
        # drums 60x quieter than everything else).
        #
        # Then by RMS, because peak levelling alone is not balance: drums are
        # almost entirely transient, so matching their one loud sample to a
        # sustained part leaves them far quieter to the ear. RMS tracks loudness
        # as heard. The clamp keeps a nearly-empty track from being amplified
        # into its own noise floor.
        peak = float(np.abs(part).max())
        if peak <= 0:
            continue
        part = part / peak
        rms = float(np.sqrt(np.mean(np.square(part))))
        if rms > 1e-6:
            mix += part * min(4.0, max(0.3, _PREVIEW_TARGET_RMS / rms))
    peak = float(np.abs(mix).max())
    if peak > 0:
        mix /= peak * 1.02

    arr = (np.clip(mix, -1.0, 1.0) * 32767).astype(np.int16)
    with _wave.open(str(preview_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(arr.tobytes())
    log.info("[midi] Preview WAV: %s (%d samples%s)",
             preview_path.name, len(arr), f", solo {only}" if only else "")


# ── Public entry point ─────────────────────────────────────────────────────────

def convert_to_midi(wav_path: Path, out_path: Path, on_progress=None) -> int:
    """
    Convert a WAV to a multi-track MIDI + sonified preview WAV.

    wav_path  – clean source WAV (avoid reverb-processed audio; reverb muddies BP)
    out_path  – destination .mid file (parent directory created if needed)

    on_progress – optional callback(fraction 0..1, stage label) for the UI's
                  progress bar. Weights are rough wall-time shares: separation
                  dominates, transcription is most of the rest.

    Returns the total note count across all tracks.
    """
    def report(frac: float, stage: str) -> None:
        if on_progress is not None:
            try:
                on_progress(frac, stage)
            except Exception:
                log.exception("[midi] progress callback failed")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("[midi] Multi-track conversion: %s → %s", wav_path.name, out_path.name)

    _SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="imgsnd_stems_", dir=_SCRATCH) as tmp:
        tmp_dir = Path(tmp)

        # 1. Separate into stems — Demucs model released before this returns
        report(0.02, "Loading the separator")
        stem_paths = _separate_stems(
            wav_path, tmp_dir,
            on_fraction=lambda f: report(0.25 + 0.35 * f, "Separating instruments"),
        )

        if not stem_paths:
            # Degenerate case: every stem was below the silence threshold.
            # Fall back to transcribing the whole file as a single "other" track.
            log.warning("[midi] All stems silent — falling back to whole-file transcription")
            stem_paths = {"other": wav_path}

        # 2. Transcribe each stem — drums by onset detection, the rest by
        #    Basic Pitch (loaded lazily, so a drums-only song never pays for it)
        # Rhythm comes from the drum stem where there is one, else the mix.
        report(0.62, "Finding the tempo")
        tempo, beats_per_bar, tempo_measured = _analyse_rhythm(stem_paths.get("drums", wav_path))

        bp_model = None
        stem_notes: dict[str, list] = {}
        for i, (name, path) in enumerate(stem_paths.items()):
            report(0.72 + 0.20 * i / len(stem_paths), f"Transcribing {name}")
            log.info("[midi] Transcribing '%s' stem…", name)
            if name == "drums":
                notes = _transcribe_drums(path)
            else:
                if bp_model is None:
                    bp_model = _get_bp_model()
                notes = _transcribe_stem(path, bp_model)
            if notes:
                stem_notes[name] = notes
            else:
                log.info("[midi] '%s' produced 0 notes — track omitted", name)

    # TemporaryDirectory exits here: all stem WAV files are deleted.

    # 3. Assemble multi-track MIDI
    if not stem_notes:
        log.warning("[midi] No notes produced — writing empty MIDI")
    midi_data = _build_multitrack_midi(stem_notes, tempo, beats_per_bar, tempo_measured)

    # 4. Write MIDI file
    report(0.92, "Writing the MIDI")
    midi_data.write(str(out_path))

    # 5. Write preview WAV alongside the MIDI
    report(0.94, "Rendering the preview")
    preview_path = out_path.with_name(out_path.stem + "_preview.wav")
    try:
        _write_preview_wav(midi_data, preview_path)
    except Exception:
        log.exception("[midi] Sonification failed — preview unavailable, MIDI still saved")

    # 6. Lead sheet: the same song reduced to melody / chords / bass / drums.
    # Written alongside rather than instead — the full transcription is what you
    # want if you are going to edit it in a DAW, and the reduction is what you
    # want if you are trying to work out what the song is. Failure here must not
    # cost the conversion, which has already succeeded by this point.
    try:
        from pipeline.lead_sheet import build_lead_sheet

        report(0.97, "Building the lead sheet")

        lead = build_lead_sheet(midi_data)
        if lead.instruments:
            lead_path = out_path.with_name(out_path.stem + "_lead.mid")
            lead.write(str(lead_path))
            _write_preview_wav(lead, out_path.with_name(out_path.stem + "_lead_preview.wav"))
            log.info("[midi] Lead sheet — %d notes across %d part(s): %s",
                     sum(len(i.notes) for i in lead.instruments), len(lead.instruments),
                     [i.name for i in lead.instruments])
    except Exception:
        log.exception("[midi] Lead-sheet reduction failed — full MIDI still saved")

    # Counted from the file, not from stem_notes: polyphony thinning happens
    # during assembly, so summing the inputs overstates it (4588 against an
    # actual 3996 on one measured track) -- and this is the value the caller
    # records, not just something in the log.
    note_count = sum(len(inst.notes) for inst in midi_data.instruments)
    log.info("[midi] Done — %d notes across %d track(s): %s",
             note_count, len(midi_data.instruments),
             [inst.name for inst in midi_data.instruments])
    return note_count
