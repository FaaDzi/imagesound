"""
arc_presets.py — per-chunk prompt modifier sequences for long-song arc shaping.

Each preset maps to an ordered list of modifiers (one per chunk, 1-indexed).
The modifier is appended to the user's base prompt so the base identity is
preserved while the energy/density evolves across chunks.

Design constraints (from listening tests):
  - Never near-empty: every modifier keeps a grounding instrument bed
  - Intensity via fullness/warmth/density, NOT pitch-height
  - No "soaring/piercing/high" language — avoids the shrill artefact
  - Cap below maximal busyness — that's where seams/grain appear worst
  - Each modifier explicitly anchors instruments so "intensify" has something
    concrete to evolve rather than inventing a shrill new layer

The `build_arc_prompts` function is the only public API needed by the pipeline.
The preset shapes (for the Step 4 custom curve) are the float energy values
stored alongside each preset.
"""

# Energy level at each chunk position (0.0–1.0, for Step 4 curve visualisation).
# 4 values = up to 4 × 30s = 120s; last value repeats for longer songs.
_PRESET_SHAPES: dict[str, list[float]] = {
    "steady":          [0.50, 0.50, 0.50, 0.50],
    "gentle_build":    [0.25, 0.45, 0.68, 0.88],
    "rise_and_settle": [0.30, 0.65, 0.88, 0.50],
    "calm_energetic":  [0.15, 0.40, 0.70, 0.95],
}

# Per-chunk modifier strings, appended to the base prompt.
# Each list entry corresponds to one 30s chunk (1-indexed).
# Shorter lists are fine — the last entry repeats for extra chunks.
_PRESET_MODIFIERS: dict[str, list[str]] = {
    "steady": [
        "consistent energy and instrumentation throughout, warm and steady",
    ],
    "gentle_build": [
        "moderate energy, grounded instrumentation, warm and present",
        "slightly fuller, added texture and warmth, same core instruments",
        "fuller sound, richer texture, warm and building",
        "full and warm, rich instrumentation, satisfying and complete",
    ],
    "rise_and_settle": [
        "moderate energy, grounded and present, warm",
        "rising energy, fuller instrumentation, building warmth",
        "full energy, rich and warm, driving groove",
        "settling back, warm and grounded, full but relaxed",
    ],
    "calm_energetic": [
        "calm but present, grounded instrumentation, warm and moderate",
        "building energy, fuller texture, same core instruments",
        "energetic and full, driving rhythm, warm and anchored",
        "full warm energy, powerful and danceable, rich and grounded",
    ],
}


def build_arc_prompts(base: str, preset: str, num_chunks: int) -> list[str]:
    """Return a list of `num_chunks` per-chunk prompts by appending each
    preset modifier to the user's base prompt.

    Unknown presets fall back to "steady" (single repeated modifier).
    """
    mods = _PRESET_MODIFIERS.get(preset, _PRESET_MODIFIERS["steady"])
    return [
        f"{base}, {mods[min(i, len(mods) - 1)]}"
        for i in range(num_chunks)
    ]


def preset_shape(preset: str) -> list[float]:
    """Return the energy-level shape for a preset (0.0–1.0 per chunk).
    Used by the frontend for SVG curve rendering (Step 4 hook point).
    """
    return _PRESET_SHAPES.get(preset, _PRESET_SHAPES["steady"])


# ── Intensity-to-modifier mapping ────────────────────────────────────────────
# Maps a 0–100 integer intensity to a clean-envelope prompt modifier.
# Constraints: lowest = "calm but present" (never empty/sparse);
# highest = "full warm energy" (never shrill, soaring, or maximal-busy).

_INTENSITY_THRESHOLDS: list[tuple[int, str]] = [
    (20,  "calm and present, grounded instrumentation, warm and moderate"),
    (40,  "moderate energy, warm and steady, grounded instrumentation"),
    (60,  "moderate-full energy, added warmth and texture, same core instruments"),
    (75,  "fuller sound, richer texture, warm and building"),
    (90,  "full and warm, rich instrumentation, satisfying and complete"),
    (101, "full warm energy, driving groove, rich and anchored"),
]


def intensity_to_modifier(v: int) -> str:
    """Map a 0–100 intensity value to a clean-envelope prompt modifier."""
    for threshold, modifier in _INTENSITY_THRESHOLDS:
        if v < threshold:
            return modifier
    return _INTENSITY_THRESHOLDS[-1][1]


def build_arc_prompts_from_segments(base: str, segments: list[int]) -> list[str]:
    """Build per-chunk prompt list from raw 0–100 intensity values.

    Each segment intensity is mapped to a clean-envelope modifier string and
    appended to the user's base prompt so the base identity is preserved.
    """
    return [f"{base}, {intensity_to_modifier(v)}" for v in segments]
