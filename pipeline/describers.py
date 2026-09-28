"""
Image -> music-prompt backends, and the order they are tried.

The instruction text (pipeline/prompting.py) is already backend-independent, so
the only thing that varies here is who reads the image. Two backends ship:

  gemini  -- Google AI Studio. Best quality, but on a free key it is rate
             limited and returns 503 when the model is busy.
  ollama  -- a local vision model over Ollama's HTTP API. No quota, no network,
             no cost; lower quality. Weights live wherever OLLAMA_MODELS points,
             which is deliberately outside this repo so other projects share them.

DESCRIBER_ORDER decides the chain. The default, "gemini,ollama", keeps Gemini's
quality as the normal path and falls back to the local model only when Gemini
actually fails -- so a quota wall degrades the result instead of failing the
request. Set it to "ollama" to go local-only, or "gemini" for the old behaviour.

Only *transient* backend failures fall through to the next backend. A bad
instruction or a corrupt image would fail identically everywhere, so those
raise immediately rather than burning the fallback on a retry that cannot help.
"""

import base64
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
# gemma3:4b, not a reasoning model, and that is the reason rather than a
# preference. qwen3-vl:4b scores better on paper but reasons before answering,
# and its reasoning does not reliably stop: with the full instruction it used
# its whole token budget deliberating in 6 of 6 attempts and produced nothing,
# and Ollama 0.34's "think": false does not gate its template. gemma3:4b cannot
# fail that way, and measured on the same images it also beat the pinned Gemini
# model on rule compliance (6/6 captions within the 8-16 descriptor rule
# against 5/6) at roughly a fifth of the latency once loaded.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_TIMEOUT_SEC = float(os.getenv("OLLAMA_TIMEOUT_SEC", "120"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "800"))

# How long Ollama keeps the model in VRAM after answering.
#
# This matters more here than in a normal Ollama setup: describing an image is
# immediately followed by music generation, which wants the whole GPU. On an
# 8GB card a 3.3GB vision model still resident is 3.3GB the music model cannot
# use. Ollama's own default is 5 minutes, which comfortably outlives the gap
# between describing and generating; 60s covers a user retrying the description
# without holding VRAM through the generation that follows.
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "60s")
DESCRIBER_ORDER = os.getenv("DESCRIBER_ORDER", "gemini,ollama")


class DescriberError(RuntimeError):
    """A backend could not produce a description."""


class BackendUnavailable(DescriberError):
    """Backend isn't usable at all (not installed, not configured, not running).

    Distinct from a failed attempt: this one is worth reporting differently,
    because the fix is setup rather than retrying.
    """


# ── Gemini ────────────────────────────────────────────────────────────────────

def _describe_gemini(images: "list[tuple[bytes, str]]", make_instruction) -> str:
    # Imported here, not at module scope: this module is imported by code paths
    # that may have no Gemini key, and google.genai is not free to import.
    from google.genai import errors as genai_errors
    from google.genai import types

    from pipeline.generate_song import _GEMINI_MODEL, _gemini_generate_content

    if not os.getenv("GEMINI_API_KEY"):
        raise BackendUnavailable("GEMINI_API_KEY is not set.")

    contents: list = [types.Part.from_bytes(data=data, mime_type=mime)
                      for data, mime in images]
    contents.append(make_instruction(False))   # the full instruction
    try:
        return _gemini_generate_content(_GEMINI_MODEL, contents).text.strip()
    except genai_errors.APIError as e:
        raise DescriberError(f"Gemini {getattr(e, 'code', '?')}: {e}") from e


# ── Ollama ────────────────────────────────────────────────────────────────────

def _ollama_post(path: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        f"{OLLAMA_HOST.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_status() -> "tuple[bool, str | None]":
    """(usable, reason). Used for diagnostics and by the chain's error message."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST.rstrip('/')}/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as e:
        return False, f"Ollama is not reachable at {OLLAMA_HOST} ({e})."
    names = {m.get("name", "") for m in tags.get("models", [])}
    # Ollama reports "qwen3-vl:4b"; tolerate the implicit ":latest" spelling.
    wanted = {OLLAMA_MODEL, f"{OLLAMA_MODEL}:latest", OLLAMA_MODEL.removesuffix(":latest")}
    if not (names & wanted):
        return False, (f"Ollama has no model '{OLLAMA_MODEL}'. "
                       f"Run: ollama pull {OLLAMA_MODEL}")
    return True, None


def _describe_ollama(images: "list[tuple[bytes, str]]", make_instruction) -> str:
    ok, reason = ollama_status()
    if not ok:
        raise BackendUnavailable(reason or "Ollama unavailable.")

    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        # Reasoning-capable models are asked to skip it. This is a formatting
        # task, and reasoning only burns the token budget before the answer.
        # Not every model honours the flag -- see the empty-output branch below.
        "think": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "messages": [{
            "role": "user",
            # Small models follow the short instruction far more reliably than
            # the full one written for a hosted model. See pipeline/prompting.py.
            "content": make_instruction(True),
            "images": [base64.b64encode(data).decode("ascii") for data, _ in images],
        }],
        # Low temperature: this is a constrained formatting task, not a creative
        # one -- the instruction asks for a specific comma-separated shape.
        "options": {"temperature": 0.4, "num_predict": OLLAMA_NUM_PREDICT},
    }
    try:
        data = _ollama_post("/api/chat", payload, OLLAMA_TIMEOUT_SEC)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise DescriberError(f"Ollama request failed: {e}") from e

    message = data.get("message") or {}
    text = _strip_thinking(message.get("content", "")).strip()
    if text:
        return text

    # Empty content with a populated `thinking` field and done_reason "length"
    # means the model reasoned until it ran out of budget and never answered.
    # Say so: the fix is a non-reasoning model or a bigger budget, not a retry.
    if message.get("thinking") and data.get("done_reason") == "length":
        raise DescriberError(
            f"'{OLLAMA_MODEL}' used its whole {OLLAMA_NUM_PREDICT}-token budget "
            f"reasoning and never produced an answer. Use a model that does not "
            f"reason (e.g. gemma3:4b) or raise OLLAMA_NUM_PREDICT.")
    raise DescriberError(f"'{OLLAMA_MODEL}' returned an empty description.")


# Seed genres for random_theme(). The genre is picked here rather than left to
# the model: asked to "choose any genre", a 4B model returns some variant of
# "cinematic ambient" most of the time. Picking it outside and asking the model
# only to build a coherent prompt *around* it guarantees the variety, and leaves
# the model the part it is actually good at.
# Grouped by family so the gaps are visible when adding more: a flat list grows
# lopsided without anyone noticing, and the odds of any one genre are 1/len, so
# an over-represented family quietly crowds out the rest.
_SEED_GENRES = [
    # Hip hop and adjacent
    "boom bap hip hop", "lo-fi hip hop", "jazz rap", "cloud rap", "trap", "drill",
    "g-funk", "memphis rap", "drift phonk", "grime",
    # Electronic, dance floor
    "festival EDM", "deep house", "tech house", "disco house", "progressive trance",
    "hardstyle", "eurobeat", "UK garage", "breakbeat", "big room techno",
    # Electronic, bass
    "drum and bass", "liquid drum and bass", "jungle", "dubstep", "future bass",
    "breakcore",
    # Electronic, retro and textural
    "synthwave", "vaporwave", "chiptune", "IDM", "ambient techno", "electro swing",
    "minimal wave",
    # Lo-fi and downtempo
    "lo-fi house", "chillhop", "downtempo", "trip hop",
    # Jazz
    "jazz trio", "bebop", "cool jazz", "smooth jazz", "jazz fusion", "big band swing",
    "nu jazz", "gypsy jazz", "acid jazz",
    # J-pop, anime and Asian pop
    "j-pop", "anime opening theme", "city pop", "j-rock", "kawaii future bass",
    "k-pop", "shibuya-kei",
    # Rock
    "indie rock", "psychedelic rock", "post-rock", "shoegaze", "dream pop", "grunge",
    "punk rock", "surf rock", "math rock", "garage rock", "stoner rock", "britpop",
    # Metal
    "melodic death metal", "djent", "power metal", "doom metal", "metalcore",
    "black metal",
    # Folk, country, acoustic
    "indie folk", "bluegrass", "celtic folk", "country ballad", "americana",
    "singer-songwriter acoustic",
    # Classical and score
    "baroque chamber music", "neoclassical piano", "cinematic orchestral",
    "epic trailer music", "string quartet", "romantic piano nocturne",
    "minimalist classical", "horror film score", "spaghetti western score",
    # Soul, funk, R&B
    "funk", "smooth soul", "neo-soul", "motown", "disco", "gospel choir", "blues",
    # World and Latin
    "bossa nova", "salsa", "afrobeat", "reggae", "dub", "cumbia", "flamenco",
    "tango", "samba", "bhangra", "mariachi", "highlife", "klezmer", "gamelan",
    # Ambient and experimental
    "dark ambient", "drone ambient", "space ambient", "new age", "gothic industrial",
    "witch house", "noise rock",
    # Everything else
    "ska", "swing", "lounge exotica", "sea shanty", "video game boss theme",
    "circus waltz", "military march",
]

_THEME_RULES = """\
Every descriptor must agree with the genre. A fast genre gets fast, energetic,
punchy descriptors; an atmospheric genre gets airy, spacious, slow ones. Never
mix contradictory ideas (no "gentle" with "aggressive").
Format: a single line of 8 to 16 short descriptors separated by commas.
Include the genre, a mood, 2-4 named instruments (name one clear lead), a
texture word, and a tempo feel.
No BPM numbers, no key names, no vocals or lyrics. Reply with the prompt only."""


def random_theme() -> str:
    """Invent one coherent music prompt, for the 'surprise me' button.

    Local-only and deliberately so: it needs no image, runs constantly while
    someone hunts for an idea, and is exactly the kind of low-stakes task worth
    spending a free local model on rather than a rate-limited quota.
    """
    import random

    genre = random.choice(_SEED_GENRES)
    ok, reason = ollama_status()
    if not ok:
        raise BackendUnavailable(reason or "Ollama unavailable.")

    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "think": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "messages": [{
            "role": "user",
            "content": f"Write ONE music prompt for an AI music model, in the genre: {genre}.\n"
                       + _THEME_RULES,
        }],
        # Hotter than describing, and a fresh seed each time: the point here is
        # variety. A fixed seed would return the same prompt for the same genre.
        "options": {"temperature": 1.0, "top_p": 0.95,
                    "seed": random.randint(1, 2_000_000_000),
                    "num_predict": OLLAMA_NUM_PREDICT},
    }
    try:
        data = _ollama_post("/api/chat", payload, OLLAMA_TIMEOUT_SEC)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise DescriberError(f"Ollama request failed: {e}") from e

    text = _strip_thinking((data.get("message") or {}).get("content", "")).strip()
    # Models sometimes wrap the line in quotes or prefix it with a label.
    text = text.strip('"').strip()
    if ":" in text.split(",")[0] and len(text.split(":")[0]) < 30:
        text = text.split(":", 1)[1].strip()
    if not text:
        raise DescriberError(f"'{OLLAMA_MODEL}' returned an empty theme.")
    # One line, no trailing full stop -- captions elsewhere in the app have none.
    return text.splitlines()[0].strip().rstrip(".").strip()


def _strip_thinking(text: str) -> str:
    """Drop a leading <think>...</think> block.

    Qwen3 models emit one when reasoning is on. It is not part of the prompt and
    would otherwise be pasted into the caption verbatim.
    """
    lowered = text.lower()
    start = lowered.find("<think>")
    if start == -1:
        return text
    end = lowered.find("</think>", start)
    return (text[:start] + (text[end + len("</think>"):] if end != -1 else "")).strip()


# ── Chain ─────────────────────────────────────────────────────────────────────

_BACKENDS = {"gemini": _describe_gemini, "ollama": _describe_ollama}


def describer_order() -> "list[str]":
    names = [n.strip().lower() for n in DESCRIBER_ORDER.split(",") if n.strip()]
    unknown = [n for n in names if n not in _BACKENDS]
    if unknown:
        raise DescriberError(
            f"DESCRIBER_ORDER names unknown backend(s): {', '.join(unknown)}. "
            f"Known: {', '.join(_BACKENDS)}.")
    if not names:
        raise DescriberError("DESCRIBER_ORDER is empty.")
    return names


def describe(images: "list[tuple[bytes, str]]", make_instruction) -> str:
    """Run the chain: first backend that answers wins.

    `images` is [(bytes, mime_type)] -- several frames for a GIF, one otherwise.
    `make_instruction(compact: bool) -> str` is asked for the wording that suits
    each backend, because a 4B local model and a hosted model want very
    different instructions for the same job.
    """
    order = describer_order()
    failures: list[str] = []
    for name in order:
        try:
            text = _BACKENDS[name](images, make_instruction)
            if failures:
                log.warning("[describe] %s succeeded after %s", name, "; ".join(failures))
            return text
        except BackendUnavailable as e:
            failures.append(f"{name} unavailable ({e})")
            log.info("[describe] skipping %s: %s", name, e)
        except DescriberError as e:
            failures.append(f"{name} failed ({e})")
            log.warning("[describe] %s failed, trying next: %s", name, e)
    raise DescriberError("No image describer succeeded. " + "; ".join(failures))
