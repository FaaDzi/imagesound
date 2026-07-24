# Design — ImageSound

A locked design system for this app. Every page redesign reads this file before
emitting code. Do not regenerate per page — extend or amend this file when the
system needs to grow.

This system is a **custom (tuned) theme**, not a catalog pick — anchored on the
app's own established identity (Syne + Geist Mono, phosphor-green accent on
near-black, hard-bordered brutalist panels) rather than swapped for one of the
20 named catalog themes. Pre-flight found a genuine, consistent identity in
use everywhere; the job here is to fix hierarchy and structure around it, not
replace it.

## Genre
Atmospheric (dark-mode generative AI tool). One deliberate deviation from the
genre's own defaults: atmospheric's baseline voice is soft blur/glow/warm-bloom
(N5 floating-pill nav, elevated cards over hairlines). ImageSound's existing
hard-edged CRT-terminal identity is kept instead — it is itself a strong
anti-slop signal (about as far from "generic AI atmospheric glow" as a dark
tool can get), and erasing it to chase genre-default softness would trade a
real point of view for a default. The theme is closest to Hallmark's own
**Terminal** catalog theme (mono-everywhere, phosphor-green accent) but tuned
to keep the existing Syne display face rather than going fully monospace.

## Macrostructure family
No marketing pages exist in this app — all three routes are functional tool
screens. Macrostructures are used here as *rhythm and hierarchy references*,
adapted for real live content instead of literal marketing sections (no fake
screenshots, no invented copy).

- **App pages** (Home, Player): Workbench discipline — small, functional
  headings that don't shout; the real interactive content (upload target,
  generation panels) IS the primary visual, not a screenshot of it; a
  restrained functional CTA, not a sales CTA.
- **Content pages** (Library): Bento Grid discipline — modular tiles of
  *varying* size instead of uniform equal cards; rhythm comes from size
  variation (most-recent / longer entries get more visual weight).

## Theme
Existing CSS custom-property names are preserved (`--bg`, `--bg-card`,
`--accent`, `--accent-secondary`, `--accent-tertiary`, `--border`,
`--text-primary`, `--text-muted`, `--text-heading`, `--selected-bg`,
`--selected-text`) — only their **values** and **roles** change, so nothing
downstream breaks. New tokens are added, not substituted in as replacements.

- `--bg` — was flat `#0a0a0a` (zero chroma — the banned "flat grey"). Now
  tinted toward the accent hue: `oklch(9% 0.012 145)`.
- `--bg-card` — `oklch(13% 0.014 145)` (was flat `#111111`).
- `--bg-elevated` *(new)* — `oklch(16% 0.016 145)` — a third elevation step;
  elevation reads as *lighter*, not shadowed, per dark-mode discipline.
- `--accent` — **kept exactly as-is**, `#39ff14`. This is the one dominant
  accent; everything else defers to it.
- `--accent-secondary` — dimmed from full-brightness `#ff2d78` to
  `oklch(58% 0.15 350)`. Reassigned a strict semantic job: warning / unsaved /
  destructive-adjacent only — never used as a co-equal decorative accent.
- `--accent-tertiary` — dimmed from full-brightness `#0ff` to
  `oklch(62% 0.11 200)`. Reassigned: informational / Library context only.
- `--color-warning` *(new)* — `oklch(80% 0.15 85)` amber, replacing the
  hardcoded `#facc15` inline in `Player.tsx`.
- `--color-danger` — red, kept for destructive actions (PURGE), unchanged.
- Light theme mirrors the same relationships; `--bg: #ffffff` (pure white,
  banned as a base surface) becomes a fractionally tinted `oklch(98% 0.006 145)`.

## Typography
Unchanged — Syne (display, 700/800, uppercase) + Geist Mono (body/UI). Two
families, which is canonical under the 2+1 rule. No font changes; the pairing
already avoids "Inter-everywhere" and reads as intentional.

## Spacing
New named 4pt scale added to the Tailwind `@theme` block (generates
`p-3xs…p-4xl` etc. utilities alongside the existing ad-hoc values):

```
--spacing-3xs: 0.125rem;  --spacing-2xs: 0.25rem;  --spacing-xs: 0.5rem;
--spacing-sm:  0.75rem;   --spacing-md:  1rem;     --spacing-lg: 1.5rem;
--spacing-xl:  2.5rem;    --spacing-2xl: 4rem;     --spacing-3xl: 6rem;
```

Used to give the redesigned sections *varied* rhythm (the audit's "every
section padded the same" finding) — not a mandate to rewrite every existing
padding value in the app; scope is the files this pass touches.

## Motion
- Easings: `--ease-out: cubic-bezier(0.16,1,0.3,1)`, `--ease-in:
  cubic-bezier(0.7,0,0.84,0)`, `--ease-in-out: cubic-bezier(0.65,0,0.35,1)`.
- Durations: `--dur-micro: 120ms`, `--dur-short: 220ms`, `--dur-long: 420ms`.
- **One signature motif**: a hand-built CSS/canvas waveform-to-spectrogram
  visual (Tier-A enrichment). Appears twice — as Home's hero visual (idle
  ambient animation, ties directly to what the product does) and as the
  one-shot reveal when a generation completes in the Player (replaces the
  current instant-appear + `animate-pulse` treatment with one deliberate
  moment). Not used anywhere else — two slots, same rule as the typographic
  outlier discipline.
- `prefers-reduced-motion: reduce` collapses all of the above to a ≤150ms
  opacity crossfade; the waveform's idle animation pauses entirely (it's
  ambient, not functional, so it's safe to just stop).
- `StressBall.tsx`'s physics toy is explicitly out of scope — untouched.

## Microinteractions stance
- Silent success — no "Saved!" toast when the result is already visible
  on-screen (Library's SAVE button already does this; keep it).
- Optimistic delete + no confirmation modal for PURGE (already the case;
  keep it — it's the correct pattern, not a gap).
- Hover tooltip delay 800ms, focus delay 0ms, where tooltips exist.
- Focus rings appear instantly, never animated in.

## CTA voice
Unchanged. `brutal-btn` (hard 2px border, offset hard shadow, translate-on-press,
zero border-radius, uppercase) is already distinctive and not a generic
pattern — no rounded pills, no gradient fills. Not touching it.

## Nav
**N8 Terminal command** (was: an N1a-shaped nav — wordmark hard-left, inline
links, sticky, border-bottom — flagged in the audit as genre-blind). Routes
become CLI flags: `> imagesound --upload --studio --library▮`, active route
highlighted via `--accent`, blinking caret only here (its one legitimate use
per Hallmark's own nav catalogue).

## Footer
**Ft2 Inline single line** — a thin one-line status strip (hairline rule
above, matching the existing hard-border language), not a marketing footer.
No column layout.

## Per-page allowances
- Home MAY use the Tier-A waveform enrichment (it's the signature element).
- Player MAY reuse the same waveform motif, but only as the completion-reveal
  motion — not as decoration elsewhere on the page.
- Library MUST NOT use enrichment — the content (the archive) carries the page.

## What pages MUST share
- The wordmark (IMAGESOUND, Syne, glitch effect — unchanged).
- The accent hierarchy above (one dominant accent, two dimmed semantic accents).
- The display + body fonts.
- The CTA voice (`brutal-btn`/`brutal-card` family, unchanged).
- The nav and footer (shared shell components, not per-page).

## What pages MAY differ on
- Macrostructure emphasis within their family (Home leans hero-first,
  Player leans panel-first, both still "app page" family).
- Content layout specifics (Library's tile spans vs. Player's 3-column panels).

## Exports

### tokens.css
```css
:root {
  --color-bg:            oklch(9%  0.012 145);
  --color-bg-card:       oklch(13% 0.014 145);
  --color-bg-elevated:   oklch(16% 0.016 145);
  --color-accent:        #39ff14;
  --color-accent-warn:   oklch(58% 0.15 350);
  --color-accent-info:   oklch(62% 0.11 200);
  --color-warning:       oklch(80% 0.15 85);
  --color-danger:        oklch(58% 0.22 25);
  --color-ink:           oklch(97% 0.006 145);
  --color-ink-muted:     oklch(58% 0.01  145);

  --font-display: "Syne", sans-serif;
  --font-body:    "Geist Mono", monospace;

  --spacing-3xs: 0.125rem; --spacing-2xs: 0.25rem; --spacing-xs: 0.5rem;
  --spacing-sm:  0.75rem;  --spacing-md:  1rem;    --spacing-lg: 1.5rem;
  --spacing-xl:  2.5rem;   --spacing-2xl: 4rem;    --spacing-3xl: 6rem;

  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in:  cubic-bezier(0.7, 0, 0.84, 0);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
  --dur-micro: 120ms; --dur-short: 220ms; --dur-long: 420ms;

  --radius: 0px; /* hard-edged, intentional — not a gap */
}
```

Tailwind v4 `@theme`, DTCG `tokens.json`, and shadcn/ui variable exports are
skipped for now (single-app project, not a multi-consumer design system) —
add on request via `export-formats.md`.
