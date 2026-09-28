import { useState, useEffect, useRef, useCallback } from 'react';
import { generateSong, getStatus, getActiveJobs, cancelJob, GenerateError, StatusResult, ModelOptionValues } from '../api';

export type GenerationPhase =
  | 'idle'
  | 'submitting'      // POST /generate in flight
  | 'queued'          // 202 received, worker hasn't started yet
  | 'loading_model'   // the model's worker process is loading weights into VRAM
  | 'processing'      // prompt being written, or the model is generating audio
  | 'done'
  | 'failed';

export interface GenerateOptions {
  fileId?: string;              // uploaded image to turn into a song
  referenceId?: string;         // existing song to condition on (remix / style reference)
  referenceMode?: string;       // how to use it — one of the model's declared reference modes
  prompt?: string;
  duration?: number;
  model?: string;               // model id from GET /models
  options?: ModelOptionValues;  // per-model options
  lyrics?: string;              // sung when options.vocals === 'lyrics'
}

const POLL_MS = 2_000;

// After the admin stops someone else's generation, the worker takes about a
// second to kill it and free the GPU. Retry the admin's own request this
// many times, TAKEOVER_RETRY_MS apart, before giving up.
const TAKEOVER_RETRIES = 10;
const TAKEOVER_RETRY_MS = 1_000;

function requestBody(opts: GenerateOptions) {
  return { id: opts.fileId, reference_id: opts.referenceId, reference_mode: opts.referenceMode, prompt: opts.prompt, duration: opts.duration, model: opts.model, options: opts.options, lyrics: opts.lyrics };
}

// After this many consecutive poll errors (network/5xx) we give up.
// At 2s intervals this is ~10s of solid failure — server genuinely unreachable.
// We do NOT time out on queued/processing jobs; legitimate queue waits are unbounded.
const MAX_CONSECUTIVE_ERRORS = 5;

// Persists the in-flight job id across remounts (page refresh, tab discard,
// navigating away from /player and back) — without this, the poll loop dies
// with the old component instance while the backend job keeps running and
// finishes unseen.
//
// localStorage, not sessionStorage: a phone that kills the backgrounded tab
// can restore it without its session storage, and then the page came back
// with the Generate button live while the job was still running. It is only a
// fast path now anyway -- resync() also asks the server (GET /jobs/active),
// which is the real source of truth.
const JOB_STORAGE_KEY = 'imagesound_active_job';

function readStoredJobId(): string | null {
  try {
    return localStorage.getItem(JOB_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredJobId(id: string | null): void {
  try {
    if (id === null) localStorage.removeItem(JOB_STORAGE_KEY);
    else localStorage.setItem(JOB_STORAGE_KEY, id);
  } catch { /* storage unavailable — the server check still covers it */ }
}

const IN_FLIGHT: ReadonlySet<GenerationPhase> = new Set(['submitting', 'queued', 'loading_model', 'processing']);

export interface UseGenerationReturn {
  phase: GenerationPhase;
  jobId: string | null;
  result: StatusResult | null;
  error: string | null;
  queueDepth: number | null;
  progress: number | null;
  /** Set when generating failed because someone else's job holds the GPU and
   *  the server says we may stop it (admin only). */
  busyJobId: string | null;
  /** Non-blocking note from the server about this run (e.g. host low on RAM). */
  warning: string | null;
  generate: (opts: GenerateOptions) => void;
  /** Stop the job in busyJobId, then retry the last generate request. */
  takeOver: () => Promise<void>;
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
  const [busyJobId, setBusyJobId] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const lastOptsRef = useRef<GenerateOptions | null>(null);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const consecutiveErrorsRef = useRef<number>(0);
  // Kept in a ref so the visibility handler can call the latest poll fn without
  // capturing a stale closure — updated every time startPolling is called.
  const pollFnRef = useRef<(() => void) | null>(null);
  // Mirrors `phase` and `jobId` for resync(), which runs from event listeners.
  const phaseRef = useRef<GenerationPhase>('idle');
  useEffect(() => { phaseRef.current = phase; }, [phase]);
  const jobIdRef = useRef<string | null>(null);
  useEffect(() => { jobIdRef.current = jobId; }, [jobId]);
  const resyncingRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    pollFnRef.current = null;
  }, []);

  // Clean up on unmount so no interval leaks when the user navigates away.
  useEffect(() => () => stopPolling(), [stopPolling]);


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
          // Job is finished and safely recorded server-side — nothing left to
          // reconnect to. Clearing this now (rather than only on 'failed')
          // stops a future, unrelated Player mount (e.g. remixing a different
          // source from the Library) from resurfacing this finished job as
          // if it were the new session's result.
          writeStoredJobId(null);
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
        // A phone suspends a backgrounded page's network, so failures while
        // hidden say nothing about the server. Counting them is what used to
        // flip a running job to "failed" on the way back from the home screen.
        if (document.visibilityState === 'hidden') return;
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

  // Apply a status we got outside the poll loop (reconnecting).
  const applyStatus = useCallback((id: string, status: StatusResult) => {
    setJobId(id);
    setError(null);
    setBusyJobId(null);
    if (status.status === 'done') {
      // See the matching comment in doPoll — a finished job doesn't need
      // to be reconnectable anymore, and leaving it stored would make it
      // resurface on the next unrelated Player visit (e.g. a Library remix).
      writeStoredJobId(null);
      setResult(status);
      setQueueDepth(null);
      setProgress(null);
      setPhase('done');
    } else if (status.status === 'failed') {
      writeStoredJobId(null);
      setJobId(null);
      setPhase('failed');
      setError('Generation failed on the server. Try again.');
    } else {
      writeStoredJobId(id);
      setResult(null);
      setPhase(status.status as GenerationPhase);
      setQueueDepth(status.queue_depth ?? null);
      setProgress(status.progress ?? null);
      startPolling(id);
    }
  }, [startPolling]);

  // Find out from the server what is actually going on, and pick it up.
  // Runs on mount and whenever the page comes back to the foreground: a job
  // that outlived the previous mount (reload, a phone discarding the tab,
  // navigating away and back) or was started on another device must show as
  // running, not leave the Generate button live for a double request.
  //
  // 1. the job this browser remembers -- it may have finished meanwhile, and
  //    then its result should show;
  // 2. otherwise any generation of this account still in flight.
  const resync = useCallback(async () => {
    if (resyncingRef.current) return;
    if (phaseRef.current === 'submitting') return;           // our own request is on its way
    if (IN_FLIGHT.has(phaseRef.current) && intervalRef.current !== null) {
      pollFnRef.current?.();                                   // already tracking: just refresh now
      return;
    }
    resyncingRef.current = true;
    // The user may press Generate while we wait on the server; their new job
    // wins over whatever this check turns up.
    const superseded = () => phaseRef.current === 'submitting' || intervalRef.current !== null;
    try {
      const storedId = readStoredJobId();
      if (storedId) {
        try {
          const status = await getStatus(storedId);
          if (!superseded()) applyStatus(storedId, status);
          return;
        } catch {
          // Gone (expired, discarded) or unreachable: fall through to the
          // server-wide check, which also tells the two apart.
        }
      }
      let jobs;
      try {
        jobs = await getActiveJobs();
      } catch {
        return;   // server unreachable or logged out -- leave things as they are
      }
      if (storedId) writeStoredJobId(null);                    // reachable server, unknown id: stale
      // MIDI conversions belong to the Library page, not the Generate button.
      const live = jobs.find(j => j.input_type !== 'midi');
      if (!live || superseded()) return;
      applyStatus(live.id, { ...live, expires_at: '' });
    } finally {
      resyncingRef.current = false;
    }
  }, [applyStatus]);

  const resyncRef = useRef(resync);
  useEffect(() => { resyncRef.current = resync; }, [resync]);

  useEffect(() => {
    resyncRef.current();
    const onVisible = () => {
      if (document.visibilityState === 'visible') resyncRef.current();
    };
    // pageshow covers a page restored from the back/forward cache, which
    // fires no visibilitychange on some mobile browsers.
    const onPageShow = (e: PageTransitionEvent) => { if (e.persisted) resyncRef.current(); };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('pageshow', onPageShow);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('pageshow', onPageShow);
    };
  }, []);

  const generate = useCallback((opts: GenerateOptions) => {
    stopPolling();
    setPhase('submitting');
    setError(null);
    setResult(null);
    setJobId(null);
    setQueueDepth(null);
    setProgress(null);
    setBusyJobId(null);
    setWarning(null);
    writeStoredJobId(null);
    lastOptsRef.current = opts;

    generateSong(requestBody(opts))
      .then(res => {
        setJobId(res.id);
        setWarning(res.warning ?? null);
        setPhase('queued');
        writeStoredJobId(res.id);
        startPolling(res.id);
      })
      .catch(err => {
        setPhase('failed');
        setError(err instanceof Error ? err.message : 'Generate request failed.');
        setBusyJobId(err instanceof GenerateError ? err.activeJobId ?? null : null);
      });
  }, [stopPolling, startPolling]);

  const takeOver = useCallback(async () => {
    const other = busyJobId;
    const opts = lastOptsRef.current;
    if (!other || !opts) return;
    setBusyJobId(null);
    setError(null);
    setPhase('submitting');
    try {
      await cancelJob(other);
    } catch (err) {
      setPhase('failed');
      setError(err instanceof Error ? err.message : 'Could not stop the other generation.');
      return;
    }
    for (let attempt = 0; attempt < TAKEOVER_RETRIES; attempt++) {
      await new Promise(resolve => setTimeout(resolve, TAKEOVER_RETRY_MS));
      try {
        const res = await generateSong(requestBody(opts));
        setJobId(res.id);
        setWarning(res.warning ?? null);
        setPhase('queued');
        writeStoredJobId(res.id);
        startPolling(res.id);
        return;
      } catch (err) {
        if (err instanceof GenerateError && err.busy) continue;  // GPU not free yet
        setPhase('failed');
        setError(err instanceof Error ? err.message : 'Generate request failed.');
        return;
      }
    }
    setPhase('failed');
    setError('Stopped the other generation, but the GPU is still busy. Try again in a moment.');
  }, [busyJobId, startPolling]);

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
    setBusyJobId(null);
    setWarning(null);
  }, [stopPolling]);

  return { phase, jobId, result, error, queueDepth, progress, busyJobId, warning, generate, takeOver, cancel, reset };
}
