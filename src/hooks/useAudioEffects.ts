import { useRef, useState, useCallback, useEffect } from 'react';
import type { RefObject } from 'react';
import { audioUrl, API_BASE } from '../api';
import type { DownloadFormat } from '../api';
import { generateIRData, createIRBuffer, encodeWav, triggerDownload } from '../utils/audioUtils';
import type { IRData } from '../utils/audioUtils';

// ── Effect parameter shape ────────────────────────────────────────────────────

export interface EffectParams {
  gain:           number;  // 0–2, default 1.0 (unity)
  eqLow:          number;  // –12 to +12 dB, default 0
  eqMid:          number;  // –12 to +12 dB, default 0
  eqHigh:         number;  // –12 to +12 dB, default 0
  compThreshold:  number;  // –60 to 0 dB, default 0 (never fires = transparent)
  compRatio:      number;  // 1–20, default 1 (1:1 = no compression)
  reverbMix:      number;  // 0–1, default 0 (fully dry)
}

export const DEFAULT_EFFECTS: EffectParams = {
  gain:          1.0,
  eqLow:         0,
  eqMid:         0,
  eqHigh:        0,
  compThreshold: 0,
  compRatio:     1,
  reverbMix:     0,
};

export function effectsAreNeutral(p: EffectParams): boolean {
  return (
    p.gain          === 1.0 &&
    p.eqLow         === 0   &&
    p.eqMid         === 0   &&
    p.eqHigh        === 0   &&
    p.compThreshold === 0   &&
    p.compRatio     === 1   &&
    p.reverbMix     === 0
  );
}

// ── Internal node bag ─────────────────────────────────────────────────────────

interface AudioNodes {
  source:     MediaElementAudioSourceNode;
  eqLow:      BiquadFilterNode;
  eqMid:      BiquadFilterNode;
  eqHigh:     BiquadFilterNode;
  compressor: DynamicsCompressorNode;
  dryGain:    GainNode;
  convolver:  ConvolverNode;
  wetGain:    GainNode;
  reverbOut:  GainNode;
  masterGain: GainNode;
}

function applyToNodes(nodes: AudioNodes, p: EffectParams): void {
  nodes.eqLow.gain.value           = p.eqLow;
  nodes.eqMid.gain.value           = p.eqMid;
  nodes.eqHigh.gain.value          = p.eqHigh;
  nodes.compressor.threshold.value = p.compThreshold;
  nodes.compressor.ratio.value     = p.compRatio;
  nodes.dryGain.gain.value         = 1 - p.reverbMix;
  nodes.wetGain.gain.value         = p.reverbMix;
  nodes.masterGain.gain.value      = p.gain;
}

/** Build the EQ → compressor → reverb → gain chain in any AudioContext type. */
function buildEffectChain(
  ctx:    AudioContext | OfflineAudioContext,
  irData: IRData,
  p:      EffectParams,
): {
  input:      AudioNode;
  output:     AudioNode;
  nodes:      Omit<AudioNodes, 'source'>;
} {
  const eqLow = ctx.createBiquadFilter();
  eqLow.type = 'lowshelf';
  eqLow.frequency.value = 200;
  eqLow.gain.value = p.eqLow;

  const eqMid = ctx.createBiquadFilter();
  eqMid.type = 'peaking';
  eqMid.frequency.value = 1500;
  eqMid.Q.value = 1.0;
  eqMid.gain.value = p.eqMid;

  const eqHigh = ctx.createBiquadFilter();
  eqHigh.type = 'highshelf';
  eqHigh.frequency.value = 8000;
  eqHigh.gain.value = p.eqHigh;

  const compressor = ctx.createDynamicsCompressor();
  compressor.threshold.value = p.compThreshold;
  compressor.knee.value      = 10;
  compressor.ratio.value     = p.compRatio;
  compressor.attack.value    = 0.003;
  compressor.release.value   = 0.25;

  const convolver = ctx.createConvolver();
  convolver.buffer = createIRBuffer(ctx, irData);

  const dryGain   = ctx.createGain();
  dryGain.gain.value = 1 - p.reverbMix;
  const wetGain   = ctx.createGain();
  wetGain.gain.value = p.reverbMix;
  const reverbOut = ctx.createGain();

  const masterGain = ctx.createGain();
  masterGain.gain.value = p.gain;

  // Wire: eqLow → eqMid → eqHigh → compressor → dry/wet split → reverbOut → masterGain
  eqLow.connect(eqMid);
  eqMid.connect(eqHigh);
  eqHigh.connect(compressor);
  compressor.connect(dryGain);
  compressor.connect(convolver);
  dryGain.connect(reverbOut);
  convolver.connect(wetGain);
  wetGain.connect(reverbOut);
  reverbOut.connect(masterGain);

  return {
    input:  eqLow,
    output: masterGain,
    nodes:  { eqLow, eqMid, eqHigh, compressor, dryGain, convolver, wetGain, reverbOut, masterGain },
  };
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useAudioEffects(audioRef: RefObject<HTMLAudioElement>) {
  const ctxRef    = useRef<AudioContext | null>(null);
  const nodesRef  = useRef<AudioNodes  | null>(null);
  const irDataRef = useRef<IRData      | null>(null);

  const [params,           setParams]           = useState<EffectParams>(DEFAULT_EFFECTS);
  const [isRendering,      setIsRendering]      = useState(false);
  // False if Web Audio setup failed — effects UI disables, generation/playback unaffected.
  const [effectsAvailable, setEffectsAvailable] = useState(true);

  // Keep a ref in sync so async callbacks always read the latest params.
  const paramsRef = useRef<EffectParams>(DEFAULT_EFFECTS);

  // Close AudioContext on unmount.
  useEffect(() => () => { ctxRef.current?.close().catch(() => {}); }, []);

  /**
   * Initialise (or resume) the Web Audio graph.
   * Must be called from a user-gesture handler (play button).
   * Fails gracefully — never throws, never crashes the page.
   */
  const ensureGraph = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;

    try {
      if (!ctxRef.current) ctxRef.current = new AudioContext();
      const ctx = ctxRef.current;
      if (ctx.state === 'suspended') ctx.resume().catch(() => {});

      if (nodesRef.current) return; // Graph already wired.

      if (!irDataRef.current) irDataRef.current = generateIRData(ctx.sampleRate);

      const { input, output, nodes } = buildEffectChain(ctx, irDataRef.current, paramsRef.current);

      // Wrap the <audio> element — redirects output through the effect graph.
      // Requires crossOrigin="anonymous" on the element for cross-origin audio.
      const source = ctx.createMediaElementSource(audio);
      source.connect(input);
      output.connect(ctx.destination);

      nodesRef.current = { source, ...nodes };
    } catch (err) {
      // Web Audio unavailable or blocked (policy, CORS, unsupported browser).
      // Log but do NOT rethrow — playback continues; effects are simply disabled.
      console.warn('[useAudioEffects] Web Audio setup failed — effects disabled:', err);
      setEffectsAvailable(false);
    }
  }, [audioRef]);

  /** Update one parameter live; applies immediately to the running graph. */
  const updateParam = useCallback(<K extends keyof EffectParams>(
    key: K,
    value: EffectParams[K],
  ) => {
    const next = { ...paramsRef.current, [key]: value };
    paramsRef.current = next;
    setParams(next);
    if (nodesRef.current) applyToNodes(nodesRef.current, next);
  }, []);

  /** Snap all parameters back to their neutral defaults. */
  const resetEffects = useCallback(() => {
    paramsRef.current = { ...DEFAULT_EFFECTS };
    setParams({ ...DEFAULT_EFFECTS });
    if (nodesRef.current) applyToNodes(nodesRef.current, DEFAULT_EFFECTS);
  }, []);

  /**
   * Fetch the source audio, run it through an OfflineAudioContext with the
   * current effect settings, encode to WAV, then either trigger a direct
   * download (WAV) or POST to /convert for other formats.
   * Falls back to plain server-side download if anything goes wrong.
   */
  const renderAndDownload = useCallback(async (
    jobId:  string,
    prompt: string | null | undefined,
    format: DownloadFormat,
  ) => {
    const p = paramsRef.current;
    setIsRendering(true);
    try {
      // Fetch source audio.
      const res = await fetch(audioUrl(jobId));
      if (!res.ok) throw new Error('Failed to fetch audio for rendering.');
      const arrBuf = await res.arrayBuffer();

      // Decode via a short-lived context (OfflineAudioContext can't decode).
      const tmpCtx = new AudioContext();
      const srcBuf = await tmpCtx.decodeAudioData(arrBuf);
      await tmpCtx.close();

      // Build offline context — always stereo so the convolver renders correctly.
      const offCtx = new OfflineAudioContext(2, srcBuf.length, srcBuf.sampleRate);

      // Ensure IR exists at the offline context's sample rate.
      if (!irDataRef.current) irDataRef.current = generateIRData(offCtx.sampleRate);
      const irData = irDataRef.current;

      const { input, output } = buildEffectChain(offCtx, irData, p);

      const offSrc = offCtx.createBufferSource();
      offSrc.buffer = srcBuf;
      offSrc.connect(input);
      output.connect(offCtx.destination);
      offSrc.start(0);

      const rendered = await offCtx.startRendering();
      const wavBlob  = encodeWav(rendered);

      const slug = prompt
        ? prompt.slice(0, 40).replace(/[^a-z0-9]+/gi, '_').toLowerCase()
        : jobId.slice(0, 8);

      if (format === 'wav') {
        triggerDownload(wavBlob, `${slug}.wav`);
        return;
      }

      // Non-WAV: ask the backend to convert the processed WAV.
      const convRes = await fetch(`${API_BASE}/convert?format=${format}`, {
        method:  'POST',
        headers: { 'Content-Type': 'audio/wav' },
        body:    wavBlob,
      });
      if (!convRes.ok) {
        // Graceful fallback — WAV rather than a silent failure.
        triggerDownload(wavBlob, `${slug}.wav`);
        return;
      }
      const outBlob = await convRes.blob();
      triggerDownload(outBlob, `${slug}.${format}`);
    } catch (err) {
      // Effects render failed entirely — log and let isRendering reset.
      console.warn('[useAudioEffects] renderAndDownload failed:', err);
    } finally {
      setIsRendering(false);
    }
  }, []);

  return { params, updateParam, resetEffects, ensureGraph, renderAndDownload, isRendering, effectsAvailable };
}
