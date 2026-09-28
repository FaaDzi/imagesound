"""Reduce a transcribed multitrack MIDI to a lead sheet.

The full transcription answers "what notes are in this recording". A lead sheet
answers "what is this song", which is a different question and the one people
actually ask. On a 180s track the first runs to ~3300 notes across five stems at
roughly 20 notes a second; nobody reads that, and neither does the Strudel
emitter, which currently has to *infer* drums/harmony/bass/melody back out of it
after the fact.

Doing the reduction here instead is strictly better placed: the stems are still
separate, the key and tempo are already estimated, and nothing has been pooled
yet.

Four parts, the same four a human would write down:

    MELODY   one monophonic line, the highest-sounding part of whichever stem
             carries the lead
    CHORDS   one block-chord progression, named per bar from every pitched note
    BASS     one monophonic line from the lowest-register stem
    DRUMS    unchanged -- it is already one hit per onset

What this deliberately loses: inner voices, countermelodies, and any part that
is neither lead nor accompaniment. That is the point. The full multitrack file
is still written alongside for anyone who wants all of it.
"""

import logging

log = logging.getLogger(__name__)

# A melody note this short is a transcription artifact, not something anyone
# sings or plays as part of a line.
_MIN_MELODY_MS = 90
# A stem needs at least this many notes before it can be called the lead.
_MIN_LEAD_NOTES = 20
# Chord voicing register: roughly the middle of a piano, where block chords sit
# without colliding with either the bass or the melody.
_VOICING_LO, _VOICING_HI = 52, 76


# Intervals above a fundamental at which the harmonic series lands: octave,
# octave+fifth, two octaves, and the next two partials.
_PARTIALS = (12, 19, 24, 28, 31)
# How close two starts must be to count as the same attack.
_ATTACK_TOL = 0.05


def strip_overtones(notes: list) -> list:
    """Drop notes that are a louder simultaneous note's own harmonics.

    Basic Pitch reports the partials of a sustained tone as real, separate
    notes. That is merely noisy for a chord track and fatal for melody
    extraction, because an overtone is by definition *above* the note that
    produced it — so taking the highest line takes the artifact every time.
    Measured on synthetic material with realistic overtone rates, skyline alone
    scored F1 0.05 at 60% overtones and 0.00 at 90%.

    A note is dropped when some other note, sounding at the same time and at
    least as loud, sits exactly a harmonic interval below it. The velocity test
    is what keeps a real melody note an octave above quiet accompaniment: a
    partial is always weaker than its fundamental, so anything louder than the
    note below it is not that note's overtone.
    """
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: n.start)
    starts = [n.start for n in ordered]
    import bisect

    keep = []
    for n in ordered:
        lo = bisect.bisect_left(starts, n.start - _ATTACK_TOL)
        hi = bisect.bisect_right(starts, n.start + _ATTACK_TOL)
        is_partial = False
        for m in ordered[lo:hi]:
            if m is n or m.velocity < n.velocity:
                continue
            if (n.pitch - m.pitch) in _PARTIALS and m.end > n.start:
                is_partial = True
                break
        if not is_partial:
            keep.append(n)
    return keep


def skyline(notes: list) -> list:
    """Keep the highest note sounding at any moment; drop the rest.

    The standard way to pull a melody out of polyphony, and it works here for
    the usual reason: a lead sits above its accompaniment.

    Implemented as a sweep over every start and end time, taking the highest
    note active in each segment and merging neighbouring segments that agree.
    An incremental version -- walk the notes in start order, truncating and
    appending against whatever was appended last -- is the obvious approach and
    is wrong: a note appended to cover the tail of a long low note begins
    *later* than notes still to be processed, so "the last thing appended" stops
    being "the thing currently sounding" and the output overlaps itself. That
    version came out monophonic on only 666 of 2000 random inputs. The sweep is
    monophonic by construction: one pitch per segment, segments do not overlap.
    """
    import heapq

    import pretty_midi

    live = [n for n in notes if n.end > n.start]
    if not live:
        return []
    ordered = sorted(live, key=lambda n: n.start)
    bounds = sorted({t for n in live for t in (n.start, n.end)})

    heap: list = []          # (-pitch, end, velocity), highest pitch on top
    idx = 0
    segments: list = []      # (start, pitch, velocity)
    for t in bounds:
        while idx < len(ordered) and ordered[idx].start <= t:
            n = ordered[idx]
            heapq.heappush(heap, (-n.pitch, n.end, n.velocity))
            idx += 1
        # Lazily drop finished notes. Only the top matters: anything expired
        # below it is covered by a higher note anyway, and gets dropped when it
        # reaches the top.
        while heap and heap[0][1] <= t:
            heapq.heappop(heap)
        segments.append((t, -heap[0][0] if heap else None, heap[0][2] if heap else 0))

    out: list = []
    for i, (t, pitch, vel) in enumerate(segments):
        if pitch is None:
            continue
        end = segments[i + 1][0] if i + 1 < len(segments) else t
        if end <= t:
            continue
        if out and out[-1].pitch == pitch and abs(out[-1].end - t) < 1e-6:
            out[-1].end = end          # same note continuing across a boundary
        else:
            out.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t, end=end))
    min_len = _MIN_MELODY_MS / 1000.0
    return [n for n in out if n.end - n.start >= min_len]


def _groundline(notes: list) -> list:
    """The bass counterpart of `skyline`: keep the LOWEST note sounding.

    Mirroring the pitches, reusing skyline, and mirroring back is exact and
    avoids a second near-identical walk that could drift out of step with it.
    """
    import pretty_midi

    flip = [pretty_midi.Note(velocity=n.velocity, pitch=127 - n.pitch,
                             start=n.start, end=n.end) for n in notes]
    return [pretty_midi.Note(velocity=n.velocity, pitch=127 - n.pitch,
                             start=n.start, end=n.end) for n in skyline(flip)]


def _median_pitch(notes) -> float:
    import statistics
    return statistics.median(n.pitch for n in notes)


def pick_parts(midi) -> "tuple[object | None, object | None, list]":
    """Choose which instrument is the lead, which is the bass, and the rest.

    By register, not by stem name: a separator's labels say where a sound came
    from, not what job it does in the arrangement. The vocal stem of an
    instrumental track is whatever leaked into it.
    """
    pitched = [i for i in midi.instruments if not i.is_drum and len(i.notes) >= 6]
    if not pitched:
        return None, None, []
    by_pitch = sorted(pitched, key=lambda i: _median_pitch(i.notes))
    bass = by_pitch[0]
    lead = next((i for i in reversed(by_pitch)
                 if i is not bass and len(i.notes) >= _MIN_LEAD_NOTES), None)
    return lead, bass, pitched


def _voice_chord(pcs: "frozenset", root_pc: int) -> "list[int]":
    """Spell a chord's pitch classes as actual pitches in a readable register."""
    pitches = [p for p in range(_VOICING_LO, _VOICING_HI) if p % 12 == root_pc][:1]
    for pc in sorted(pcs):
        if pc == root_pc:
            continue
        cand = [p for p in range(_VOICING_LO, _VOICING_HI) if p % 12 == pc]
        if cand:
            # Nearest voice above the root keeps the shape compact.
            above = [p for p in cand if pitches and p > pitches[0]]
            pitches.append(above[0] if above else cand[0])
    return sorted(set(pitches))


def chord_track(midi, bar_seconds: float, phase: float, n_bars: int) -> list:
    """One sustained block chord per bar, merged while the chord holds."""
    import pretty_midi
    from pipeline.strudel_convert import _bar_chord_weights
    from pipeline.strudel_emit import chords_bar_names, smooth_chords

    pooled = [n for i in midi.instruments if not i.is_drum for n in i.notes]
    if not pooled:
        return []
    names = smooth_chords(chords_bar_names(
        _bar_chord_weights(pooled, bar_seconds, n_bars, phase)))

    notes: list = []
    run_start, run_name = None, None

    def flush(end_bar):
        if run_name in (None, "~") or run_start is None:
            return
        parsed = _parse_symbol(run_name)
        if not parsed:
            return
        root_pc, pcs = parsed
        start = phase + run_start * bar_seconds
        end = phase + end_bar * bar_seconds
        for p in _voice_chord(pcs, root_pc):
            notes.append(pretty_midi.Note(velocity=70, pitch=p,
                                          start=start, end=end))

    for b, name in enumerate(names):
        if name != run_name:
            flush(b)
            run_start, run_name = b, name
    flush(len(names))
    return notes


_ROOTS = {"C": 0, "Db": 1, "D": 2, "Eb": 3, "E": 4, "F": 5,
          "Gb": 6, "G": 7, "Ab": 8, "A": 9, "Bb": 10, "B": 11,
          "C#": 1, "D#": 3, "F#": 6, "G#": 8, "A#": 10}
_QUALITIES = {
    "": (0, 4, 7), "m": (0, 3, 7), "5": (0, 7), "sus4": (0, 5, 7),
    "sus2": (0, 2, 7), "dim": (0, 3, 6), "aug": (0, 4, 8), "6": (0, 4, 7, 9),
    "m6": (0, 3, 7, 9), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10), "m7b5": (0, 3, 6, 10), "9": (0, 4, 7, 10, 14),
    "maj9": (0, 4, 7, 11, 14), "m9": (0, 3, 7, 10, 14),
}


def _parse_symbol(sym: str) -> "tuple[int, frozenset] | None":
    """Chord symbol back into (root pitch class, pitch classes)."""
    for n in (2, 1):
        if len(sym) >= n and sym[:n] in _ROOTS:
            root, qual = _ROOTS[sym[:n]], sym[n:]
            if qual in _QUALITIES:
                return root, frozenset((root + i) % 12 for i in _QUALITIES[qual])
    return None


def build_lead_sheet(midi):
    """Reduce a transcribed PrettyMIDI to MELODY / CHORDS / BASS / DRUMS."""
    import pretty_midi
    from pipeline.strudel_convert import _estimate_bpm, _estimate_phase

    bpm = _estimate_bpm(midi)
    beats_per_bar = 4
    if midi.time_signature_changes:
        ts = midi.time_signature_changes[0]
        if ts.denominator == 4 and 2 <= ts.numerator <= 7:
            beats_per_bar = ts.numerator
    sec_per_step = (60.0 / bpm) / 4
    bar_seconds = sec_per_step * 4 * beats_per_bar
    phase = _estimate_phase(midi, sec_per_step)
    end = max((n.end for i in midi.instruments for n in i.notes), default=0.0)
    n_bars = max(1, int(max(0.0, end - phase) / bar_seconds) + 1)

    lead, bass, pitched = pick_parts(midi)
    # With only one pitched stem there is no second track to be the lead, and
    # skipping the melody would throw away the tune to keep the bass. Take both
    # lines from that one stem instead: its top is the melody, its bottom the
    # bass. On dense single-stem material they differ substantially.
    if lead is None and bass is not None and len(pitched) == 1:
        lead = bass
    out = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    # The lead sheet's grid (chord blocks, phase) is laid on exactly this bpm,
    # so its header is authoritative even when it happens to read 120.
    from pipeline.midi_convert import TEMPO_MEASURED_MARK
    out.text_events.append(pretty_midi.Text(TEMPO_MEASURED_MARK, 0.0))
    out.time_signature_changes.append(
        pretty_midi.TimeSignature(numerator=beats_per_bar, denominator=4, time=0.0))

    def add(name, program, notes, is_drum=False):
        if not notes:
            return
        inst = pretty_midi.Instrument(program=program, is_drum=is_drum, name=name)
        inst.notes = list(notes)
        out.instruments.append(inst)
        log.info("[lead] %s: %d notes", name, len(inst.notes))

    # Strip partials before taking the top line, never after: skyline's whole
    # premise is that the melody is the highest thing sounding, which is false
    # while a note's own overtones are still in the list.
    add("Melody", 40, skyline(strip_overtones(lead.notes)) if lead else [])  # Violin
    add("Chords", 0, chord_track(midi, bar_seconds, phase, n_bars))  # Piano
    add("Bass", 33, _groundline(bass.notes) if bass else [])         # Electric Bass
    for inst in midi.instruments:
        if inst.is_drum:
            add("Drums", 0, inst.notes, is_drum=True)
            break
    return out
