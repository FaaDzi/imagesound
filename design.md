# Design — ImageSound

A locked design system for this app. Every page redesign reads this file before
emitting code. Do not regenerate per page — extend or amend this file when the
system needs to grow.

This system is a **Y2K / Aqua-Chrome** material language — early Mac OS X-era
glossy glass/chrome (cool blue-grey, pillowy pill buttons with a bevel
highlight) — replacing the app's previous hard-edged CRT-terminal identity
(phosphor green on near-black, hard borders, offset shadows, zero radius).
The wordmark, route set, and copy voice carry over untouched; the material
underneath them changed completely.

## Genre

Operate mode (task-completion tool: upload → generate → save), not a
marketing site. Brand lives in precise component-level detail — button
bevels, glow treatment, corner-radius consistency, the one ambient motif —
not in landing-page-style hero rhetoric. One deliberate signature visual
moment survives from the old system's discipline (the waveform/spectrogram
motif, unchanged in role, see Motion below); everything around it is now
soft-glass instead of hard-terminal.

## Theme

Single anchor hue, **230° (cool blue)**, shared by both light and dark mode —
the hue never switches between modes, only lightness/chroma move (dark mode
gets more chroma plus glow; light mode stays airy). All values are OKLCH,
defined once in `src/index.css` under `:root, body.dark` (dark is the
default/root state) with `body.light` as the override block. The active mode
is applied by `document.body.className = 'dark' | 'light'` in `App.tsx`
(persisted to `localStorage`), not a `data-theme` attribute.

**Dark (`:root, body.dark`):**
```css
--bg:               oklch(13% 0.014 230);
--bg-card:          oklch(17% 0.016 230);
--bg-elevated:      oklch(21% 0.018 230);
--input-bg:         oklch(13% 0.014 230);
--text-primary:     oklch(94% 0.008 230);
--text-muted:       oklch(68% 0.010 230);
--text-heading:     oklch(94% 0.008 230);
--accent:           oklch(55% 0.19 230);
--accent-glow:      oklch(55% 0.19 230 / 0.5);
--accent-secondary: oklch(74% 0.09 230);
--accent-tertiary:  oklch(60% 0.07 230);
--color-warning:    oklch(72% 0.15 70);
--color-danger:     oklch(62% 0.20 25);
--border:           oklch(30% 0.012 230);
--border-muted:     oklch(30% 0.012 230);
--selected-bg:      oklch(55% 0.19 230);
--selected-text:    oklch(97% 0.006 230);
```

**Light (`body.light`):**
```css
--bg:               oklch(97% 0.008 230);
--bg-card:          oklch(94% 0.010 230);
--bg-elevated:      oklch(99% 0.006 230);
--input-bg:         oklch(99% 0.006 230);
--text-primary:     oklch(20% 0.012 230);
--text-muted:       oklch(45% 0.010 230);
--text-heading:     oklch(20% 0.012 230);
--accent:           oklch(58% 0.17 230);
--accent-glow:      oklch(58% 0.17 230 / 0.35);
--accent-secondary: oklch(42% 0.10 230);
--accent-tertiary:  oklch(52% 0.07 230);
--color-warning:    oklch(70% 0.16 70);
--color-danger:     oklch(58% 0.20 25);
--border:           oklch(85% 0.008 230);
--border-muted:     oklch(85% 0.008 230);
--selected-bg:      oklch(58% 0.17 230);
--selected-text:    oklch(99% 0.004 230);
```

Existing custom-property names are preserved from the old system (`--bg`,
`--bg-card`, `--accent`, `--accent-secondary`, `--accent-tertiary`,
`--border`, `--text-primary`, `--text-muted`, `--text-heading`,
`--selected-bg`, `--selected-text`) — only values and roles changed, plus new
additions (`--bg-elevated`, `--input-bg`, `--accent-glow`, `--border-muted`,
`--color-warning`, `--color-danger`). Elevation reads as *lighter*
(`--bg` → `--bg-card` → `--bg-elevated`), never shadowed.

Semantic roles, reassigned from the old flat-neon accents:
- `--accent` — the one dominant accent (buttons, active nav pill, focus
  glow, links).
- `--accent-secondary` — warning/unsaved/destructive-adjacent context (e.g.
  GeneratePanel's unsaved-item border/label).
- `--accent-tertiary` — informational / Library-context accent (Library's
  header icon, saved-state border, info actions).
- `--color-warning` — amber semantic warning (quality-check flags, unsaved
  badges).
- `--color-danger` — semantic destructive/error red (delete actions, error
  panels).

`--theme-grid-opacity: 0` is a retired token (the old cyberpunk grid overlay)
kept at zero rather than deleted, so nothing that still references it breaks.

Radius tokens (also defined in the `:root, body.dark` block — one consistent
three-step scale, no ad-hoc radius values):
```css
--radius-pill:  999px;  /* buttons, chips, pills, nav items */
--radius-panel: 14px;   /* cards, panels, containers, modals */
--radius-chip:  9px;    /* inputs, small tags, inline icon buttons */
```
There is no generic zero/hard-edge radius token anymore — the old system's
`--radius: 0px` is fully retired.

## Typography

New pairing, defined via the Google Fonts `@import` at the top of
`src/index.css`:
```css
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=Geist:wght@100..900&display=swap');
```
- **`--font-display: 'Syne', sans-serif;`** — kept from the old system for
  headings (700/800, uppercase). It's a distinctive geometric face, not
  inherently brutalist, so it survives the material change.
- **`--font-ui: 'Geist', sans-serif;`** — the new general body/UI voice,
  replacing Geist Mono in that role. `body`'s `font-family` is
  `var(--font-ui)`. Clean humanist-sans, pairs with Syne's geometric display
  voice.
- **`--font-mono: 'Geist Mono', monospace;`** — kept, but demoted to
  "genuinely tabular content only" (uppercase tracking-widest labels,
  numeric/status readouts, code-like strings), not the general UI voice
  anymore. Note: only Syne and Geist are actually fetched by the `@import`
  above — `'Geist Mono'` itself is not loaded, so `font-mono` usage falls
  back to the browser's generic monospace unless Geist Mono happens to be
  installed locally.
- Two families loaded (Syne + Geist) plus the monospace fallback exception —
  matches the 2+1 rule the old system also followed.

## Spacing

Named 4pt scale in the Tailwind `@theme` block (generates `p-3xs…p-3xl`,
`gap-3xs…gap-3xl`, etc. utilities), unchanged from the prior pass:
```css
--spacing-3xs: 0.125rem;  --spacing-2xs: 0.25rem;  --spacing-xs: 0.5rem;
--spacing-sm:  0.75rem;   --spacing-md:  1rem;     --spacing-lg: 1.5rem;
--spacing-xl:  2.5rem;    --spacing-2xl: 4rem;     --spacing-3xl: 6rem;
```

## Motion

- Easings: `--ease-out: cubic-bezier(0.16, 1, 0.3, 1)`, `--ease-in:
  cubic-bezier(0.7, 0, 0.84, 0)`, `--ease-in-out: cubic-bezier(0.65, 0, 0.35,
  1)`.
- Durations: `--dur-micro: 120ms`, `--dur-short: 220ms`, `--dur-long: 420ms`.
  Unchanged from the old system — these tokens did not need to move for the
  material redesign.
- **Signature visual #1 — the waveform** (`src/components/Waveform.tsx` /
  `.waveform` styles in `src/index.css`): a hand-built CSS "spectrogram
  strip resolving into an animated waveform," no libraries. Two variants:
  `ambient` (idle looping bob, `waveform--ambient`) used once, as Home's hero
  visual; `reveal` (one-shot scale-in, `waveform--reveal`) is the variant the
  component itself supports, though the Player's actual generation-complete
  moment uses a sibling class directly on its real output bars — see next
  point. Not used anywhere else.
- **Signature visual #2 — the generation-complete reveal**
  (`.output-bar--reveal` in `src/index.css`, applied inline in `Player.tsx`):
  the Player's real output bars (not a decorative stand-in) get a
  `scaleY(0) → scaleY(1)` reveal animation the moment a generation finishes,
  keyed by remounting the bar container on `generation.phase === 'done'`.
- **Ambient motif — "Aqua Drift"** (`src/components/AquaDrift.tsx`, `.aqua-drift*`
  styles in `src/index.css`): two soft blurred glow blooms
  (`.aqua-drift__bloom--a/b`, staggered 10s/12s loops) plus three horizontal
  drifting wave bands (`.aqua-drift__wave--a/b/c`, staggered 12s/15s/17s
  loops), all sharing the app's single accent hue and a `blur()` treatment so
  they read as one atmospheric system. `aria-hidden`, purely decorative.
  **Home hero ONLY** — not present on Player, Library, or Login.
- `prefers-reduced-motion: reduce` collapses all of the above: the glitch
  wordmark animation and Aqua Drift's blooms/waves stop entirely (ambient,
  safe to fully halt); the waveform's ambient bob freezes at its resting
  height and its reveal/output-bar-reveal animations collapse to a ≤150ms
  opacity crossfade; Tailwind's `animate-pulse`/`animate-bounce` utilities
  used elsewhere in the app are capped to a single 150ms iteration.
- `StressBall.tsx`'s physics toy remains explicitly out of scope — untouched
  by this redesign. There is no physics-effects toggle anywhere in the nav
  or elsewhere in the app.

## Microinteractions stance

- Silent success — no "Saved!" toast when the result is already visible
  on-screen (Library's SAVE button already does this; keep it).
- Optimistic delete + no confirmation modal for PURGE (already the case;
  keep it — it's the correct pattern, not a gap).
- Hover tooltip delay 800ms, focus delay 0ms, where tooltips exist.
- Focus rings appear instantly, never animated in (`brutal-btn`/
  `brutal-btn-pink` `:focus-visible` sets a solid `outline` with no
  transition).
- Buttons get real `:hover`/`:active`/`:focus-visible` coverage: a
  `translateY(-1px)` lift + brighter glow on hover, `translateY(0)
  scale(0.97)` on press (80ms), all transform transitions removed entirely
  under `prefers-reduced-motion: reduce`.

## CTA voice

**Aqua-Chrome pill buttons**, replacing the old `brutal-btn` (hard 2px
border, offset hard shadow, translate-on-press, zero radius, uppercase).
Both button classes live in `src/index.css`:

- `.brutal-btn` (primary, accent-colored) and `.brutal-btn-pink` (destructive,
  `--color-danger`-colored) — class names kept from the old system, styling
  fully replaced.
- Shape: `border-radius: var(--radius-pill)` (999px), `border: none`.
- Fill: `linear-gradient(160deg, oklch(from var(--accent) calc(l + 0.14) c h),
  var(--accent) 65%)` — a lighter tint of the accent fading into the accent
  itself, giving a chrome/bevel look from one hue rather than a second color.
- Highlight: `inset 0 1px 0 oklch(from var(--accent) calc(l + 0.35) calc(c *
  0.3) h / 0.6)` — a specular glass-edge detail — plus an outer
  `0 4px 10px var(--accent-glow)` soft glow shadow.
- Hover brightens the glow and lifts `translateY(-1px)`; active presses to
  `translateY(0) scale(0.97)` over 80ms; `:focus-visible` gets a solid
  `outline: 2px solid var(--accent)` with `outline-offset: 2px`.
- No uppercase mandate anymore (the shouty CTA voice was a brutalist-only
  requirement); no offset hard shadows; no translate-on-press snap.
- Inputs (`.brutal-input`) get the small `--radius-chip` (9px), a thin
  `1.5px solid var(--border)`, and an accent glow ring on focus
  (`box-shadow: 0 0 0 3px var(--accent-glow)`) instead of a hard border swap.
- Cards/panels (`.brutal-card`) get `--radius-panel` (14px), a `1px solid
  var(--border)`, and a soft ambient shadow (`0 2px 12px oklch(0% 0 0 /
  0.06)`) instead of a hard offset shadow.

## Nav

`src/components/Navigation.tsx` — a pill-based nav, replacing the old N8
"Terminal command" CLI-flag nav (`> imagesound --upload --studio --library▮`,
blinking caret). No `--flag` command-line framing survives; there is no
physics-effects toggle.

- Wordmark (`imagesound`, glitch effect, Syne, uppercase) — unchanged,
  hard-left.
- Three route pills (`nav-term__btn`, `--radius-pill`), one per route
  (`/` → `upload`, `/player` → `studio`, `/library` → `library`); the active
  route gets a filled `--accent` background with `--selected-text`, inactive
  routes are `--text-muted` and brighten to `--accent` on hover.
- Theme toggle button (sun/moon icon from `lucide-react`), same pill
  treatment, no label.
- Auth control, right-aligned: a filled `--accent` "Log in" pill when logged
  out, or a `--text-muted` username + logout-icon pill when logged in.
- Sticky (`sticky top-0 z-50`), single `1px solid var(--border)`
  bottom hairline — no blinking caret, no `>` prompt glyph anywhere.

## Footer

`src/components/Layout.tsx` — a thin one-line status strip: `1px solid
var(--border)` top hairline, `11px` uppercase tracking-widest text in
`--text-muted`: `imagesound · browser-based audio synthesis & converter ·
system: online`. No column layout, not a marketing footer — same inline
single-line shape (`Ft2`) the old system used, restyled to the new tokens.

## Per-page allowances

- Home MAY use the Aqua Drift ambient motif (`<AquaDrift />`, hero section
  only) and the Waveform component's `ambient` variant (the signature
  spectrogram→waveform visual, in the hero copy column).
- Player MAY reuse the generation-complete reveal (`.output-bar--reveal`) on
  its own real output bars, but gets no ambient background motion — it's a
  task screen where idle motion would compete with the waveform's own
  functional state.
- Library MUST NOT use ambient enrichment — the content (the archive)
  carries the page; it does use `--accent-tertiary` as its informational
  accent and `--radius-panel`/`--color-warning`/`--color-danger` for its
  cards and status states.
- Login is a clean functional gate — same token system, no ambient motion,
  `--radius-panel` card with an `--accent`-colored top rule.

## What pages MUST share

- The wordmark (`imagesound`, Syne, glitch effect — unchanged).
- The palette above: one dominant accent (`--accent`), two reassigned
  semantic accents (`--accent-secondary` = warning/unsaved,
  `--accent-tertiary` = informational/Library), `--color-warning` /
  `--color-danger` for status.
- Syne (display) + Geist (UI) as the two loaded families; `--font-mono`
  reserved for genuinely tabular/labeled content only.
- The three-step radius scale (`--radius-pill` / `--radius-panel` /
  `--radius-chip`) — no ad-hoc radius values.
- The CTA voice (`.brutal-btn` / `.brutal-btn-pink` / `.brutal-input` /
  `.brutal-card` family — class names kept, Aqua-Chrome styling shipped).
- The nav and footer (shared shell components in `Navigation.tsx` /
  `Layout.tsx`, not per-page).

## What pages MAY differ on

- Whether the Aqua Drift ambient motif is present (Home only, per Per-page
  allowances above).
- Macrostructure/content layout specifics — Home leans hero-first, Player
  leans panel-first, Library uses variable-size tiles — unchanged from the
  existing IA; this redesign is a material/token change, not a structural
  one.

## Exports

### tokens.css
```css
:root, body.dark {
  --bg:               oklch(13% 0.014 230);
  --bg-card:          oklch(17% 0.016 230);
  --bg-elevated:      oklch(21% 0.018 230);
  --input-bg:         oklch(13% 0.014 230);
  --text-primary:     oklch(94% 0.008 230);
  --text-muted:       oklch(68% 0.010 230);
  --text-heading:     oklch(94% 0.008 230);
  --accent:           oklch(55% 0.19 230);
  --accent-glow:      oklch(55% 0.19 230 / 0.5);
  --accent-secondary: oklch(74% 0.09 230);
  --accent-tertiary:  oklch(60% 0.07 230);
  --color-warning:    oklch(72% 0.15 70);
  --color-danger:     oklch(62% 0.20 25);
  --border:           oklch(30% 0.012 230);
  --border-muted:     oklch(30% 0.012 230);
  --selected-bg:      oklch(55% 0.19 230);
  --selected-text:    oklch(97% 0.006 230);

  --radius-pill:  999px;
  --radius-panel: 14px;
  --radius-chip:  9px;
}

body.light {
  --bg:               oklch(97% 0.008 230);
  --bg-card:          oklch(94% 0.010 230);
  --bg-elevated:      oklch(99% 0.006 230);
  --input-bg:         oklch(99% 0.006 230);
  --text-primary:     oklch(20% 0.012 230);
  --text-muted:       oklch(45% 0.010 230);
  --text-heading:     oklch(20% 0.012 230);
  --accent:           oklch(58% 0.17 230);
  --accent-glow:      oklch(58% 0.17 230 / 0.35);
  --accent-secondary: oklch(42% 0.10 230);
  --accent-tertiary:  oklch(52% 0.07 230);
  --color-warning:    oklch(70% 0.16 70);
  --color-danger:     oklch(58% 0.20 25);
  --border:           oklch(85% 0.008 230);
  --border-muted:     oklch(85% 0.008 230);
  --selected-bg:      oklch(58% 0.17 230);
  --selected-text:    oklch(99% 0.004 230);
}

@theme {
  --font-mono:    'Geist Mono', monospace;
  --font-display: 'Syne', sans-serif;
  --font-ui:      'Geist', sans-serif;

  --spacing-3xs: 0.125rem; --spacing-2xs: 0.25rem; --spacing-xs: 0.5rem;
  --spacing-sm:  0.75rem;  --spacing-md:  1rem;    --spacing-lg: 1.5rem;
  --spacing-xl:  2.5rem;   --spacing-2xl: 4rem;    --spacing-3xl: 6rem;

  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in:  cubic-bezier(0.7, 0, 0.84, 0);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
  --dur-micro: 120ms; --dur-short: 220ms; --dur-long: 420ms;
}
```

Tailwind v4 `@theme`, DTCG `tokens.json`, and shadcn/ui variable exports are
skipped for now (single-app project, not a multi-consumer design system) —
add on request via `export-formats.md`.
