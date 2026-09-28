"""Ground truth for the kick/snare/hat classifier.

There is no labelled drum stem to test against, so this builds one: synthesised
kicks, snares and hats at known times, rendered to a WAV that the real
_transcribe_drums() then reads back. Accuracy is measured against the labels
the renderer used, matched by nearest onset within 30 ms.

Two conditions, because the failure being chased is specific: a plain drum
pattern, and the same pattern under a sustained 808 that rings through
everything -- which is what a phonk drum stem actually looks like.
"""
import numpy as np
import soundfile as sf

SR = 44100


def _env(n, decay):
    return np.exp(-np.linspace(0, decay, n))


def kick(dur=0.35):
    n = int(SR * dur)
    t = np.arange(n) / SR
    # Pitch drop 90 -> 45 Hz, the standard synthesised kick.
    f = 45 + 45 * np.exp(-t * 25)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 9)
    click = np.random.randn(n) * _env(n, 400) * 0.08
    return (body + click) * 0.9


def snare(dur=0.22):
    n = int(SR * dur)
    t = np.arange(n) / SR
    tone = (np.sin(2 * np.pi * 190 * t) + np.sin(2 * np.pi * 330 * t)) * _env(n, 22) * 0.35
    noise = np.random.randn(n) * _env(n, 16)
    # Rolled off below ~250 Hz the way a real snare is.
    noise = np.convolve(noise, [1, -0.92], mode="same")
    return (tone + noise) * 0.6


def hat(dur=0.07, open_=False):
    n = int(SR * (0.32 if open_ else dur))
    noise = np.random.randn(n) * _env(n, 12 if open_ else 55)
    for _ in range(3):                      # crude high-pass: differentiate
        noise = np.convolve(noise, [1, -0.95], mode="same")
    return noise * 0.35


def eight_o_eight(freq, dur):
    """A sustained sub that rings under the whole bar -- the phonk condition."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * freq * t) * _env(n, 2.2) * 0.85


# Beat offsets per bar. Deliberately NON-COINCIDENT: one onset carries exactly
# one true label, so this measures the classifier and not the detector. A real
# kit puts a hat on top of every kick, and a single onset can then only ever
# carry one of the two labels -- that is a limitation of one-note-per-onset
# transcription, and mixing it in here would just cap the score at a number
# that says nothing about which band the classifier picked.
PATTERNS = {
    "trap":    {"kick": (0.0, 2.5), "snare": (1.0, 3.0), "hat": (0.5, 1.5, 2.25, 3.5)},
    "four":    {"kick": (0.0, 1.0, 2.0, 3.0), "snare": (1.5, 3.5), "hat": (0.5, 2.5, 3.25)},
    "sparse":  {"kick": (0.0, 2.75), "snare": (2.0,), "hat": (1.0, 3.5)},
    "no_hats": {"kick": (0.0, 1.5, 2.5), "snare": (1.0, 3.5), "hat": ()},
}


def render(bpm=150, bars=16, with_808=False, seed=0, pattern="trap"):
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    bar = beat * 4
    total = int(SR * (bars * bar + 1.0))
    buf = np.zeros(total)
    truth = []

    def place(sig, t, label):
        i = int(t * SR)
        end = min(total, i + len(sig))
        buf[i:end] += sig[:end - i]
        if label:
            truth.append((t, label))

    make = {"kick": lambda: kick(), "snare": lambda: snare(), "hat": lambda: hat()}
    for b in range(bars):
        t0 = b * bar
        for label, offsets in PATTERNS[pattern].items():
            for off in offsets:
                place(make[label](), t0 + off * beat, label)
        if with_808:
            # One long sub per bar, plus a second mid-bar, both ringing over the hits.
            place(eight_o_eight(rng.choice([41.2, 43.7, 49.0]), bar * 0.55), t0, None)
            place(eight_o_eight(rng.choice([36.7, 38.9]), bar * 0.45), t0 + 2 * beat, None)

    buf /= np.abs(buf).max() * 1.05
    return buf, sorted(truth)


def score(detected, truth, tol=0.030):
    """Match each true hit to the nearest detection and tally the labels."""
    from pipeline.midi_convert import _GM_KICK, _GM_SNARE, _GM_HAT
    name = {_GM_KICK: "kick", _GM_SNARE: "snare", _GM_HAT: "hat"}
    det = sorted((n.start, name[n.pitch]) for n in detected)
    if not det:
        return 0.0, {}, 0
    times = np.array([d[0] for d in det])
    hits, confusion, missed = 0, {}, 0
    for t, label in truth:
        j = int(np.argmin(np.abs(times - t)))
        if abs(times[j] - t) > tol:
            missed += 1
            continue
        got = det[j][1]
        confusion[(label, got)] = confusion.get((label, got), 0) + 1
        hits += got == label
    matched = len(truth) - missed
    return (hits / matched if matched else 0.0), confusion, missed


def report(tag, detected, truth):
    acc, conf, missed = score(detected, truth)
    print(f"\n{tag}")
    print(f"  {len(detected)} detected, {len(truth)} true, {missed} true hits with no onset within 30ms")
    print(f"  label accuracy on matched hits: {acc*100:.1f}%")
    labels = ["kick", "snare", "hat"]
    print("  " + "true/got".ljust(10) + "".join(f"{g:>8}" for g in labels))
    for tl in labels:
        row = "".join(f"{conf.get((tl, g), 0):>8}" for g in labels)
        print(f"  {tl:10}{row}")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from pipeline.midi_convert import _transcribe_drums

    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    overall = []
    for pattern in PATTERNS:
        for with_808 in (False, True):
            for bpm, seed in ((150, 0), (92, 3)):
                buf, truth = render(bpm=bpm, with_808=with_808, seed=seed, pattern=pattern)
                path = out / "bench.wav"
                sf.write(str(path), buf, SR)
                acc, conf, missed = score(_transcribe_drums(path), truth)
                tag = f"{pattern:8} {'808 ' if with_808 else 'dry '} {bpm}bpm"
                overall.append((tag, acc, conf, missed, len(truth)))

    labels = ["kick", "snare", "hat"]
    print("\n  " + "case".ljust(22) + "acc    unmatched   " +
          "  ".join(f"{l}->{l[:2]}" for l in labels))
    for tag, acc, conf, missed, n in overall:
        per = "  ".join(
            f"{conf.get((l, l), 0):>3}/{sum(v for (t, _), v in conf.items() if t == l):<3}"
            for l in labels)
        print(f"  {tag:22} {acc*100:5.1f}%  {missed:>3}/{n:<5}  {per}")
    print(f"\n  MEAN ACCURACY {100*sum(a for _, a, *_ in overall)/len(overall):.1f}%")
