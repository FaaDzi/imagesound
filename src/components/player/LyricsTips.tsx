import React, { useEffect, useRef, useState } from 'react';
import { CircleHelp, X } from 'lucide-react';

// The "?" next to the Lyrics label: a short how-to for the box, not the full
// picture. Anchored under the button and capped to the viewport width so it
// never runs off a phone screen; closes on Escape or a click outside.
const TIPS: [string, string][] = [
  ['[Verse] [Chorus] …', 'Song parts. Write the words under each one, one short line per row.'],
  ['///', 'On its own line under a part: no singing there, the music plays alone.'],
  ['+ buttons', 'Add a part where your cursor is.'],
  ['Pre-Chorus', 'Builds up, so the Chorus hits harder.'],
  ['Build → Drop', 'For EDM: sing the Build, put /// under the Drop.'],
  ['SURPRISE ME', 'Writes lyrics into your parts. Leave the starting layout as it is and it picks a shape for the genre.'],
  ['Keep it simple', 'Short lines, one language. Too many lines for the length and the singer rushes.'],
];

export function LyricsTips() {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    const onDown = (e: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('pointerdown', onDown);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('pointerdown', onDown);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative inline-flex">
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-controls="lyrics-tips"
        aria-label="How the lyrics box works"
        title="How the lyrics box works"
        className="inline-flex items-center justify-center p-1 -m-1"
        style={{ color: open ? 'var(--accent)' : 'var(--accent-tertiary)' }}
      >
        <CircleHelp size={14} aria-hidden="true" />
      </button>
      {open && (
        <div
          id="lyrics-tips"
          role="dialog"
          aria-label="Lyrics tips"
          className="absolute left-0 top-full mt-2 z-50 border-2 p-3 flex flex-col gap-2 w-[min(22rem,calc(100vw-2.5rem))]"
          style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}
        >
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>
              Lyrics tips
            </span>
            <button onClick={() => setOpen(false)} aria-label="Close tips" className="p-1 -m-1" style={{ color: 'var(--text-muted)' }}>
              <X size={12} aria-hidden="true" />
            </button>
          </div>
          <dl className="flex flex-col gap-1.5 text-[11px] font-mono leading-relaxed normal-case tracking-normal font-normal">
            {TIPS.map(([term, text]) => (
              <div key={term}>
                <dt className="inline font-bold" style={{ color: 'var(--accent-tertiary)' }}>{term}</dt>
                <dd className="inline" style={{ color: 'var(--text-primary)' }}> — {text}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}
