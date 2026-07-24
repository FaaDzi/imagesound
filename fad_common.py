"""Shared genre-bucket definitions for FAD scoring.

Pure Python, no torch/fadtk imports -- safe to import from either the main
.venv or the isolated .venv-fad. This is the single source of truth for
bucket ceilings; check_fad.py (manual dev tool) and fad_score_one.py
(backend scoring) both import from here so they can never drift apart.

GENRE BUCKETS -- first pass, calibrated from a handful of hand-labeled songs
(see project conversation history, 2026-07-23/24). Expect to retune ceilings
as more songs get labeled; these are a starting point, not settled numbers:
    dense     (EDM, rock, rhythm-game, psytrance, techno, ...)  ceiling ~150
    jazz      (multi-instrument: piano, drums, clapping, funk, ...) ceiling ~170
    ambient   (sparse, "absence of information": drone, koto, calm, ...) ceiling ~300
    unclassified (prompt didn't match any bucket keyword) ceiling ~200, flagged
"""

# (ceiling, keywords) -- prompt is lowercased before matching. Order matters:
# first bucket whose keyword appears wins, so more specific buckets go first.
GENRE_BUCKETS: dict[str, tuple[float, list[str]]] = {
    "dense": (150, [
        "edm", "rock", "rhythm game", "psytrance", "techno", "dance", "electro",
        "dubstep", "drum and bass", "dnb", "hardstyle", "trance", "house",
        "metal", "punk", "hyperpop", "synth lead", "high-energy", "high energy",
    ]),
    "jazz": (170, [
        "jazz", "piano", "clapping", "drums", "funk", "soul", "swing", "blues",
        "big band", "hot coffee",
    ]),
    "ambient": (300, [
        "ambient", "drone", "serene", "calm", "meditative", "soundscape",
        "koto", "atmospheric", "pad", "lofi", "lo-fi",
    ]),
}
DEFAULT_CEILING = 200  # unclassified prompt -- moderate fallback, flagged as such


def classify_genre(prompt: str) -> tuple[str, float]:
    p = prompt.lower()
    for bucket, (ceiling, keywords) in GENRE_BUCKETS.items():
        if any(kw in p for kw in keywords):
            return bucket, ceiling
    return "unclassified", DEFAULT_CEILING
