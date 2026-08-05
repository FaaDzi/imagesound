import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Trash2, Music, Database, Play, Pause, Download, Save, FileMusic, Shuffle, Check, AlertTriangle } from 'lucide-react';
import { getLibrary, discardJob, saveJob, audioUrl, imageUrl, downloadSong, convertToMidi, downloadMidi, midiPreviewUrl, LibraryItem, DOWNLOAD_FORMATS, DownloadFormat } from '../api';
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

export function Library() {
  const navigate = useNavigate();
  const { username } = useAuth();
  const loggedIn = !!username;
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [downloadFormats, setDownloadFormats] = useState<Record<string, DownloadFormat | 'midi'>>({});

  // Tracks in-progress MIDI conversions keyed by source audio ID.
  const [midiConversions, setMidiConversions] = useState<Record<string, 'converting' | 'failed'>>({});
  const midiPollRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({});

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

  useEffect(() => {
    getLibrary()
      .then(data => setItems(data))
      .catch(err => setFetchError(err instanceof Error ? err.message : 'Failed to load library.'))
      .finally(() => setLoading(false));
  }, []);

  // Clean up MIDI polling intervals on unmount.
  useEffect(() => {
    return () => { Object.values(midiPollRefs.current).forEach(clearInterval); };
  }, []);

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

  const handlePlay = useCallback((id: string, url: string) => {
    const audio = audioRef.current;
    if (!audio) return;
    if (playingId === id) {
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

  const handleConvertToMidi = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setMidiConversions(prev => ({ ...prev, [id]: 'converting' }));
    try {
      await convertToMidi(id);
      // Poll the library every 3s until the MIDI entry appears (job_status='done').
      const iv = setInterval(async () => {
        try {
          const updated = await getLibrary();
          const midiDone = updated.some(item => item.source_file_id === id && item.output_format === 'midi');
          if (midiDone) {
            clearInterval(midiPollRefs.current[id]);
            delete midiPollRefs.current[id];
            setItems(updated);
            setMidiConversions(prev => { const n = { ...prev }; delete n[id]; return n; });
          }
        } catch { /* transient — keep polling */ }
      }, 3000);
      midiPollRefs.current[id] = iv;
      // Safety: give up after 3 minutes.
      setTimeout(() => {
        if (midiPollRefs.current[id]) {
          clearInterval(midiPollRefs.current[id]);
          delete midiPollRefs.current[id];
          setMidiConversions(prev => ({ ...prev, [id]: 'failed' }));
        }
      }, 180_000);
    } catch {
      setMidiConversions(prev => ({ ...prev, [id]: 'failed' }));
    }
  }, []);

  return (
    <div className="container mx-auto p-4 md:p-8 flex-grow">
      {/* Shared hidden audio element */}
      <audio ref={audioRef} preload="metadata" />

      <div className="flex items-center gap-4 mb-8 border-b-4 pb-4" style={{ borderBottomColor: 'var(--accent-tertiary)' }}>
        <Database className="w-10 h-10" style={{ color: 'var(--accent-tertiary)' }} />
        <h2 className="text-4xl font-display font-bold uppercase tracking-widest" style={{ color: 'var(--text-heading)' }}>
          SYS_ARCHIVES
        </h2>
      </div>

      {loading && (
        <div className="text-center py-16 font-mono uppercase text-sm animate-pulse" style={{ color: 'var(--accent-tertiary)' }}>
          // LOADING ARCHIVE...
        </div>
      )}

      {fetchError && (
        <div className="border-2 rounded-[var(--radius-panel)] p-4 text-sm" style={{ borderColor: 'var(--color-danger)', color: 'var(--color-danger)' }}>
          ERROR: {fetchError}
        </div>
      )}

      {!loading && !fetchError && items.length === 0 && (
        <div className="border-4 border-dashed rounded-[var(--radius-panel)] p-16 text-center" style={{ borderColor: 'var(--border-muted)', color: 'var(--text-muted)' }}>
          <h3 className="text-2xl font-bold uppercase tracking-widest mb-2">[ DATA_VOID ]</h3>
          <p className="monospace text-sm uppercase">NO FILES FOUND IN ARCHIVE REGISTRY.</p>
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6 items-start">
          {items.map((item, index) => {
            const isMidi = item.output_format === 'midi';
            const isThisPlaying = playingId === item.id;
            const isExpiringSoon = !item.saved &&
              (new Date(item.expires_at).getTime() - Date.now()) < 2 * 3600000;
            const midiState = isMidi ? undefined : midiConversions[item.id];
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
                  boxShadow: item.saved
                    ? '-6px 6px 0 0 var(--accent-tertiary)'
                    : '-6px 6px 0 0 var(--color-warning)',
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
                        <span className={isExpiringSoon ? 'animate-pulse' : ''}>
                          ⚠ {formatTimeRemaining(item.expires_at)}
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
                      <span className="text-[10px] uppercase tracking-wider opacity-50" style={{ color: 'var(--text-muted)' }}>
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
                  <div className="flex justify-between px-4 pb-1 text-[10px] font-mono" style={{ color: 'var(--accent)', opacity: 0.65 }}>
                    <span ref={el => { currentTimeTextRefs.current[item.id] = el; }}>{formatTime(0)}</span>
                    <span className="flex items-center gap-1">
                      {isMidi && <span className="opacity-60 tracking-wider">MIDI PREVIEW · SYNTH</span>}
                      {audioDuration > 0 ? formatTime(audioDuration) : '--:--'}
                    </span>
                  </div>
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
                        onClick={() => handlePlay(item.id, midiPreviewUrl(item.id))}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : (isThisPlaying && isAudioPlaying ? 'Pause MIDI preview' : 'Play MIDI preview (synth rendering)')}
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                        onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--bg)'; } }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent)'; }}
                      >
                        {isThisPlaying && isAudioPlaying ? <Pause size={12} /> : <Play size={12} />}
                        {isThisPlaying && isAudioPlaying ? 'PAUSE' : 'PLAY'}
                      </button>
                      {/* Download MIDI */}
                      <button
                        onClick={(e) => handleDownloadMidi(item.id, item.prompt, e)}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : 'Download MIDI file'}
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ borderColor: 'var(--accent-tertiary)', color: 'var(--accent-tertiary)' }}
                        onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'var(--accent-tertiary)'; e.currentTarget.style.color = 'var(--bg)'; } }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent-tertiary)'; }}
                      >
                        <Download size={12} /> MIDI
                      </button>
                      {/* Strudel — placeholder until Step 2 */}
                      <button
                        disabled
                        title="Strudel export — coming soon"
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest flex items-center gap-1 opacity-30 cursor-not-allowed"
                        style={{ borderColor: 'var(--text-muted)', color: 'var(--text-muted)' }}
                      >
                        STRUDEL
                      </button>
                    </>
                  ) : (
                    /* ── Audio entry actions ── */
                    <>
                      {/* Play / Pause */}
                      <button
                        onClick={() => handlePlay(item.id, audioUrl(item.id))}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : (isThisPlaying && isAudioPlaying ? 'Pause' : 'Play')}
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                        onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--bg)'; } }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent)'; }}
                      >
                        {isThisPlaying && isAudioPlaying ? <Pause size={12} /> : <Play size={12} />}
                        {isThisPlaying && isAudioPlaying ? 'PAUSE' : 'PLAY'}
                      </button>

                      {/* Remix: use this song's audio as a melody reference for a new generation */}
                      <button
                        onClick={(e) => handleRemix(item.id, item.prompt, e)}
                        disabled={!loggedIn}
                        title={!loggedIn ? 'Login required' : 'Use as melody reference for a new song'}
                        className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                        onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--bg)'; } }}
                        onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent)'; }}
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
                              className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1"
                              style={{
                                borderColor: btnColor,
                                color: btnColor,
                                opacity: !loggedIn || (isMidiSelected && midiState === 'converting') ? 0.5 : 1,
                                cursor: !loggedIn || (isMidiSelected && midiState === 'converting') ? 'not-allowed' : 'pointer',
                              }}
                              onMouseEnter={e => {
                                if (loggedIn && !(isMidiSelected && midiState === 'converting')) {
                                  e.currentTarget.style.backgroundColor = btnColor;
                                  e.currentTarget.style.color = 'var(--bg)';
                                }
                              }}
                              onMouseLeave={e => {
                                e.currentTarget.style.backgroundColor = 'transparent';
                                e.currentTarget.style.color = btnColor;
                              }}
                            >
                              {isMidiSelected ? <FileMusic size={12} /> : <Download size={12} />}
                              {isMidiSelected
                                ? (midiState === 'converting' ? 'CONVERTING…' : midiState === 'failed' ? 'CONVERT ✗' : 'CONVERT')
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
                      className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                      style={{ borderColor: 'var(--color-warning)', color: 'var(--color-warning)' }}
                      onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'var(--color-warning)'; e.currentTarget.style.color = 'var(--bg)'; } }}
                      onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--color-warning)'; }}
                    >
                      <Save size={12} /> SAVE
                    </button>
                  )}

                  {/* Purge / Discard */}
                  <button
                    onClick={(e) => handleDiscard(item.id, e)}
                    disabled={!loggedIn}
                    title={!loggedIn ? 'Login required' : 'Delete'}
                    className="p-1 px-3 border text-xs font-bold uppercase tracking-widest transition-colors flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed"
                    style={{ borderColor: 'red', color: 'red' }}
                    onMouseEnter={e => { if (loggedIn) { e.currentTarget.style.backgroundColor = 'red'; e.currentTarget.style.color = 'var(--bg)'; } }}
                    onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'red'; }}
                  >
                    <Trash2 size={12} /> PURGE
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
