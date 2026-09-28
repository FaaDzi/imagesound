"""Does the transcriber work at all, on audio where every note is known?

MR-MT3 scored at null on a generated track. That has two possible causes -- the
music is out of its training distribution, or the mt3-infer wrapper feeds it
malformed features -- and they call for opposite responses. This decides which,
by transcribing a simple synthesised piano line whose notes are known exactly.

If a model misses THIS, it is broken, not merely challenged.
"""
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100


def piano_note(freq, dur, sr=SR):
    """Harmonic-rich note with a struck envelope -- crudely piano-like."""
    n = int(sr * dur)
    t = np.arange(n) / sr
    y = np.zeros(n)
    for k, amp in enumerate([1.0, 0.5, 0.28, 0.14, 0.07, 0.04], start=1):
        y += amp * np.sin(2 * np.pi * freq * k * t)
    env = np.exp(-t * 3.2) * (1 - np.exp(-t * 400))
    return y * env / 2.0


def render(seconds=20.0, bpm=100):
    """A scale, then arpeggiated triads. Monophonic then polyphonic."""
    beat = 60.0 / bpm
    total = int(SR * (seconds + 1))
    buf = np.zeros(total)
    truth = []

    def place(midi_pitch, t, dur):
        f = 440.0 * 2 ** ((midi_pitch - 69) / 12)
        sig = piano_note(f, dur)
        i = int(t * SR)
        e = min(total, i + len(sig))
        buf[i:e] += sig[:e - i]
        truth.append((t, midi_pitch))

    t = 0.0
    # C major scale up and down, two octaves, quarter notes.
    scale = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84]
    for p in scale + scale[-2::-1]:
        place(p, t, beat * 0.9)
        t += beat
    # Triads, one chord per two beats.
    for root in (60, 65, 67, 60):
        for p in (root, root + 4, root + 7):
            place(p, t, beat * 1.8)
        t += beat * 2

    buf /= np.abs(buf).max() * 1.05
    return buf[:int(SR * seconds)], [x for x in truth if x[0] < seconds]


def score(detected, truth, onset_tol=0.06):
    """Note-level precision/recall/F1: right pitch within onset_tol seconds."""
    det = sorted(detected)
    used = [False] * len(det)
    hits = 0
    for t, p in truth:
        for i, (dt, dp) in enumerate(det):
            if not used[i] and dp == p and abs(dt - t) <= onset_tol:
                used[i] = True
                hits += 1
                break
    prec = hits / len(det) if det else 0.0
    rec = hits / len(truth) if truth else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1


if __name__ == "__main__":
    out = Path(sys.argv[1])
    buf, truth = render()
    wav = out / "sanity_piano.wav"
    sf.write(str(wav), buf, SR)
    np.save(out / "sanity_truth.npy", np.array(truth))
    print(f"wrote {wav}  ({len(truth)} known notes, {len(buf)/SR:.0f}s)")
