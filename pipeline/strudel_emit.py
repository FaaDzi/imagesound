"""
Turning analysed MIDI into idiomatic Strudel, rather than a dump of the grid.

The previous emitter wrote every step of every bar as a literal token, which had
two consequences: the snippet only covered the first 8 bars (past that it was
unreadable), and what it did cover looked nothing like Strudel people actually
write. Hand-written Strudel stays short by *naming* things -- a chord symbol
instead of its notes, a rhythm applied with `.struct()` instead of spelled out
per sound, a repeat written `!4` instead of four times.

Doing the same here buys both at once: naming things is what makes covering a
whole three-minute song affordable.

What is derived from the audio and what is not
----------------------------------------------
Derived: tempo, meter, key, chords, drum rhythms, note pitches, arrangement.
Invented: the sounds and the effect chain. A filter sweep or a phaser is a
musical decision, not a property of a recording, so those stay to a plain
starting point and are marked in the output as the first thing to change.
"""

import itertools
import logging

log = logging.getLogger(__name__)

# Chord qualities worth naming, as semitone sets above the root. Ordered so
# that when two score equally the simpler one wins -- calling a plain triad a
# 9th chord because one passing note landed there helps nobody.
_CHORD_TEMPLATES: "list[tuple[str, tuple[int, ...]]]" = [
    ("",      (0, 4, 7)),           # major
    ("m",     (0, 3, 7)),           # minor
    ("5",     (0, 7)),              # power chord / ambiguous
    ("sus4",  (0, 5, 7)),
    ("sus2",  (0, 2, 7)),
    ("dim",   (0, 3, 6)),
    ("aug",   (0, 4, 8)),
    ("6",     (0, 4, 7, 9)),
    ("m6",    (0, 3, 7, 9)),
    ("7",     (0, 4, 7, 10)),       # dominant
    ("maj7",  (0, 4, 7, 11)),
    ("m7",    (0, 3, 7, 10)),
    ("m7b5",  (0, 3, 6, 10)),
    ("9",     (0, 4, 7, 10, 14)),
    ("maj9",  (0, 4, 7, 11, 14)),
    ("m9",    (0, 3, 7, 10, 14)),
]

_ROOT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# A bar has to be more than a passing smear before it is called a chord.
_CHORD_MIN_WEIGHT = 0.12
_CHORD_MIN_SCORE = 0.55

_DRUM_ORDER = ["bd", "sd", "hh", "oh", "cp", "rim", "rd", "cr"]

# Euclidean rhythms below this many steps are coincidences, not a pattern worth
# naming: almost any 2-of-4 bar "is" euclidean.
_EUCLID_MIN_STEPS = 4


def _bjorklund(k: int, n: int) -> "list[bool]":
    """Euclid's algorithm as a rhythm: k onsets spread as evenly as possible
    over n steps. This is what Strudel's `(k,n)` expands to.

    Implemented by the usual list-pairing method: start with k [x] groups and
    n-k [~] groups, then repeatedly append the remainder groups onto the front
    ones until at most one remainder group is left. E(3,8) comes out
    x..x..x. and E(5,8) x.xx.xx., which is what the notation means elsewhere.
    """
    if k <= 0 or k > n:
        return [False] * n
    a = [[True] for _ in range(k)]
    b = [[False] for _ in range(n - k)]
    while len(b) > 1 and len(a) > 0:
        m = min(len(a), len(b))
        paired = [a[i] + b[i] for i in range(m)]
        rest = a[m:] if len(a) > m else b[m:]
        a, b = paired, rest
    return [x for group in a + b for x in group]


def _euclid_name(hits: "list[bool]") -> "str | None":
    """`x(k,n)` if these onsets are exactly a euclidean rhythm, else None.

    Only the un-rotated form is emitted. Strudel does take a third rotation
    argument, but which way it turns is not something the docs pin down, and a
    rhythm written the wrong way round is worse than one written out in full --
    the elongation path below is always correct.
    """
    n = len(hits)
    k = sum(hits)
    if n < _EUCLID_MIN_STEPS or not 2 <= k < n:
        return None
    return f"x({k},{n})" if _bjorklund(k, n) == hits else None


def compact_steps(toks: "list[str]", rest: str = "~") -> str:
    """One bar of grid steps as the shortest mini-notation that still means it.

    A quantized bar arrives as a fixed grid -- sixteen slots whether or not
    anything is in them -- and written out literally that is what it looks
    like: `x ~ ~ ~ x ~ ~ ~ x ~ ~ ~ x ~ ~ ~`. Nobody writes Strudel that way,
    and the rest runs are most of what makes the generated snippet unreadable.
    Mini-notation has three ways of saying the same thing shorter, applied here
    in order of how much they say:

      1. halve the grid while every odd slot is empty -- a bar of quarter notes
         is a four-slot bar, not a sixteen-slot bar with twelve rests;
      2. `(k,n)` when the onsets are a euclidean rhythm;
      3. `@n` elongation, which absorbs each rest run into the event before it
         -- `x ~ ~ ~` is `x@4`. For `.struct()` only the onset matters, so this
         is exactly equivalent; for a note pattern it additionally says the
         note is held, which is nearer the truth than an instant note plus
         three silences anyway;
      4. `*n` when what is left is one token repeated evenly.

    Every step is lossless: the same events at the same times.
    """
    if not toks or all(t == rest for t in toks):
        return rest

    # 1. Drop empty odd slots while the grid stays aligned.
    while len(toks) % 2 == 0 and all(t == rest for t in toks[1::2]):
        toks = toks[0::2]

    # 2. Euclidean, when there is a single sound to distribute.
    distinct = {t for t in toks if t != rest}
    if len(distinct) == 1:
        sound = next(iter(distinct))
        euclid = _euclid_name([t != rest for t in toks])
        if euclid:
            return euclid if sound == "x" else euclid.replace("x", sound, 1)

    # 3. Absorb each run of rests into the token that precedes it.
    out: list[str] = []
    i = 0
    while i < len(toks):
        j = i + 1
        while j < len(toks) and toks[j] == rest:
            j += 1
        span = j - i
        out.append(toks[i] if span == 1 else f"{toks[i]}@{span}")
        i = j

    # 4. n copies of one token, evenly spaced, is that token n times as fast.
    if len(set(out)) == 1:
        base = out[0].split("@")[0]
        return base if len(out) == 1 else f"{base}*{len(out)}"
    return " ".join(out)


def detect_chord(weights: "dict[int, float]") -> "str | None":
    """Name the chord implied by pitch-class weights (duration-weighted).

    Scores every root against every template as
    `matched weight - unmatched weight`, so a template is rewarded for covering
    what is played and penalised for notes it fails to explain. That penalty is
    what stops the biggest template always winning.
    """
    total = sum(weights.values())
    if total <= 0:
        return None
    norm = {pc: w / total for pc, w in weights.items()}
    if max(norm.values()) < _CHORD_MIN_WEIGHT:
        return None

    best, best_score = None, 0.0
    for root in range(12):
        for name, intervals in _CHORD_TEMPLATES:
            members = {(root + i) % 12 for i in intervals}
            matched = sum(w for pc, w in norm.items() if pc in members)
            missed = sum(w for pc, w in norm.items() if pc not in members)
            # Chords whose root is not actually sounding are usually an artifact.
            if norm.get(root, 0.0) < 0.05:
                continue
            score = matched - missed
            if score > best_score + 1e-9:
                best, best_score = f"{_ROOT_NAMES[root]}{name}", score
    return best if best_score >= _CHORD_MIN_SCORE else None


def compress(seq: "list[str]") -> str:
    """Collapse consecutive repeats: [a,a,a,b] -> 'a!3 b'."""
    out = []
    for tok, group in itertools.groupby(seq):
        n = sum(1 for _ in group)
        out.append(tok if n == 1 else f"{tok}!{n}")
    return " ".join(out)


def alternation(bars: "list[str]") -> str:
    """One bar per cycle, as Strudel's <> alternation.

    Bars containing spaces are bracketed so each stays one cycle's worth
    instead of its steps being spread across the alternation.
    """
    if not bars:
        return "~"
    # Every bar identical means there is nothing to alternate between: the bar
    # *is* the pattern, and one cycle of it repeats forever on its own.
    if len(set(bars)) == 1:
        return bars[0]
    # Anything carrying an operator gets bracketed before it goes in, so that
    # the `!n` from compress() and the `*n`/`@n`/`(k,n)` from compact_steps()
    # can't bind to each other -- `[x*4]!3` is unambiguous where `x*4!3` asks
    # the reader (and the parser) to know the precedence.
    wrapped = [b if b.isalnum() or b == "~" else f"[{b}]" for b in bars]
    return f"<{compress(wrapped)}>"


def drum_struct(bar_steps: "list[list[list[str]]]", sound: str) -> "str | None":
    """A `.struct()` rhythm for one drum sound across every bar.

    One sound at a time is the idiom -- `s("bd").struct("x ~ x ~")` rather than
    a single string mixing bd, sd and hh -- because it is what a person can read
    and edit afterwards.
    """
    bars = []
    hit_any = False
    for steps in bar_steps:
        toks = []
        for slot in steps:
            if sound in slot:
                toks.append("x")
                hit_any = True
            else:
                toks.append("~")
        bars.append(compact_steps(toks))
    return alternation(bars) if hit_any else None


# Where Strudel puts scale degree 0: `.scale("A:minor")` with no octave in the
# name roots the scale in octave 3 (@strudel/tonal: `oct = 3`), so n("0") is
# A3, not A4. Counting degrees from octave 4, as this did, played every bass
# and melody line in the snippet an octave low -- measured through Strudel's
# own engine, 0% of notes at the transcribed pitch, all of them 12 semitones
# down.
SCALE_ROOT_MIDI = 48    # C3


def degrees_token(pitches: "list[int]", root_pc: int, scale: "tuple[int, ...]") -> str:
    """One step's pitches as a scale-degree token: `3`, or `[0,2,4]`."""
    if len(pitches) == 1:
        return str(_degree(pitches[0], root_pc, scale))
    return "[" + ",".join(str(_degree(p, root_pc, scale)) for p in sorted(pitches)) + "]"


def timed_bar(steps: list, fmt) -> str:
    """One bar with note lengths: a step is a list of pitches (an onset), "_"
    (the note before is still held) or "~" (silence).

    Each note becomes `tok@n` for the steps it sounds, silences `~@n`, and the
    whole bar is divided by the common factor of those lengths -- mini-notation
    weights are relative, so `a@4 ~@4` and `a ~` mean the same. Lossless, like
    compact_steps, but it keeps the rests that compact_steps folds into the
    note before them (fine for a drum onset, wrong for a melody).
    """
    import math

    runs: "list[list]" = []
    for st in steps:
        if isinstance(st, list):
            runs.append([fmt(st), 1])
        elif st == "_" and runs and runs[-1][0] != "~":
            runs[-1][1] += 1
        elif runs and runs[-1][0] == "~":
            runs[-1][1] += 1
        else:
            runs.append(["~", 1])
    if all(tok == "~" for tok, _ in runs):
        return "~"
    g = 0
    for _, n in runs:
        g = math.gcd(g, n)
    out = [tok if n // g == 1 else f"{tok}@{n // g}" for tok, n in runs]
    return out[0] if len(out) == 1 else " ".join(out)


def _degree(pitch: int, root_pc: int, scale: "tuple[int, ...]") -> int:
    """Index of `pitch` within the scale, counting 0 as the root in octave 3.

    Strudel's `.scale()` takes *scale steps*, not semitones -- `n("7")` on a
    seven-note scale is one octave up, not a fifth. Emitting semitones here
    (which an earlier version did) transposes everything wildly: -36 became 36
    scale steps down rather than three octaves. Chromatic notes that are not in
    the scale snap to the nearest degree, which is the same compromise
    `.scale()` itself makes.
    """
    rel = pitch - (SCALE_ROOT_MIDI + root_pc)
    octave, semis = divmod(rel, 12)
    idx = min(range(len(scale)), key=lambda i: abs(scale[i] - semis))
    return octave * len(scale) + idx


def chords_bar_names(bar_weights: "list[dict[int, float]]") -> "list[str]":
    """One chord symbol per bar, with gaps filled by the previous chord.

    A bar with no confident chord is usually a bar where the harmony simply did
    not change -- holding the last one reads better than a rest in the middle of
    a progression.
    """
    names: list[str] = []
    last = None
    for weights in bar_weights:
        name = detect_chord(weights) or last
        names.append(name or "~")
        if name:
            last = name
    return names


def smooth_chords(names: "list[str]") -> "list[str]":
    """Remove one-bar chords wedged between two identical neighbours.

    Transcribed pitch content is noisy enough that a single passing tone can
    outvote the real harmony for one bar. A progression that reads
    `Dm Dm F Dm Dm` almost certainly had no F in it, and the smoothing is safe
    because it only ever replaces a chord with one already present next to it.
    """
    if len(names) < 3:
        return names
    out = list(names)
    for i in range(1, len(names) - 1):
        if names[i - 1] == names[i + 1] and names[i] != names[i - 1]:
            out[i] = names[i - 1]
    return out


def split_top_level(body: str) -> "list[str]":
    """Split on whitespace that is not inside brackets."""
    items, depth, cur = [], 0, []
    for ch in body:
        if ch in "[<(":
            depth += 1
        elif ch in "]>)":
            depth -= 1
        if ch.isspace() and depth == 0:
            if cur:
                items.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        items.append("".join(cur))
    return items


def pattern_literal(pat: str, indent: str = "    ", width: int = 72) -> str:
    """A pattern as JS source: a quoted string, or a backtick one bar per line.

    Compaction gets a bar down from sixteen tokens to two or three, but sixteen
    bars of it still ran to a single 424-character line, which is its own kind
    of unreadable. Strudel patterns are ordinary JS strings, so a template
    literal can break one across lines; inside `<>` a newline is just more
    whitespace between elements. Written out this way each line is one entry in
    the alternation, which is the unit someone editing it actually wants to
    grab.
    """
    if len(pat) <= width or not (pat.startswith("<") and pat.endswith(">")):
        return f'"{pat}"'
    items = split_top_level(pat[1:-1])
    body = "\n".join(f"{indent}  {it}" for it in items)
    return f"`<\n{body}\n{indent}>`"


def pick_roles(tracks: "list[tuple[str, list]]") -> "dict[str, str]":
    """Assign each MIDI track a musical role, the way the snippet is laid out.

    Strudel written by hand has a few clear parts -- drums, harmony, bass, a
    melody -- not one part per stem a separator happened to produce. Six
    separate chord tracks playing the same progression is both unreadable and
    wrong about the music.
    """
    import statistics

    roles: dict[str, str] = {}
    pitched = [(name, notes) for name, notes in tracks if notes]
    if not pitched:
        return roles
    medians = {name: statistics.median(n.pitch for n in notes) for name, notes in pitched}
    by_pitch = sorted(medians, key=medians.get)

    roles[by_pitch[0]] = "bass"
    if len(by_pitch) > 1:
        # Melody = the highest track that actually plays enough to be a line.
        counts = {name: len(notes) for name, notes in pitched}
        candidates = [n for n in reversed(by_pitch) if n != by_pitch[0] and counts[n] >= 20]
        if candidates:
            roles[candidates[0]] = "melody"
    for name, _ in pitched:
        roles.setdefault(name, "harmony")
    return roles
