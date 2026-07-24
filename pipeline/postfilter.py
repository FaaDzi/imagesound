"""
postfilter.py — optional post-processing filter for MusicGen output.

MusicGen (via EnCodec) has two characteristic artifact classes:
  - Broadband "hallucination" bursts: the LM occasionally samples tokens that
    decode to noise-like content that doesn't fit the surrounding harmonic
    context. These look like outliers against a local spectral floor, which
    is exactly what adaptive spectral gating is designed to catch.
  - Steady-state high-frequency "sparkle"/metallic ringing: a near-constant
    shimmer from residual vector quantization. This is NOT an outlier (it's
    always present), so spectral gating alone won't reliably remove it — a
    static tonal cut is a better fit.

Two stages, always in this order (adaptive first — needs the unmodified
spectrum to judge outliers; static shelf second — final tonal shaping):
  1. Adaptive spectral gate (noisereduce, non-stationary mode) — the
     "threshold: is this ongoing harmonic content or an outlier burst" check.
  2. Static high-shelf cut (hand-rolled RBJ cookbook biquad) — targets the
     steady-state sparkle specifically.

Both stages are attenuation-only by construction (gate reduces outlier energy,
shelf only cuts, never boosts), so the pipeline cannot raise the peak above
the input's — the final clamp is just a defensive guard against STFT/ISTFT
float round-off, not a limiter.

None of the constants below are tuned against this project's actual output —
they're reasonable starting points from general audio-restoration practice.
Real tuning is a listening-based loop: if output still sounds harsh, lower
_SHELF_GAIN_DB further; if it sounds thin/watery ("musical noise"), lower
_PROP_DECREASE.
"""

import numpy as np
import torch

# -- Stage 1: adaptive spectral gate --------------------------------------
_PROP_DECREASE = 0.5          # fraction of flagged "excess" energy removed — deliberately
                               # conservative; too high causes its own "musical noise" warble.
_FREQ_MASK_SMOOTH_HZ = 500     # smooth the gate mask across frequency — avoids isolated-bin chatter.
_TIME_MASK_SMOOTH_MS = 50      # smooth the gate mask across time — avoids frame-to-frame flicker.
_N_FFT = 1024                  # sized for MusicGen's 32kHz output (default librosa n_fft is speech-sized).

# -- Stage 2: static high-shelf cut (RBJ audio-EQ-cookbook biquad) --------
_SHELF_FREQ_HZ = 9000.0        # corner where EnCodec "sparkle" tends to concentrate.
_SHELF_GAIN_DB = -4.0          # cut only — never boosts, so it can't introduce clipping.
_SHELF_Q = 0.707                # Butterworth-style shelf, no resonant bump at the corner.


def _high_shelf_sos(freq_hz: float, gain_db: float, q: float, sr: int) -> np.ndarray:
    """RBJ audio-EQ-cookbook high-shelf biquad, as a scipy `sos` section."""
    a_gain = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * freq_hz / sr
    cos_w0 = np.cos(w0)
    alpha = np.sin(w0) / (2 * q)
    sqrt_a = np.sqrt(a_gain)

    b0 = a_gain * ((a_gain + 1) + (a_gain - 1) * cos_w0 + 2 * sqrt_a * alpha)
    b1 = -2 * a_gain * ((a_gain - 1) + (a_gain + 1) * cos_w0)
    b2 = a_gain * ((a_gain + 1) + (a_gain - 1) * cos_w0 - 2 * sqrt_a * alpha)
    a0 = (a_gain + 1) - (a_gain - 1) * cos_w0 + 2 * sqrt_a * alpha
    a1 = 2 * ((a_gain - 1) - (a_gain + 1) * cos_w0)
    a2 = (a_gain + 1) - (a_gain - 1) * cos_w0 - 2 * sqrt_a * alpha

    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1 / a0, a2 / a0])
    return np.concatenate([b, a])[None, :]  # shape (1, 6) — one sos section


def apply_postfilter(audio: torch.Tensor, sr: int) -> torch.Tensor:
    """Apply the artifact-reduction filter to a generated track.

    audio: CPU tensor, shape [C, T], float in roughly [-1, 1] (torchaudio
    convention — same tensor `_prompt_to_wav` is about to pass to
    torchaudio.save).
    Returns a same-shape, same-dtype CPU tensor.
    """
    import noisereduce as nr
    from scipy.signal import sosfilt

    orig_dtype = audio.dtype
    orig_len = audio.shape[-1]
    orig_peak = audio.abs().max().item()

    y = audio.numpy().astype(np.float32)  # [C, T]

    # Stage 1 — adaptive spectral gate.
    gated = nr.reduce_noise(
        y=y,
        sr=sr,
        stationary=False,
        prop_decrease=_PROP_DECREASE,
        freq_mask_smooth_hz=_FREQ_MASK_SMOOTH_HZ,
        time_mask_smooth_ms=_TIME_MASK_SMOOTH_MS,
        n_fft=_N_FFT,
    )

    # STFT/ISTFT round-trip can shift sample count by a handful of frames —
    # re-align to the original length before the next stage.
    if gated.shape[-1] != orig_len:
        if gated.shape[-1] > orig_len:
            gated = gated[..., :orig_len]
        else:
            pad = orig_len - gated.shape[-1]
            gated = np.pad(gated, [(0, 0)] * (gated.ndim - 1) + [(0, pad)])

    # Stage 2 — static high-shelf cut.
    sos = _high_shelf_sos(_SHELF_FREQ_HZ, _SHELF_GAIN_DB, _SHELF_Q, sr)
    shelved = sosfilt(sos, gated, axis=-1).astype(np.float32)

    result = torch.from_numpy(shelved).to(orig_dtype)

    # Defensive clamp: both stages are attenuation-only, so this should be a
    # no-op in practice — guards only against float round-off from the
    # STFT/ISTFT reconstruction.
    result = torch.clamp(result, -orig_peak, orig_peak) if orig_peak > 0 else result
    return result
