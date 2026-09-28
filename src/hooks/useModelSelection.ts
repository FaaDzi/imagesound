import { useEffect, useMemo, useState } from 'react';
import { getModels, ModelInfo, ModelOption, ModelOptionValues, ModelsResponse } from '../api';

// Everything the Player needs to know about "which model, and what it can do".
// All of it derives from GET /models -- nothing here names a particular model,
// so a model added to pipeline/models.json shows up (with its own options and
// limits) with no frontend change.
//
// `wantsReference`: the current source is an existing song, so only models
// that declare at least one reference mode are usable.

const DEFAULT_DURATION = 15;

/** Whether an option applies right now: its reference-mode and other-option
 *  conditions both hold. Mirrors the server (pipeline/models.py), which drops
 *  an option whose condition fails. */
export function optionApplies(o: ModelOption, referenceMode: string | null, values: ModelOptionValues, all: ModelOption[]): boolean {
  if (o.when_reference_mode && o.when_reference_mode !== referenceMode) return false;
  return Object.entries(o.when_option ?? {}).every(([key, allowed]) => {
    const value = values[key] ?? all.find(x => x.key === key)?.default;
    return allowed.includes(String(value));
  });
}

export interface ModelSelection {
  loading: boolean;
  error: string | null;
  models: ModelInfo[];
  model: ModelInfo | null;
  usable: (m: ModelInfo) => boolean;
  selectModel: (id: string) => void;
  duration: number;
  setDuration: (seconds: number) => void;
  referenceMode: string | null;       // null unless the source is an existing song
  setReferenceMode: (mode: string) => void;
  optionValues: ModelOptionValues;
  setOptionValue: (key: string, value: boolean | number | string) => void;
  // Options to send with /generate: only ones that apply to the current reference mode.
  requestOptions: ModelOptionValues;
}

export function useModelSelection(wantsReference: boolean): ModelSelection {
  const [data, setData] = useState<ModelsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modelId, setModelId] = useState<string | null>(null);
  const [duration, setDurationState] = useState<number>(DEFAULT_DURATION);
  const [referenceMode, setReferenceModeState] = useState<string | null>(null);
  const [optionValues, setOptionValues] = useState<ModelOptionValues>({});

  useEffect(() => {
    let cancelled = false;
    getModels()
      .then(res => { if (!cancelled) setData(res); })
      .catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load models.'); });
    return () => { cancelled = true; };
  }, []);

  const models = data?.models ?? [];
  const usable = (m: ModelInfo) =>
    m.available && (!wantsReference || (m.capabilities.reference?.length ?? 0) > 0);
  const model = models.find(m => m.id === modelId) ?? null;

  // Pick a model once the list arrives, and re-pick if the current one stops
  // being usable (e.g. the source switches to a song, which needs reference support).
  useEffect(() => {
    if (!data) return;
    if (model && usable(model)) return;
    const next =
      data.models.find(m => m.id === data.default && usable(m)) ??
      data.models.find(usable) ??
      null;
    setModelId(next?.id ?? null);
  }, [data, wantsReference, modelId]); // eslint-disable-line react-hooks/exhaustive-deps

  // A different model brings its own option defaults and duration limits.
  useEffect(() => {
    if (!model) return;
    setOptionValues(Object.fromEntries(model.options.map(o => [o.key, o.default])));
    const { min, max } = model.capabilities.duration;
    setDurationState(d => Math.min(max, Math.max(min, d)));
  }, [model?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const modes = model?.capabilities.reference ?? [];
    setReferenceModeState(prev =>
      !wantsReference ? null : prev && modes.includes(prev) ? prev : (modes[0] ?? null));
  }, [model?.id, wantsReference]); // eslint-disable-line react-hooks/exhaustive-deps

  const requestOptions = useMemo(() => {
    const out: ModelOptionValues = {};
    const all = model?.options ?? [];
    for (const o of all) {
      if (!optionApplies(o, referenceMode, optionValues, all)) continue;
      out[o.key] = optionValues[o.key] ?? o.default;
    }
    return out;
  }, [model, referenceMode, optionValues]);

  return {
    loading: data === null && error === null,
    error,
    models,
    model,
    usable,
    selectModel: setModelId,
    duration,
    setDuration: (seconds: number) => {
      const { min, max } = model?.capabilities.duration ?? { min: 1, max: 3600 };
      setDurationState(Math.min(max, Math.max(min, seconds)));
    },
    referenceMode,
    setReferenceMode: setReferenceModeState,
    optionValues,
    setOptionValue: (key, value) => setOptionValues(prev => ({ ...prev, [key]: value })),
    requestOptions,
  };
}
