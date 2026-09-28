"""
Gemini instructions that turn an image (or animated GIF) into a music prompt.

The framing is model-independent; only the final "rules" block depends on the
model that will consume the prompt, chosen by the manifest's `prompt_style`.
A model with different prompting quirks gets its own entry in _STYLE_RULES --
nothing else changes. Unknown styles fall back to "generic".
"""

_STILL_HEAD = """\
You are a music director who translates the emotional feeling of images into music.

Your task: look at this image and write a concise music prompt for an AI music model.
"""

_GIF_HEAD = """\
You are a music director who translates animated images into music.

These images are frames sampled from an animated GIF in sequence (first, middle, and last frames).
Your task: write one concise music prompt for an AI music model that captures BOTH the scene AND its energy.

Consider the animation as a whole:
- Rapid or dramatic changes across frames -> energetic, fast, high-tempo music
- Slow or subtle changes -> calm, flowing, minimal music
- Frames look nearly identical -> treat as a still image; focus on content mood
"""

_COMMON_RULES = """\
Rules:
- Output ONLY the music prompt — no preamble, no explanation, nothing else.
- Use musical language only: genre, mood, instruments, tempo feel, texture, energy.
- Do NOT describe what you see (no "a photo of", "the image shows", "the frames show", etc.).
- Never put numeric BPM or key names in the prompt.
"""

_STYLE_RULES = {
    # Caption style ACE-Step responds to: specific, multi-dimensional, no conflicts.
    "ace-step": """\
- Write ONE comma-separated caption of 8-16 descriptors (under 60 words).
- Combine several dimensions: genre/era, mood, 2-4 named instruments, timbre/texture
  (warm, airy, punchy, lush...), production style, and a tempo feel (slow, mid-tempo, driving).
- Name one clear lead instrument or melodic focus; the rest support it.
- Be specific, not vague: "warm fingerpicked acoustic guitar" beats "nice guitar".
- Avoid contradictory descriptors (e.g. "gentle lullaby" with "aggressive metal").
- Instrumental music: do not mention vocals or lyrics.

Examples of good output:
  "cinematic ambient, brooding solo cello lead over sparse low strings, slow and spacious, dark warm timbre, intimate studio recording"
  "indie folk, upbeat fingerpicked acoustic guitar melody, light tambourine, bright and warm, mid-tempo, live room feel"
  "dreamy synthwave, lead analog synth melody, subtle bass drone, wide reverb, melancholic, slow tempo, polished production"
""",
    "generic": """\
- 1-2 sentences, ideally under 25 words.
- Choose 2-4 well-defined instruments and name one clear lead element.
- Keep adjectives minimal and purposeful.
""",
}


# Compact variants for small local models.
#
# The full instruction above is written for a large hosted model: a role, a
# rules block and worked examples. A 4B model handles that much worse -- with
# the long version, qwen3-vl:4b produced a usable caption in 1 of 4 attempts and
# otherwise ran out of tokens still deliberating, while the same model answered
# in ~4s when the instruction was short. Same requirements, fewer words, one
# example instead of three.
_COMPACT_STILL = """\
Write ONE music prompt describing music that fits this image."""

_COMPACT_GIF = """\
These are frames from an animated GIF, in order. Write ONE music prompt for
music that fits the scene AND its energy: big changes between frames mean
faster, more energetic music; near-identical frames mean calm music."""

_COMPACT_RULES = """\
Format: a single line of 8 to 16 short descriptors separated by commas.
Include a genre, a mood, 2-4 named instruments (name one clear lead), a texture
word, and a tempo feel.
Do not describe the image itself. No BPM numbers, no key names, no vocals or lyrics.
Example: cinematic ambient, brooding solo cello lead, sparse low strings, slow and spacious, dark warm timbre, intimate studio recording
Reply with the prompt only."""


def _rules(style: str) -> str:
    return _COMMON_RULES + _STYLE_RULES.get(style, _STYLE_RULES["generic"])


def image_instruction(style: str, compact: bool = False) -> str:
    if compact:
        return _COMPACT_STILL + "\n" + _COMPACT_RULES
    return _STILL_HEAD + "\n" + _rules(style)


def gif_instruction(style: str, compact: bool = False) -> str:
    if compact:
        return _COMPACT_GIF + "\n" + _COMPACT_RULES
    return _GIF_HEAD + "\n" + _rules(style)
