import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation, Link } from 'react-router-dom';
import { Play, Pause, FastForward, Rewind, AlertTriangle, Lock, Check, Dices } from 'lucide-react';
import { describeImage, randomTheme, writeLyrics, saveJob, discardJob, audioUrl, downloadSong, DownloadFormat } from '../api';
import { usePromptHistory } from '../hooks/usePromptHistory';
import { useGeneration } from '../hooks/useGeneration';
import { useModelSelection } from '../hooks/useModelSelection';
import { useInProgress } from '../context/InProgressContext';
import { useAuth } from '../context/AuthContext';
import { useAudioEffects } from '../hooks/useAudioEffects';
import { SourcePreview } from '../components/player/SourcePreview';
import { ModelPanel } from '../components/player/ModelPanel';
import { EffectsPanel } from '../components/player/EffectsPanel';
import { GeneratePanel } from '../components/player/GeneratePanel';
import { LyricsTips } from '../components/player/LyricsTips';

function formatTime(s: number): string {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
}

// The lyrics box starts with the basic song parts, no words. /// under a
// section means no singing there (pipeline/lyrics.py expand_marks).
const LYRICS_TEMPLATE = '[Intro]\n///\n\n[Verse]\n\n\n[Pre-Chorus]\n\n\n[Chorus]\n\n\n[Outro]\n///\n';
const LYRICS_SECTIONS = ['Intro', 'Verse', 'Pre-Chorus', 'Chorus', 'Build', 'Drop', 'Bridge', 'Outro'];
const TAG_LINE = /^\s*\[[^\]]+\]\s*$/;

// Anything to sing: a line that is not a tag, not ///, not blank.
function hasSungWords(text: string): boolean {
  return text.split('\n').some(l => l.trim() && l.trim() !== '///' && !TAG_LINE.test(l));
}

// The box's sections alone: tags and /// marks, in order.
function structureOf(text: string): string {
  return text.split('\n').map(l => l.trim()).filter(l => l === '///' || TAG_LINE.test(l)).join('\n');
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
  // state would re-render this whole page (waveform bars, model panel, effects
  // panel) on every tick, so it's written directly to the DOM via these refs
  // instead — see the 'timeupdate' handler below.
  const progressFillRef = useRef<HTMLDivElement>(null);
  const progressHeadRef = useRef<HTMLDivElement>(null);
  const currentTimeTextRef = useRef<HTMLSpanElement>(null);
  const [audioDuration, setAudioDuration] = useState(0);
  const [discardConfirmPending, setDiscardConfirmPending] = useState(false);
  const history = usePromptHistory();
  const [commitFlash, setCommitFlash] = useState(false);
  const [themeLoading, setThemeLoading] = useState(false);
  const [themeError, setThemeError] = useState<string | null>(null);
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
  // Which model + its duration / reference-mode / options. An existing song as
  // the source means "remix it", which only some models support.
  const selection = useModelSelection(isAudio);
  const duration = selection.duration;
  const fileId  = source?.fileId ?? null;
  const filename = isText ? '// TEXT_INPUT' : ((source?.filename) || 'NO SOURCE');
  const url = source?.url || 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?q=80&w=2564&auto=format&fit=crop';
  const textPreview = history.draftText || state?.prompt || '--';

  // Lyrics for a vocal song: shown whenever Vocals is on Lyrics.
  const wantsLyrics = selection.requestOptions.vocals === 'lyrics';
  const vocalLanguage = String(selection.requestOptions.vocal_language ?? 'ja');
  const [lyrics, setLyrics] = useState('');
  const [lyricsLanguage, setLyricsLanguage] = useState<string | null>(null);  // language the draft is in
  const [lyricsLoading, setLyricsLoading] = useState(false);
  const [lyricsError, setLyricsError] = useState<string | null>(null);
  // Generate with Vocals on Lyrics but an empty box asks first: draft them,
  // or write them. Nothing is drafted unasked -- the box starts empty.
  const [lyricsPromptOpen, setLyricsPromptOpen] = useState(false);
  const lyricsBoxRef = useRef<HTMLDivElement>(null);
  const lyricsInputRef = useRef<HTMLTextAreaElement>(null);

  // Phone bar: a pinned copy of the Generate action, shown while the real
  // panel (at the end of the settings) is scrolled out of view.
  const generatePanelRef = useRef<HTMLDivElement>(null);
  const outputRef = useRef<HTMLDivElement>(null);
  // The player too: once there is a song, the phone bar becomes a mini-player
  // while the real one is off screen, so playback is in reach from anywhere.
  const transportRef = useRef<HTMLDivElement>(null);
  const miniTimeRef = useRef<HTMLSpanElement>(null);
  const miniFillRef = useRef<HTMLDivElement>(null);
  const [generatePanelVisible, setGeneratePanelVisible] = useState(true);
  const [transportVisible, setTransportVisible] = useState(true);
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    const watch = (el: HTMLElement | null, set: (v: boolean) => void) => {
      if (!el) return null;
      const io = new IntersectionObserver(([entry]) => set(entry.isIntersecting));
      io.observe(el);
      return io;
    };
    const ios = [watch(generatePanelRef.current, setGeneratePanelVisible),
                 watch(transportRef.current, setTransportVisible)];
    return () => ios.forEach(io => io?.disconnect());
  }, []);

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

    describeImage(fileId, controller.signal, selection.model?.id)
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
      if (miniTimeRef.current) miniTimeRef.current.textContent = formatTime(t);
      if (miniFillRef.current) miniFillRef.current.style.width = `${pct}%`;
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
    // Measure the finished track so the effects panel can offer an AUTO preset
    // fitted to it. Runs alongside loading; failure leaves the fixed presets.
    effects.analyseTrack(generation.jobId, generation.result?.prompt);
  }, [generation.phase, generation.jobId]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Generation ────────────────────────────────────────────────────────────

  const handleGenerate = () => {
    if (wantsLyrics && !hasSungWords(lyrics)) {
      setLyricsPromptOpen(true);
      return;
    }
    setIsSaving(false);
    setIsDiscarding(false);
    setSaveConfirmed(false);
    setDiscardConfirmPending(false);
    history.commit();
    generation.generate({
      fileId: isAudio ? undefined : (fileId ?? undefined),
      referenceId: isAudio ? (fileId ?? undefined) : undefined,
      referenceMode: isAudio ? (selection.referenceMode ?? undefined) : undefined,
      prompt: history.draftText.trim() || undefined,
      duration,
      model: selection.model?.id,
      options: selection.requestOptions,
      lyrics: wantsLyrics ? lyrics.trim() : undefined,
    });
  };

  const handleWriteLyrics = useCallback(async () => {
    if (lyricsLoading) return;
    setLyricsLoading(true);
    setLyricsError(null);
    const structure = structureOf(lyrics);
    try {
      const draft = await writeLyrics({
        id: isImage ? (fileId ?? undefined) : undefined,
        prompt: history.draftText.trim() || undefined,
        language: vocalLanguage,
        duration,
        // An edited structure is the user's plan; the untouched template
        // leaves it to the genre's tested plans.
        structure: structure && structure !== structureOf(LYRICS_TEMPLATE) ? structure : undefined,
      });
      setLyrics(draft.lyrics);
      setLyricsLanguage(vocalLanguage);
      // Show the suggested tempo on the Lock BPM control, unless one is
      // already set by hand. (Left at 0, the server picks one anyway.)
      if (selection.model?.options.some(o => o.key === 'bpm') && !Number(selection.optionValues.bpm)) {
        selection.setOptionValue('bpm', draft.bpm);
      }
    } catch (err) {
      setLyricsError(err instanceof Error ? err.message : 'Writing lyrics failed.');
    } finally {
      setLyricsLoading(false);
    }
  }, [lyricsLoading, lyrics, isImage, fileId, history.draftText, vocalLanguage, duration, selection]);

  // Both answers to the empty-lyrics prompt lead to the lyrics box.
  const handleLyricsPromptChoice = (auto: boolean) => {
    setLyricsPromptOpen(false);
    lyricsBoxRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (auto) void handleWriteLyrics();
    else {
      const el = lyricsInputRef.current;
      if (!el) return;
      el.focus({ preventScroll: true });
      const verse = lyrics.indexOf('[Verse]');
      const at = verse >= 0 ? verse + '[Verse]'.length + 1 : lyrics.length;
      el.setSelectionRange(at, at);
    }
  };

  // Section buttons: put a tag (or ///) on its own line at the cursor.
  const insertIntoLyrics = (snippet: string) => {
    const el = lyricsInputRef.current;
    const start = el ? el.selectionStart : lyrics.length;
    const end = el ? el.selectionEnd : lyrics.length;
    const before = lyrics.slice(0, start);
    const after = lyrics.slice(end);
    const isTag = snippet.startsWith('[');
    const lead = !before || before.endsWith('\n\n') ? '' : before.endsWith('\n') ? (isTag ? '\n' : '') : (isTag ? '\n\n' : '\n');
    const text = lead + snippet + '\n';
    setLyrics(before + text + after);
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      const at = before.length + text.length;
      el.setSelectionRange(at, at);
    });
  };

  useEffect(() => {
    if (!lyricsPromptOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setLyricsPromptOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lyricsPromptOpen]);

  // Switching Vocals to Lyrics fills an empty box with the basic parts.
  useEffect(() => {
    if (wantsLyrics) setLyrics(l => (l.trim() ? l : LYRICS_TEMPLATE));
  }, [wantsLyrics]);

  // A different source gets its own lyrics.
  useEffect(() => {
    setLyrics(wantsLyrics ? LYRICS_TEMPLATE : '');
    setLyricsLanguage(null);
    setLyricsError(null);
  }, [fileId]);

  const handleCommit = () => {
    history.commit();
    setCommitFlash(true);
    setTimeout(() => setCommitFlash(false), 1400);
  };

  // Fill the box with an invented theme. Remixing an existing song starts with
  // nothing to go on -- there is no image to describe -- so this is the way in
  // for "I want something different but I don't know what".
  const handleRandomTheme = useCallback(async () => {
    if (themeLoading) return;
    setThemeLoading(true);
    setThemeError(null);
    try {
      const prompt = await randomTheme();
      history.setDraftText(prompt);
    } catch (err) {
      setThemeError(err instanceof Error ? err.message : 'Theme generation failed.');
    } finally {
      setThemeLoading(false);
    }
  }, [themeLoading, history]);

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

  // A song is only loaded into the <audio> element once a generation finishes;
  // everything in the transport is inert until then.
  const hasTrack = generation.phase === 'done' && !!generation.jobId;

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

  const generateDisabled = describeLoading || !selection.model || (!history.draftText.trim() && !fileId);
  const generating = ['submitting', 'queued', 'loading_model', 'processing'].includes(generation.phase);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="container mx-auto p-4 pb-24 md:p-8 md:pb-24 lg:pb-8 flex-grow flex flex-col">
      <audio ref={audioRef} preload="metadata" crossOrigin="use-credentials" />

      <div className="flex items-center justify-between border-b-4 pb-4 mb-8" style={{ borderBottomColor: 'var(--accent-secondary)' }}>
        <div>
          <h2 className="text-3xl font-display font-bold uppercase tracking-widest" style={{ color: 'var(--accent-secondary)' }}>
            // SYS_STUDIO
          </h2>
          <p className="text-xs font-mono mt-1" style={{ color: 'var(--text-muted)' }}>
            Studio · turn your source into a song
          </p>
        </div>

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
            <p className="text-xs uppercase tracking-wide opacity-70 max-w-[20rem]" style={{ color: 'var(--text-muted)' }}>
              The studio's generation controls are locked until you log in.
            </p>
            <Link to="/login" className="brutal-btn text-xs">
              GO TO LOGIN
            </Link>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-8 items-start">

          {/* INPUT: everything the user sets, top to bottom, ending in
              Generate. On a phone the two columns stack, so this order is
              also the phone order: inputs before the button that uses them. */}
          <div className="lg:col-span-3 flex flex-col gap-8 min-w-0">
            <SourcePreview
              filename={filename}
              imageUrl={url}
              rawUrl={source?.url ?? null}
              fileId={fileId}
              isImage={isImage}
              isText={isText}
              isAudio={isAudio}
              textPreview={textPreview}
            />

            {/* DESCRIPTION — for an image (its AI description), text, and audio (the remix style) */}
            {(isImage || isText || isAudio) && (
              <div data-collider className="brutal-card">
                <h4 className="font-bold uppercase tracking-widest text-sm mb-4 border-b pb-2" style={{ color: 'var(--accent-tertiary)', borderBottomColor: 'var(--accent-tertiary)' }}>
                  [ {isText ? 'PROMPT_EDITOR' : isAudio ? 'REMIX_STYLE_PROMPT' : 'IMAGE_DESCRIPTION'} ]
                </h4>
                {isAudio && (
                  <p className="text-[11px] uppercase opacity-70 mb-4 font-mono">
                    Describe the style you want. The reference track is used as chosen under “How to use the reference”.
                  </p>
                )}

                {/* Description textarea — the words the song is made from */}
                {(isText || isAudio || isImage) && (
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
                            <p className="text-xs" style={{ color: 'var(--color-danger)' }}>
                              {describeError}
                            </p>
                            <button
                              onClick={retryDescribe}
                              className="text-[11px] font-bold uppercase tracking-widest border px-2 py-0.5 shrink-0 transition-colors"
                              style={{ borderColor: 'var(--color-danger)', color: 'var(--color-danger)' }}
                              onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--color-danger)'; e.currentTarget.style.color = 'var(--bg)'; }}
                              onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--color-danger)'; }}
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
                          <span className="text-[11px] font-mono opacity-40 uppercase" style={{ color: 'var(--accent-tertiary)' }}>
                            Enter=commit · Shift+Enter=newline
                          </span>
                          <div className="flex items-center gap-1 shrink-0">
                            {/* Invent a theme. Offered for every source, but it is the
                                remix flow that needs it: that one starts with an empty
                                box because there is no image to describe. */}
                            <button
                              onClick={handleRandomTheme}
                              disabled={themeLoading}
                              title="Invent a random music style (runs on the local model)"
                              className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-widest border px-2 py-1 transition-colors"
                              style={{
                                borderColor: 'var(--accent)',
                                color: 'var(--accent)',
                                opacity: themeLoading ? 0.5 : 1,
                                cursor: themeLoading ? 'wait' : 'pointer',
                              }}
                            >
                              <Dices size={10} aria-hidden="true" />
                              {themeLoading ? 'THINKING' : 'SURPRISE ME'}
                            </button>
                            <button
                              onClick={handleCommit}
                              className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-widest border px-2 py-1 transition-colors"
                              style={{
                                borderColor: commitFlash ? 'var(--accent)' : 'var(--accent-tertiary)',
                                color: commitFlash ? 'var(--accent)' : 'var(--accent-tertiary)',
                              }}
                            >
                              <Check size={10} aria-hidden="true" />
                              {commitFlash ? 'SAVED' : 'COMMIT'}
                            </button>
                          </div>
                        </div>
                        {themeError && (
                          <p className="text-[11px] font-mono mt-1" style={{ color: 'var(--color-danger)' }}>
                            {themeError}
                          </p>
                        )}
                        {history.checkpoints.length > 1 && (
                          <div
                            className="flex items-center justify-between border-t pt-2 mt-1"
                            style={{ borderTopColor: 'var(--accent-tertiary)' }}
                          >
                            <button
                              onClick={history.revertToOriginal}
                              disabled={history.index === 0}
                              className="text-[11px] font-mono uppercase underline disabled:opacity-20 disabled:no-underline transition-opacity"
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
                                className="text-[11px] font-mono uppercase opacity-60 w-12 text-center"
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
                        <p className="text-[11px] font-mono uppercase opacity-40 mt-1" style={{ color: 'var(--accent-tertiary)' }}>
                          ↑ generates from the text above
                        </p>
                      </>
                    )}
                  </div>
                )}

              </div>
            )}

            {/* SETTINGS: length, model, vocals -- all driven by GET /models */}
            <div data-collider className="brutal-card flex flex-col gap-6">
              <h4 className="font-bold uppercase tracking-widest text-sm border-b pb-2 flex items-baseline justify-between gap-2" style={{ color: 'var(--accent-secondary)', borderBottomColor: 'var(--accent-secondary)' }}>
                <span>[ AUDIO_TWEAKER ]</span>
                <span className="text-[11px] font-mono font-normal normal-case tracking-normal" style={{ color: 'var(--text-muted)' }}>
                  length · model · vocals
                </span>
              </h4>

              {/* MODEL, LENGTH, REFERENCE MODE + MODEL OPTIONS — all driven by GET /models */}
              <ModelPanel selection={selection} wantsReference={isAudio} />
            </div>

            {/* Lyrics — only with Vocals: lyrics */}
            {wantsLyrics && (
              <div ref={lyricsBoxRef} data-collider className="brutal-card flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <label htmlFor="lyrics-input" className="text-xs uppercase tracking-widest font-bold" style={{ color: 'var(--accent-tertiary)' }}>
                    Lyrics
                  </label>
                  <LyricsTips />
                </div>
                {!lyricsLoading && (
                  <div className="flex gap-1 overflow-x-auto thin-scroll pb-1 sm:flex-wrap sm:overflow-visible sm:pb-0" role="toolbar" aria-label="Add a song section">
                    {LYRICS_SECTIONS.map(name => (
                      <button
                        key={name}
                        onClick={() => insertIntoLyrics(`[${name}]`)}
                        title={`Add a ${name} section at the cursor`}
                        className="shrink-0 whitespace-nowrap text-[11px] font-bold uppercase tracking-widest border px-2.5 min-h-[36px] sm:min-h-0 sm:py-0.5"
                        style={{ borderColor: 'var(--accent-tertiary)', color: 'var(--accent-tertiary)' }}
                      >
                        + {name}
                      </button>
                    ))}
                    <button
                      onClick={() => insertIntoLyrics('///')}
                      title="No singing in this section: the music plays on its own"
                      className="shrink-0 whitespace-nowrap text-[11px] font-bold uppercase tracking-widest border px-2.5 min-h-[36px] sm:min-h-0 sm:py-0.5"
                      style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                    >
                      /// No singing
                    </button>
                  </div>
                )}
                {lyricsLoading ? (
                  <div className="brutal-input w-full font-mono text-xs flex items-center" style={{ minHeight: '120px', opacity: 0.7 }}>
                    <span className="animate-pulse" style={{ color: 'var(--accent-tertiary)' }}>
                      // WRITING_LYRICS ({vocalLanguage.toUpperCase()})...
                    </span>
                  </div>
                ) : (
                  <textarea
                    ref={lyricsInputRef}
                    id="lyrics-input"
                    className="brutal-input w-full font-mono text-sm resize-y"
                    rows={10}
                    value={lyrics}
                    onChange={e => setLyrics(e.target.value)}
                    placeholder={'[Intro]\n///\n\n[Verse]\nshort lines, each ending in a comma,\n\n[Chorus]\n...\n\nWrite your own, or press SURPRISE ME.\n/// under a section = no singing there.'}
                  />
                )}
                {lyricsError && (
                  <p className="text-[11px] font-mono" style={{ color: 'var(--color-danger)' }}>{lyricsError}</p>
                )}
                {lyricsLanguage && lyricsLanguage !== vocalLanguage && hasSungWords(lyrics) && !lyricsLoading && (
                  <p className="flex items-start gap-2 text-[11px] font-mono uppercase" style={{ color: 'var(--color-warning)' }}>
                    <AlertTriangle size={12} className="shrink-0 mt-[1px]" aria-hidden="true" />
                    <span>These lyrics are in {lyricsLanguage.toUpperCase()}, but the language is set to {vocalLanguage.toUpperCase()}. Rewrite them, or the song will be sung with the wrong pronunciation.</span>
                  </p>
                )}
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[11px] font-mono opacity-40 uppercase" style={{ color: 'var(--accent-tertiary)' }}>
                    Short lines · one language · /// = no singing
                  </span>
                  <button
                    onClick={() => { void handleWriteLyrics(); }}
                    disabled={lyricsLoading || describeLoading}
                    title="Write lyrics that fit the description, into the sections above (one AI request)"
                    className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-widest border px-2 py-1 transition-colors shrink-0"
                    style={{
                      borderColor: 'var(--accent)',
                      color: 'var(--accent)',
                      opacity: lyricsLoading || describeLoading ? 0.5 : 1,
                      cursor: lyricsLoading ? 'wait' : 'pointer',
                    }}
                  >
                    <Dices size={10} aria-hidden="true" />
                    {lyricsLoading ? 'WRITING' : hasSungWords(lyrics) ? 'REWRITE' : 'SURPRISE ME'}
                  </button>
                </div>
              </div>
            )}

            {/* GENERATE SONG -- watched, so the phone bar shows only while
                this panel is off screen */}
            <div ref={generatePanelRef}>
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
                generateDisabled={generateDisabled}
                generateDisabledTitle={
                  describeLoading ? 'Waiting for AI description to finish...'
                  : !selection.model ? 'No usable model — see the MODEL panel.'
                  : !history.draftText.trim() && !fileId ? 'Nothing to make a song from yet — add an image, a song or a description on the Upload page.'
                  : undefined}
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

          {/* OUTPUT: the song and what shapes its sound. Stays in view on a
              laptop while the input column scrolls. */}
          <div ref={outputRef} className="lg:col-span-2 flex flex-col gap-8 min-w-0 lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)] lg:overflow-y-auto thin-scroll">
            {/* TRANSPORT: seek bar + time.
                This panel used to be 256px tall and mostly a bank of 48 animated
                bars driven by a fixed array -- they reacted to play/pause but
                never to the audio, so they showed nothing that was true. Removed,
                along with the height they needed. The seek bar and clock below are
                wired to the <audio> element and stay. */}
            <div ref={transportRef} data-collider className="border-4 rounded-[var(--radius-panel)] px-4 pt-8 pb-3 relative flex flex-col" style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}>
              <div className="absolute top-0 left-0 rounded-tl-[var(--radius-chip)] rounded-br-[var(--radius-chip)] text-xs font-bold px-2 py-1 uppercase tracking-widest z-10" style={{ backgroundColor: 'var(--accent)', color: 'var(--selected-text)' }}>
                TRANSPORT
              </div>

              {/* Transport buttons live here now rather than in their own
                  [ PLAYBACK ] card below: they drive the same <audio> element as
                  this seek bar, and splitting them across two panels put the
                  controls further from the thing they control.

                  Disabled until a song is actually loaded. The <audio> element
                  only gets a src when a generation finishes, so before that these
                  looked live but play() rejected on a source-less element and the
                  rejection was swallowed -- pressing play appeared to do nothing
                  at all, with no indication why. */}
              <div className="flex items-center justify-center gap-6 mb-3" style={{ opacity: hasTrack ? 1 : 0.35 }}>
                <button
                  onClick={handleRewind}
                  disabled={!hasTrack}
                  className="transition-colors"
                  style={{ color: 'var(--text-heading)', cursor: hasTrack ? 'pointer' : 'not-allowed' }}
                  aria-label="Rewind 10 seconds"
                  title="-10s"
                >
                  <Rewind size={28} />
                </button>
                <button
                  className="w-14 h-14 border-4 rounded-[var(--radius-chip)] flex items-center justify-center transition-colors"
                  onClick={handlePlayPause}
                  disabled={!hasTrack}
                  aria-label={isPlaying ? 'Pause' : 'Play'}
                  title={hasTrack ? (isPlaying ? 'Pause' : 'Play') : 'Generate a song first'}
                  style={{
                    backgroundColor: 'var(--bg)', color: 'var(--accent)',
                    borderColor: 'var(--accent)', cursor: hasTrack ? 'pointer' : 'not-allowed',
                  }}
                >
                  {isPlaying ? <Pause size={28} /> : <Play size={28} className="ml-1" />}
                </button>
                <button
                  onClick={handleFastForward}
                  disabled={!hasTrack}
                  className="transition-colors"
                  style={{ color: 'var(--text-heading)', cursor: hasTrack ? 'pointer' : 'not-allowed' }}
                  aria-label="Fast forward 10 seconds"
                  title="+10s"
                >
                  <FastForward size={28} />
                </button>
              </div>

              <div
                className="h-4 border relative w-full shrink-0 cursor-pointer select-none rounded-[var(--radius-pill)] overflow-hidden"
                style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}
                onClick={hasTrack ? handleSeek : undefined}
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

              <div className="flex justify-between text-[11px] font-mono uppercase mt-1 shrink-0" style={{ color: 'var(--text-muted)' }}>
                <span ref={currentTimeTextRef}>{formatTime(0)}</span>
                <span>{audioDuration > 0 ? formatTime(audioDuration) : '--:--'}</span>
              </div>
              {!hasTrack && (
                <p className="text-[11px] font-mono text-center mt-1" style={{ color: 'var(--text-muted)' }}>
                  no track loaded — generate a song first
                </p>
              )}
            </div>

            {/* EFFECTS -- a live Web Audio chain on the loaded song, so it
                only opens once there is one to hear. */}
            <div data-collider className="brutal-card">
              {hasTrack ? (
                <EffectsPanel effects={effects} />
              ) : (
                <p className="text-xs font-bold uppercase tracking-widest flex items-center justify-between gap-2" style={{ color: 'var(--accent-tertiary)' }}>
                  <span>Effects</span>
                  <span className="text-[11px] font-mono normal-case tracking-normal font-normal" style={{ color: 'var(--text-muted)' }}>
                    available once a song is generated
                  </span>
                </p>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Bottom bar. Before a song: the Generate action, while the real panel
          is scrolled away -- pinned on a phone, floating on a laptop, where a
          long lyrics box can push Generate below the fold too. After: a
          mini-player on a phone while the real player is off screen (on a
          laptop the player column already stays in view). */}
      {username && !hasTrack && !generatePanelVisible && (
        <div
          className="fixed z-40 bottom-0 inset-x-0 p-3 border-t-2 lg:inset-x-auto lg:left-1/2 lg:-translate-x-1/2 lg:bottom-6 lg:w-[26rem] lg:border-2 lg:rounded-[var(--radius-panel)] lg:shadow-lg"
          style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)', paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))' }}
        >
          {generating ? (
            <button
              onClick={() => generatePanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
              className="brutal-btn w-full min-h-[44px] flex items-center justify-center gap-2 animate-pulse"
            >
              {generation.phase === 'processing' && generation.progress != null
                ? `GENERATING — ${Math.round(generation.progress * 100)}%`
                : 'GENERATING…'}
            </button>
          ) : (
            <button
              onClick={handleGenerate}
              disabled={generateDisabled}
              className="brutal-btn brutal-btn-pink w-full min-h-[44px] flex items-center justify-center gap-2"
              style={{ opacity: generateDisabled ? 0.5 : 1 }}
            >
              GENERATE SONG
            </button>
          )}
        </div>
      )}
      {username && hasTrack && !transportVisible && (
        <div
          className="lg:hidden fixed z-40 bottom-0 inset-x-0 border-t-2"
          style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)', paddingBottom: 'env(safe-area-inset-bottom)' }}
          role="region"
          aria-label="Mini player"
        >
          <div className="h-1 w-full" style={{ backgroundColor: 'var(--border-muted)' }}>
            <div ref={miniFillRef} className="h-full" style={{ width: '0%', backgroundColor: 'var(--accent)' }} />
          </div>
          <div className="flex items-center gap-3 px-3 py-2">
            <button
              onClick={handlePlayPause}
              aria-label={isPlaying ? 'Pause' : 'Play'}
              className="w-11 h-11 shrink-0 border-2 rounded-[var(--radius-chip)] flex items-center justify-center"
              style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
            >
              {isPlaying ? <Pause size={20} /> : <Play size={20} className="ml-0.5" />}
            </button>
            <span className="font-mono text-xs" style={{ color: 'var(--text-primary)' }}>
              <span ref={miniTimeRef}>{formatTime(audioRef.current?.currentTime ?? 0)}</span>
              {' / '}{audioDuration > 0 ? formatTime(audioDuration) : '--:--'}
            </span>
            <button
              onClick={() => transportRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
              className="ml-auto text-[11px] font-bold uppercase tracking-widest border px-3 min-h-[40px]"
              style={{ borderColor: 'var(--accent-tertiary)', color: 'var(--accent-tertiary)' }}
            >
              Player ↑
            </button>
          </div>
        </div>
      )}

      {lyricsPromptOpen && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center p-4"
          style={{ backgroundColor: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(3px)' }}
          onClick={() => setLyricsPromptOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="lyrics-prompt-title"
        >
          <div
            className="border-2 w-full max-w-[26rem] flex flex-col gap-4 p-5"
            style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}
            onClick={e => e.stopPropagation()}
          >
            <h3 id="lyrics-prompt-title" className="font-bold text-xs uppercase tracking-widest flex items-center gap-2" style={{ color: 'var(--accent)' }}>
              <AlertTriangle size={13} aria-hidden="true" /> NO LYRICS YET
            </h3>
            <p className="text-sm font-mono" style={{ color: 'var(--text-primary)' }}>
              Vocals are on, but the lyrics box is empty. Auto-generate them, or write your own?
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <button
                onClick={() => handleLyricsPromptChoice(false)}
                className="text-[11px] font-bold uppercase tracking-widest border px-3 py-2"
                style={{ borderColor: 'var(--accent-tertiary)', color: 'var(--accent-tertiary)' }}
              >
                WRITE MY OWN
              </button>
              <button
                autoFocus
                onClick={() => handleLyricsPromptChoice(true)}
                className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-widest border px-3 py-2"
                style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
              >
                <Dices size={10} aria-hidden="true" /> AUTO-GENERATE
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
