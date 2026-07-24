/**
 * Seeded LCG random — identical sequence for any given seed.
 * Used to produce the same IR shape at any sample rate.
 */
function seededRand(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 0x100000000;
  };
}

// ── Impulse-response generation ───────────────────────────────────────────────

const IR_SEED     = 0xdeadbeef;
const IR_DURATION = 2.5;  // seconds
const IR_DECAY    = 2.5;  // exponential power

export interface IRData {
  left:       Float32Array;
  right:      Float32Array;
  sampleRate: number;
}

/**
 * Generate a synthetic reverb impulse response at the given sample rate.
 * Uses a fixed seed so live preview and offline render produce the same
 * spectral character regardless of which AudioContext they live in.
 */
export function generateIRData(sampleRate: number): IRData {
  const rand   = seededRand(IR_SEED);
  const length = Math.floor(sampleRate * IR_DURATION);
  const left   = new Float32Array(length);
  const right  = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const env = Math.pow(1 - i / length, IR_DECAY);
    left[i]   = (rand() * 2 - 1) * env;
    right[i]  = (rand() * 2 - 1) * env;
  }
  return { left, right, sampleRate };
}

/** Create an AudioBuffer from cached IRData — works in any AudioContext. */
export function createIRBuffer(
  ctx: AudioContext | OfflineAudioContext,
  irData: IRData,
): AudioBuffer {
  const buf = ctx.createBuffer(2, irData.left.length, irData.sampleRate);
  buf.getChannelData(0).set(irData.left);
  buf.getChannelData(1).set(irData.right);
  return buf;
}

// ── WAV encoding ──────────────────────────────────────────────────────────────

function writeStr(view: DataView, offset: number, s: string): void {
  for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
}

/** Encode an AudioBuffer to a 16-bit PCM WAV Blob. */
export function encodeWav(buffer: AudioBuffer): Blob {
  const numCh  = buffer.numberOfChannels;
  const sr     = buffer.sampleRate;
  const len    = buffer.length;
  const dataLen = len * numCh * 2;
  const ab     = new ArrayBuffer(44 + dataLen);
  const v      = new DataView(ab);

  writeStr(v, 0,  'RIFF');
  v.setUint32(4,  36 + dataLen, true);
  writeStr(v, 8,  'WAVE');
  writeStr(v, 12, 'fmt ');
  v.setUint32(16, 16,    true);   // chunk size
  v.setUint16(20, 1,     true);   // PCM
  v.setUint16(22, numCh, true);
  v.setUint32(24, sr,    true);
  v.setUint32(28, sr * numCh * 2, true);
  v.setUint16(32, numCh * 2,      true);
  v.setUint16(34, 16,    true);   // 16-bit
  writeStr(v, 36, 'data');
  v.setUint32(40, dataLen, true);

  let off = 44;
  for (let i = 0; i < len; i++) {
    for (let c = 0; c < numCh; c++) {
      const s = Math.max(-1, Math.min(1, buffer.getChannelData(c)[i]));
      v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
      off += 2;
    }
  }

  return new Blob([ab], { type: 'audio/wav' });
}

// ── Download helper ───────────────────────────────────────────────────────────

export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a   = document.createElement('a');
  a.href    = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
