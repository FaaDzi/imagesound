"""
Lyrics for vocal songs: writing them, cleaning them, and the caption words that
go with them.

Everything here was settled by listening tests (September 2026), not guessed:

  * Mixed languages stumble. "Rocket jump, let's go now" inside Japanese lyrics
    was sung with no timing, so a song is one language, and Japanese is
    written in romaji, which ACE-Step sang cleanly.
  * The same line several times in a row makes the model lose its place in the
    chorus (rushing, dragging). The hook appears at most twice per chorus.
  * Short lines that end in punctuation give the singer somewhere to breathe;
    long unpunctuated ones got cut off.
  * A vocalise ("aa, aa,") intro and outro was the best-liked part of a take.
  * The "[Chorus - lead vocal, high belt]" tag and energetic caption words are
    what give a vocal song its drop. Without them (tested with a fixed seed)
    the voice turned more synthetic and the build-up disappeared.

Lyrics are written by the same backend chain that describes images (Gemini,
then the local Ollama model). The local model's drafts are usable but ignore
structure -- twice the requested length, translations in brackets, stage
directions -- so every draft goes through clean_lyrics(), which rebuilds it on
the planned skeleton. A bracketed line left in would be *sung*.
"""

import random
import re
from dataclasses import dataclass

CHORUS_TAG = "Chorus - lead vocal, high belt"
# "high belt" pulled the male voice up into the female range (median ~400 Hz
# on both seeds); without it one seed sat at ~280 Hz. Swapped in at generation
# time (voice_lyrics), so a draft stays valid when the voice is changed after.
MALE_CHORUS_TAG = "Chorus - powerful male lead vocal"
DUET_CHORUS_TAG = "Chorus - male and female duet, harmonies"
# Typed on its own line under a tag in the lyrics box: "no singing here".
INSTRUMENTAL_MARK = "///"
PRE_CHORUS_TAG = "Pre-Chorus - building tension, quieter"

# code -> (name for the instruction, how to write it). ACE-Step takes the code
# as vocal_language. Romaji for Japanese: it is what the tests used and sang
# well, and it lets clean_lyrics() drop stray non-Latin characters safely.
LANGUAGES = {
    "ja": ("Japanese", "written in romaji (Latin letters), no kana or kanji, and no English "
                       "words or fillers (no 'yeah', 'baby', 'fly high', 'let's go')"),
    "en": ("English", "plain everyday English"),
    "ko": ("Korean", "written in Hangul"),
    "zh": ("Mandarin Chinese", "written in simplified Chinese characters"),
}
_LATIN_ONLY = {"ja", "en"}

# How a line should be built, per language. Each was tuned by ear:
#   ja -- short 5-8 syllable lines with a comma or period to breathe on.
#   en -- tested against the ja rule on the same image and seeds (rock, then
#         EDM): the short lines sounded choppy and "old school"; longer,
#         conversational lines that rhyme in pairs were the clear pick.
_LINE_RULES = {
    "ja": "- Every lyric line is short (5 to 8 syllables) and ends with a comma or a period.",
    "en": ("- Every lyric line is 6 to 10 syllables of natural, conversational English, "
           "ending with a comma or a period.\n"
           "- Lines rhyme in pairs (AABB) or alternately (ABAB). Prefer simple, strong rhymes."),
}

# Languages that have been through listening tests. Another language changes
# phrasing and how the melody fits the words, not just the words, so each one
# stays locked until it has been tested. The same lock is in
# pipeline/models.json (vocal_language.locked_choices); unlock in both places,
# and give the language its own _LINE_RULES entry.
TESTED_LANGUAGES = {"ja", "en"}

# Caption words for the singer. ACE-Step has no voice presets: the caption's
# words (and the seed) are the voice. Female is the preferred take (N1).
# Male is M3 of the voice A/B (same lyrics and seeds): swapping "female" for
# "male" in the female wording still sang female (the model hears "vocaloid"
# as the Miku voice), dropping "vocaloid" gave a male voice, and "baritone"
# plus the male chorus tag below was judged the most natural of the three.
VOICE_HINTS = {
    "female": "vocaloid, synthesized female vocals, auto-tuned robotic voice, powerful lead vocal",
    "male": "male vocal, deep baritone male lead singer, auto-tuned, powerful male lead vocal",
    # D3 of the duet A/B: the two tested voices named together, plus who-sings
    # tags on the sections (voice_lyrics). Asking for "male and female duet"
    # alone sang female throughout; with only the tags the male came through
    # but the female swamped the chorus. D3 was the one where both could be
    # heard in the chorus, on both seeds.
    "duet": "duet, vocaloid female vocal and deep baritone male vocal, auto-tuned, powerful lead vocals",
}
VOICES = ("auto", "female", "male", "duet")
# Admin-only tone words for listening tests; none of them is tested yet.
TONE_WORDS = {
    "bright": "bright clear vocal tone",
    "breathy": "breathy vocals",
    "raspy": "raspy gritty vocals",
    "soft": "soft gentle vocals",
    "falsetto": "falsetto vocals",
}
# The chops set asks for the quiet, background version -- the syllable-only
# test was judged too loud and too machine-like without it.
_CHOPS_HINT = "soft background vocal chops, airy, breathy, low in the mix"
# "Auto" picks male for these genres, female for the vocalise (J-pop-like)
# ones, and either otherwise.
_MALE_GENRES = r"rock|punk|metal|grunge|hip-?hop|\brap\b|country|blues"
_MAX_CAPTION_CHARS = 512   # ACE-Step's caption limit, same as the worker's


# ── Structure ─────────────────────────────────────────────────────────────────

# A section is (tag, lines). Special line counts:
#   _VOCALISE      one wordless line ("aa, aa,")
#   _OUTRO         a vocalise, then an echo of the hook
#   _HOOK_ECHO     one line echoing the hook
#   _INSTRUMENTAL  the tag alone: the model plays the section without singing
_VOCALISE, _OUTRO, _INSTRUMENTAL, _HOOK_ECHO = 0, -1, -2, -3

_POP_HOOK = "- The chorus has one hook phrase, used at most twice, never on two lines in a row."
_EDM_HOOK = ("- The two Build lines are the hook: two DIFFERENT lines that lift into the drop. "
             "Every Build repeats the first Build's two lines. Sections marked instrumental have "
             "no words at all.")
_CHOP_HOOK = (" The second Build line may instead be a short fragment of the hook repeated, "
              "like a vocal chop (\"the light, the light, the light\").")

# EDM-family captions get the drop-based plan. Checked before anything else,
# and deliberately specific: "progressive" alone would catch progressive rock.
_EDM_WORDS = (r"\bedm\b|house|techno|trance|dubstep|future bass|big room|electro|"
              r"drum and bass|\bdnb\b|\bclub\b|festival|supersaw")
# Genres where the "aa, aa" vocalise intro/outro belongs -- it came from a
# J-pop test, where it was the best-liked part, but was judged generic when
# every song got it.
_VOCALISE_GENRES = r"j-?pop|anime|vocaloid|city pop|k-?pop|kawaii"


@dataclass(frozen=True)
class Plan:
    """The song's shape, decided once: the same plan writes the lyrics and
    then cleans them, so any randomness in it has to be fixed up front."""
    family: str                 # "edm" or "pop"
    sections: tuple             # ((tag, lines), ...)
    hook_rule: str


def plan_for(seconds: float, caption: str = "", rng: "random.Random | None" = None) -> Plan:
    rng = rng or random.Random()
    text = (caption or "").lower()
    if re.search(_EDM_WORDS, text):
        return _edm_plan(seconds, rng)
    return _pop_plan(seconds, text, rng)


def _edm_plan(seconds: float, rng: random.Random) -> Plan:
    """Vocals in the verse and build, instrumental drops, few words.

    Tested (English, progressive house): the verse-chorus plan sang straight
    through where the drops should be. This plan left vocal-free drops, and at
    150 s with a word budget gave a 20 s clean drop and the best-rated build.
    Too many lines for the length and the singer spills into the drops, so the
    budget shrinks with the song. The 150 s shape is the tested one.
    """
    chop = rng.random() < 0.35      # the "Clarity" pre-drop chop: sometimes, never always
    build = ("Build - rising, vocal chop" if chop else "Build - rising", 2)
    drop = ("Drop - instrumental", _INSTRUMENTAL)
    intro = ("Intro - instrumental", _INSTRUMENTAL)
    outro = ("Outro - instrumental, fade out", _INSTRUMENTAL)
    if seconds <= 75:
        sections = [intro, ("Verse", 4), build, drop, outro]
    elif seconds <= 130:
        sections = [intro, ("Verse", 4), build, drop, build, drop, outro]
    else:
        sections = [intro, ("Verse", 4), build, drop, ("Breakdown - soft", 2), build, drop, outro]
    return Plan("edm", tuple(sections), _EDM_HOOK + (_CHOP_HOOK if chop else ""))


def _pop_plan(seconds: float, text: str, rng: random.Random) -> Plan:
    """Verse-chorus. The body scales with length (the 60 s plan is the tested
    one); the intro and outro vary, so songs do not all open and close alike."""
    if re.search(_VOCALISE_GENRES, text):
        # The vocalise belongs here, but "aa, aa" at both ends of every song
        # was noticed as a pattern, so it opens or closes the song, not both.
        if rng.random() < 0.5:
            intro = ("Intro", _VOCALISE)
            outro = rng.choice([("Outro", _HOOK_ECHO), ("Outro - instrumental, fade out", _INSTRUMENTAL)])
        else:
            intro, outro = ("Intro - instrumental", _INSTRUMENTAL), ("Outro", _OUTRO)
    else:
        intro = (("Intro - instrumental", _INSTRUMENTAL) if rng.random() < 0.7
                 else ("Intro - soft", _VOCALISE))
        outro = rng.choice([("Outro - instrumental, fade out", _INSTRUMENTAL),
                            ("Outro", _HOOK_ECHO), ("Outro", _OUTRO)])
    # Verse straight into chorus gave the chorus nothing to land on: judged "not
    # punchy, no delay to build up". A two-line pre-chorus is the lift; the
    # longest songs also drop out to an instrumental break before the last
    # chorus, so it hits after a gap.
    pre = (PRE_CHORUS_TAG, 2)
    if seconds <= 40:
        body = [(CHORUS_TAG, 4)]
    elif seconds <= 75:
        body = [("Verse", 4), pre, (CHORUS_TAG, 4)]
    elif seconds <= 130:
        body = [("Verse", 4), pre, (CHORUS_TAG, 4), ("Verse", 4), pre, (CHORUS_TAG, 4)]
    else:
        body = [("Verse", 4), pre, (CHORUS_TAG, 4), ("Verse", 4), pre, (CHORUS_TAG, 4),
                ("Bridge", 2), ("Break - instrumental, building", _INSTRUMENTAL), (CHORUS_TAG, 4)]
    sections = [intro, *body, outro]
    repeat = ("\n- When a chorus or pre-chorus comes back, repeat its first lines exactly."
              if sum(1 for t, _ in sections if t == CHORUS_TAG) > 1 else "")
    return Plan("pop", tuple(sections), _POP_HOOK + repeat)


def _skeleton(plan: Plan) -> str:
    bodies = {
        _VOCALISE: '(1 short vocalise line, like "aa, aa, aa," or "oh, oh,")',
        _OUTRO: "(2 short lines: a vocalise, then an echo of the hook)",
        _HOOK_ECHO: "(1 short line echoing the hook)",
        _INSTRUMENTAL: "(instrumental: no lyrics, leave this section empty)",
    }
    return "\n\n".join(f"[{tag}]\n{bodies.get(n, f'({n} lines)')}" for tag, n in plan.sections)


# ── Writing ───────────────────────────────────────────────────────────────────

def lyrics_instruction(language: str, seconds: float, caption: "str | None", with_image: bool,
                       plan: Plan) -> str:
    name, script = LANGUAGES[language]
    source = ("inspired by this image" if with_image else "for a song")
    style = f"\nThe music is: {caption.strip()}\n" if caption and caption.strip() else "\n"
    return f"""Write song lyrics {source}, for a {int(seconds)}-second song.{style}
First line: "BPM: <number>", a steady tempo between {_BPM_MIN} and {_BPM_MAX} that suits the music.
Then the lyrics.

Rules:
- Language: {name} only, {script}. No words from any other language.
- Use exactly this structure, with each tag on its own line:
{_skeleton(plan)}
{_LINE_RULES.get(language, _LINE_RULES["ja"])}
{plan.hook_rule}
- Capture the mood and story; do not describe the picture literally.
- Output ONLY the lyrics with the tags. No title, no translation, no parentheses, no notes."""


# Plain tags typed in the lyrics box get the tested wording for their section,
# at generation and when a user's structure is drafted into: the chorus tag
# carries the energy of the drop, and the Male/Duet swaps look for it exactly.
_TESTED_TAGS = {"chorus": CHORUS_TAG, "pre": PRE_CHORUS_TAG, "build": "Build - rising"}
# Lines to ask for per section of a user's structure; anything else gets 2.
_CUSTOM_LINES = {"verse": 4, "chorus": 4}


def normalize_tags(lyrics: str) -> str:
    """"[Chorus]" -> "[Chorus - lead vocal, high belt]". A tag that already
    says something after " - " is the user's and is left alone."""
    def fix(line: str) -> str:
        m = _TAG.match(line)
        if not m or " - " in m.group(1):
            return line
        return f"[{_TESTED_TAGS.get(_base(m.group(1)), m.group(1).strip())}]"
    return "\n".join(fix(line) for line in lyrics.splitlines())


def prepare_lyrics(lyrics: str, voice: str) -> str:
    """Lyrics as the model gets them: /// expanded, plain tags given their
    tested wording, then this voice's tags."""
    return voice_lyrics(normalize_tags(expand_marks(lyrics)), voice)


def plan_from_structure(text: str) -> "Plan | None":
    """A plan from the sections in the lyrics box, so SURPRISE ME writes into
    the user's shape. Sections marked /// stay instrumental; an unmarked intro
    or outro gets a vocalise. None when there is nothing to sing."""
    sections = []
    for line in normalize_tags(expand_marks(text)).splitlines():
        m = _TAG.match(line)
        if not m:
            continue
        tag = m.group(1).strip()
        base = _base(tag)
        if "instrumental" in tag.lower():
            sections.append((tag, _INSTRUMENTAL))
        elif base == "intro":
            sections.append((tag, _VOCALISE))
        elif base == "outro":
            sections.append((tag, _OUTRO))
        else:
            sections.append((tag, _CUSTOM_LINES.get(base, 2)))
    if not any(n > 0 for _, n in sections):
        return None
    bases = [_base(t) for t, _ in sections]
    if "chorus" in bases:
        hook = _POP_HOOK + ("\n- When a section comes back, repeat its first lines exactly."
                            if len(bases) != len(set(bases)) else "")
    elif "build" in bases:
        hook = _EDM_HOOK
    else:
        hook = "- One short hook phrase, used at most twice."
    return Plan("custom", tuple(sections), hook)


def write_lyrics(*, language: str, seconds: float, caption: "str | None" = None,
                 images: "list[tuple[bytes, str]] | None" = None,
                 structure: "str | None" = None) -> "tuple[str, int]":
    """Draft lyrics with the describer chain (Gemini, then local), cleaned.
    Returns (lyrics, bpm): the writer's tempo, or one read off the caption.
    `structure` is the user's own section list; without one (or with nothing
    singable in it) the genre's plan is used.

    Raises describers.DescriberError when no backend answers or the answer has
    no usable lines."""
    from pipeline.describers import DescriberError, describe

    if language not in LANGUAGES:
        raise DescriberError(f"Unsupported lyrics language '{language}'.")
    if language not in TESTED_LANGUAGES:
        raise DescriberError(f"Lyrics in '{language}' are locked until that language has been tested.")
    plan = (structure and plan_from_structure(structure)) or plan_for(seconds, caption or "")
    instruction = lyrics_instruction(language, seconds, caption, bool(images), plan)
    raw = describe(images or [], lambda _compact: instruction)
    cleaned = clean_lyrics(raw, language, plan)
    if not cleaned:
        raise DescriberError("The lyrics draft had no usable lines.")
    return cleaned, _parse_bpm(raw) or tempo_for_caption(caption or "")


# ── Tempo ─────────────────────────────────────────────────────────────────────
#
# Vocal songs always get a locked tempo. Left to pick its own, the model chose
# ~200 BPM for a fast song and, in 2 of 3 runs, fell into half time partway
# through -- measured with a beat tracker: 200 -> 97 and 200 -> 100, with
# beat-interval jitter 33-37%. Locked at 140, neither run dropped (one eased
# from 143 to 136) and bar lengths held to 0.7% jitter. That drop is what
# sounded like a song that "didn't know which part was the chorus".

# Capped at 160: at 150 the bars came out uneven (bar-length jitter 18-40% on
# two seeds) where 140 held them to 0.7% on both. Fast songs get 140.
_BPM_MIN, _BPM_MAX = 70, 160

# First match wins, so the specific genres come before the generic tempo words.
_TEMPO_WORDS = [
    (r"ballad|lullaby|ambient|downtempo|\bslow\b", 80),
    (r"lo-?fi|chill|laid-?back|relaxed|boom bap|hip hop", 90),
    (r"mid-?tempo|groov|funk|city pop|disco|r&b", 112),
    (r"house|techno|trance|\bedm\b|dance", 126),
    (r"punk|metal|rock|j-?pop|anime|fast|energetic|driving|upbeat|high-energy", 140),
]


def _parse_bpm(raw: str) -> "int | None":
    m = re.search(r"\bBPM\s*[:=]\s*(\d{2,3})", raw, re.I)
    return min(_BPM_MAX, max(_BPM_MIN, int(m.group(1)))) if m else None


def tempo_for_caption(caption: str) -> int:
    """A sensible locked tempo from the caption's genre and tempo words."""
    lowered = caption.lower()
    return next((bpm for pattern, bpm in _TEMPO_WORDS if re.search(pattern, lowered)), 120)


# ── Cleaning ──────────────────────────────────────────────────────────────────

_TAG = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _base(tag: str) -> str:
    """"Chorus - lead vocal, high belt" / "Verse 2" / "chorus" -> "chorus"."""
    return re.sub(r"[\s\d]+$", "", tag.split("-")[0].split(":")[0]).strip().lower()


# One romaji word: morae (optional consonant or sh/ch/ts, optional y, vowel),
# syllabic n, and doubled consonants (kitto, zutto). English words almost never
# fit ("fly", "high", "yeah", "let's"), which is the point: the writer was told
# not to mix in English and still did, and mixed-in English is what stumbled.
_ROMAJI_WORD = re.compile(
    r"^(?:(?:sh|ch|ts|[kgsztdnhbpmrwyfjv])?y?[aeiou]|n|([kstpgdbcfhmrjz])(?=\1|ch))+$")
_VOCALISE_WORDS = {"oh", "ah", "aa", "hmm", "mm", "la", "na", "uh", "ooh"}


def _romaji_only(line: str) -> str:
    """Drop words that cannot be romaji; drop the line if most of it goes."""
    words = line.split()
    keep = [w for w in words
            if (bare := re.sub(r"[^a-z]", "", w.lower())) in _VOCALISE_WORDS
            or (bare and _ROMAJI_WORD.match(bare))]
    return " ".join(keep) if len(keep) * 2 > len(words) else ""


def _clean_line(line: str, language: str) -> str:
    line = re.sub(r"\([^)]*\)|\*[^*]*\*", "", line)          # (translations), *directions*
    line = line.replace("…", ",").strip().strip('"').strip()
    if language in _LATIN_ONLY:
        line = re.sub(r"[^\x20-\x7e\u00c0-\u024f]", "", line)  # Latin letters (+ accents) only
    line = re.sub(r"\s+", " ", line).strip(" -")
    if language == "ja":
        line = _romaji_only(line).rstrip(" ,")
    if line and line[-1] not in ",.!?、。！？":
        line += ","
    return line


def clean_lyrics(text: str, language: str, plan: Plan) -> str:
    """Rebuild a draft on the planned skeleton.

    Sections are matched to the plan in order by name, extra sections and
    extra lines are dropped, and the tags are rewritten to the planned ones --
    so the chorus always carries the tag the drop depends on, whatever the
    writer put there. Returns "" if the plan's sung core does not survive."""
    text = re.sub(r"^```\w*|```$", "", text.strip(), flags=re.M)
    drafted: "list[tuple[str, list[str]]]" = []
    for raw in text.splitlines():
        m = _TAG.match(raw)
        if m:
            drafted.append((_base(m.group(1)), []))
        elif drafted:
            line = _clean_line(raw, language)
            if line:
                drafted[-1][1].append(line)

    out, cursor, sung = [], 0, False
    first: "dict[str, list[str]]" = {}     # first lines seen per repeating section
    for tag, n in plan.sections:
        want = _base(tag)
        found = None
        for i in range(cursor, len(drafted)):
            if drafted[i][0] == want:
                found, cursor = drafted[i][1], i + 1
                break
        if n > 0:
            # A missing repeat (the second chorus, the second build) reuses the
            # first rather than leaving a hole where the drop should be.
            lines = (found or [])[:n] or first.get(want, [])
            if lines:
                first.setdefault(want, lines)
        elif n == _VOCALISE:
            lines = (found or ["aa, aa, aa,"])[:1]
        elif n == _OUTRO:
            lines = (found or ["aa, aa,"])[:2]
        elif n == _HOOK_ECHO:
            hook = first.get("chorus") or first.get("build") or []
            lines = (found or hook)[:1]
        else:   # _INSTRUMENTAL: played without singing; shown with the same
            # mark a user types for it, so the draft teaches the symbol
            out.append(f"[{tag}]\n{INSTRUMENTAL_MARK}")
            continue
        if lines:
            out.append(f"[{tag}]\n" + "\n".join(lines))
            sung = True
    # The section that carries the song has to survive: the chorus in a pop
    # plan, the build (the hook before the drop) in an EDM plan.
    # A user's own structure ("custom") only needs something sung.
    core = {"pop": "chorus", "edm": "build"}.get(plan.family)
    return "\n\n".join(out) if (sung and (core is None or core in first)) else ""


# ── Vocal chops and captions ──────────────────────────────────────────────────

def chops_lyrics(seconds: float) -> str:
    """Wordless vocalise for the background 'vocal chops' mode. Sparse on
    purpose: the dense la-la take was judged too loud and too machine-like."""
    sections = ["[Intro - soft vocal chops]\noh,"]
    sections.append("[Main Theme - vocal chops]\naa, aa,\noh, oh,")
    if seconds > 60:
        sections.append("[Build]\nhmm, hmm,")
        sections.append("[Climax - vocal chops]\naa, aa, aa,\noh, oh,")
    sections.append("[Outro - fade out]\naa,")
    return "\n\n".join(sections)


def resolve_voice(voice: str, caption: str, rng: "random.Random | None" = None) -> str:
    """'female' or 'male' for a request's Voice setting; 'auto' goes by genre."""
    if voice in VOICE_HINTS:
        return voice
    text = (caption or "").lower()
    if re.search(_VOCALISE_GENRES, text):
        return "female"
    if re.search(_MALE_GENRES, text):
        return "male"
    return (rng or random.Random()).choice(["female", "male"])


def expand_marks(lyrics: str) -> str:
    """Turn the lyrics box's "///" (this section has no singing) into what
    the model reads: an "instrumental" tag with no lines under it. A "///"
    before any tag is an instrumental opening."""
    out: "list[str]" = []
    tag_at = None                      # index in `out` of the current section's tag
    for line in lyrics.splitlines():
        if line.strip() == INSTRUMENTAL_MARK:
            if tag_at is None:
                out.append("[Intro - instrumental]")
                tag_at = len(out) - 1
            elif "instrumental" not in out[tag_at].lower():
                tag = _TAG.match(out[tag_at]).group(1).strip()
                out[tag_at] = f"[{tag}, instrumental]" if " - " in tag else f"[{tag} - instrumental]"
            continue
        if _TAG.match(line):
            tag_at = len(out)
        out.append(line)
    return "\n".join(out)


def _duet_tag(tag: str) -> str:
    """Who sings a section in a duet: the tested split is verse male,
    pre-chorus female, chorus both. Bridges and builds follow the same idea."""
    if "instrumental" in tag.lower():
        return tag
    if tag == CHORUS_TAG:
        return DUET_CHORUS_TAG
    base = _base(tag)
    name, _, detail = tag.partition(" - ")
    if base == "verse":
        return f"{name} - male vocal"
    if base in ("pre", "bridge"):
        return f"{name} - female vocal" + (f", {detail}" if detail else "")
    if base == "build":
        return f"{tag}, male and female duet"
    return tag


def voice_lyrics(lyrics: str, voice: str) -> str:
    """Put this voice's section tags on a draft (see MALE_CHORUS_TAG)."""
    if voice == "male":
        return lyrics.replace(f"[{CHORUS_TAG}]", f"[{MALE_CHORUS_TAG}]")
    if voice == "duet":
        return "\n".join(f"[{_duet_tag(m.group(1).strip())}]" if (m := _TAG.match(line)) else line
                         for line in lyrics.splitlines())
    return lyrics


def vocal_caption(caption: str, vocals: str, voice: str = "female", tone: str = "default") -> str:
    """The caption with this mode's vocal words added (and a stray
    'instrumental' removed -- it would argue with them). `voice` is a
    resolved one: 'female' or 'male'."""
    if vocals == "lyrics":
        hint = VOICE_HINTS[voice] + (f", {TONE_WORDS[tone]}" if tone in TONE_WORDS else "")
    elif vocals == "chops":
        hint = _CHOPS_HINT
    else:
        return caption
    caption = re.sub(r",?\s*\binstrumental\b", "", caption, flags=re.I).strip(" ,")
    room = _MAX_CAPTION_CHARS - len(hint) - 2
    return f"{caption[:room].rstrip(' ,')}, {hint}"
