import React from 'react';

// The one-line explanations under controls.
//
// These used to be --accent-secondary at 0.4-0.55 opacity, which is close to
// unreadable in both themes: --accent-secondary is #38bdf8 on a #e0f2fe page in
// light mode and #0ea5e9 on #092135 in dark, so it is a low-contrast pairing
// before the opacity is applied at all. They now use --text-primary on
// --bg-elevated, which is the pairing the palette actually intends for body
// text, at full opacity — near-white on dark navy, near-navy on white.
//
// Sentence case rather than the uppercase used elsewhere: these are sentences,
// and uppercase mono at 10px is the hardest combination to read of the lot.
//
// Set loose rather than snug: mono at 11px in a narrow column wraps to three or
// four lines, and tight leading on a wrapped mono paragraph is what makes it
// read as a block rather than as lines. The extra height is wanted here too --
// it is what brings the options panel up to the height of the column opposite.
//
// `more` is the detail behind a "more" toggle: the first sentence says what a
// setting does, the rest (tuning guides, test history) was making the options
// panel a wall of text -- several screens of it on a phone.
export function Hint({ children, more }: { children: React.ReactNode; more?: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  return (
    <p
      className="text-[11px] font-mono leading-relaxed mt-2.5 py-2 px-2.5 border-l-2"
      style={{
        color:           'var(--text-primary)',
        backgroundColor: 'var(--bg-elevated)',
        borderLeftColor: 'var(--accent)',
        borderRadius:    'var(--radius-chip)',
      }}
    >
      {children}
      {more && open && <> {more}</>}
      {more && (
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          aria-expanded={open}
          className="ml-1.5 underline underline-offset-2 whitespace-nowrap"
          style={{ color: 'var(--accent)' }}
        >
          {open ? 'less' : 'more'}
        </button>
      )}
    </p>
  );
}

/** "First sentence. The rest." -> ["First sentence.", "The rest."]; the rest
 *  is "" when there is only one sentence. */
export function splitFirstSentence(text: string): [string, string] {
  const m = text.match(/^(.+?[.!?])\s+(\S[\s\S]*)$/);
  return m ? [m[1], m[2]] : [text, ''];
}

/** Small secondary text that sits inline (values, counts, status). */
export function Muted({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={`text-[11px] font-mono ${className}`} style={{ color: 'var(--text-muted)' }}>
      {children}
    </span>
  );
}
