import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Play, Pause } from 'lucide-react';

// Compact player for the reference track in the source panel.
//
// Replaces a bare <audio controls>, whose native chrome ignores the app's
// palette and looks pasted in. Self-contained: it owns the one <audio> element
// it drives, so the source panel stays presentational from the caller's side.
export function AudioPreview({ src }: { src: string }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const fillRef  = useRef<HTMLDivElement>(null);
  const timeRef  = useRef<HTMLSpanElement>(null);
  const [playing, setPlaying]   = useState(false);
  const [duration, setDuration] = useState(0);

  const fmt = (s: number) =>
    `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

  // Playhead goes straight to the DOM rather than through state: a 4Hz
  // re-render of this card would re-render the source image with it.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onTime = () => {
      const d = audio.duration;
      const pct = isFinite(d) && d > 0 ? (audio.currentTime / d) * 100 : 0;
      if (fillRef.current) fillRef.current.style.width = `${pct}%`;
      if (timeRef.current) timeRef.current.textContent = fmt(audio.currentTime);
    };
    const onDur   = () => setDuration(isFinite(audio.duration) ? audio.duration : 0);
    const onPlay  = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnd   = () => { setPlaying(false); onTime(); };
    audio.addEventListener('timeupdate', onTime);
    audio.addEventListener('loadedmetadata', onDur);
    audio.addEventListener('durationchange', onDur);
    audio.addEventListener('play', onPlay);
    audio.addEventListener('pause', onPause);
    audio.addEventListener('ended', onEnd);
    return () => {
      audio.removeEventListener('timeupdate', onTime);
      audio.removeEventListener('loadedmetadata', onDur);
      audio.removeEventListener('durationchange', onDur);
      audio.removeEventListener('play', onPlay);
      audio.removeEventListener('pause', onPause);
      audio.removeEventListener('ended', onEnd);
    };
  }, []);

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  }, []);

  const seek = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const audio = audioRef.current;
    if (!audio || !isFinite(audio.duration) || audio.duration <= 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    audio.currentTime =
      Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * audio.duration;
  }, []);

  return (
    <div className="w-full flex items-center gap-2 mt-2">
      <audio ref={audioRef} src={src} preload="metadata" />
      <button
        onClick={toggle}
        aria-label={playing ? 'Pause reference track' : 'Play reference track'}
        className="shrink-0 w-8 h-8 border rounded-[var(--radius-chip)] flex items-center justify-center transition-colors"
        style={{ borderColor: 'var(--accent-tertiary)', color: 'var(--accent-tertiary)' }}
      >
        {playing ? <Pause size={14} /> : <Play size={14} className="ml-0.5" />}
      </button>

      <div
        onClick={seek}
        title="Click to seek"
        className="flex-grow h-2 border relative cursor-pointer rounded-[var(--radius-pill)] overflow-hidden"
        style={{ borderColor: 'var(--accent-tertiary)' }}
      >
        <div
          ref={fillRef}
          className="absolute top-0 left-0 h-full"
          style={{ width: '0%', backgroundColor: 'var(--accent-tertiary)' }}
        />
      </div>

      <span
        className="shrink-0 text-[11px] font-mono tabular-nums normal-case"
        style={{ color: 'var(--text-muted)' }}
      >
        <span ref={timeRef}>00:00</span>/{duration > 0 ? fmt(duration) : '--:--'}
      </span>
    </div>
  );
}
