"""
Multi-track MIDI to Strudel (https://strudel.cc) pattern code.

Reads a .mid written by pipeline/midi_convert.py and emits a self-contained
JavaScript snippet that can be pasted straight into the Strudel REPL.

How the mapping works
---------------------
Strudel is cycle-based, not timeline-based: a pattern string is divided evenly
across one cycle. So the notes are quantized onto a fixed grid (16 steps per
bar, i.e. sixteenth notes in 4/4) and the whole grid is emitted as one
mini-notation string per track, with `.slow(bars)` stretching it back out so
each cycle plays exactly one bar.

Quantizing is lossy by design: any triplet or swing feel is lost, and notes
shorter than a step can collide into one another.

Why some styles survive this better than others
-----------------------------------------------
Measured on generated tracks, scoring each part's mean distance to the nearest
grid line against what uniformly random note times would score (1.0 = no better
than noise, lower = genuinely quantized):

    drums, straight 8ths ....... 0.29
    drums, straight 16ths ...... 0.51
    pitched parts, any grid .... 0.82 - 0.99

The drum track carries essentially all of the rhythm that survives. It comes
from onset detection here in the pipeline; the pitched tracks come from Basic
Pitch, whose note starts on this material are close to unquantizable. So a
style built on clear percussive transients converts well, and one carried by
sustained or heavily processed pitched material (waltz, ambient, orchestral)
converts into something rhythmically vague however the grid is set.

Things that sound like they would help and do not, both measured: a finer grid
(32nds score 0.73 on drums, i.e. worse relative to noise -- they fit everything,
which means they discriminate nothing) and dropping low-confidence notes
(filtering to the loudest or longest half moved the pitched tracks by 0.01-0.06
and the drums not at all). Triplet and swung grids also fit worse than straight
ones on every part measured, so the problem is not the grid shape.

The fix that did help is aligning the grid's phase; see _estimate_phase.

Each track also gets a sound and a short effect chain, so the snippet is
playable as-is rather than a bare list of pitches. Those are starting points
picked per instrument, not anything detected from the audio; they are the
first thing worth changing by hand.

Nothing here imports torch or Basic Pitch; it only needs pretty_midi, so it is
cheap enough to run per request rather than caching the result.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

STEPS_PER_BEAT = 4      # sixteenth notes; bar length follows the time signature
# The whole song by default (up to MAX_BARS). This was 16, which cut the
# snippet off after ~30 s -- a 2-minute song came out as its first quarter.
# Compaction and <> alternation keep a full song readable.
DEFAULT_BARS = None
MAX_STACK_PER_STEP = 4  # cap simultaneous notes so a chord stays legible

# Sound + effect chain per track. The sounds all ship with Strudel, so the
# snippet makes noise without the user first loading a soundfont pack.
_TRACK_STYLE = {
    "Drums":  {"chain": '.bank("RolandTR909")'},
    "Bass":   {"sound": "sawtooth", "chain": ".lpf(600).gain(.9)"},
    "Guitar": {"sound": "triangle", "chain": ".room(.3).gain(.7)"},
    "Piano":  {"sound": "piano",    "chain": ".room(.4).gain(.6)"},
    "Other":  {"sound": "piano",    "chain": ".room(.4).gain(.6)"},
    "Vocals": {"sound": "sine",     "chain": ".room(.5).gain(.5)"},
}
_DEFAULT_STYLE = {"sound": "piano", "chain": ".room(.3).gain(.6)"}

# General MIDI percussion keys -> Strudel drum names. midi_convert.py writes
# kick/snare/hat; the rest are here so a MIDI from elsewhere still maps.
_DRUM_NAMES = {
    35: "bd", 36: "bd", 38: "sd", 40: "sd", 39: "cp", 37: "rim",
    42: "hh", 44: "hh", 46: "oh", 49: "cr", 51: "rd",
    41: "lt", 45: "lt", 47: "mt", 48: "ht", 50: "ht",
}
# Fallback for MIDI whose "drum" track is really pitched notes (what Basic
# Pitch used to produce): split by register instead.
_DRUM_BY_REGISTER = ((50, "bd"), (65, "sd"), (128, "hh"))

_PITCH_CLASS = ["c", "cs", "d", "ds", "e", "f", "fs", "g", "gs", "a", "as", "b"]

# Krumhansl-Schmuckler key profiles, used to name the key in a comment.
_MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def _note_name(pitch: int) -> str:
    """MIDI number -> Strudel note name. Both sides use C4 = 60."""
    return f"{_PITCH_CLASS[pitch % 12]}{pitch // 12 - 1}"


def _drum_name(pitch: int) -> str:
    if pitch in _DRUM_NAMES:
        return _DRUM_NAMES[pitch]
    for hi, name in _DRUM_BY_REGISTER:
        if pitch < hi:
            return name
    return "hh"


_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

# How much the winning key must beat the best *harmonically different* key
# before its scale is trusted enough to correct notes against.
_KEY_CONFIDENCE_MIN = 0.03


def _scale_pcs(root: int, mode: str) -> frozenset:
    scale = _MAJOR_SCALE if mode == "major" else _MINOR_SCALE
    return frozenset((root + i) % 12 for i in scale)


def _estimate_key(midi):
    """Estimate the key by correlating a duration-weighted pitch-class
    histogram against the Krumhansl-Schmuckler profiles.

    Returns (name, scale_pitch_classes, confidence) or None.

    Confidence compares the winner against the best key with a *different*
    scale, not simply the runner-up: a key and its relative minor contain
    exactly the same pitch classes, so confusing the two is harmless here —
    both would correct notes identically. Only a genuinely different note set
    counts as disagreement.
    """
    hist = [0.0] * 12
    for inst in midi.instruments:
        if inst.is_drum:
            continue
        for n in inst.notes:
            hist[n.pitch % 12] += max(0.0, n.end - n.start)
    total = sum(hist)
    if total <= 0:
        return None
    hist = [h / total for h in hist]

    scored = []
    for mode, profile in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)):
        norm = sum(p * p for p in profile) ** 0.5
        for root in range(12):
            rotated = profile[-root:] + profile[:-root] if root else profile
            scored.append((sum(h * p for h, p in zip(hist, rotated)) / norm, root, mode))
    scored.sort(reverse=True)

    best_score, root, mode = scored[0]

    # Settle major vs minor on the third that is actually sounding, rather than
    # on the profile correlation alone. The profiles weight every degree, so on
    # a transcription dominated by roots and fifths -- which a bass-heavy one is
    # -- the thirds barely influence the result and the mode becomes a coin
    # flip. Measured on a pop-punk track: A major won by 0.7%, with the major
    # third (C#) at 1% of duration against the minor third (C) at 4%, and the
    # leading tone G# absent entirely.
    major_third = hist[(root + 4) % 12]
    minor_third = hist[(root + 3) % 12]
    if max(major_third, minor_third) > 0:
        mode = "major" if major_third > minor_third else "minor"

    pcs = _scale_pcs(root, mode)
    rival = next((s for s, r, m in scored[1:] if _scale_pcs(r, m) != pcs), 0.0)
    confidence = (best_score - rival) / best_score if best_score > 0 else 0.0

    name = f"{_PITCH_CLASS[root].upper().replace('S', '#')} {mode}"
    return name, pcs, confidence


def _snap_to_scale(pitch: int, pcs: frozenset) -> int:
    """Move an out-of-key note to the nearest scale tone.

    Basic Pitch reports overtones and octave errors as real notes, and those
    strays are what make stacked tracks sound sour. Nudging them by a semitone
    keeps the rhythm and the density intact while removing the clash; a
    genuine chromatic passing note gets flattened along with them, which is
    the accepted cost of cleaning up a noisy transcription.
    """
    if pitch % 12 in pcs:
        return pitch
    for delta in (-1, 1, -2, 2):
        if (pitch + delta) % 12 in pcs:
            return pitch + delta
    return pitch


_BPM_MIN, _BPM_MAX = 60.0, 180.0

# Within this much of a whole number, call it that whole number.
_BPM_SNAP = 0.75

# Tempo prior for the autocorrelation fallback: centre, and width in octaves.
_BPM_PRIOR_CENTER = 120.0
_BPM_PRIOR_WIDTH = 0.9


def _round_bpm(bpm: float) -> float:
    """Report a tempo at a precision the measurement actually supports.

    Neither source of tempo here resolves fractions of a BPM: autocorrelation
    is quantized to whole lags (over 3 BPM apart at fast tempos) and the beat
    tracker's estimate carries its own error. Printing `setcpm(139.6/4)` implies
    a precision that does not exist, and a tempo written to one decimal is also
    simply harder to read and to edit than 140.

    A whole number is used when the estimate is close to one, which covers
    essentially all produced music. Anything genuinely between whole numbers is
    left alone rather than forced, so a real 137.5 survives.
    """
    nearest = round(bpm)
    return float(nearest) if abs(bpm - nearest) <= _BPM_SNAP else round(bpm, 1)


def _estimate_bpm(midi) -> float:
    """Find the beat period by autocorrelating the onset train.

    The MIDI we read was assembled by pretty_midi with its default 120 BPM
    header and real-time note positions, so the header tells us nothing — the
    tempo has to come from the onsets. pretty_midi's own `estimate_tempo()`
    works off consecutive note gaps and octave-errors badly on this material
    (a 90 BPM lo-fi track came back as 178); autocorrelation looks for the
    period that the *whole* onset train repeats at, which is far steadier.

    Drum onsets are used when present, since they mark the beat directly and
    the drum track is detected by onset rather than pitch-transcribed.
    """
    import numpy as np

    # midi_convert.py beat-tracks the real audio and writes the result into the
    # header, which beats anything recoverable from quantized notes. Foreign
    # MIDI files carry a meaningful tempo here too. 120 is pretty_midi's
    # default and so indistinguishable from "unset" — fall through on it.
    header = None
    try:
        _, tempi = midi.get_tempo_changes()
        h = float(tempi[0])
        # midi_convert marks a header it measured; then 120 is a real 120.
        measured = any(t.text == "imagesound:tempo-measured"
                       for t in getattr(midi, "text_events", []))
        if measured or abs(h - 120.0) > 0.05:
            header = h
    except Exception:  # noqa: BLE001 -- unreadable header; estimate below
        pass

    times = [n.start for inst in midi.instruments if inst.is_drum for n in inst.notes]
    if len(times) < 8:
        times = [n.start for inst in midi.instruments for n in inst.notes]
    if len(times) < 8:
        return _round_bpm(header) if header and _BPM_MIN <= header <= _BPM_MAX else 120.0

    fs = 100  # onset grid, Hz — 10 ms is well under a sixteenth at any tempo
    env = np.zeros(int((max(times) + 1.0) * fs))
    for t in times:
        idx = int(t * fs)
        if 0 <= idx < env.size:
            env[idx] += 1.0
    env -= env.mean()

    ac = np.correlate(env, env, mode="full")[env.size - 1:]
    lo, hi = int(fs * 60 / _BPM_MAX), int(fs * 60 / _BPM_MIN)
    hi = min(hi, ac.size - 1)
    if lo >= hi:
        return 120.0

    # Weight by how likely a tempo is before choosing the strongest lag.
    # A pattern repeats at its beat AND at every multiple and division of it, so
    # raw autocorrelation is nearly as happy with half or double the real tempo
    # -- measured on synthetic kit patterns at known tempos, the unweighted peak
    # landed on the wrong metrical level in 9 of 13 cases (60->120, 174->87,
    # 160->80). The log-normal prior around 120 BPM is the standard remedy
    # (Ellis 2007, and what librosa's own tempo estimator uses): it does not
    # forbid extreme tempos, it just requires better evidence for them.
    lags = np.arange(lo, hi, dtype=float)
    bpms = 60.0 * fs / np.maximum(lags, 1e-9)
    prior = np.exp(-0.5 * (np.log2(bpms / _BPM_PRIOR_CENTER) / _BPM_PRIOR_WIDTH) ** 2)

    def support(bpm: float) -> float:
        """How well the onsets actually repeat at this tempo, times its prior."""
        lag = 60.0 * fs / bpm
        i = int(round(lag))
        if not lo <= i < hi:
            return -1.0
        w = np.exp(-0.5 * (np.log2(bpm / _BPM_PRIOR_CENTER) / _BPM_PRIOR_WIDTH) ** 2)
        return float(ac[i]) * w

    # The header is trusted for the *value* but not for the octave. Beat
    # trackers famously halve and double, and this one does: on two Cool Jazz
    # tracks it wrote 60.0 where the real tempo was ~118, and 187.5 where it was
    # ~94. A half-speed tempo is not a small error -- every bar covers two real
    # bars, so the chord detected for it pools two different chords.
    #
    # So the header's own octave is re-checked against the onset autocorrelation
    # here: whichever of half, actual and double has the most support wins. This
    # only ever moves the answer by a factor of two, so a header that is right
    # stays right.
    if header is not None:
        cands = [c for c in (header / 2, header, header * 2) if _BPM_MIN <= c <= _BPM_MAX]
        if cands:
            best = max(cands, key=support)
            if support(best) > 0:
                return _round_bpm(best)

    peak = lo + int(np.argmax(ac[lo:hi] * prior))

    # Refine the peak between samples before converting to BPM. Only whole lags
    # exist on this grid, and at fast tempos they are far apart in BPM terms:
    # near 140 the reachable values are 139.53 (lag 43) and 142.86 (lag 42), so
    # 140 itself cannot be produced at all. That is where readings like "139.6"
    # came from -- not a song at 139.6 BPM, but the nearest lag to one at 140.
    # Fitting a parabola through the peak and its two neighbours recovers the
    # fractional lag.
    if 0 < peak < ac.size - 1:
        y0, y1, y2 = float(ac[peak - 1]), float(ac[peak]), float(ac[peak + 1])
        denom = y0 - 2 * y1 + y2
        if denom != 0:
            peak += max(-0.5, min(0.5, 0.5 * (y0 - y2) / denom))
    return _round_bpm(60.0 * fs / peak) if peak > 0 else 120.0


_PHASE_STEPS = 32   # offsets tried within one step; ~4 ms at 150 BPM


def _estimate_phase(midi, sec_per_step: float) -> float:
    """Where the step grid actually starts, in seconds.

    midi_convert.py writes the time signature at t=0 and keeps no downbeat, so
    without this the first grid step is assumed to land exactly on the file's
    first sample. Measured on generated tracks that assumption is wrong by up
    to half a beat, and being wrong shifts *every* note in the snippet by the
    same amount -- the pattern is then internally right but sits off the beat.

    Fitted on the drum track when there is one. Drums come from onset detection
    rather than Basic Pitch and are by far the most grid-aligned part of the
    file: measured mean quantization error was 0.51x what random note times
    would give, against 0.82-0.99x (i.e. indistinguishable from noise) for
    every pitched track.
    """
    drums = [i for i in midi.instruments if i.is_drum and len(i.notes) >= 8]
    pool = drums or midi.instruments
    starts = [n.start for inst in pool for n in inst.notes]
    if len(starts) < 8 or sec_per_step <= 0:
        return 0.0

    best_offset, best_error = 0.0, None
    for k in range(_PHASE_STEPS):
        offset = k * sec_per_step / _PHASE_STEPS
        # Distance from each note to its nearest grid line, in steps.
        error = sum(abs((((s - offset) / sec_per_step) + 0.5) % 1.0 - 0.5) for s in starts)
        if best_error is None or error < best_error:
            best_offset, best_error = offset, error
    return best_offset


def _grid(notes, sec_per_step: float, total_steps: int, as_drums: bool,
          pcs: "frozenset | None" = None, phase: float = 0.0):
    """Quantize notes onto the step grid, correcting off-key ones when `pcs`
    is given. Returns (per-step token lists, number of notes corrected)."""
    steps: list[list[str]] = [[] for _ in range(total_steps)]
    corrected = 0
    for n in notes:
        idx = int(round((n.start - phase) / sec_per_step))
        if not (0 <= idx < total_steps):
            continue
        if as_drums:
            tok = _drum_name(n.pitch)
        else:
            pitch = n.pitch
            if pcs is not None:
                snapped = _snap_to_scale(pitch, pcs)
                corrected += snapped != pitch
                pitch = snapped
            tok = _note_name(pitch)
        if tok not in steps[idx]:
            steps[idx].append(tok)
    return steps, corrected


def _mini_notation(steps, bars: int, steps_per_bar: int) -> str:
    """Render the grid as multi-line mini-notation, one bar per line."""
    lines = []
    for b in range(bars):
        toks = []
        for s in steps[b * steps_per_bar:(b + 1) * steps_per_bar]:
            if not s:
                toks.append("~")
            elif len(s) == 1:
                toks.append(s[0])
            else:
                toks.append("[" + ",".join(s[:MAX_STACK_PER_STEP]) + "]")
        lines.append("    " + " ".join(toks))
    return "\n".join(lines)


# ── Emission ──────────────────────────────────────────────────────────────────

# Sound + effect chain per track. These are *not* derived from the audio -- a
# reverb size or a filter cutoff is a musical decision, not a property of a
# recording -- so they stay to a plain, quiet starting point. The header comment
# in the output says so, because a generated chain that looks considered invites
# people to assume it means something.
_SOUNDS = {
    "Bass":   ('s("sawtooth")', ".lpf(600)"),
    "Guitar": ('s("triangle")', ".room(.2)"),
    "Piano":  ('s("piano")',    ".room(.3)"),
    "Vocals": ('s("sine")',     ".room(.3)"),
    "Other":  ('s("triangle")', ".room(.2)"),
}
_DEFAULT_SOUND = ('s("piano")', ".room(.3)")
_CHORD_SOUND = 'gm_epiano1'

# Chord qualities as Strudel's "ireal" voicing dictionary spells them. The
# internal names (strudel_emit, lead_sheet) stay as they are; only the emitted
# symbol changes. Without this, maj7, maj9, sus4, sus2 and dim voiced to
# *nothing* -- checked through Strudel's own engine -- so every bar holding one
# of those chords was silent, and on a synth-pop track that was a third of them.
_IREAL_QUALITY = {"maj7": "^7", "maj9": "^9", "dim": "o", "sus4": "sus", "sus2": "2"}
_SHARP_NAMES = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
# Keys written with sharps; in them a chord root reads as F#, not Gb.
_SHARP_KEYS = {"G major", "D major", "A major", "E major", "B major", "F# major",
               "E minor", "B minor", "F# minor", "C# minor", "G# minor"}


def _strudel_chord(name: str, sharps: bool) -> str:
    """Internal chord name -> a symbol Strudel's ireal dictionary voices."""
    if name == "~":
        return name
    root = name[:2] if len(name) > 1 and name[1] == "b" else name[:1]
    quality = name[len(root):]
    if sharps:
        root = _SHARP_NAMES.get(root, root)
    return root + _IREAL_QUALITY.get(quality, quality)


MAX_BARS = 96          # ~3 minutes at 140bpm 4/4; beyond this nobody reads it
_MIN_TRACK_NOTES = 6   # below this a "track" is noise, not a part


def _bar_timed(notes, sec_per_step, steps_per_bar, n_bars, phase, pcs):
    """Bucket notes into [bar][step], keeping how long each one sounds.

    A step is a list of pitches starting there, "_" while the last note is
    still held, or "~" once it has ended. Without the holds every note was
    stretched until the next one started -- measured 1.3-1.6x the transcribed
    length on melody and bass. A note held across a bar line stops at it:
    each bar is its own cycle in the pattern, and nothing ties between them.
    """
    total = n_bars * steps_per_bar
    onsets: "list[list[int]]" = [[] for _ in range(total)]
    ends = [0] * total
    corrected = 0
    for n in notes:
        idx = int(round((n.start - phase) / sec_per_step))
        if not (0 <= idx < total):
            continue
        pitch = n.pitch
        if pcs is not None:
            snapped = _snap_to_scale(pitch, pcs)
            corrected += snapped != pitch
            pitch = snapped
        if pitch not in onsets[idx]:
            onsets[idx].append(pitch)
        ends[idx] = max(ends[idx], idx + 1, int(round((n.end - phase) / sec_per_step)))
    steps: list = []
    busy = 0
    for i in range(total):
        if onsets[i]:
            steps.append(onsets[i])
            busy = ends[i]
        elif i < busy and i % steps_per_bar:
            steps.append("_")
        else:
            steps.append("~")
    return [steps[b * steps_per_bar:(b + 1) * steps_per_bar] for b in range(n_bars)], corrected


def _bar_drum_slots(notes, sec_per_step, steps_per_bar, n_bars, phase):
    total = n_bars * steps_per_bar
    steps: list[list[str]] = [[] for _ in range(total)]
    for n in notes:
        idx = int(round((n.start - phase) / sec_per_step))
        if not (0 <= idx < total):
            continue
        name = _drum_name(n.pitch)
        if name not in steps[idx]:
            steps[idx].append(name)
    return [steps[b * steps_per_bar:(b + 1) * steps_per_bar] for b in range(n_bars)]


def _block_chord_names(notes, bar_seconds, n_bars, phase) -> "list[str]":
    """Chord names per bar read straight off a lead sheet's block chords.

    Those blocks are already the progression -- one chord per bar, merged
    while it holds -- so each is named from exactly its own notes and put on
    the bar lines it was written on. Detecting again by weighting notes per
    bar blended neighbouring chords whenever this file's grid, estimated
    afresh, sat a fraction off the one the blocks were written on.
    """
    from pipeline.strudel_emit import detect_chord

    blocks: "dict[tuple[float, float], set[int]]" = {}
    for n in notes:
        blocks.setdefault((round(n.start, 3), round(n.end, 3)), set()).add(n.pitch % 12)
    names = ["~"] * n_bars
    for (start, end), pcs in sorted(blocks.items()):
        name = detect_chord({pc: 1.0 for pc in pcs})
        if not name:
            continue
        b0 = max(0, round((start - phase) / bar_seconds))
        b1 = min(n_bars, max(b0 + 1, round((end - phase) / bar_seconds)))
        for b in range(b0, b1):
            names[b] = name
    # A bar between blocks holds the chord before it, as in chords_bar_names.
    last = "~"
    for b, name in enumerate(names):
        names[b] = name if name != "~" else last
        last = names[b]
    return names


_BAR_EPS = 1e-6   # in bars: a note starting exactly on a bar line belongs to it


def _bar_chord_weights(notes, bar_seconds, n_bars, phase):
    """Pitch-class content per bar, for chord naming: each note counts in
    every bar it sounds in, by how long it sounds there.

    This used to file a note under the bar it started in, found with a floor
    division. A chord starting on a bar line came out 5.9999 bars in as often
    as 6.0, landing a bar early -- so one bar pooled two chords and the next
    looked empty -- and a chord held across bars counted only in the first.
    """
    import math

    weights = [dict() for _ in range(n_bars)]
    for n in notes:
        s = (n.start - phase) / bar_seconds
        e = (n.end - phase) / bar_seconds
        first = math.floor(s + _BAR_EPS)
        last = math.ceil(e - _BAR_EPS)
        pc = n.pitch % 12
        for b in range(max(0, first), min(n_bars, max(last, first + 1))):
            overlap = (min(e, b + 1) - max(s, b)) * bar_seconds
            # A very short note still counts a little in the bar it starts in.
            w = max(overlap, 0.05 if b == first else 0.0)
            if w > 0:
                weights[b][pc] = weights[b].get(pc, 0.0) + w
    return weights


def midi_to_strudel(midi_path: Path, title: str | None = None,
                    bars: "int | None" = None, mode: str = "chords") -> str:
    """Convert a multi-track MIDI file into a Strudel snippet.

    The snippet is laid out the way Strudel is written by hand -- drums, one
    harmony, a bass and a melody -- rather than one part per separated stem.
    Stems are what a source separator produced; they are not the parts of the
    music, and emitting six chord tracks playing the same progression is both
    unreadable and wrong about what is being played.

    mode="chords" names the harmony once for the whole song and gives the bass
    and melody as scale degrees. mode="notes" keeps every transcribed pitch,
    which is far longer but is what you want when the transcription itself is
    the thing you are checking.
    """
    import pretty_midi
    from pipeline.strudel_emit import (alternation, chords_bar_names, compress,
                                       degrees_token, drum_struct, pattern_literal,
                                       pick_roles, smooth_chords, timed_bar, _DRUM_ORDER)

    midi = pretty_midi.PrettyMIDI(str(midi_path))
    bpm = _estimate_bpm(midi)

    beats_per_bar = 4
    if midi.time_signature_changes:
        ts = midi.time_signature_changes[0]
        if ts.denominator == 4 and 2 <= ts.numerator <= 7:
            beats_per_bar = ts.numerator
    steps_per_bar = beats_per_bar * STEPS_PER_BEAT
    sec_per_step = (60.0 / bpm) / STEPS_PER_BEAT
    bar_seconds = sec_per_step * steps_per_bar
    phase = _estimate_phase(midi, sec_per_step)

    detected = _estimate_key(midi)
    key_name = detected[0] if detected else None
    confidence = detected[2] if detected else 0.0
    pcs = detected[1] if detected and confidence >= _KEY_CONFIDENCE_MIN else None
    root_pc, scale_name = 0, "C:major"
    if key_name:
        root, _, quality = key_name.partition(" ")
        if root.lower() in _PITCH_CLASS:
            root_pc = _PITCH_CLASS.index(root.lower())
        scale_name = f"{root.upper()}:{'minor' if quality.startswith('min') else 'major'}"
    scale_steps = _MINOR_SCALE if scale_name.endswith("minor") else _MAJOR_SCALE
    # `pcs` already respects the confidence threshold, but the scale name did
    # not, so a key the estimator had no confidence in was still written into
    # `.scale()` and every degree computed against it. Measured on a pop-punk
    # track, confidence came out at 0.007 -- indistinguishable from a guess --
    # and the snippet still asserted A major. Say so instead of implying a
    # certainty that was never there.
    key_uncertain = bool(key_name) and confidence < _KEY_CONFIDENCE_MIN

    end = max((n.end for inst in midi.instruments for n in inst.notes), default=0.0)
    available = max(1, int(max(0.0, end - phase) / bar_seconds) + 1)
    n_bars = max(1, min(available, bars or DEFAULT_BARS or MAX_BARS, MAX_BARS))

    drum_tracks = [i for i in midi.instruments if i.is_drum and len(i.notes) >= _MIN_TRACK_NOTES]
    pitched = [(i.name or "Other", list(i.notes)) for i in midi.instruments
               if not i.is_drum and len(i.notes) >= _MIN_TRACK_NOTES]
    roles = pick_roles(pitched)

    parts: list[str] = []
    prelude: list[str] = []
    corrected_total = 0

    # ── DRUMS ────────────────────────────────────────────────────────────────
    for inst in drum_tracks:
        slots = _bar_drum_slots(inst.notes, sec_per_step, steps_per_bar, n_bars, phase)
        lines = [f'    s("{snd}").struct({pattern_literal(pat, "    ")})'
                 for snd in _DRUM_ORDER
                 if (pat := drum_struct(slots, snd))]
        if lines:
            parts.append(f"  // DRUMS -- {len(inst.notes)} hits, from onset detection\n"
                         "  stack(\n" + ",\n".join(lines) + '\n  ).bank("RolandTR909")')

    # ── HARMONY ──────────────────────────────────────────────────────────────
    # Detected once, from every pitched note pooled: a song has one harmony, and
    # each stem only sees part of it.
    if mode == "chords" and pitched:
        # A lead sheet already carries the progression as a "Chords" track of
        # block chords, one per bar. Name those directly: pooling the melody
        # and bass back in and detecting again re-guessed a progression that
        # had already been decided, and disagreed with it.
        chord_part = next((notes for name, notes in pitched if name == "Chords"), None)
        if chord_part:
            names = _block_chord_names(chord_part, bar_seconds, n_bars, phase)
        else:
            pooled = [n for _, notes in pitched for n in notes]
            names = smooth_chords(chords_bar_names(
                _bar_chord_weights(pooled, bar_seconds, n_bars, phase)))
        if any(x != "~" for x in names):
            sharps = key_name in _SHARP_KEYS
            names = [_strudel_chord(x, sharps) for x in names]
            prelude.append("let chords = chord("
                           + pattern_literal(f"<{compress(names)}>", "") + ').dict("ireal")')
            parts.append("  // HARMONY -- one progression, "
                         + ("from the lead sheet's chords\n" if chord_part else "from all pitched stems pooled\n")
                         + f'  chords.voicing().s("{_CHORD_SOUND}").room(.4).gain(.5)')

    # ── BASS / MELODY (and everything, in notes mode) ─────────────────────────
    for name, notes in pitched:
        role = roles.get(name, "harmony")
        if mode == "chords" and role == "harmony":
            continue        # already covered by the pooled progression
        # Chords mode writes scale degrees against the key, which is what makes
        # it short; notes mode writes the transcribed pitches exactly, with no
        # key snapping, so a wrong key guess can't bend a single note.
        as_degrees = mode == "chords"
        slots, corrected = _bar_timed(notes, sec_per_step, steps_per_bar, n_bars, phase,
                                      pcs if as_degrees else None)
        corrected_total += corrected
        if as_degrees:
            fmt = lambda ps: degrees_token(ps, root_pc, scale_steps)
        else:
            fmt = lambda ps: (_note_name(ps[0]) if len(ps) == 1 else
                              "[" + ",".join(_note_name(p) for p in sorted(ps)) + "]")
        bar_strings = [timed_bar(b, fmt) for b in slots]
        if not any(t != "~" for s in bar_strings for t in s.split()):
            continue
        sound, chain = _SOUNDS.get(name, _DEFAULT_SOUND)
        label = role.upper() if mode == "chords" else name.upper()
        if as_degrees:
            parts.append(f"  // {label} ({name}) -- {len(notes)} notes as scale degrees\n"
                         f"  n({pattern_literal(alternation(bar_strings), '  ')})"
                         f'.scale("{scale_name}")\n'
                         f"    .{sound}{chain}")
        else:
            parts.append(f"  // {label} ({name}) -- {len(notes)} notes, exact pitches\n"
                         f"  note({pattern_literal(alternation(bar_strings), '  ')})\n"
                         f"    .{sound}{chain}")

    if not parts:
        return "// Nothing transcribable in this MIDI file.\nsilence"

    header = [
        f"// {title or midi_path.stem}",
        f"// ~{bpm:g} BPM, {beats_per_bar}/4, {n_bars} of {available} bars"
        + (f", key {key_name}" if key_name else "")
        + (" (uncertain)" if key_uncertain else ""),
    ]
    if key_uncertain:
        header.append("// The key estimate is weak on this track -- the scale "
                      "degrees below follow it,")
        header.append("// so check .scale() first if the melody sounds wrong.")
    if corrected_total:
        header.append(f"// {corrected_total} off-key notes snapped to {key_name}")
    header.append("// Sounds and effects are plain defaults, not detected from "
                  "the audio -- change them first.")
    header.append("")
    header.append(f"setcpm({bpm:g}/{beats_per_bar})")
    header.extend(prelude)
    return "\n".join(header) + "\nstack(\n" + ",\n".join(parts) + "\n)\n"
