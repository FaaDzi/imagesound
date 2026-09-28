"""Does melody extraction find the tune, or the overtones above it?

`skyline` takes the highest note sounding, which is the standard heuristic and
has an obvious hazard on transcribed audio: Basic Pitch reports overtones as
real simultaneous notes, and an overtone is by definition *above* the note that
produced it. Whenever it fires, skyline prefers the artifact.

There is no labelled melody for generated music, so this builds one: a known
tune, plus the accompaniment and the overtone artifacts a transcriber would add
around it, as MIDI. That isolates the extraction step from the transcriber —
the question here is which note an algorithm picks given a realistic mess, not
how good Basic Pitch is.

Overtones are modelled the way they were measured on real output: a copy of a
note an octave, an octave-and-a-fifth or two octaves up, quieter, starting at
about the same time and usually not lasting as long.
"""

import random

# Partial, relative velocity, share of the parent note's length.
_OVERTONES = ((12, 0.55, 0.7), (19, 0.40, 0.5), (24, 0.30, 0.4))

_SCALE = [0, 2, 4, 5, 7, 9, 11]


def make_case(seed=0, bars=32, bpm=120, overtone_rate=0.6, with_chords=True):
    """Return (notes_given_to_the_extractor, true_melody_notes)."""
    import pretty_midi

    rng = random.Random(seed)
    beat = 60.0 / bpm
    given, truth = [], []

    def note(pitch, start, dur, vel):
        return pretty_midi.Note(velocity=vel, pitch=pitch, start=start,
                                end=start + dur)

    root = 60
    for b in range(bars):
        t0 = b * 4 * beat
        # Melody: two to four notes a bar, in a comfortable singing register.
        n_notes = rng.choice((2, 3, 4))
        step = 4 * beat / n_notes
        for k in range(n_notes):
            deg = rng.randrange(len(_SCALE))
            octv = rng.choice((0, 0, 0, 12))
            pitch = root + 7 + _SCALE[deg] + octv
            start = t0 + k * step + rng.uniform(-0.01, 0.01)
            dur = step * rng.uniform(0.6, 0.95)
            vel = rng.randint(85, 110)
            m = note(pitch, start, dur, vel)
            truth.append(m)
            given.append(note(pitch, start, dur, vel))
            # The artifacts a transcriber hangs off it.
            for semis, vscale, dscale in _OVERTONES:
                if rng.random() < overtone_rate:
                    given.append(note(pitch + semis, start + rng.uniform(0, 0.02),
                                      dur * dscale, max(1, int(vel * vscale))))
        if with_chords:
            # Accompaniment underneath, as a stem carrying a lead often has.
            for k in (0, 2):
                for off in (0, 4, 7):
                    given.append(note(root - 5 + off, t0 + k * 2 * beat,
                                      2 * beat * 0.9, rng.randint(55, 70)))
    return given, truth


def score(extracted, truth, onset_tol=0.05):
    """Note-level P/R/F1: right pitch, onset within tolerance."""
    det = sorted((n.start, n.pitch) for n in extracted)
    used = [False] * len(det)
    hits = 0
    for n in sorted(truth, key=lambda n: n.start):
        for i, (dt, dp) in enumerate(det):
            if not used[i] and dp == n.pitch and abs(dt - n.start) <= onset_tol:
                used[i] = True
                hits += 1
                break
    prec = hits / len(det) if det else 0.0
    rec = hits / len(truth) if truth else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1


def octave_error_rate(extracted, truth, onset_tol=0.05):
    """Share of extracted notes that are a true note transposed up an octave
    or more — i.e. an overtone chosen in place of the note itself."""
    if not extracted:
        return 0.0
    by_start = sorted(truth, key=lambda n: n.start)
    wrong = 0
    for e in extracted:
        near = [t for t in by_start if abs(t.start - e.start) <= onset_tol]
        if near and any(e.pitch > t.pitch and (e.pitch - t.pitch) % 12 in (0, 7)
                        for t in near) and not any(e.pitch == t.pitch for t in near):
            wrong += 1
    return wrong / len(extracted)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pipeline.lead_sheet import skyline

    print(f"  {'case':28} {'notes':>6} {'P':>6} {'R':>6} {'F1':>6} {'oct err':>8}")
    for rate in (0.0, 0.3, 0.6, 0.9):
        for chords in (False, True):
            given, truth = make_case(seed=1, overtone_rate=rate, with_chords=chords)
            got = skyline(given)
            p, r, f = score(got, truth)
            oe = octave_error_rate(got, truth)
            tag = f"overtones {rate:.0%}, {'chords' if chords else 'melody only'}"
            print(f"  {tag:28} {len(got):6} {p:6.2f} {r:6.2f} {f:6.2f} {oe:8.0%}")
