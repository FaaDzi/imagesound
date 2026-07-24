import { useState } from 'react';

const CELL_COUNT = 48;
const BAR_COUNT = 28;

interface WaveformProps {
  /** ambient: idle looping animation for the Home hero.
   *  reveal: one-shot settle animation, played once when a generation finishes. */
  variant?: 'ambient' | 'reveal';
  className?: string;
}

/**
 * The app's one signature visual — a static "spectrogram" strip (frozen
 * image data) resolving into a live animated waveform (the sound it becomes).
 * Hand-built CSS, no libraries. Used in exactly two places: Home's hero and
 * the Player's generation-complete reveal — see design.md § Motion.
 */
export function Waveform({ variant = 'ambient', className = '' }: WaveformProps) {
  const [cellOpacities] = useState(() =>
    Array.from({ length: CELL_COUNT }, () => 0.12 + Math.random() * 0.68)
  );
  const [barHeights] = useState(() =>
    Array.from({ length: BAR_COUNT }, () => 0.22 + Math.random() * 0.72)
  );

  return (
    <div className={`waveform waveform--${variant} ${className}`} aria-hidden="true">
      <div className="waveform__spectrogram">
        {cellOpacities.map((o, i) => (
          <span key={i} style={{ opacity: o }} />
        ))}
      </div>
      <div className="waveform__bars">
        {barHeights.map((h, i) => (
          <span
            key={i}
            style={{
              '--h': h,
              '--dur': `${900 + (i % 7) * 140}ms`,
              animationDelay: variant === 'reveal' ? `${i * 16}ms` : `${(i % 9) * 110}ms`,
            } as React.CSSProperties}
          />
        ))}
      </div>
    </div>
  );
}
