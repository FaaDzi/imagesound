import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Trash2, Music, Database, Play, Pause, Download, Save, FileMusic, Shuffle, Check, AlertTriangle, X, Code2, Copy, ExternalLink, SlidersHorizontal } from 'lucide-react';
import { getLibrary, getStatus, getActiveJobs, discardJob, saveJob, audioUrl, imageUrl, downloadSong, convertToMidi, downloadMidi, midiPreviewUrl, getMidiTracks, getStrudelCode, LibraryItem, MidiTrack, MidiView, DOWNLOAD_FORMATS, DownloadFormat, StrudelMode } from '../api';
import { useAuth } from '../context/AuthContext';

function formatTimeRemaining(expiresAt: string): string {
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return 'EXPIRED';
  const totalMins = Math.floor(ms / 60000);
  const hours = Math.floor(totalMins / 60);
  const mins = totalMins % 60;
  if (hours >= 24) return `${Math.floor(hours / 24)}d remaining`;
  if (hours > 0) return `${hours}h ${mins}m remaining`;
  return `${mins}m remaining`;
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return '--:--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function formatTime(s: number): string {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
}

interface MidiJob {
  state: 'converting' | 'failed';
  midiId?: string;
  status?: string;              // queued | loading_model | processing
  progress?: number | null;     // 0..1 while processing
  stage?: string | null;        // "Separating instruments", ...
  queueDepth?: number | null;
}

function MidiProgress({ job }: { job: MidiJob }) {
  const waiting = job.status === 'queued';
  const pct = waiting ? 0 : Math.round((job.progress ?? 0) * 100);
  const label = waiting
    ? (job.queueDepth ? `Waiting · ${job.queueDepth} ahead` : 'Waiting for the current job to finish')
    : (job.stage ?? 'Converting');
  return (
    <div className="mx-4 mb-2" role="status" aria-live="polite">
      <div className="flex justify-between gap-2 text-[11px] font-mono mb-1" style={{ color: 'var(--accent)' }}>
        <span className="truncate">MIDI · {label}…</span>
        {!waiting && <span className="tabular-nums">{pct}%</span>}
      </div>
      <div
        className="h-2 border relative overflow-hidden"
        style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}
        role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={waiting ? undefined : pct}
        aria-label="MIDI conversion progress"
      >
        <div
          className={`absolute inset-y-0 left-0 ${waiting ? 'animate-pulse' : ''}`}
          style={{ width: waiting ? '100%' : `${pct}%`, opacity: waiting ? 0.25 : 1, backgroundColor: 'var(--accent)', transition: 'width 0.6s ease' }}
        />
      </div>
      <p className="text-[11px] font-mono mt-1" style={{ color: 'var(--text-muted)' }}>
        Keeps going if you leave this page.
      </p>
    </div>
  );
}

export function Library() {
  const navigate = useNavigate();
  const { username, loading: authLoading } = useAuth();
  const loggedIn = !!username;
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);   // bumped by RETRY
  const [downloadFormats, setDownloadFormats] = useState<Record<string, DownloadFormat | 'midi'>>({});

  // Tracks in-progress MIDI conversions keyed by source audio ID.
  const [midiConversions, setMidiConversions] = useState<Record<string, MidiJob>>({});
  const midiPollRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  // The poll function per source, so coming back to the page can check at once
  // instead of waiting out a throttled interval.
  const midiPollFns = useRef<Record<string, () => void>>({});

  // Single shared audio element for the whole page.
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [isAudioPlaying, setIsAudioPlaying] = useState(false);
  const [audioDuration, setAudioDuration] = useState(0);
  // Playhead position updates many times/sec during playback. Driving it
  // through React state would re-render the ENTIRE card grid on every tick
  // just to move one card's progress bar, so it's written directly to the
  // DOM instead — see the 'timeupdate' handler below. playingIdRef mirrors
  // playingId so that handler (attached once) always knows which card's
  // refs to write to without needing to re-run on every play/pause.
  const playingIdRef = useRef<string | null>(null);
  const progressFillRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const currentTimeTextRefs = useRef<Record<string, HTMLSpanElement | null>>({});

  // The library needs a login and shows only this account's songs, so wait
  // for the auth check and re-fetch whenever the account changes.
  useEffect(() => {
    if (authLoading) return;
    if (!username) {
      setItems([]);
      setFetchError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setFetchError(null);
    getLibrary()
      .then(data => setItems(data))
      .catch(err => setFetchError(err instanceof Error ? err.message : 'Failed to load library.'))
      .finally(() => setLoading(false));
  }, [authLoading, username, reloadKey]);

  // Clean up MIDI polling intervals on unmount.
  useEffect(() => {
    return () => { Object.values(midiPollRefs.current).forEach(clearInterval); };
  }, []);

  const stopMidiPoll = useCallback((sourceId: string) => {
    clearInterval(midiPollRefs.current[sourceId]);
    delete midiPollRefs.current[sourceId];
    delete midiPollFns.current[sourceId];
  }, []);

  // Follow one conversion by its own status until it finishes. No time limit:
  // a long song on a busy machine legitimately takes minutes, and the old
  // 3-minute cut-off reported those as failed while they were still running.
  const trackMidiJob = useCallback((sourceId: string, midiId: string) => {
    if (midiPollRefs.current[sourceId]) return;
    let errors = 0;
    const poll = async () => {
      try {
        const st = await getStatus(midiId);
        errors = 0;
        if (st.status === 'done') {
          stopMidiPoll(sourceId);
          setMidiConversions(prev => { const n = { ...prev }; delete n[sourceId]; return n; });
          getLibrary().then(setItems).catch(() => setReloadKey(k => k + 1));
        } else if (st.status === 'failed') {
          stopMidiPoll(sourceId);
          setMidiConversions(prev => ({ ...prev, [sourceId]: { state: 'failed' } }));
        } else {
          setMidiConversions(prev => ({
            ...prev,
            [sourceId]: {
              state: 'converting', midiId, status: st.status,
              progress: st.progress ?? prev[sourceId]?.progress ?? null,
              stage: st.stage ?? prev[sourceId]?.stage ?? null,
              queueDepth: st.queue_depth ?? null,
            },
          }));
        }
      } catch {
        // A backgrounded phone has no network; that is not a failed job.
        if (document.visibilityState === 'hidden') return;
        if (++errors >= 5) {
          stopMidiPoll(sourceId);
          setMidiConversions(prev => ({ ...prev, [sourceId]: { state: 'failed' } }));
        }
      }
    };
    midiPollFns.current[sourceId] = poll;
    midiPollRefs.current[sourceId] = setInterval(poll, 2000);
    poll();
  }, [stopMidiPoll]);

  // Pick up conversions this page has no memory of: started before a reload,
  // before a phone discarded the tab, or on another device. Without this the
  // CONVERT button came back live mid-conversion and invited a second one.
  const resyncMidi = useCallback(async () => {
    Object.values(midiPollFns.current).forEach(fn => fn());
    let jobs;
    try { jobs = await getActiveJobs(); } catch { return; }
    for (const j of jobs) {
      if (j.input_type !== 'midi' || !j.source_file_id) continue;
      const sourceId = j.source_file_id;
      setMidiConversions(prev => prev[sourceId]?.state === 'converting' ? prev : {
        ...prev,
        [sourceId]: { state: 'converting', midiId: j.id, status: j.status, progress: j.progress ?? null, stage: j.stage ?? null, queueDepth: j.queue_depth ?? null },
      });
      trackMidiJob(sourceId, j.id);
    }
  }, [trackMidiJob]);

  useEffect(() => {
    if (authLoading || !username) return;
    resyncMidi();
    const onVisible = () => { if (document.visibilityState === 'visible') resyncMidi(); };
    const onPageShow = (e: PageTransitionEvent) => { if (e.persisted) resyncMidi(); };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('pageshow', onPageShow);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('pageshow', onPageShow);
    };
  }, [authLoading, username, resyncMidi]);

  // Strudel code panel: null when closed, otherwise the entry being shown
  // (code and error both null while the request is still in flight).
  const [strudel, setStrudel] = useState<{ id: string; code: string | null; error: string | null } | null>(null);
  const [strudelMode, setStrudelMode] = useState<StrudelMode>('chords');
  // Per-MIDI-entry track lists, fetched once each, and which part is soloed.
  const [midiTracks, setMidiTracks] = useState<Record<string, MidiTrack[]>>({});
  const [soloTrack, setSoloTrack] = useState<string | null>(null);
  // FULL (every transcribed stem) vs LEAD (melody/chords/bass/drums), per entry.
  const [midiView, setMidiView] = useState<Record<string, MidiView>>({});
  // Mirrors midiView so loadStrudel (memoised with no deps, like the other
  // handlers here) reads the current choice rather than the one at mount.
  const midiViewRef = useRef<Record<string, MidiView>>({});
  // Entries converted before the lead sheet existed: the server answers with
  // the full file, and saying so beats labelling five stems "LEAD".
  const [noLeadSheet, setNoLeadSheet] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!strudel) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setStrudel(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [strudel]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const setPlayhead = (t: number) => {
      const id = playingIdRef.current;
      if (!id) return;
      const dur = audio.duration;
      const pct = isFinite(dur) && dur > 0 ? (t / dur) * 100 : 0;
      const fill = progressFillRefs.current[id];
      const text = currentTimeTextRefs.current[id];
      if (fill) fill.style.width = `${pct}%`;
      if (text) text.textContent = formatTime(t);
    };
    const onTimeUpdate    = () => setPlayhead(audio.currentTime);
    const onDuration      = () => setAudioDuration(isFinite(audio.duration) ? audio.duration : 0);
    const onPlay          = () => setIsAudioPlaying(true);
    const onPause         = () => setIsAudioPlaying(false);
    const onEnded         = () => { setIsAudioPlaying(false); setPlayhead(0); setPlayingId(null); playingIdRef.current = null; };
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
  }, []);

  // Which part of a MIDI entry is soloed, or null for the full mix. Keyed off
  // the URL rather than the id, because soloing swaps the source while the card
  // stays the same -- treating that as "already playing" would pause instead.
  const handlePlay = useCallback((id: string, url: string) => {
    const audio = audioRef.current;
    if (!audio) return;
    const sameSource = playingId === id && audio.src === new URL(url, window.location.href).href;
    if (sameSource) {
      if (isAudioPlaying) audio.pause();
      else audio.play().catch(() => {});
      return;
    }
    setPlayingId(id);
    playingIdRef.current = id;
    setAudioDuration(0);
    audio.src = url;
    audio.load();
    audio.play().catch(() => {});
  }, [playingId, isAudioPlaying]);

  // Switch an entry between FULL and LEAD and (re)list its parts. Always
  // re-fetches: the two files have different tracks, so a cached list from the
  // other view would offer solo buttons for parts that are not there.
  const loadTracks = useCallback(async (id: string, view: MidiView) => {
    setMidiView(v => ({ ...v, [id]: view }));
    midiViewRef.current = { ...midiViewRef.current, [id]: view };
    setMidiTracks(t => ({ ...t, [id]: [] }));
    try {
      const res = await getMidiTracks(id, view);
      setMidiTracks(t => ({ ...t, [id]: res.tracks }));
      setNoLeadSheet(n => ({ ...n, [id]: view === 'lead' && res.view === 'full' }));
    } catch {
      setMidiTracks(t => { const n = { ...t }; delete n[id]; return n; });
    }
  }, []);

  const handleSeek = useCallback((e: React.MouseEvent<HTMLDivElement>, id: string) => {
    if (playingId !== id) return;
    const audio = audioRef.current;
    if (!audio || !audioDuration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    audio.currentTime = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * audioDuration;
  }, [playingId, audioDuration]);

  const handleSave = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await saveJob(id);
      setItems(prev => prev.map(item =>
        item.id === id
          ? { ...item, saved: true, expires_at: new Date(Date.now() + 7 * 86400000).toISOString() }
          : item
      ));
    } catch { /* nothing — optimistic update didn't happen */ }
  }, []);

  const handleDiscard = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await discardJob(id);
      if (playingId === id) {
        audioRef.current?.pause();
        setPlayingId(null);
        playingIdRef.current = null;
      }
      setItems(prev => prev.filter(item => item.id !== id));
    } catch { /* nothing */ }
  }, [playingId]);

  const handleRemix = useCallback((id: string, prompt: string | null, e: React.MouseEvent) => {
    e.stopPropagation();
    navigate('/player', {
      state: {
        fileId: id,
        filename: prompt ? prompt.slice(0, 40) : id.slice(0, 8),
        type: 'audio',
        url: audioUrl(id),
      },
    });
  }, [navigate]);

  const handleDownload = useCallback(async (id: string, prompt: string | null, e: React.MouseEvent) => {
    e.stopPropagation();
    await downloadSong(id, prompt, (downloadFormats[id] ?? 'mp3') as DownloadFormat);
  }, [downloadFormats]);

  const handleDownloadMidi = useCallback(async (id: string, prompt: string | null, e: React.MouseEvent) => {
    e.stopPropagation();
    await downloadMidi(id, prompt);
  }, []);

  // Follows the entry's FULL/LEAD choice, and uses the lead sheet until one is
  // made: the emitter's whole job is to find drums/harmony/bass/melody, and the
  // lead sheet has already separated them (its chords also convert exactly).
  // The server falls back to the full file for entries that have no lead sheet.
  const loadStrudel = useCallback(async (id: string, mode: StrudelMode) => {
    setCopied(false);
    setStrudel({ id, code: null, error: null });
    try {
      const code = await getStrudelCode(id, mode, midiViewRef.current[id] ?? 'lead');
      setStrudel({ id, code, error: null });
    } catch (err) {
      setStrudel({ id, code: null, error: err instanceof Error ? err.message : 'Strudel conversion failed.' });
    }
  }, []);

  const handleStrudel = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await loadStrudel(id, strudelMode);
  }, [loadStrudel, strudelMode]);

  // Re-fetch rather than caching both: the conversion is milliseconds, and
  // holding two versions invites them drifting out of sync with the mode shown.
  const switchStrudelMode = useCallback((mode: StrudelMode) => {
    setStrudelMode(mode);
    if (strudel) loadStrudel(strudel.id, mode);
  }, [strudel, loadStrudel]);

  const handleCopyStrudel = useCallback(async () => {
    if (!strudel?.code) return;
    try {
      await navigator.clipboard.writeText(strudel.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard unavailable — the code is on screen to select manually */ }
  }, [strudel]);

  const handleConvertToMidi = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (midiPollRefs.current[id]) return;   // already converting this one
    setMidiConversions(prev => ({ ...prev, [id]: { state: 'converting', status: 'queued', progress: null, stage: null, queueDepth: null } }));
    try {
      // The server answers with the running job if this song is already
      // converting (another tab, a double tap), so this never starts a second.
      const { id: midiId } = await convertToMidi(id);
      trackMidiJob(id, midiId);
    } catch {
      setMidiConversions(prev => ({ ...prev, [id]: { state: 'failed' } }));
    }
  }, [trackMidiJob]);

  return (
    <div className="container mx-auto p-4 md:p-8 flex-grow">
      {/* Shared hidden audio element */}
      <audio ref={audioRef} preload="metadata" />

      <div className="flex items-center gap-4 mb-8 border-b-4 pb-4" style={{ borderBottomColor: 'var(--accent-tertiary)' }}>
        <Database className="w-10 h-10" style={{ color: 'var(--accent-tertiary)' }} />
        <div>
          <h2 className="text-4xl font-display font-bold uppercase tracking-widest" style={{ color: 'var(--text-heading)' }}>
            SYS_ARCHIVES
          </h2>
          <p className="text-xs font-mono mt-1" style={{ color: 'var(--text-muted)' }}>
            Library · your saved songs
          </p>
        </div>
      </div>

      {loading && (
        <div className="text-center py-16 font-mono uppercase text-sm animate-pulse" style={{ color: 'var(--accent-tertiary)' }}>
          // LOADING ARCHIVE...
        </div>
      )}

      {fetchError && (
        <div className="border-2 rounded-[var(--radius-panel)] p-4 text-sm flex flex-wrap items-center justify-between gap-3" style={{ borderColor: 'var(--color-danger)', color: 'var(--color-danger)' }} role="alert">
          <span className="flex items-start gap-2">
            <AlertTriangle size={16} className="shrink-0 mt-0.5" aria-hidden="true" />
            <span>Couldn't load your library. {fetchError}</span>
          </span>
          <button
            onClick={() => setReloadKey(k => k + 1)}
            className="text-xs font-bold uppercase tracking-widest border px-3 min-h-[40px]"
            style={{ borderColor: 'var(--color-danger)', color: 'var(--color-danger)' }}
          >
            RETRY
          </button>
        </div>
      )}

      {!loading && !fetchError && items.length === 0 && (
        <div className="border-4 border-dashed rounded-[var(--radius-panel)] p-16 text-center" style={{ borderColor: 'var(--border-muted)', color: 'var(--text-muted)' }}>
          <h3 className="text-2xl font-bold uppercase tracking-widest mb-2">[ DATA_VOID ]</h3>
          <p className="monospace text-sm uppercase">{loggedIn ? 'NO SONGS YET. SAVED SONGS SHOW UP HERE.' : 'LOG IN TO SEE YOUR SAVED SONGS.'}</p>
          <Link to={loggedIn ? '/' : '/login'} className="brutal-btn text-xs mt-6 inline-flex items-center min-h-[44px]">
            {loggedIn ? 'MAKE A SONG' : 'LOG IN'}
          </Link>
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6 items-start">
          {items.map((item, index) => {
            const isMidi = item.output_format === 'midi';
            const isThisPlaying = playingId === item.id;
            const view: MidiView = midiView[item.id] ?? 'full';
            const isExpiringSoon = !item.saved &&
              (new Date(item.expires_at).getTime() - Date.now()) < 2 * 3600000;
            const midiJob = isMidi ? undefined : midiConversions[item.id];
            const midiState = midiJob?.state;
            // Bento-grid discipline: rhythm comes from size variation, not
            // uniform cards. The most-recent entry (API sorts by created_at
            // DESC) is the one real "hero" tile — everything else stays a
            // normal tile. See design.md § Macrostructure family.
            const isHero = index === 0;

            return (
              <div
                data-collider
                key={item.id}
                className={`border-2 brutal-card p-0 flex flex-col group ${isHero ? 'md:col-span-2' : ''}`}
                style={{
                  borderColor: item.saved ? 'var(--accent-tertiary)' : 'var(--color-warning)',
                }}
              >
                {/* HEADER */}
                <div
                  className="p-2 flex justify-between items-center"
                  style={{
                    backgroundColor: item.saved ? 'var(--accent-tertiary)' : 'var(--color-warning)',
                    color: 'var(--selected-text)',
                  }}
                >
                  <span className="font-bold text-xs uppercase tracking-widest truncate max-w-[200px] flex items-center gap-1">
                    {isMidi && <FileMusic size={11} />}
                    {isHero && 'MOST RECENT · '}
                    {isMidi ? (item.saved ? 'MIDI · SAVED' : 'MIDI · TEMPORARY') : (item.saved ? 'SAVED' : 'TEMPORARY')}
                  </span>
                  <span className="text-xs font-bold font-mono opacity-80">
                    {item.saved
                      ? formatTimeRemaining(item.expires_at)
                      : (
                        <span className={`inline-flex items-center gap-1 ${isExpiringSoon ? 'animate-pulse' : ''}`}>
                          <AlertTriangle size={10} aria-hidden="true" /> {formatTimeRemaining(item.expires_at)}
                        </span>
                      )
                    }
                  </span>
                </div>

                {/* BODY */}
                <div className="p-4 flex gap-4 items-start" style={{ backgroundColor: 'var(--bg-card)' }}>
                  {/* Cover */}
                  <div
                    className={`border flex-shrink-0 relative overflow-hidden flex items-center justify-center ${isHero ? 'w-32 h-32' : 'w-20 h-20'}`}
                    style={{ borderColor: 'var(--border-muted)', backgroundColor: 'var(--bg)' }}
                  >
                    {!isMidi && item.input_type === 'image' ? (
                      <img
                        src={imageUrl(item.id)}
                        alt="cover"
                        className="w-full h-full object-cover filter grayscale opacity-80 group-hover:opacity-100 group-hover:filter-none transition-all duration-500"
                      />
                    ) : isMidi ? (
                      <FileMusic className={isHero ? 'w-12 h-12 opacity-40' : 'w-8 h-8 opacity-40'} style={{ color: 'var(--text-heading)' }} />
                    ) : (
                      <Music className={isHero ? 'w-12 h-12 opacity-40' : 'w-8 h-8 opacity-40'} style={{ color: 'var(--text-heading)' }} />
                    )}
                  </div>

                  {/* Info */}
                  <div className="flex-grow min-w-0 flex flex-col gap-1">
                    <p
                      className={`font-mono leading-snug ${isHero ? 'text-sm line-clamp-3' : 'text-xs line-clamp-2'}`}
                      style={{ color: 'var(--text-muted)' }}
                      title={item.prompt ?? undefined}
                    >
                      {item.prompt ?? '// TEXT_GENERATION'}
                    </p>
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-xs font-bold font-mono" style={{ color: 'var(--accent)' }}>
                        {isMidi ? 'MIDI' : formatDuration(item.duration)}
                      </span>
                      <span className="text-[11px] uppercase tracking-wider opacity-50" style={{ color: 'var(--text-muted)' }}>
                        {isMidi ? 'BASIC PITCH' : item.input_type.toUpperCase()}
                      </span>
                      {!isMidi && item.fad_verdict === 'satisfactory' && (
                        <Check size={12} style={{ color: 'var(--accent)' }} aria-label="Quality check: satisfactory" />
                      )}
                      {!isMidi && item.fad_verdict === 'unsatisfactory' && (
                        <AlertTriangle size={12} style={{ color: 'var(--color-warning)' }} aria-label="Quality check: unsatisfactory" />
                      )}
                    </div>
                  </div>
                </div>

                {/* MINI PROGRESS BAR — shows for any playing entry (audio or MIDI preview) */}
                {isThisPlaying && (
                  <div
                    className="mx-4 mb-1 h-2 border relative cursor-pointer select-none"
                    style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}
                    onClick={(e) => handleSeek(e, item.id)}
                    title="Seek"
                  >
                    <div
                      ref={el => { progressFillRefs.current[item.id] = el; }}
                      className="absolute top-0 left-0 h-full"
                      style={{ width: '0%', backgroundColor: 'var(--accent)', transition: 'width 0.1s linear' }}
                    />
                  </div>
                )}
                {isThisPlaying && (
                  <div className="flex justify-between px-4 pb-1 text-[11px] font-mono" style={{ color: 'var(--accent)', opacity: 0.65 }}>
                    <span ref={el => { currentTimeTextRefs.current[item.id] = el; }}>{formatTime(0)}</span>
                    <span className="flex items-center gap-1">
                      {isMidi && <span className="opacity-60 tracking-wider">MIDI PREVIEW · SYNTH</span>}
                      {audioDuration > 0 ? formatTime(audioDuration) : '--:--'}
                    </span>
                  </div>
                )}

                {/* MIDI CONVERSION PROGRESS — independent of the format picker, so it
                    still shows after a reload resets the picker to MP3 */}
                {midiJob?.state === 'converting' && (
                  <MidiProgress job={midiJob} />
                )}

                {/* FOOTER ACTIONS */}
                <div
                  className="border-t p-2 flex flex-wrap gap-1 justify-end"
                  style={{ borderColor: 'var(--border-muted)', backgroundColor: 'var(--bg-card)' }}
                >
                  {isMidi ? (
                    /* ── MIDI entry actions ── */
                    <>
                      {/* Play sonified WAV preview */}
                      <button
                        onClick={() => { setSoloTrack(null); handlePlay(item.id, midiPreviewUrl(item.id, null, view)); }}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : (isThisPlaying && isAudioPlaying ? 'Pause MIDI preview' : 'Play all parts together (synth rendering)')}
                        className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ '--btn-c': 'var(--accent)' } as React.CSSProperties}
                      >
                        {isThisPlaying && isAudioPlaying && !soloTrack ? <Pause size={12} /> : <Play size={12} />}
                        {isThisPlaying && isAudioPlaying && !soloTrack ? 'PAUSE' : 'PLAY ALL'}
                      </button>
                      {/* FULL = every transcribed stem. LEAD = the same song cut
                          down to melody / chords / bass / drums. Neither is more
                          accurate than the other by measurement; LEAD is far
                          shorter and meant to be readable. */}
                      <button
                        onClick={() => { setSoloTrack(null); loadTracks(item.id, view === 'full' ? 'lead' : 'full'); }}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required'
                          : view === 'full' ? 'Switch to the lead sheet: melody, chords, bass, drums'
                          : 'Switch back to every transcribed part'}
                        className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ '--btn-c': 'var(--accent-tertiary)' } as React.CSSProperties}
                      >
                        <FileMusic size={12} /> {view === 'full' ? 'FULL' : 'LEAD'}
                      </button>
                      {/* Solo one part. Several instruments at once is right for
                          judging the whole transcription and useless for
                          checking whether one line is correct. */}
                      <button
                        onClick={() => loadTracks(item.id, view)}
                        disabled={!loggedIn || !!midiTracks[item.id]}
                        title={!loggedIn ? 'Login required' : 'List the parts so you can hear them one at a time'}
                        className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ '--btn-c': 'var(--accent-secondary)' } as React.CSSProperties}
                      >
                        <SlidersHorizontal size={12} /> PARTS
                      </button>
                      {/* Download MIDI */}
                      <button
                        onClick={(e) => handleDownloadMidi(item.id, item.prompt, e)}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : 'Download MIDI file'}
                        className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ '--btn-c': 'var(--accent-tertiary)' } as React.CSSProperties}
                      >
                        <Download size={12} /> MIDI
                      </button>
                      {/* Strudel: render this MIDI as live-codeable pattern code */}
                      <button
                        onClick={(e) => handleStrudel(item.id, e)}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : 'Show this MIDI as Strudel pattern code'}
                        className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ '--btn-c': 'var(--accent-secondary)' } as React.CSSProperties}
                      >
                        <Code2 size={12} /> STRUDEL
                      </button>

                      {noLeadSheet[item.id] && (
                        <p className="w-full text-right text-[11px] font-mono pt-1" style={{ color: 'var(--text-muted)' }}>
                          converted before lead sheets existed — showing the full transcription
                        </p>
                      )}

                      {/* One button per part, once PARTS has listed them. */}
                      {midiTracks[item.id]?.length > 0 && (
                        <div className="w-full flex flex-wrap gap-1 justify-end pt-1">
                          {midiTracks[item.id].map(t => {
                            const active = isThisPlaying && soloTrack === t.name;
                            return (
                              <button
                                key={t.name}
                                onClick={() => { setSoloTrack(t.name); handlePlay(item.id, midiPreviewUrl(item.id, t.name, view)); }}
                                title={`Hear only ${t.name} — ${t.notes} notes`}
                                className="lib-icon-btn p-1 px-2 border text-[11px] font-bold uppercase tracking-widest flex items-center gap-1"
                                style={{
                                  '--btn-c': 'var(--accent-tertiary)',
                                  backgroundColor: active ? 'var(--accent-tertiary)' : 'transparent',
                                  color: active ? 'var(--selected-text)' : 'var(--accent-tertiary)',
                                } as React.CSSProperties}
                              >
                                {active && isAudioPlaying ? <Pause size={10} /> : <Play size={10} />}
                                {t.name}
                                <span style={{ opacity: 0.6 }}>{t.notes}</span>
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </>
                  ) : (
                    /* ── Audio entry actions ── */
                    <>
                      {/* Play / Pause */}
                      <button
                        onClick={() => handlePlay(item.id, audioUrl(item.id))}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : (isThisPlaying && isAudioPlaying ? 'Pause' : 'Play')}
                        className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ '--btn-c': 'var(--accent)' } as React.CSSProperties}
                      >
                        {isThisPlaying && isAudioPlaying ? <Pause size={12} /> : <Play size={12} />}
                        {isThisPlaying && isAudioPlaying ? 'PAUSE' : 'PLAY'}
                      </button>

                      {/* Remix: use this song's audio as the reference for a new generation */}
                      <button
                        onClick={(e) => handleRemix(item.id, item.prompt, e)}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : 'Remix this song into a new one'}
                        className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ '--btn-c': 'var(--accent)' } as React.CSSProperties}
                      >
                        <Shuffle size={12} />
                        REMIX
                      </button>

                      {/* Format selector: audio formats then MIDI */}
                      {(() => {
                        const selectedFmt = downloadFormats[item.id] ?? 'mp3';
                        const isMidiSelected = selectedFmt === 'midi';
                        const btnColor = isMidiSelected
                          ? (midiState === 'failed' ? 'red' : 'var(--accent)')
                          : 'var(--accent-tertiary)';
                        return (
                          <>
                            <select
                              value={selectedFmt}
                              onChange={e => {
                                e.stopPropagation();
                                setDownloadFormats(prev => ({ ...prev, [item.id]: e.target.value as DownloadFormat | 'midi' }));
                              }}
                              disabled={!loggedIn}
                              className="border text-xs font-bold font-mono uppercase px-1 py-1 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                              style={{ borderColor: 'var(--accent-tertiary)', color: 'var(--accent-tertiary)', backgroundColor: 'var(--bg)', outline: 'none' }}
                              title={!loggedIn ? 'Login required' : 'Export format'}
                            >
                              {DOWNLOAD_FORMATS.map(f => (
                                <option key={f} value={f}>{f.toUpperCase()}</option>
                              ))}
                              <option disabled>──────</option>
                              <option value="midi">MIDI</option>
                            </select>

                            {/* Single action button: Download (audio) or Convert (MIDI) */}
                            <button
                              onClick={(e) => isMidiSelected ? handleConvertToMidi(item.id, e) : handleDownload(item.id, item.prompt, e)}
                              disabled={!loggedIn || (isMidiSelected && midiState === 'converting')}
                              title={
                                !loggedIn
                                  ? 'Login required'
                                  : isMidiSelected
                                    ? (midiState === 'failed' ? 'MIDI conversion failed — retry' : 'Convert to MIDI')
                                    : `Download ${selectedFmt.toUpperCase()}`
                              }
                              className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 disabled:cursor-not-allowed"
                              style={{
                                '--btn-c': btnColor,
                                opacity: !loggedIn || (isMidiSelected && midiState === 'converting') ? 0.5 : 1,
                              } as React.CSSProperties}
                            >
                              {isMidiSelected ? (midiState === 'failed' ? <X size={12} /> : <FileMusic size={12} />) : <Download size={12} />}
                              {isMidiSelected
                                ? (midiState === 'converting' ? 'CONVERTING…' : midiState === 'failed' ? 'CONVERT FAILED' : 'CONVERT')
                                : 'DOWNLOAD'}
                            </button>
                          </>
                        );
                      })()}
                    </>
                  )}

                  {/* Save (unsaved only) */}
                  {!item.saved && (
                    <button
                      onClick={(e) => handleSave(item.id, e)}
                      disabled={!loggedIn}
                      title={!loggedIn ? 'Login required' : 'Save to library'}
                      className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                      style={{ '--btn-c': 'var(--color-warning)' } as React.CSSProperties}
                    >
                      <Save size={12} /> SAVE
                    </button>
                  )}

                  {/* Purge / Discard */}
                  <button
                    onClick={(e) => handleDiscard(item.id, e)}
                    disabled={!loggedIn}
                    title={!loggedIn ? 'Login required' : 'Delete'}
                    className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                    style={{ '--btn-c': 'red' } as React.CSSProperties}
                  >
                    <Trash2 size={12} /> PURGE
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* STRUDEL CODE PANEL */}
      {strudel && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center p-4"
          style={{ backgroundColor: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(3px)' }}
          onClick={() => setStrudel(null)}
          role="dialog"
          aria-modal="true"
          aria-label="Strudel pattern code"
        >
          {/* Explicit width, not max-w-3xl: design.md's @theme defines
              --spacing-3xl (6rem), and Tailwind v4 builds max-w-* from that
              namespace when no --container-* exists — so max-w-3xl silently
              means 96px here, not 48rem. */}
          <div
            className="border-2 w-full max-w-[56rem] max-h-[85vh] flex flex-col"
            style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}
            onClick={e => e.stopPropagation()}
          >
            <div
              className="shrink-0 flex items-center justify-between gap-2 px-4 py-2 border-b"
              style={{ borderColor: 'var(--border-muted)', backgroundColor: 'var(--bg-card)' }}
            >
              <span className="font-bold text-xs uppercase tracking-widest whitespace-nowrap flex items-center gap-2" style={{ color: 'var(--accent)' }}>
                <Code2 size={13} /> STRUDEL PATTERN
              </span>
              <div className="flex items-center gap-1 ml-auto mr-1">
                {([
                  ['chords', 'Names the harmony once; bass and melody as scale degrees. Shorter and closer to how Strudel is written by hand.'],
                  ['notes',  'Every transcribed pitch, per track. Longer, but shows what the transcription actually found.'],
                ] as [StrudelMode, string][]).map(([m, tip]) => (
                  <button
                    key={m}
                    onClick={() => switchStrudelMode(m)}
                    title={tip}
                    className="px-2 py-0.5 border text-[11px] font-bold uppercase"
                    style={{
                      borderColor: 'var(--accent)',
                      color: strudelMode === m ? 'var(--bg)' : 'var(--accent)',
                      backgroundColor: strudelMode === m ? 'var(--accent)' : 'transparent',
                    }}
                  >
                    {m}
                  </button>
                ))}
              </div>
              <button
                onClick={() => setStrudel(null)}
                title="Close (Esc)"
                className="lib-icon-btn p-1 px-2 border text-xs font-bold uppercase flex items-center gap-1"
                style={{ '--btn-c': 'var(--text-muted)' } as React.CSSProperties}
              >
                <X size={12} />
              </button>
            </div>

            <div className="overflow-auto flex-grow p-4">
              {!strudel.code && !strudel.error && (
                <p className="text-xs font-mono animate-pulse" style={{ color: 'var(--text-muted)' }}>
                  CONVERTING MIDI TO STRUDEL…
                </p>
              )}
              {strudel.error && (
                <p className="text-xs font-mono flex items-start gap-2" style={{ color: 'var(--color-danger)' }}>
                  <AlertTriangle size={14} className="shrink-0 mt-[1px]" /> {strudel.error}
                </p>
              )}
              {strudel.code && (
                <pre
                  className="text-[11px] font-mono leading-relaxed whitespace-pre"
                  style={{ color: 'var(--text-primary)' }}
                >
                  {strudel.code}
                </pre>
              )}
            </div>

            {strudel.code && (
              <div
                className="shrink-0 flex flex-wrap items-center justify-end gap-2 px-4 py-2 border-t"
                style={{ borderColor: 'var(--border-muted)', backgroundColor: 'var(--bg-card)' }}
              >
                <span className="text-[11px] font-mono mr-auto opacity-60 whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                  QUANTIZED TO 1/16 — EDIT FREELY
                </span>
                <a
                  href="https://strudel.cc"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1"
                  style={{ '--btn-c': 'var(--accent-tertiary)' } as React.CSSProperties}
                >
                  <ExternalLink size={12} /> OPEN STRUDEL
                </a>
                <button
                  onClick={handleCopyStrudel}
                  className="lib-icon-btn p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1"
                  style={{ '--btn-c': 'var(--accent)' } as React.CSSProperties}
                >
                  {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? 'COPIED' : 'COPY'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
