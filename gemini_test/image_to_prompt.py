"""
image_to_prompt.py — Phase D  (isolated, no backend/generation-model integration)
Takes an image, asks Gemini to describe what it should SOUND like,
and prints a text-to-music prompt.

Usage:
  python image_to_prompt.py                    # auto-generates 3 synthetic test images
  python image_to_prompt.py photo1.jpg ...     # run on your own images
"""

import io
import os
import random
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# .env lives at the project root — one level above gemini_test/
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise SystemExit(f"GEMINI_API_KEY not found. Looked in: {_ENV_PATH}")

from google import genai
from google.genai import types
from PIL import Image

CLIENT = genai.Client(api_key=GEMINI_API_KEY)
MODEL = "gemini-2.5-flash"

# ── Prompt engineering ────────────────────────────────────────────────────────
# Explicitly forbid visual language; require pure musical language.
# Music models generally do best with short, focused, instrument/mood-specific prompts.

MUSIC_DIRECTION_INSTRUCTION = """\
You are a music director who translates the emotional feeling of images into music.

Your task: look at this image and write the music that belongs with it.

Rules:
- Output ONLY the music prompt — nothing else, no preamble, no explanation.
- 1-2 sentences maximum, ideally under 25 words.
- Use musical language only: genre, mood, instruments, tempo, texture, energy.
- Do NOT describe what you see (no "a photo of", "the image shows", "waves", "sky", etc.).
- Think: if this image were a film scene, what music would a composer write?

Examples of good output:
  "Dark orchestral tension, heavy cello and low brass, slow tempo, rising dread."
  "Upbeat indie folk, fingerpicked acoustic guitar, tambourine, warm and joyful, 120 bpm."
  "Ethereal ambient synth pads, sparse piano, slow reverb, melancholic and introspective."
"""


# ── Core function ─────────────────────────────────────────────────────────────

def image_to_music_prompt(image_path: Path) -> str:
    """Send an image to Gemini and return a text-to-music prompt."""
    img = Image.open(image_path)

    # Convert to bytes — use JPEG for photos (smaller), PNG for synthetic images
    buf = io.BytesIO()
    fmt = (img.format or "PNG").upper()
    if fmt not in ("JPEG", "PNG", "WEBP"):
        fmt = "PNG"
    img.save(buf, format=fmt)
    img_bytes = buf.getvalue()

    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt]

    response = CLIENT.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type=mime),
            MUSIC_DIRECTION_INSTRUCTION,
        ],
    )
    return response.text.strip()


# ── Synthetic test image generation ──────────────────────────────────────────
# Provides 3 visually distinct images when no real photos are available.
# Real photographs will give much richer, more varied prompts.

def _make_calm_image(size: int) -> Image.Image:
    """Soft blue-green gradient: still water / open sky feeling."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = y / size
            px[x, y] = (
                int(60 + 40 * t),
                int(130 + 60 * t),
                int(200 - 30 * t),
            )
    return img


def _make_energetic_image(size: int) -> Image.Image:
    """Clashing red/yellow/orange shards: high-energy, chaotic."""
    from PIL import ImageDraw
    img = Image.new("RGB", (size, size), (220, 50, 20))
    draw = ImageDraw.Draw(img)
    rng = random.Random(7)
    for _ in range(60):
        x0, y0 = rng.randint(0, size), rng.randint(0, size)
        x1, y1 = x0 + rng.randint(30, 180), y0 + rng.randint(30, 120)
        color = (
            rng.randint(180, 255),
            rng.randint(80, 220),
            rng.randint(0, 60),
        )
        draw.polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], fill=color)
    return img


def _make_moody_image(size: int) -> Image.Image:
    """Deep indigo with dim light beam: late-night, atmospheric."""
    from PIL import ImageDraw, ImageFilter
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = y / size
            px[x, y] = (
                int(15 + 25 * (1 - t)),
                int(5 + 15 * (1 - t)),
                int(50 + 60 * (1 - t)),
            )
    # faint diagonal light streak
    draw = ImageDraw.Draw(img)
    for offset in range(-8, 8):
        alpha = 255 - abs(offset) * 20
        draw.line(
            [(size // 3 + offset, 0), (size * 2 // 3 + offset, size)],
            fill=(alpha // 4, alpha // 4, alpha // 3),
            width=1,
        )
    return img.filter(ImageFilter.GaussianBlur(radius=3))


def make_synthetic_test_images(out_dir: Path) -> list[tuple[str, Path]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        ("Calm / serene landscape",   "calm_landscape.png",   _make_calm_image),
        ("Energetic / chaotic scene", "energetic_chaos.png",  _make_energetic_image),
        ("Moody / atmospheric night", "moody_night.png",      _make_moody_image),
    ]
    results = []
    for label, filename, builder in specs:
        path = out_dir / filename
        if not path.exists():
            builder(512).save(path)
        results.append((label, path))
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) > 1:
        images = [(Path(p).stem.replace("_", " "), Path(p)) for p in sys.argv[1:]]
        print("Using provided images.")
    else:
        print("No images provided — generating synthetic test images in sample_images/")
        print("(Pass real photo paths as arguments for richer results.)\n")
        images = make_synthetic_test_images(Path(__file__).parent / "sample_images")

    print(f"Model : {MODEL}")
    print(f".env  : {_ENV_PATH}")
    print("=" * 64)

    for label, path in images:
        print(f"\n  Image : {label}")
        print(f"  File  : {path.name}")
        t0 = time.perf_counter()
        try:
            prompt = image_to_music_prompt(path)
            elapsed = time.perf_counter() - t0
            print(f"  Prompt: {prompt}")
            print(f"          ({elapsed:.1f}s)")
        except Exception as exc:
            print(f"  ERROR : {exc}")

    print("\n" + "=" * 64)
    print("These prompts can be passed directly to a text-to-music model.")
    print("For best results, test with real photographs.")


if __name__ == "__main__":
    main()
