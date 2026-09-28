import { useRef, useState, useCallback, useEffect } from 'react';
import type { RefObject } from 'react';
import { apiFetch, audioUrl, API_BASE } from '../api';
import type { DownloadFormat } from '../api';
import { generateIRData, createIRBuffer, encodeWav, triggerDownload } from '../utils/audioUtils';
import type { IRData } from '../utils/audioUtils';
import { analyseSpectrum } from '../utils/audioAnalysis';
import { targetForPrompt, autoEqFor } from '../utils/autoEq';

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

// One-click presets. The frequencies come from measuring generated tracks: the
// 8-14 kHz band carried 17.1% of a buzzy drift phonk's magnitude against
// 3.1-9.5% for three tracks that sound fine, which is what the high shelf
// targets. The mid band sits at 3.2 kHz, the ear's most sensitive region and
// where a peak-vs-baseline scan found every track's loudest resonance — note
// that scan did NOT separate the buzzy track from the clean ones (+6.1 dB
// against +5.0 to +8.1 dB), so the mid control is a general bite control, not
// a fix for a defect specific to this model.
export interface EffectPreset {
  id: string; label: string; help: string; params: EffectParams;
}

export const EFFECT_PRESETS: EffectPreset[] = [
  {
    id: 'raw',
    label: 'RAW',
    help: 'Model output, untouched.',
    params: DEFAULT_EFFECTS,
  },
  {
    id: 'debuzz',
    label: 'DE-BUZZ',
    help: 'Pulls down the 3 kHz bite and the 8 kHz+ hash that reads as buzzing.',
    params: { ...DEFAULT_EFFECTS, eqMid: -5, eqHigh: -5 },
  },
  {
    id: 'soften',
    label: 'SOFTEN',
    help: 'Rolls the whole top end back, for tracks that screech.',
    params: { ...DEFAULT_EFFECTS, eqMid: -4, eqHigh: -10, eqLow: 1 },
  },
  {
    id: 'tame',
    label: 'TAME',
    help: 'De-buzz plus gentle compression, evening out tracks that lurch.',
    params: { ...DEFAULT_EFFECTS, eqMid: -4, eqHigh: -5, compThreshold: -18, compRatio: 3 },
  },
];

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
  limiter:    DynamicsCompressorNode;
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

  // Centred at 3.2 kHz rather than a generic 1.5 kHz: that is where measuring
  // generated tracks found the sustained resonance that reads as harsh, and
  // it is also where human hearing is most sensitive.
  const eqMid = ctx.createBiquadFilter();
  eqMid.type = 'peaking';
  eqMid.frequency.value = 3200;
  eqMid.Q.value = 1.2;
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

  // Always-on safety limiter, last in the chain and deliberately not exposed
  // as a parameter. The model peak-normalises to -1 dBFS, which is already
  // close to full scale, and a boosted EQ band, a gain above unity or a wet
  // reverb tail on top of that clips hard and painfully. A hard knee at -3 dB
  // only engages in the last few dB, so ordinary material passes untouched.
  const limiter = ctx.createDynamicsCompressor();
  limiter.threshold.value = -3;
  limiter.knee.value      = 0;
  limiter.ratio.value     = 20;
  limiter.attack.value    = 0.001;
  limiter.release.value   = 0.1;

  // Wire: eqLow → eqMid → eqHigh → compressor → dry/wet split → reverbOut → masterGain → limiter
  eqLow.connect(eqMid);
  eqMid.connect(eqHigh);
  eqHigh.connect(compressor);
  compressor.connect(dryGain);
  compressor.connect(convolver);
  dryGain.connect(reverbOut);
  convolver.connect(wetGain);
  wetGain.connect(reverbOut);
  reverbOut.connect(masterGain);
  masterGain.connect(limiter);

  return {
    input:  eqLow,
    output: limiter,
    nodes:  { eqLow, eqMid, eqHigh, compressor, dryGain, convolver, wetGain, reverbOut, masterGain, limiter },
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
  // Derived from the track itself once it has been analysed; null until then,
  // and also null when the track measures inside its style's targets.
  const [autoPreset,       setAutoPreset]       = useState<EffectPreset | null>(null);
  const [isAnalysing,      setIsAnalysing]      = useState(false);

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
      // Requires crossOrigin="use-credentials" on the element (CORS-clean + sends
      // the session cookie, since /audio/{id} is a protected endpoint).
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

  // AUTO first when the track earned one, so it reads as the default choice.
  const presets: EffectPreset[] = autoPreset ? [autoPreset, ...EFFECT_PRESETS] : EFFECT_PRESETS;
  const presetsRef = useRef<EffectPreset[]>(presets);
  presetsRef.current = presets;

  /** Apply a named preset in one go. */
  const applyPreset = useCallback((presetId: string) => {
    const preset = presetsRef.current.find(p => p.id === presetId);
    if (!preset) return;
    const next = { ...preset.params };
    paramsRef.current = next;
    setParams(next);
    if (nodesRef.current) applyToNodes(nodesRef.current, next);
  }, []);

  /** Which preset the current settings correspond to, or null if hand-tuned. */
  const activePreset = presets.find(p =>
    (Object.keys(p.params) as (keyof EffectParams)[])
      .every(k => p.params[k] === params[k]))?.id ?? null;

  /**
   * Measure a finished track and, if it sits over what its style should carry
   * up top, build an AUTO preset from the difference and switch to it.
   *
   * Applied only while the settings are still untouched: if the listener has
   * already moved something, the analysis lands as an offered button rather
   * than overriding their choice. Analysis failure is silent — the fixed
   * presets remain, which is the behaviour without this.
   */
  const analyseTrack = useCallback(async (jobId: string, prompt?: string | null) => {
    setAutoPreset(null);
    setIsAnalysing(true);
    let ctx: AudioContext | null = null;
    try {
      const res = await apiFetch(audioUrl(jobId), { credentials: 'include' });
      if (!res.ok) return;
      ctx = new AudioContext();
      const buffer = await ctx.decodeAudioData(await res.arrayBuffer());

      const bands = analyseSpectrum(buffer);
      if (!bands) return;

      const target = targetForPrompt(prompt);
      const auto   = autoEqFor(bands, target);
      if (!auto.eqMid && !auto.eqHigh) return;   // already inside target — RAW is right

      const preset: EffectPreset = {
        id:     'auto',
        label:  'AUTO',
        help:   `${auto.reason} Cutting ${[
          auto.eqHigh && `${auto.eqHigh}dB up top`,
          auto.eqMid  && `${auto.eqMid}dB at 3k`,
        ].filter(Boolean).join(' and ')}.`,
        params: { ...DEFAULT_EFFECTS, eqMid: auto.eqMid, eqHigh: auto.eqHigh },
      };
      setAutoPreset(preset);

      if (effectsAreNeutral(paramsRef.current)) {
        const next = { ...preset.params };
        paramsRef.current = next;
        setParams(next);
        if (nodesRef.current) applyToNodes(nodesRef.current, next);
      }
    } catch (err) {
      console.warn('[useAudioEffects] track analysis failed — auto EQ unavailable:', err);
    } finally {
      ctx?.close().catch(() => {});
      setIsAnalysing(false);
    }
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
      const res = await apiFetch(audioUrl(jobId), { credentials: 'include' });
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
      const convRes = await apiFetch(`${API_BASE}/convert?format=${format}`, {
        method:      'POST',
        credentials: 'include',
        headers:     { 'Content-Type': 'audio/wav' },
        body:        wavBlob,
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

  return { params, updateParam, resetEffects, applyPreset, activePreset, presets,
           analyseTrack, isAnalysing, ensureGraph, renderAndDownload, isRendering,
           effectsAvailable };
}
