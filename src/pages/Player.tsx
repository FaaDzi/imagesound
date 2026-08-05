import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation, Link } from 'react-router-dom';
import { Play, Pause, FastForward, Rewind, AlertTriangle, Lock, Check } from 'lucide-react';
import { describeImage, saveJob, discardJob, audioUrl, downloadSong, DownloadFormat } from '../api';
import { usePromptHistory } from '../hooks/usePromptHistory';
import { useGeneration } from '../hooks/useGeneration';
import { useInProgress } from '../context/InProgressContext';
import { useAuth } from '../context/AuthContext';
import { useAudioEffects } from '../hooks/useAudioEffects';
import { SourcePreview } from '../components/player/SourcePreview';
import { ArcEditor, ARC_PRESETS, samplePreset } from '../components/player/ArcEditor';
import { EffectsPanel } from '../components/player/EffectsPanel';
import { GeneratePanel } from '../components/player/GeneratePanel';

// Set to true once facebook/musicgen-small has been downloaded locally.
const SMALL_MODEL_AVAILABLE = true;

// Fixed decorative bar scale factors (0..1) — computed once, no per-render
// randomisation. Applied as `transform: scaleY(var(--h))` on .output-bar, not
// as an animated height: transform/opacity are composite-only, height is not.
// Same mechanic as <Waveform>'s bars — see index.css § .output-bar.
const BAR_HEIGHTS = Array.from({ length: 48 }, (_, i) =>
  Math.max(8, Math.abs(Math.sin(i * 0.42) * 38 + Math.sin(i * 0.91 + 1.3) * 18 + 28)) / 100
);

// Chunk boundary — matches _CHUNK_SEC in generate_song.py.
const LONG_SONG_SEC = 30;

function formatTime(s: number): string {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
}

export function Player() {
  const location = useLocation();
  const state = location.state as {
    fileId?: string;
    filename?: string;
    type?: 'image' | 'audio' | 'text';
    mode?: string;
    url?: string;
    prompt?: string;
  } | null;

  const { item, setItem, updatePrompt, markSaved, clearItem } = useInProgress();
  const { username } = useAuth();
  const audioRef = useRef<HTMLAudioElement>(null);
  const describedForRef = useRef<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  // Playhead position updates many times/sec during playback (browsers fire
  // 'timeupdate' well above the spec's 250ms floor). Driving it through React
  // state would re-render this whole page (waveform bars, arc editor, effects
  // panel) on every tick, so it's written directly to the DOM via these refs
  // instead — see the 'timeupdate' handler below.
  const progressFillRef = useRef<HTMLDivElement>(null);
  const progressHeadRef = useRef<HTMLDivElement>(null);
  const currentTimeTextRef = useRef<HTMLSpanElement>(null);
  const [audioDuration, setAudioDuration] = useState(0);
  const [duration, setDuration] = useState<number>(15);
  const [modelQuality, setModelQuality] = useState<'medium' | 'small'>('medium');
  const [filterMode, setFilterMode] = useState<'raw' | 'filtered'>('filtered');
  const [discardConfirmPending, setDiscardConfirmPending] = useState(false);
  const [mode, setMode] = useState<'classic' | 'vibe'>('classic');
  const history = usePromptHistory();
  const [commitFlash, setCommitFlash] = useState(false);
  const [describeLoading, setDescribeLoading] = useState(false);
  const [describeError, setDescribeError] = useState<string | null>(null);
  const [describeRetryTrigger, setDescribeRetryTrigger] = useState(0);
  const [isSaving, setIsSaving] = useState(false);
  const [isDiscarding, setIsDiscarding] = useState(false);
  const [saveConfirmed, setSaveConfirmed] = useState(false);
  const [downloadFormat, setDownloadFormat] = useState<DownloadFormat>('mp3');
  const [showFormatPicker, setShowFormatPicker] = useState(false);
  const generation = useGeneration();
  const effects    = useAudioEffects(audioRef);

  // ── Arc editor state ─────────────────────────────────────────────────────
  const [arcSegments, setArcSegments] = useState<number[]>([50, 50]);
  const [activePresetId, setActivePresetId] = useState<string>('steady');
  const activePresetRef = useRef<string>('steady');
  const arcDragRef = useRef(false);
  // Bar index locked in at pointerdown for the duration of one drag gesture.
  // Without this, a drag intended as purely vertical (raise/lower one bar)
  // silently "slips" onto a neighboring bar the moment natural hand/trackpad
  // drift carries the cursor's X past that bar's boundary mid-drag.
  const arcDragIdxRef = useRef<number | null>(null);
  const arcBarRef = useRef<HTMLDivElement>(null);

  // Number of chunks for the current duration (only meaningful when >LONG_SONG_SEC).
  const numChunks = duration > LONG_SONG_SEC ? Math.ceil(duration / LONG_SONG_SEC) : 1;
  // Show arc editor when song is long AND we're not displaying a finished result.
  const showArcEditor = duration > LONG_SONG_SEC && generation.phase !== 'done';

  // Determine source. Text path has no fileId — fall back on inputType instead.
  const isTextPath = state?.type === 'text' || (!state?.fileId && item?.inputType === 'text');

  const source = state?.fileId
    ? state
    : isTextPath
      ? { fileId: null as null, type: 'text' as const, filename: null as null, url: null as null }
      : item?.fileId
        ? { fileId: item.fileId, filename: item.filename, type: item.inputType, url: item.url }
        : null;

  const isText  = source?.type === 'text';
  const isImage = source?.type === 'image';
  const isAudio = source?.type === 'audio';
  const fileId  = source?.fileId ?? null;
  const filename = isText ? '// TEXT_INPUT' : ((source?.filename) || '[NO_FILE_DETECTED.RAW]');
  const url = source?.url || 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?q=80&w=2564&auto=format&fit=crop';
  const textPreview = history.draftText || state?.prompt || '--';

  // ── Effects ───────────────────────────────────────────────────────────────

  // On fresh file upload, register the new file in context.
  useEffect(() => {
    if (state?.fileId && state.fileId !== item?.fileId) {
      setItem({
        fileId: state.fileId,
        inputType: state.type ?? null,
        filename: state.filename ?? null,
        url: state.url ?? null,
        prompt: null,
      });
    } else if (state?.type === 'text' && item?.inputType !== 'text') {
      setItem({ fileId: null, inputType: 'text', filename: null, url: null, prompt: state.prompt ?? null });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Text path: initialise history from the typed prompt or restored context.
  useEffect(() => {
    if (!isText) return;
    const prompt = state?.prompt ?? (item?.inputType === 'text' ? item?.prompt : null);
    if (prompt) history.initialize(prompt);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Save current draft text to context whenever it changes.
  useEffect(() => {
    if (history.draftText) updatePrompt(history.draftText);
  }, [history.draftText]); // eslint-disable-line react-hooks/exhaustive-deps

  // Describe effect: fires only on genuine fresh image upload.
  useEffect(() => {
    if (!fileId || !isImage) return;
    setMode('vibe');

    const existingPrompt = item?.fileId === fileId ? item?.prompt : null;
    if (existingPrompt) {
      history.initialize(existingPrompt);
      return;
    }

    if (!state?.fileId) return;

    if (describedForRef.current === fileId) return;
    describedForRef.current = fileId;

    const controller = new AbortController();
    setDescribeLoading(true);
    setDescribeError(null);

    describeImage(fileId, controller.signal)
      .then(prompt => { if (!controller.signal.aborted) history.initialize(prompt); })
      .catch(err => {
        if (!controller.signal.aborted)
          setDescribeError(err instanceof Error ? err.message : 'AI description failed.');
      })
      .finally(() => { if (!controller.signal.aborted) setDescribeLoading(false); });

    return () => { controller.abort(); describedForRef.current = null; };
  }, [fileId, isImage, describeRetryTrigger]); // eslint-disable-line react-hooks/exhaustive-deps

  const retryDescribe = useCallback(() => {
    describedForRef.current = null;
    setDescribeRetryTrigger(n => n + 1);
  }, []);

  // Keep activePresetRef in sync so the duration-change effect always sees current value.
  useEffect(() => { activePresetRef.current = activePresetId; }, [activePresetId]);

  // When duration changes, reshape arcSegments to match the new chunk count.
  useEffect(() => {
    if (duration <= LONG_SONG_SEC) return;
    const n = Math.ceil(duration / LONG_SONG_SEC);
    const named = ARC_PRESETS.find(p => p.id === activePresetRef.current);
    if (named) {
      setArcSegments(samplePreset(named.points, n));
    } else {
      // Custom shape: interpolate to the new length.
      setArcSegments(prev => {
        if (prev.length === n) return prev;
        if (prev.length === 0) return Array(n).fill(50);
        return Array.from({ length: n }, (_, i) => {
          const t  = prev.length <= 1 ? 0 : (i / Math.max(1, n - 1)) * (prev.length - 1);
          const lo = Math.min(prev.length - 1, Math.floor(t));
          const hi = Math.min(prev.length - 1, Math.ceil(t));
          return Math.round(prev[lo] * (1 - (t - lo)) + prev[hi] * (t - lo));
        });
      });
    }
  }, [duration]); // eslint-disable-line react-hooks/exhaustive-deps

  // Wire the audio element events once (ref never changes).
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    // Writes the playhead position straight to the DOM — no setState, so a
    // tick never re-renders the rest of the page. See the refs' declaration
    // above for why.
    const setPlayhead = (t: number) => {
      const dur = audio.duration;
      const pct = isFinite(dur) && dur > 0 ? (t / dur) * 100 : 0;
      if (progressFillRef.current) progressFillRef.current.style.width = `${pct}%`;
      if (progressHeadRef.current) progressHeadRef.current.style.left = `calc(${pct}% - 4px)`;
      if (currentTimeTextRef.current) currentTimeTextRef.current.textContent = formatTime(t);
    };
    const onTimeUpdate    = () => setPlayhead(audio.currentTime);
    const onDuration      = () => setAudioDuration(isFinite(audio.duration) ? audio.duration : 0);
    const onPlay          = () => setIsPlaying(true);
    const onPause         = () => setIsPlaying(false);
    const onEnded         = () => { setIsPlaying(false); setPlayhead(0); };
    audio.addEventListener('timeupdate',     onTimeUpdate);
    audio.addEventListener('durationchange', onDuration);
    audio.addEventListener('loadedmetadata', onDuration);
    audio.addEventListener('play',  onPlay);
    audio.addEventListener('pause', onPause);
    audio.addEventListener('ended', onEnded);
    return () => {
      audio.removeEventListener('timeupdate',     onTimeUpdate);
      audio.removeEventListener('durationchange', onDuration);
      audio.removeEventListener('loadedmetadata', onDuration);
      audio.removeEventListener('play',  onPlay);
      audio.removeEventListener('pause', onPause);
      audio.removeEventListener('ended', onEnded);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load audio when generation finishes.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || generation.phase !== 'done' || !generation.jobId) return;
    if (progressFillRef.current) progressFillRef.current.style.width = '0%';
    if (progressHeadRef.current) progressHeadRef.current.style.left = 'calc(0% - 4px)';
    if (currentTimeTextRef.current) currentTimeTextRef.current.textContent = formatTime(0);
    setAudioDuration(0);
    setIsPlaying(false);
    audio.src = audioUrl(generation.jobId);
    audio.load();
  }, [generation.phase, generation.jobId]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Arc editor handlers ───────────────────────────────────────────────────

  const applyPreset = (presetId: string) => {
    const preset = ARC_PRESETS.find(p => p.id === presetId);
    if (!preset) return;
    setActivePresetId(presetId);
    setArcSegments(samplePreset(preset.points, Math.max(2, numChunks)));
  };

  const getArcIdx = (e: React.PointerEvent): number | null => {
    const el = arcBarRef.current;
    if (!el) return null;
    const rect  = el.getBoundingClientRect();
    const xFrac = Math.max(0, Math.min(1 - 1e-9, (e.clientX - rect.left) / rect.width));
    return Math.min(numChunks - 1, Math.floor(xFrac * numChunks));
  };

  const getArcVal = (e: React.PointerEvent): number => {
    const el = arcBarRef.current;
    if (!el) return 50;
    const rect  = el.getBoundingClientRect();
    const yFrac = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
    return Math.round((1 - yFrac) * 100);
  };

  const handleArcPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault(); // block native drag-ghost and text-selection start
    e.currentTarget.setPointerCapture(e.pointerId);
    const idx = getArcIdx(e);
    if (idx === null) return;
    arcDragRef.current = true;
    arcDragIdxRef.current = idx; // lock for the rest of this gesture
    const val = getArcVal(e);
    setArcSegments(prev => { const n = [...prev]; n[idx] = val; return n; });
    setActivePresetId('custom');
  };

  const handleArcPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!arcDragRef.current || arcDragIdxRef.current === null) return;
    e.preventDefault(); // block scroll on touch and text-highlight on mouse
    const idx = arcDragIdxRef.current; // never re-derived from X mid-drag
    const val = getArcVal(e);
    setArcSegments(prev => { const n = [...prev]; n[idx] = val; return n; });
  };

  const handleArcPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    arcDragRef.current = false;
    arcDragIdxRef.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
  };

  const handleArcPointerCancel = (e: React.PointerEvent<HTMLDivElement>) => {
    arcDragRef.current = false;
    arcDragIdxRef.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
  };

  // ── Generation ────────────────────────────────────────────────────────────

  const handleGenerate = () => {
    setIsSaving(false);
    setIsDiscarding(false);
    setSaveConfirmed(false);
    setDiscardConfirmPending(false);
    history.commit();
    generation.generate({
      fileId: isAudio ? undefined : (fileId ?? undefined),
      melodySourceId: isAudio ? (fileId ?? undefined) : undefined,
      prompt: history.draftText.trim() || undefined,
      duration,
      model: modelQuality,
      arc_segments: duration > LONG_SONG_SEC ? arcSegments : undefined,
      filter_mode: filterMode,
    });
  };

  const handleCommit = () => {
    history.commit();
    setCommitFlash(true);
    setTimeout(() => setCommitFlash(false), 1400);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleCommit();
    }
  };

  const handlePlayPause = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
    } else {
      // Initialise Web Audio on user gesture. Wrapped in try-catch so any
      // AudioContext/CORS/policy error disables effects but NEVER breaks playback.
      try { effects.ensureGraph(); } catch { /* ensureGraph already logs internally */ }
      audio.play().catch(() => {});
    }
  }, [isPlaying, effects.ensureGraph]);

  const handleSeek = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const audio = audioRef.current;
    if (!audio || !audioDuration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    audio.currentTime = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * audioDuration;
  }, [audioDuration]);

  const handleRewind = useCallback(() => {
    const audio = audioRef.current;
    if (audio) audio.currentTime = Math.max(0, audio.currentTime - 10);
  }, []);

  const handleFastForward = useCallback(() => {
    const audio = audioRef.current;
    if (audio) audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 10);
  }, []);

  const handleDownload = useCallback(async () => {
    if (!generation.jobId) return;
    await downloadSong(generation.jobId, generation.result?.prompt, downloadFormat);
  }, [generation.jobId, generation.result?.prompt, downloadFormat]);

  const handleSave = async () => {
    if (!generation.jobId || isSaving || isDiscarding) return;
    setIsSaving(true);
    try {
      await saveJob(generation.jobId);
      setSaveConfirmed(true);
      setIsSaving(false);
      // Deliberately NOT clearing item/generation here — saving shouldn't kick
      // the user off the result panel. They can still play/download the song
      // they just saved; "GENERATE AGAIN" (below) is the explicit way to move on.
      // markSaved() instead, so Home's "unfinished work" resume banner (which
      // reads this same item) knows this session is no longer actually unsaved.
      markSaved();
    } catch {
      setIsSaving(false);
    }
  };

  const handleDiscardClick = () => {
    if (!generation.jobId || isSaving || isDiscarding) return;
    setDiscardConfirmPending(true);
  };

  const handleDiscardConfirm = async () => {
    if (!generation.jobId || isSaving || isDiscarding) return;
    setDiscardConfirmPending(false);
    setIsDiscarding(true);
    try {
      await discardJob(generation.jobId);
      setIsDiscarding(false);
      clearItem();
      generation.reset();
    } catch {
      setIsDiscarding(false);
    }
  };

  const handleDiscardCancel = () => setDiscardConfirmPending(false);

  const handleCancel = () => {
    generation.cancel();
    clearItem();
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="container mx-auto p-4 md:p-8 flex-grow flex flex-col">
      <audio ref={audioRef} preload="metadata" crossOrigin="use-credentials" />

      <div className="flex items-center justify-between border-b-4 pb-4 mb-8" style={{ borderBottomColor: 'var(--accent-secondary)' }}>
        <h2 className="text-3xl font-display font-bold uppercase tracking-widest" style={{ color: 'var(--accent-secondary)' }}>
          // SYS_STUDIO
        </h2>

        {generation.phase === 'done' && !saveConfirmed && (
          <div className="flex items-center gap-2 border p-2 uppercase text-xs font-bold animate-pulse" style={{ color: 'var(--color-warning)', backgroundColor: 'color-mix(in oklch, var(--color-warning) 10%, transparent)', borderColor: 'var(--color-warning)' }}>
            <AlertTriangle size={16} />
            <span>UNSAVED — EXPIRES IN 6H</span>
          </div>
        )}
        {generation.phase !== 'done' && !generation.jobId && (fileId || isText) && (
          <div className="flex items-center gap-2 border p-2 uppercase text-xs font-bold" style={{ color: 'var(--color-warning)', backgroundColor: 'color-mix(in oklch, var(--color-warning) 10%, transparent)', borderColor: 'var(--color-warning)', opacity: 0.6 }}>
            <AlertTriangle size={16} />
            <span>{isText ? 'PROMPT LOADED — NOT YET GENERATED' : 'FILE LOADED — NOT YET GENERATED'}</span>
          </div>
        )}
      </div>

      <div className="relative">
        {!username && (
          <div
            className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-4 text-center px-6"
            style={{ backgroundColor: 'rgba(0, 0, 0, 0.88)' }}
          >
            <Lock className="w-12 h-12" style={{ color: 'var(--accent)' }} strokeWidth={1} />
            <p className="text-lg font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>
              LOGIN REQUIRED
            </p>
            <p className="text-xs uppercase tracking-wide opacity-70 max-w-xs" style={{ color: 'var(--text-muted)' }}>
              The studio's generation controls are locked until you log in.
            </p>
            <Link to="/login" className="brutal-btn text-xs">
              GO TO LOGIN
            </Link>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">

          {/* LEFT COL: ORIGINAL SOURCE */}
          <SourcePreview
            filename={filename}
            imageUrl={url}
            rawUrl={source?.url ?? null}
            fileId={fileId}
            isImage={isImage}
            isText={isText}
            isAudio={isAudio}
            mode={mode}
            textPreview={textPreview}
          />

          {/* RIGHT COL: VISUALIZER & CONTROLS */}
          <div className="col-span-1 lg:col-span-2 flex flex-col gap-8">

          {/* DUAL-MODE PANEL: ARC_EDITOR (long song, not done) or WAVEFORM_OUTPUT (playback / short) */}
          <div data-collider className="border-4 p-4 h-64 relative overflow-hidden flex flex-col" style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}>
            <div className="absolute top-0 left-0 text-xs font-bold px-2 py-1 uppercase tracking-widest z-10" style={{ backgroundColor: 'var(--accent)', color: 'var(--selected-text)' }}>
              {showArcEditor ? 'ARC_EDITOR' : 'WAVEFORM_OUTPUT'}
            </div>

            {showArcEditor ? (
              /* ── ARC EDITOR MODE ── */
              <ArcEditor
                segments={arcSegments}
                numChunks={numChunks}
                activePresetId={activePresetId}
                barRef={arcBarRef}
                onApplyPreset={applyPreset}
                onPointerDown={handleArcPointerDown}
                onPointerMove={handleArcPointerMove}
                onPointerUp={handleArcPointerUp}
                onPointerCancel={handleArcPointerCancel}
              />
            ) : (
              /* ── WAVEFORM / PLAYBACK MODE ── */
              <>
                {/* Decorative bars — animate on play/pause. Keying the container
                    on the done-state forces a fresh mount right when a
                    generation completes, so the reveal animation plays once
                    at that exact moment — see design.md § Motion. */}
                <div
                  key={generation.phase === 'done' ? 'output-done' : 'output-pending'}
                  className="flex-grow flex items-end justify-between gap-[2px] mt-8 pt-2 px-2"
                >
                  {BAR_HEIGHTS.map((h, i) => (
                    <div
                      key={i}
                      className={generation.phase === 'done' ? 'output-bar output-bar--reveal' : 'output-bar'}
                      style={{
                        '--h': isPlaying ? h : h * 0.45,
                        '--o': isPlaying ? 0.85 : 0.3,
                        animationDelay: generation.phase === 'done' ? `${i * 10}ms` : undefined,
                      } as React.CSSProperties}
                    />
                  ))}
                </div>

                {/* Seekable progress bar */}
                <div
                  className="h-4 border mt-2 relative w-full shrink-0 cursor-pointer select-none"
                  style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}
                  onClick={handleSeek}
                  title="Click to seek"
                >
                  <div
                    ref={progressFillRef}
                    className="absolute top-0 left-0 h-full"
                    style={{ width: '0%', backgroundColor: 'var(--accent)', transition: 'width 0.1s linear' }}
                  />
                  <div
                    ref={progressHeadRef}
                    className="absolute top-0 h-full w-2"
                    style={{ left: 'calc(0% - 4px)', backgroundColor: 'var(--text-heading)' }}
                  />
                </div>

                {/* Time display */}
                <div className="flex justify-between text-[10px] font-mono uppercase mt-1 shrink-0" style={{ color: 'var(--accent)', opacity: 0.65 }}>
                  <span ref={currentTimeTextRef}>{formatTime(0)}</span>
                  <span>{audioDuration > 0 ? formatTime(audioDuration) : '--:--'}</span>
                </div>
              </>
            )}
          </div>

          {/* CONTROLS & CONVERTER */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

            <div className="flex flex-col gap-4">
              <div data-collider className="brutal-card">
                <h4 className="font-bold uppercase tracking-widest text-sm mb-4 border-b pb-2" style={{ color: 'var(--accent)', borderBottomColor: 'var(--accent)' }}>
                  [ PLAYBACK ]
                </h4>
                <div className="flex items-center justify-center gap-6">
                  <button
                    onClick={handleRewind}
                    className="transition-colors"
                    style={{ color: 'var(--text-heading)' }}
                    aria-label="Rewind 10 seconds"
                    title="-10s"
                  >
                    <Rewind size={32} />
                  </button>
                  <button
                    className="w-16 h-16 border-4 flex flex-col items-center justify-center transition-colors"
                    onClick={handlePlayPause}
                    aria-label={isPlaying ? 'Pause' : 'Play'}
                    title={isPlaying ? 'Pause' : 'Play'}
                    style={{
                      borderRadius: '0',
                      backgroundColor: 'var(--bg)',
                      color: 'var(--accent)',
                      borderColor: 'var(--accent)',
                    }}
                  >
                    {isPlaying ? <Pause size={32} /> : <Play size={32} className="ml-2" />}
                  </button>
                  <button
                    onClick={handleFastForward}
                    className="transition-colors"
                    style={{ color: 'var(--text-heading)' }}
                    aria-label="Fast forward 10 seconds"
                    title="+10s"
                  >
                    <FastForward size={32} />
                  </button>
                </div>
              </div>

              {/* DESCRIPTION / CONVERSION MODE — shown for image (vibe), text, and audio (melody) */}
              {(isImage || isText || isAudio) && (
                <div data-collider className="brutal-card">
                  <h4 className="font-bold uppercase tracking-widest text-sm mb-4 border-b pb-2" style={{ color: 'var(--accent-tertiary)', borderBottomColor: 'var(--accent-tertiary)' }}>
                    [ {isText ? 'PROMPT_EDITOR' : isAudio ? 'MELODY_STYLE_PROMPT' : 'CONVERSION MODE'} ]
                  </h4>
                  {isAudio && (
                    <p className="text-[10px] uppercase opacity-70 mb-4 font-mono">
                      Follows the reference track's melody via facebook/musicgen-melody.
                      {duration > LONG_SONG_SEC && ' Melody conditioning follows the reference for its full length; if the song runs longer than the reference track, the remainder continues from the prompt.'}
                    </p>
                  )}

                  {/* Classic / Vibe toggle — image only */}
                  {isImage && (
                    <>
                      <div className="flex gap-2 mb-4">
                        <button
                          onClick={() => setMode('classic')}
                          className="flex-1 border p-2 text-xs font-bold uppercase tracking-wider transition-colors"
                          style={{
                            borderColor: 'var(--accent-tertiary)',
                            backgroundColor: mode === 'classic' ? 'var(--accent-tertiary)' : 'transparent',
                            color: mode === 'classic' ? 'var(--selected-text)' : 'var(--accent-tertiary)',
                          }}
                        >
                          [ CLASSIC ]
                        </button>
                        <button
                          onClick={() => setMode('vibe')}
                          className="flex-1 border p-2 text-xs font-bold uppercase tracking-wider transition-colors"
                          style={{
                            borderColor: 'var(--accent-tertiary)',
                            backgroundColor: mode === 'vibe' ? 'var(--accent-tertiary)' : 'transparent',
                            color: mode === 'vibe' ? 'var(--selected-text)' : 'var(--accent-tertiary)',
                          }}
                        >
                          [ VIBE ]
                        </button>
                      </div>
                      <div className="text-[10px] uppercase opacity-70 mb-4 font-mono">
                        {mode === 'classic' ? 'Deterministic spectrogram. Instant. No API.' : 'AI shapes sound based on image mood.'}
                      </div>
                    </>
                  )}

                  {/* Description textarea — image vibe mode, text mode, OR audio (melody) mode */}
                  {(isText || isAudio || mode === 'vibe') && (
                    <div className="flex flex-col gap-2">
                      <label className="text-xs uppercase tracking-widest font-bold" style={{ color: 'var(--accent-tertiary)' }}>
                        Description
                      </label>
                      {describeLoading ? (
                        <div
                          className="brutal-input w-full font-mono text-xs flex items-center"
                          style={{ minHeight: '80px', opacity: 0.7 }}
                        >
                          <span className="animate-pulse" style={{ color: 'var(--accent-tertiary)' }}>
                            // AI_ANALYZING_IMAGE...
                          </span>
                        </div>
                      ) : (
                        <>
                          {describeError && (
                            <div className="flex items-start justify-between gap-2 mb-1">
                              <p className="text-xs" style={{ color: 'var(--accent-secondary)' }}>
                                {describeError}
                              </p>
                              <button
                                onClick={retryDescribe}
                                className="text-[10px] font-bold uppercase tracking-widest border px-2 py-0.5 shrink-0 transition-colors"
                                style={{ borderColor: 'var(--accent-secondary)', color: 'var(--accent-secondary)' }}
                                onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent-secondary)'; e.currentTarget.style.color = 'var(--bg)'; }}
                                onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent-secondary)'; }}
                              >
                                RETRY
                              </button>
                            </div>
                          )}
                          <textarea
                            className="brutal-input w-full font-mono text-sm resize-none"
                            rows={3}
                            value={history.draftText}
                            onChange={e => history.setDraftText(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder={isAudio ? "describe the target genre/style… e.g. 'dark synthwave, driving bassline'" : 'dark, slow, melancholic...'}
                          />
                          <div className="flex items-center justify-between gap-1 mt-1">
                            <span className="text-[10px] font-mono opacity-40 uppercase" style={{ color: 'var(--accent-tertiary)' }}>
                              Enter=commit · Shift+Enter=newline
                            </span>
                            <button
                              onClick={handleCommit}
                              className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest border px-2 py-1 transition-colors shrink-0"
                              style={{
                                borderColor: commitFlash ? 'var(--accent)' : 'var(--accent-tertiary)',
                                color: commitFlash ? 'var(--accent)' : 'var(--accent-tertiary)',
                              }}
                            >
                              <Check size={10} aria-hidden="true" />
                              {commitFlash ? 'SAVED' : 'COMMIT'}
                            </button>
                          </div>
                          {history.checkpoints.length > 1 && (
                            <div
                              className="flex items-center justify-between border-t pt-2 mt-1"
                              style={{ borderTopColor: 'var(--accent-tertiary)' }}
                            >
                              <button
                                onClick={history.revertToOriginal}
                                disabled={history.index === 0}
                                className="text-[10px] font-mono uppercase underline disabled:opacity-20 disabled:no-underline transition-opacity"
                                style={{ color: 'var(--accent-tertiary)' }}
                              >
                                revert
                              </button>
                              <div className="flex items-center gap-1">
                                <button
                                  onClick={history.stepBack}
                                  disabled={!history.canStepBack}
                                  aria-label="Previous prompt version"
                                  className="text-xs font-bold w-6 text-center disabled:opacity-20 transition-opacity"
                                  style={{ color: 'var(--accent-tertiary)' }}
                                >
                                  ←
                                </button>
                                <span
                                  className="text-[10px] font-mono uppercase opacity-60 w-12 text-center"
                                  style={{ color: 'var(--accent-tertiary)' }}
                                  aria-live="polite"
                                >
                                  v{history.index + 1} / {history.checkpoints.length}
                                </span>
                                <button
                                  onClick={history.stepForward}
                                  disabled={!history.canStepForward}
                                  aria-label="Next prompt version"
                                  className="text-xs font-bold w-6 text-center disabled:opacity-20 transition-opacity"
                                  style={{ color: 'var(--accent-tertiary)' }}
                                >
                                  →
                                </button>
                              </div>
                            </div>
                          )}
                          <p className="text-[10px] font-mono uppercase opacity-40 mt-1" style={{ color: 'var(--accent-tertiary)' }}>
                            ↑ generates from the text above
                          </p>
                        </>
                      )}
                    </div>
                  )}

                  {/* Classic sliders — image classic mode only */}
                  {isImage && mode === 'classic' && (
                    <div className="flex flex-col gap-4">
                      <div>
                        <label className="text-xs uppercase tracking-widest font-bold block mb-1" style={{ color: 'var(--accent-tertiary)' }}>BRIGHTNESS</label>
                        <input type="range" className="w-full" style={{ accentColor: 'var(--accent-tertiary)' }} min="0" max="100" defaultValue="50" />
                        <span className="text-[10px] uppercase opacity-50 block mt-1 font-mono">Left: Dark/Low freq — Right: Bright/High freq</span>
                      </div>
                      <div>
                        <label className="text-xs uppercase tracking-widest font-bold block mb-1" style={{ color: 'var(--accent-tertiary)' }}>DENSITY</label>
                        <input type="range" className="w-full" style={{ accentColor: 'var(--accent-tertiary)' }} min="0" max="100" defaultValue="50" />
                        <span className="text-[10px] uppercase opacity-50 block mt-1 font-mono">Left: Sparse/Minimal — Right: Dense/Complex</span>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div data-collider className="brutal-card flex flex-col gap-5">
              <h4 className="font-bold uppercase tracking-widest text-sm border-b pb-2" style={{ color: 'var(--accent-secondary)', borderBottomColor: 'var(--accent-secondary)' }}>
                [ AUDIO_TWEAKER ]
              </h4>

              {/* GENERATION LENGTH */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent-secondary)' }}>
                    GENERATION LENGTH
                  </span>
                  <span className="text-[10px] font-mono" style={{ color: 'var(--accent-secondary)', opacity: 0.55 }}>
                    {duration}s selected
                  </span>
                </div>
                <div className="flex gap-1">
                  {[8, 15, 30, 45, 60, 90, 120, 180].map(d => (
                    <button
                      key={d}
                      onClick={() => setDuration(d)}
                      className="flex-1 border py-1.5 text-xs font-bold uppercase tracking-wider transition-colors"
                      style={{
                        borderColor: 'var(--accent-secondary)',
                        backgroundColor: duration === d ? 'var(--accent-secondary)' : 'transparent',
                        color: duration === d ? 'var(--selected-text)' : 'var(--accent-secondary)',
                        opacity: duration === d ? 1 : 0.45,
                      }}
                    >
                      {d}s
                    </button>
                  ))}
                </div>
                <p className="text-[10px] font-mono uppercase mt-2" style={{ color: 'var(--accent-secondary)', opacity: 0.45 }}>
                  // &gt;30s enables arc editor · longer = slower
                </p>
              </div>

              {/* MODEL QUALITY — hidden for melody mode, which always uses facebook/musicgen-melody */}
              {isAudio ? (
                <div className="border-t pt-4" style={{ borderTopColor: 'var(--accent-secondary)' }}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent-secondary)' }}>
                      MODEL
                    </span>
                  </div>
                  <p className="text-xs font-mono" style={{ color: 'var(--accent-secondary)', opacity: 0.7 }}>
                    facebook/musicgen-melody (fixed — required for melody conditioning)
                  </p>
                </div>
              ) : (
                <div className="border-t pt-4" style={{ borderTopColor: 'var(--accent-secondary)' }}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent-secondary)' }}>
                      MODEL QUALITY
                    </span>
                    <span className="text-[10px] font-mono" style={{ color: 'var(--accent-secondary)', opacity: 0.55 }}>
                      Affects quality &amp; speed
                    </span>
                  </div>
                  <div className="flex gap-1">
                    {(['medium', 'small'] as const).map(m => {
                      const unavailable = m === 'small' && !SMALL_MODEL_AVAILABLE;
                      return (
                        <button
                          key={m}
                          onClick={() => !unavailable && setModelQuality(m)}
                          disabled={unavailable}
                          className="flex-1 border py-1.5 text-xs font-bold uppercase tracking-wider transition-colors relative"
                          style={{
                            borderColor: 'var(--accent-secondary)',
                            backgroundColor: modelQuality === m && !unavailable ? 'var(--accent-secondary)' : 'transparent',
                            color: modelQuality === m && !unavailable ? 'var(--selected-text)' : 'var(--accent-secondary)',
                            opacity: unavailable ? 0.35 : modelQuality === m ? 1 : 0.55,
                            cursor: unavailable ? 'not-allowed' : 'pointer',
                          }}
                        >
                          {m}
                          {unavailable && (
                            <span className="block text-[8px] font-normal normal-case tracking-normal leading-tight mt-0.5 opacity-80">
                              coming soon
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                  <p className="text-[10px] font-mono uppercase mt-2" style={{ color: 'var(--accent-secondary)', opacity: 0.45 }}>
                    // medium: active · small: faster/lighter, not yet downloaded
                  </p>
                </div>
              )}

              {/* OUTPUT QUALITY — raw vs. artifact-filtered, applies to every generation path */}
              <div className="border-t pt-4" style={{ borderTopColor: 'var(--accent-secondary)' }}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent-secondary)' }}>
                    OUTPUT QUALITY
                  </span>
                  <span className="text-[10px] font-mono" style={{ color: 'var(--accent-secondary)', opacity: 0.55 }}>
                    Reduces MusicGen artifacts
                  </span>
                </div>
                <div className="flex gap-1">
                  {(['filtered', 'raw'] as const).map(fm => (
                    <button
                      key={fm}
                      onClick={() => setFilterMode(fm)}
                      className="flex-1 border py-1.5 text-xs font-bold uppercase tracking-wider transition-colors"
                      style={{
                        borderColor: 'var(--accent-secondary)',
                        backgroundColor: filterMode === fm ? 'var(--accent-secondary)' : 'transparent',
                        color: filterMode === fm ? 'var(--selected-text)' : 'var(--accent-secondary)',
                        opacity: filterMode === fm ? 1 : 0.55,
                      }}
                    >
                      {fm}
                    </button>
                  ))}
                </div>
                <p className="text-[10px] font-mono uppercase mt-2" style={{ color: 'var(--accent-secondary)', opacity: 0.45 }}>
                  // filtered: tames sparkle/harsh artifacts · raw: untouched MusicGen output
                </p>
              </div>

              {/* EFFECTS — live Web Audio chain */}
              <EffectsPanel effects={effects} />
            </div>

          </div>

          {/* GENERATE SONG */}
          <GeneratePanel
            generation={generation}
            effects={effects}
            isSaving={isSaving}
            isDiscarding={isDiscarding}
            saveConfirmed={saveConfirmed}
            discardConfirmPending={discardConfirmPending}
            showFormatPicker={showFormatPicker}
            setShowFormatPicker={setShowFormatPicker}
            downloadFormat={downloadFormat}
            setDownloadFormat={setDownloadFormat}
            generateDisabled={describeLoading || (!history.draftText.trim() && !fileId)}
            generateDisabledTitle={describeLoading ? 'Waiting for AI description to finish...' : undefined}
            onGenerate={handleGenerate}
            onCancel={handleCancel}
            onSave={handleSave}
            onDiscardClick={handleDiscardClick}
            onDiscardConfirm={handleDiscardConfirm}
            onDiscardCancel={handleDiscardCancel}
            onGenerateAgain={() => { clearItem(); generation.reset(); }}
          />

        </div>
      </div>
      </div>
    </div>
  );
}
