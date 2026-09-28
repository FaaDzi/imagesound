// Spectral analysis of a decoded track, used to pick EQ settings automatically.
//
// The measure is the share of total magnitude in each frequency band. Magnitude
// (not power) is deliberate: a power spectrum is so dominated by bass that every
// track lands at 80-90% low and the bands that carry harshness vanish into
// rounding. Magnitude shares separate real material cleanly — measured over four
// generated tracks, the 8-14 kHz share ran 3.1% / 4.0% / 9.5% for tracks that
// sound fine against 17.1% for the one that audibly buzzes.

const FFT_SIZE = 2048;
const MAX_WINDOWS = 240;     // ~0.5s of audio total; plenty for an average spectrum

/** In-place iterative radix-2 Cooley-Tukey FFT. Length must be a power of two. */
function fft(re: Float64Array, im: Float64Array): void {
  const n = re.length;

  // Bit-reversal permutation.
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      const tr = re[i]; re[i] = re[j]; re[j] = tr;
      const ti = im[i]; im[i] = im[j]; im[j] = ti;
    }
  }

  for (let len = 2; len <= n; len <<= 1) {
    const ang = -2 * Math.PI / len;
    const wr = Math.cos(ang), wi = Math.sin(ang);
    const half = len >> 1;
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < half; k++) {
        const a = i + k, b = a + half;
        const ur = re[a], ui = im[a];
        const vr = re[b] * cr - im[b] * ci;
        const vi = re[b] * ci + im[b] * cr;
        re[a] = ur + vr; im[a] = ui + vi;
        re[b] = ur - vr; im[b] = ui - vi;
        const ncr = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = ncr;
      }
    }
  }
}

/** Share of total audible magnitude (20 Hz - 14 kHz) in each band, 0-1. */
export interface SpectrumBands {
  low:   number;  // 20-200 Hz
  body:  number;  // 200 Hz - 2.5 kHz
  harsh: number;  // 2.5-8 kHz    — bite, the ear's most sensitive region
  air:   number;  // 8-14 kHz     — hiss and buzz
}

/**
 * Average the spectrum over windows spread evenly across the whole track, so
 * the result describes the song rather than whichever second we happened to
 * sample. Returns null for buffers too short to window.
 */
export function analyseSpectrum(buffer: AudioBuffer): SpectrumBands | null {
  const total = buffer.length;
  if (total < FFT_SIZE) return null;

  // Mono-sum once up front rather than per window.
  const mono = new Float64Array(total);
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < total; i++) mono[i] += data[i];
  }
  const scale = 1 / buffer.numberOfChannels;
  for (let i = 0; i < total; i++) mono[i] *= scale;

  const hann = new Float64Array(FFT_SIZE);
  for (let i = 0; i < FFT_SIZE; i++) {
    hann[i] = 0.5 * (1 - Math.cos(2 * Math.PI * i / (FFT_SIZE - 1)));
  }

  const nWindows = Math.max(1, Math.min(MAX_WINDOWS, Math.floor(total / FFT_SIZE)));
  const step = nWindows > 1 ? (total - FFT_SIZE) / (nWindows - 1) : 0;

  const bins = FFT_SIZE / 2 + 1;
  const mag = new Float64Array(bins);
  const re = new Float64Array(FFT_SIZE);
  const im = new Float64Array(FFT_SIZE);

  for (let w = 0; w < nWindows; w++) {
    const start = Math.round(w * step);
    for (let i = 0; i < FFT_SIZE; i++) {
      re[i] = mono[start + i] * hann[i];
      im[i] = 0;
    }
    fft(re, im);
    for (let b = 0; b < bins; b++) mag[b] += Math.hypot(re[b], im[b]);
  }

  const hzPerBin = buffer.sampleRate / FFT_SIZE;
  const sum = (lo: number, hi: number) => {
    let acc = 0;
    const from = Math.max(1, Math.ceil(lo / hzPerBin));
    const to   = Math.min(bins - 1, Math.floor(hi / hzPerBin));
    for (let b = from; b <= to; b++) acc += mag[b];
    return acc;
  };

  const audible = sum(20, 14000);
  if (!(audible > 0)) return null;

  return {
    low:   sum(20, 200)     / audible,
    body:  sum(200, 2500)   / audible,
    harsh: sum(2500, 8000)  / audible,
    air:   sum(8000, 14000) / audible,
  };
}
