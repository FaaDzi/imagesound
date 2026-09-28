"""Is a transcription actually of this song?

There is no ground-truth MIDI for generated audio, so quality cannot be scored
by comparing against a reference. What can be done is ask whether the notes
that were written down are present in the recording, and whether the harmony
moves the way the recording's harmony moves.

Two measures, both reported against null baselines. The baselines are the point:
a similarity number on its own is meaningless because two unrelated pieces of
tonal music already share most of their pitch content. Only the gap over a null
says information was captured.

  note support   For each transcribed note, is there energy at that pitch, at
                 that time, in the source? Scored as the note's rank among all
                 semitone bins in its frames, so it asks "was this pitch among
                 the prominent ones" rather than "was it audible at all". Catches
                 hallucinated notes -- overtones, separation artifacts, leakage.

  chroma agreement  Frame-by-frame cosine similarity between the MIDI's pitch
                 class profile and the recording's. Catches wrong key, wrong
                 chords, and timing that has slipped.

Nulls, for both:
  tritone   the same notes moved 6 semitones. Identical rhythm, density and
            range; maximally wrong pitch. This is the important one.
  shifted   the same notes slid 30s through the song. Right pitch vocabulary,
            wrong moment.
  shuffled  (note support only) each note's pitch redrawn from the track's own
            pitch distribution. Right vocabulary and timing, unrelated melody.

Usage:
    python scripts/amt_eval.py <audio.wav> <transcription.mid>
"""

import sys
from pathlib import Path

import librosa
import numpy as np
import pretty_midi

SR = 22050
HOP = 512
FMIN_NOTE = 24          # C1; below this the CQT needs windows longer than a note
N_BINS = 84             # 7 octaves, one bin per semitone
SUPPORT_PERCENTILE = 75  # a note counts as supported above this rank in its frame


def _cqt_semitones(y, sr):
    """Magnitude CQT with exactly one bin per semitone, from C1 up."""
    C = np.abs(librosa.cqt(y=y, sr=sr, hop_length=HOP,
                           fmin=librosa.midi_to_hz(FMIN_NOTE),
                           n_bins=N_BINS, bins_per_octave=12))
    return C


def note_support(midi: pretty_midi.PrettyMIDI, C: np.ndarray, sr=SR) -> float:
    """Share of notes whose pitch is prominent in the source while they sound.

    Rank within the frame rather than absolute energy: loud passages would
    otherwise pass every note and quiet ones fail every note, which measures
    the mix and not the transcription.
    """
    notes = [n for inst in midi.instruments if not inst.is_drum for n in inst.notes]
    if not notes:
        return float("nan")
    frames_per_sec = sr / HOP
    n_frames = C.shape[1]
    # Percentile threshold per frame, computed once.
    thresh = np.percentile(C, SUPPORT_PERCENTILE, axis=0)

    supported = 0
    for n in notes:
        b = n.pitch - FMIN_NOTE
        if not 0 <= b < N_BINS:
            continue
        f0 = int(n.start * frames_per_sec)
        f1 = max(f0 + 1, int(n.end * frames_per_sec))
        f0, f1 = max(0, f0), min(n_frames, f1)
        if f0 >= f1:
            continue
        # Supported if the pitch is prominent for any part of the note: a held
        # note whose tail is masked by a louder part is still a real note.
        if np.any(C[b, f0:f1] >= thresh[f0:f1]):
            supported += 1
    return supported / len(notes)


def chroma_agreement(midi: pretty_midi.PrettyMIDI, C: np.ndarray, sr=SR) -> float:
    """Mean per-frame cosine similarity of pitch-class profiles.

    Averaged over every frame where the *recording* has energy, scoring zero
    where the transcription has nothing to say. Restricting to frames where
    both sides sound -- which an earlier version did -- makes deleting notes
    free: the frames they used to cover simply leave the average, so a
    transcription of the bass alone scored far above a full one. Coverage has
    to cost something, or the metric rewards transcribing less.
    """
    fs = sr / HOP
    m = midi.get_chroma(fs=fs)
    a = np.zeros((12, C.shape[1]))
    for b in range(C.shape[0]):
        a[(FMIN_NOTE + b) % 12] += C[b]
    w = min(m.shape[1], a.shape[1])
    if w < 10:
        return float("nan")
    m, a = m[:, :w], a[:, :w]
    audible = a.sum(0) > 0
    if audible.sum() < 10:
        return float("nan")
    m, a = m[:, audible], a[:, audible]
    mn = np.linalg.norm(m, axis=0)
    an = np.linalg.norm(a, axis=0)
    sim = np.zeros(m.shape[1])
    ok = mn > 0
    sim[ok] = (m[:, ok] * a[:, ok]).sum(0) / (mn[ok] * an[ok])
    return float(sim.mean())


def _transpose(midi, semitones):
    out = pretty_midi.PrettyMIDI()
    for inst in midi.instruments:
        c = pretty_midi.Instrument(program=inst.program, is_drum=inst.is_drum, name=inst.name)
        for n in inst.notes:
            p = n.pitch + semitones
            if 0 <= p < 128:
                c.notes.append(pretty_midi.Note(velocity=n.velocity, pitch=p,
                                                start=n.start, end=n.end))
        out.instruments.append(c)
    return out


def _time_shift(midi, seconds):
    end = midi.get_end_time() or 1.0
    out = pretty_midi.PrettyMIDI()
    for inst in midi.instruments:
        c = pretty_midi.Instrument(program=inst.program, is_drum=inst.is_drum, name=inst.name)
        for n in inst.notes:
            s = (n.start + seconds) % end
            c.notes.append(pretty_midi.Note(velocity=n.velocity, pitch=n.pitch,
                                            start=s, end=s + (n.end - n.start)))
        out.instruments.append(c)
    return out


def _shuffle_pitches(midi, seed=0):
    rng = np.random.default_rng(seed)
    pool = [n.pitch for inst in midi.instruments if not inst.is_drum for n in inst.notes]
    out = pretty_midi.PrettyMIDI()
    for inst in midi.instruments:
        c = pretty_midi.Instrument(program=inst.program, is_drum=inst.is_drum, name=inst.name)
        for n in inst.notes:
            p = int(rng.choice(pool)) if (pool and not inst.is_drum) else n.pitch
            c.notes.append(pretty_midi.Note(velocity=n.velocity, pitch=p,
                                            start=n.start, end=n.end))
        out.instruments.append(c)
    return out


def evaluate(audio_path: Path, midi_path: Path) -> dict:
    y, sr = librosa.load(str(audio_path), sr=SR, mono=True)
    C = _cqt_semitones(y, sr)
    midi = pretty_midi.PrettyMIDI(str(midi_path))

    variants = {
        "actual":   midi,
        "tritone":  _transpose(midi, 6),
        "shifted":  _time_shift(midi, 30.0),
        "shuffled": _shuffle_pitches(midi),
    }
    out = {}
    for name, m in variants.items():
        out[name] = {"support": note_support(m, C), "chroma": chroma_agreement(m, C)}
    n = sum(len(i.notes) for i in midi.instruments if not i.is_drum)
    out["_notes"] = n
    return out


def print_report(tag: str, res: dict) -> None:
    print(f"\n  {tag}   ({res['_notes']} pitched notes)")
    print(f"    {'':10} {'note support':>14} {'chroma':>10}")
    for k in ("actual", "tritone", "shifted", "shuffled"):
        mark = "  <-- real" if k == "actual" else ""
        print(f"    {k:10} {res[k]['support']*100:13.1f}% {res[k]['chroma']:10.3f}{mark}")
    best_null = max(res[k]["support"] for k in ("tritone", "shifted", "shuffled"))
    best_nullc = max(res[k]["chroma"] for k in ("tritone", "shifted", "shuffled"))
    print(f"    {'GAIN':10} {(res['actual']['support']-best_null)*100:+13.1f}pp "
          f"{res['actual']['chroma']-best_nullc:+10.3f}   (over best null)")


if __name__ == "__main__":
    audio, mid = Path(sys.argv[1]), Path(sys.argv[2])
    print_report(mid.name, evaluate(audio, mid))
