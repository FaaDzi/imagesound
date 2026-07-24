"""
test_postfilter.py — standalone sanity check for pipeline/postfilter.py.

Run directly (same style as pipeline/test_arc.py). Loads one real,
already-generated WAV and compares raw vs. filtered — this is the only
rigorous A/B available, since MusicGen generation itself is stochastic
(no seed control), so two fresh /generate calls are never directly
comparable to each other.
"""

import sys
import time
from pathlib import Path

import numpy as np
import torch
import torchaudio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.postfilter import apply_postfilter


def _hf_energy(y: np.ndarray, sr: int, lo=8000, hi=16000) -> float:
    import librosa
    S = np.abs(librosa.stft(y.mean(axis=0) if y.ndim == 2 else y, n_fft=2048))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    band = (freqs >= lo) & (freqs <= hi)
    return float(S[band].sum())


def main(wav_path: Path) -> None:
    print(f"Loading: {wav_path}")
    raw, sr = torchaudio.load(str(wav_path))
    print(f"  shape={tuple(raw.shape)}  sr={sr}")

    t0 = time.perf_counter()
    filtered = apply_postfilter(raw, sr)
    elapsed = time.perf_counter() - t0
    print(f"apply_postfilter: {elapsed:.2f}s")

    raw_np, filt_np = raw.numpy(), filtered.numpy()

    raw_peak, filt_peak = np.abs(raw_np).max(), np.abs(filt_np).max()
    raw_rms, filt_rms = np.sqrt(np.mean(raw_np ** 2)), np.sqrt(np.mean(filt_np ** 2))
    raw_hf, filt_hf = _hf_energy(raw_np, sr), _hf_energy(filt_np, sr)

    a, b = raw_np.flatten(), filt_np.flatten()
    corr = float(np.corrcoef(a, b)[0, 1])

    print("\n--- Metrics: raw vs filtered ---")
    print(f"  length          : {raw_np.shape[-1]} vs {filt_np.shape[-1]}  (match={raw_np.shape[-1] == filt_np.shape[-1]})")
    print(f"  peak            : {raw_peak:.4f} vs {filt_peak:.4f}  (no-clip-increase={filt_peak <= raw_peak + 1e-6})")
    print(f"  RMS             : {raw_rms:.5f} vs {filt_rms:.5f}  (ratio={filt_rms / raw_rms:.3f})")
    print(f"  HF energy 8-16k : {raw_hf:.1f} vs {filt_hf:.1f}  (ratio={filt_hf / raw_hf:.3f})")
    print(f"  correlation     : {corr:.4f}")

    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    filt_path = out_dir / f"{wav_path.stem}_filtered_test.wav"
    torchaudio.save(str(filt_path), filtered, sr)
    print(f"\nSaved filtered copy for listening: {filt_path}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        wav_path = Path(sys.argv[1])
    else:
        raise SystemExit("Usage: python pipeline/test_postfilter.py <path_to_wav>")
    if not wav_path.exists():
        raise SystemExit(f"Not found: {wav_path}")
    main(wav_path)
