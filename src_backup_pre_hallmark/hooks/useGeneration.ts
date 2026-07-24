import { useState, useEffect, useRef, useCallback } from 'react';
import { generateSong, getStatus, cancelJob, StatusResult, ArcPreset } from '../api';

export type GenerationPhase =
  | 'idle'
  | 'submitting'      // POST /generate in flight
  | 'queued'          // 202 received, worker hasn't started yet
  | 'loading_model'   // worker is loading MusicGen into VRAM
  | 'processing'      // worker is running MusicGen inference
  | 'done'
  | 'failed';

const POLL_MS = 2_000;

// After this many consecutive poll errors (network/5xx) we give up.
// At 2s intervals this is ~10s of solid failure — server genuinely unreachable.
// We do NOT time out on queued/processing jobs; legitimate queue waits are unbounded.
const MAX_CONSECUTIVE_ERRORS = 5;

// Persists the in-flight job id across remounts (page refresh, tab discard,
// navigating away from /player and back) — without this, the poll loop dies
// with the old component instance while the backend job keeps running and
// finishes unseen.
const JOB_STORAGE_KEY = 'imagesound_active_job';

function readStoredJobId(): string | null {
  try {
    return sessionStorage.getItem(JOB_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredJobId(id: string | null): void {
  try {
    if (id === null) sessionStorage.removeItem(JOB_STORAGE_KEY);
    else sessionStorage.setItem(JOB_STORAGE_KEY, id);
  } catch { /* sessionStorage unavailable — in-memory only */ }
}

export interface UseGenerationReturn {
  phase: GenerationPhase;
  jobId: string | null;
  result: StatusResult | null;
  error: string | null;
  queueDepth: number | null;
  progress: number | null;
  generate: (opts: { fileId?: string; melodySourceId?: string; prompt?: string; duration?: number; model?: 'medium' | 'small'; arc_preset?: ArcPreset; arc_segments?: number[]; filter_mode?: 'raw' | 'filtered' }) => void;
  cancel: () => void;
  reset: () => void;
}

export function useGeneration(): UseGenerationReturn {
  const [phase, setPhase] = useState<GenerationPhase>('idle');
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<StatusResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queueDepth, setQueueDepth] = useState<number | null>(null);
  const [progress, setProgress] = useState<number | null>(null);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const consecutiveErrorsRef = useRef<number>(0);
  // Kept in a ref so the visibility handler can call the latest poll fn without
  // capturing a stale closure — updated every time startPolling is called.
  const pollFnRef = useRef<(() => void) | null>(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    pollFnRef.current = null;
  }, []);

  // Clean up on unmount so no interval leaks when the user navigates away.
  useEffect(() => () => stopPolling(), [stopPolling]);

  // When the browser tab becomes visible again, poll immediately rather than
  // waiting up to a minute for the throttled setInterval to fire.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') pollFnRef.current?.();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, []);

  const startPolling = useCallback((id: string) => {
    stopPolling();
    consecutiveErrorsRef.current = 0;

    const doPoll = async () => {
      try {
        const status = await getStatus(id);
        consecutiveErrorsRef.current = 0; // reset on any successful response

        if (status.status === 'done') {
          stopPolling();
          setResult(status);
          setQueueDepth(null);
          setProgress(null);
          setPhase('done');
        } else if (status.status === 'failed') {
          stopPolling();
          setQueueDepth(null);
          setProgress(null);
          setPhase('failed');
          setError('Generation failed on the server. Try again.');
          writeStoredJobId(null);
        } else {
          // 'queued' | 'loading_model' | 'processing' — all legitimate waiting states.
          // Never time out here; the single-worker queue may have multiple jobs ahead.
          setPhase(status.status as GenerationPhase);
          setQueueDepth(status.queue_depth ?? null);
          setProgress(status.progress ?? null);
        }
      } catch {
        // Network error or 5xx — count consecutive failures.
        consecutiveErrorsRef.current += 1;
        if (consecutiveErrorsRef.current >= MAX_CONSECUTIVE_ERRORS) {
          stopPolling();
          setPhase('failed');
          setError('Server stopped responding. Check that the backend is running and try again.');
        }
        // else: transient blip — keep polling silently.
      }
    };

    pollFnRef.current = doPoll;
    intervalRef.current = setInterval(doPoll, POLL_MS);
  }, [stopPolling]);

  // Reconnect to a job that outlived the previous mount (page refresh, tab
  // discard, or navigating away from /player and back). Generation runs
  // server-side independent of this component — without this, the finished
  // result goes unseen forever because nothing still holds its id.
  useEffect(() => {
    const storedId = readStoredJobId();
    if (!storedId) return;

    setJobId(storedId);
    getStatus(storedId)
      .then(status => {
        if (status.status === 'done') {
          setResult(status);
          setPhase('done');
        } else if (status.status === 'failed') {
          writeStoredJobId(null);
          setJobId(null);
          setPhase('failed');
          setError('Generation failed on the server. Try again.');
        } else {
          setPhase(status.status as GenerationPhase);
          setQueueDepth(status.queue_depth ?? null);
          setProgress(status.progress ?? null);
          startPolling(storedId);
        }
      })
      .catch(() => {
        // Job no longer exists (expired/discarded) or server unreachable at
        // mount time — drop the stale reference rather than getting stuck.
        writeStoredJobId(null);
        setJobId(null);
      });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const generate = useCallback((opts: { fileId?: string; melodySourceId?: string; prompt?: string; duration?: number; model?: 'medium' | 'small'; arc_preset?: ArcPreset; arc_segments?: number[]; filter_mode?: 'raw' | 'filtered' }) => {
    stopPolling();
    setPhase('submitting');
    setError(null);
    setResult(null);
    setJobId(null);
    setQueueDepth(null);
    setProgress(null);
    writeStoredJobId(null);

    generateSong({ id: opts.fileId, melody_source_id: opts.melodySourceId, prompt: opts.prompt, duration: opts.duration, model: opts.model, arc_preset: opts.arc_preset, arc_segments: opts.arc_segments, filter_mode: opts.filter_mode })
      .then(res => {
        setJobId(res.id);
        setPhase('queued');
        writeStoredJobId(res.id);
        startPolling(res.id);
      })
      .catch(err => {
        setPhase('failed');
        setError(err instanceof Error ? err.message : 'Generate request failed.');
      });
  }, [stopPolling, startPolling]);

  const cancel = useCallback(() => {
    const idToCancel = jobId;
    // Reset UI immediately — don't wait for the server round-trip.
    stopPolling();
    setPhase('idle');
    setJobId(null);
    setResult(null);
    setError(null);
    setQueueDepth(null);
    setProgress(null);
    writeStoredJobId(null);
    // Fire the cancel request in background. Best-effort: if it fails (e.g. already done),
    // the backend will clean up via the expiry scheduler. 409 is silently ignored.
    if (idToCancel) {
      cancelJob(idToCancel).catch(() => {});
    }
  }, [jobId, stopPolling]);

  const reset = useCallback(() => {
    stopPolling();
    setPhase('idle');
    setJobId(null);
    setResult(null);
    setError(null);
    setQueueDepth(null);
    writeStoredJobId(null);
    setProgress(null);
  }, [stopPolling]);

  return { phase, jobId, result, error, queueDepth, progress, generate, cancel, reset };
}
