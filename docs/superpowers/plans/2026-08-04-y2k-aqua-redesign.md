# Y2K/Aqua-Chrome Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ImageSound's brutalist-terminal visual identity with the Y2K/Aqua-Chrome system
defined in `docs/superpowers/specs/2026-08-04-y2k-aqua-redesign-design.md`, without changing any
routes, IA, copy, or functional behavior.

**Architecture:** Because the entire existing app already reads colors exclusively through CSS
custom properties (`var(--accent)`, `var(--bg)`, etc. — never hardcoded hex in JSX), the palette
swap is concentrated almost entirely in `src/index.css`'s token block (Task 1) and its shared
component classes (Task 2). Per-page work (Tasks 6-9) is limited to adding the new corner-radius
scale to raw-bordered containers that don't go through a shared class, plus wiring in the new
ambient background component on Home only.

**Tech Stack:** React + TypeScript + Vite + Tailwind v4 (existing). No new runtime dependencies —
the ambient motif is pure CSS, no animation library needed.

## Global Constraints

- **One accent hue (230°) for both light and dark mode — never switch hue between modes.** Only
  lightness/chroma differ. (spec § Theme — palette)
- **`--accent-secondary`/`--accent-tertiary` are decorative tonal-blue steps, NOT semantic warning
  tokens** — discovered during plan-writing via a full-codebase grep (100+ usages, mostly non-
  semantic). Any consuming site that's a genuine warning, error, or destructive-confirm must use
  `--color-warning` or `--color-danger` instead — see the explicit swap list in Tasks 6-9. Don't
  assume every `--accent-secondary`/`--accent-tertiary` site found later is safe to leave as-is
  without checking whether it's actually alarming a real user about something.
- **Corner radius: exactly 3 values, no ad-hoc radii anywhere.** `--radius-pill: 999px` (buttons,
  chips, toggles), `--radius-panel: 14px` (cards, panels, containers, modals), `--radius-chip: 9px`
  (small tags/badges/inline controls). (spec § Shape / corner-radius system)
- **Aqua Drift ambient motif appears on the Home hero ONLY.** Not Player, not Library, not Login.
  (spec § Signature ambient motif)
- **`prefers-reduced-motion: reduce` fully disables the ambient motif's animation** (not slowed —
  disabled outright, same as the old waveform idle animation). (spec § Signature ambient motif)
- **Contrast:** body text 4.5:1 minimum against paper in both modes; verify with browser devtools
  contrast checker before considering a task done. (spec § Theme — palette)
- **Typography:** Syne (display, unchanged) + Geist Sans (body/UI, replacing Geist Mono). 2-font
  system, no third family introduced except where tabular numerals are already in use.
- **No new npm dependencies.** Geist Sans loads via the same Google Fonts `@import` mechanism
  already used for Syne/Geist Mono in `src/index.css:1` — don't switch to `next/font` or a package
  install; this isn't a Next.js project and the existing mechanism is the established pattern.
- **Existing motion tokens (`--ease-out`, `--ease-in`, `--ease-in-out`, `--dur-micro/short/long`)
  are reused unchanged** — they're not brutalism-specific, don't redefine them.
- **Class names are NOT renamed** (`.brutal-btn`, `.brutal-card`, etc. keep their existing names
  even though their content changes completely) — renaming is a mechanical, unrelated refactor
  that would touch ~15 files for zero functional benefit; scope is the visual redesign only.

---

### Task 1: Palette, radius, and font token foundation

**Files:**
- Modify: `src/index.css:1-89` (font import + `:root, body.dark` + `body.light` + `@theme` blocks)

**Interfaces:**
- Consumes: nothing (foundational task)
- Produces: every color/radius/font token later tasks and all existing JSX (which already
  reference these var names via inline `style={{ color: 'var(--accent)' }}` etc.) will read.
  Token names `--bg`, `--bg-card`, `--bg-elevated`, `--text-primary`, `--text-muted`,
  `--text-heading`, `--accent`, `--accent-secondary`, `--accent-tertiary`, `--color-warning`,
  `--color-danger`, `--border`, `--border-muted`, `--selected-bg`, `--selected-text`, `--input-bg`
  are **preserved exactly by name** (only their values change) so no consuming file needs to
  change. New tokens added: `--accent-glow`, `--radius-pill`, `--radius-panel`, `--radius-chip`.

- [ ] **Step 1: Replace the font import**

Current (`src/index.css:1`):
```css
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=Geist+Mono:wght@100..900&display=swap');
```

New:
```css
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=Geist:wght@100..900&display=swap');
```

(Google Fonts serves the sans family under the name `Geist`, not `Geist Sans` — the CSS
`font-family` value below uses the correct `'Geist'` name.)

- [ ] **Step 2: Replace the `:root, body.dark` token block**

Current (`src/index.css:4-30`) — replace the whole block with:

```css
:root, body.dark {
  /* Y2K/Aqua-Chrome redesign — see docs/superpowers/specs/2026-08-04-y2k-aqua-redesign-design.md
     Single anchor hue 230deg (cool blue) for BOTH light and dark mode — dark mode gets more
     chroma + glow, never a different hue. Elevation reads as lighter, never shadowed. */
  --bg: oklch(13% 0.014 230);
  --bg-card: oklch(17% 0.016 230);
  --bg-elevated: oklch(21% 0.018 230);
  --input-bg: oklch(13% 0.014 230);
  --text-primary: oklch(94% 0.008 230);
  --text-muted: oklch(68% 0.010 230);
  --text-heading: oklch(94% 0.008 230);
  --accent: oklch(55% 0.19 230);
  --accent-glow: oklch(55% 0.19 230 / 0.5);
  --accent-secondary: oklch(74% 0.09 230);
  --accent-tertiary: oklch(60% 0.07 230);
  --color-warning: oklch(72% 0.15 70);
  --color-danger: oklch(62% 0.20 25);
  --border: oklch(30% 0.012 230);
  --border-muted: oklch(30% 0.012 230);
  --selected-bg: oklch(55% 0.19 230);
  --selected-text: oklch(97% 0.006 230);

  --theme-font-ui: 'Geist', sans-serif;
  --theme-grid-opacity: 0; /* the cyberpunk grid overlay is retired — see Task 3 */

  --radius-pill: 999px;
  --radius-panel: 14px;
  --radius-chip: 9px;
}
```

**Important correction from the original spec draft:** a full-codebase grep during plan-writing
found `--accent-secondary`/`--accent-tertiary` are NOT narrow warning/informational tokens as the
old `design.md`'s comment claimed — they're used as the app's general second and third decorative
colors in 100+ places across Player.tsx, EffectsPanel.tsx, SourcePreview.tsx, Home.tsx, and
Library.tsx (section headings, toggle active-states, borders, labels — most unrelated to warnings).
Repointing them to amber/red (as originally drafted) would have splashed warning/danger colors
across dozens of purely decorative elements — a real regression, and a worse "one accent hue"
violation than the original brutalist system (three clashing saturated hues instead of one). Fixed
here: both tokens stay in the **same 230° hue as `--accent`**, just progressively less saturated
(`--accent` chroma 0.19 → `--accent-secondary` 0.09 → `--accent-tertiary` 0.07) — a 3-step tonal
family, not three competing hues. The **actually semantic** warning/error moments (Home's
unfinished-work banner, GeneratePanel's unsaved-warning banner, Login's error banner, Player's
discard-confirm) get repointed to the dedicated `--color-warning`/`--color-danger` tokens at their
specific call sites instead — see the added steps in Tasks 6, 7, 8, and 9 below.

- [ ] **Step 3: Replace the `body.light` token block**

Current (`src/index.css:32-52`) — replace the whole block with:

```css
body.light {
  --bg: oklch(97% 0.008 230);
  --bg-card: oklch(94% 0.010 230);
  --bg-elevated: oklch(99% 0.006 230);
  --input-bg: oklch(99% 0.006 230);
  --text-primary: oklch(20% 0.012 230);
  --text-muted: oklch(45% 0.010 230);
  --text-heading: oklch(20% 0.012 230);
  --accent: oklch(58% 0.17 230);
  --accent-glow: oklch(58% 0.17 230 / 0.35);
  --accent-secondary: oklch(42% 0.10 230);
  --accent-tertiary: oklch(52% 0.07 230);
  --color-warning: oklch(70% 0.16 70);
  --color-danger: oklch(58% 0.20 25);
  --border: oklch(85% 0.008 230);
  --border-muted: oklch(85% 0.008 230);
  --selected-bg: oklch(58% 0.17 230);
  --selected-text: oklch(99% 0.004 230);

  --theme-font-ui: 'Geist', sans-serif;
  --theme-grid-opacity: 0;
}
```

- [ ] **Step 4: Update the `@theme` font tokens**

Current (`src/index.css:54-57`):
```css
@theme {
  --font-mono: 'Geist Mono', monospace;
  --font-display: 'Syne', sans-serif;
  --font-ui: 'Geist Mono', monospace;
```

New:
```css
@theme {
  --font-mono: 'Geist Mono', monospace;  /* kept only for genuinely tabular content, not general UI */
  --font-display: 'Syne', sans-serif;
  --font-ui: 'Geist', sans-serif;
```

(`--font-mono` stays defined in case a later task needs it for timestamps/durations, but nothing
in the base body/UI styling references it anymore after this task.)

- [ ] **Step 5: Verify — start the dev server and check both modes render without console errors**

Run: `npm run dev` (or use the project's `run.py` launcher), open the app, toggle
`--theme:dark`/`--theme:light` via the nav. Confirm: no white-on-white or black-on-black text
anywhere yet (some elements will still look broken — the shared component classes aren't updated
until Task 2 — the check here is just "the token block itself loads without a CSS syntax error and
text is still legible against the new backgrounds").

- [ ] **Step 6: Commit**

```bash
git add src/index.css
git commit -m "Redesign: replace brutalist palette with Y2K/Aqua-Chrome OKLCH tokens"
```

---

### Task 2: Rebuild shared component classes (buttons, inputs, cards, nav)

**Files:**
- Modify: `src/index.css:181-330` (`.brutal-btn`, `.brutal-btn-pink`, `.brutal-input`,
  `.brutal-card`, `.nav-term__caret`, `.nav-term__btn`, `.lib-icon-btn`)

**Interfaces:**
- Consumes: `--accent`, `--accent-glow`, `--color-danger`, `--radius-pill`, `--radius-panel`, `--radius-chip` from
  Task 1.
- Produces: same class names as before (`.brutal-btn`, `.brutal-btn-pink`, `.brutal-input`,
  `.brutal-card`, `.nav-term__btn`, `.lib-icon-btn`) — every consuming file across Home, Player,
  Library, Login, GeneratePanel, Navigation keeps working with zero JSX changes. `.nav-term__caret`
  is deleted (the blinking terminal caret is retired per the Nav redesign in Task 4).

- [ ] **Step 1: Replace `.brutal-btn` / `.brutal-btn-pink`**

Current (`src/index.css:181-211`):
```css
.brutal-btn {
  @apply border-2 px-4 py-2 uppercase font-bold transition-none active:translate-y-1 active:translate-x-1;
  border-color: var(--accent);
  color: var(--accent);
  box-shadow: 4px 4px 0 0 var(--accent);
}

.brutal-btn:hover {
  background-color: var(--accent);
  color: var(--selected-text);
  box-shadow: 2px 2px 0 0 var(--accent);
}
.brutal-btn:active {
  box-shadow: 0px 0px 0 0 var(--accent);
}

.brutal-btn-pink {
  @apply border-2 px-4 py-2 uppercase font-bold transition-none active:translate-y-1 active:translate-x-1;
  border-color: var(--accent-secondary);
  color: var(--accent-secondary);
  box-shadow: 4px 4px 0 0 var(--accent-secondary);
}

.brutal-btn-pink:hover {
  background-color: var(--accent-secondary);
  color: var(--selected-text);
  box-shadow: 2px 2px 0 0 var(--accent-secondary);
}
.brutal-btn-pink:active {
  box-shadow: 0px 0px 0 0 var(--accent-secondary);
}
```

New:
```css
.brutal-btn {
  @apply px-5 py-2.5 font-semibold;
  border: none;
  border-radius: var(--radius-pill);
  color: var(--selected-text);
  background: linear-gradient(160deg, oklch(from var(--accent) calc(l + 0.14) c h), var(--accent) 65%);
  box-shadow:
    inset 0 1px 0 oklch(from var(--accent) calc(l + 0.35) calc(c * 0.3) h / 0.6),
    0 4px 10px var(--accent-glow);
  transition: transform var(--dur-micro) var(--ease-out), box-shadow var(--dur-micro) var(--ease-out);
}
.brutal-btn:hover {
  box-shadow:
    inset 0 1px 0 oklch(from var(--accent) calc(l + 0.35) calc(c * 0.3) h / 0.7),
    0 6px 14px var(--accent-glow);
  transform: translateY(-1px);
}
.brutal-btn:active {
  transform: translateY(0) scale(0.97);
  transition-duration: 80ms;
}
.brutal-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
  .brutal-btn:hover, .brutal-btn:active { transform: none; }
}

.brutal-btn-pink {
  @apply px-5 py-2.5 font-semibold;
  border: none;
  border-radius: var(--radius-pill);
  color: var(--selected-text);
  background: linear-gradient(160deg, oklch(from var(--color-danger) calc(l + 0.12) c h), var(--color-danger) 65%);
  box-shadow:
    inset 0 1px 0 oklch(from var(--color-danger) calc(l + 0.3) calc(c * 0.3) h / 0.6),
    0 4px 10px oklch(from var(--color-danger) l c h / 0.35);
  transition: transform var(--dur-micro) var(--ease-out), box-shadow var(--dur-micro) var(--ease-out);
}
.brutal-btn-pink:hover {
  transform: translateY(-1px);
}
.brutal-btn-pink:active {
  transform: translateY(0) scale(0.97);
  transition-duration: 80ms;
}
@media (prefers-reduced-motion: reduce) {
  .brutal-btn-pink:hover, .brutal-btn-pink:active { transform: none; }
}
```

(`.brutal-btn-pink` is used for destructive actions — e.g. Library's PURGE, Player's DISCARD.
It's now mapped to `--color-danger` (red) rather than `--accent-secondary` like the original —
`--accent-secondary` was retinted in Task 1 into a soft muted-blue tonal color for general
secondary decoration, which would no longer read as "this is dangerous" if kept on a destructive
button. `--color-danger` is the dedicated semantic token for exactly this case. `uppercase` is
dropped: the soft-glass voice doesn't need the brutalist shouty-CTA convention: check
each consuming button's own JSX for a hardcoded `uppercase` class if the label still needs it —
none currently hardcode it separately, they inherited it from `.brutal-btn` alone.)

- [ ] **Step 2: Replace `.brutal-input`**

Current (`src/index.css:213-223`):
```css
.brutal-input {
  @apply border-2 p-2 outline-none;
  border-color: var(--accent);
  color: var(--accent);
  background-color: var(--input-bg);
  box-shadow: none;
}
.brutal-input:focus {
  border-color: var(--accent-secondary);
  box-shadow: 0 0 10px var(--accent-secondary);
}
```

New:
```css
.brutal-input {
  @apply p-2.5 outline-none;
  border: 1.5px solid var(--border);
  border-radius: var(--radius-chip);
  color: var(--text-primary);
  background-color: var(--input-bg);
  box-shadow: none;
  transition: border-color var(--dur-micro) var(--ease-out), box-shadow var(--dur-micro) var(--ease-out);
}
.brutal-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}
```

- [ ] **Step 3: Replace `.brutal-card`**

Current (`src/index.css:225-236`):
```css
.brutal-card {
  @apply border-2 p-4;
  border-color: var(--accent);
  background-color: var(--bg-card);
  box-shadow: -4px 4px 0 0 var(--accent-tertiary);
}

body.light .brutal-card {
  background-color: var(--bg-card);
  border-color: var(--border);
  box-shadow: -4px 4px 0 0 var(--accent-tertiary);
}
```

New (the `body.light` override is deleted entirely — the base rule now works correctly in both
modes since it references tokens that already flip per-theme):
```css
.brutal-card {
  @apply p-4;
  border: 1px solid var(--border);
  border-radius: var(--radius-panel);
  background-color: var(--bg-card);
  box-shadow: 0 2px 12px oklch(0% 0 0 / 0.06);
}
```

- [ ] **Step 4: Delete the now-redundant `body.light .brutal-btn` / `.brutal-btn-pink` / `.brutal-input` overrides**

Current (`src/index.css:238-264`) — this entire block (`body.light .brutal-btn`,
`body.light .brutal-btn:hover`, `body.light .brutal-btn-pink`, `body.light .brutal-btn-pink:hover`,
`body.light .brutal-input`) is **deleted**. It existed because the old system's light-mode buttons
needed different literal color values than dark mode; the new rules from Steps 1-2 above reference
`var(--accent)` etc. directly, which already resolve correctly per-theme — no light-mode override
needed.

- [ ] **Step 5: Delete `.nav-term__caret` and its keyframes**

Current (`src/index.css:266-278`) — delete the entire block (`.nav-term__caret` rule, the
`nav-caret-blink` keyframes, and its reduced-motion override). The blinking terminal caret has no
place in the redesigned nav (Task 4 removes its one call site in `Navigation.tsx`).

- [ ] **Step 6: Update `.nav-term__btn`**

Current (`src/index.css:284-307`, keep the comment's *spirit* — instant color change on tap — but
the "scaled-down brutal-btn press family" framing no longer applies since `.brutal-btn` itself
changed shape in Step 1):

```css
.nav-term__btn {
  transition: color var(--dur-micro) var(--ease-out), background-color var(--dur-micro) var(--ease-out);
  border-radius: var(--radius-pill);
}
.nav-term__btn:active {
  transform: scale(0.97);
}
@media (prefers-reduced-motion: reduce) {
  .nav-term__btn:active {
    transform: none;
  }
}
```

- [ ] **Step 7: Update `.lib-icon-btn`**

Current (`src/index.css:291-300`):
```css
.lib-icon-btn {
  border-color: var(--btn-c);
  color: var(--btn-c);
  background-color: transparent;
  transition: background-color var(--dur-micro) var(--ease-out), color var(--dur-micro) var(--ease-out);
}
.lib-icon-btn:hover:not(:disabled) {
  background-color: var(--btn-c);
  color: var(--bg);
}
```

New (add radius; behavior otherwise unchanged since `--btn-c` is still set per-instance from
`Library.tsx`):
```css
.lib-icon-btn {
  border-color: var(--btn-c);
  border-radius: var(--radius-chip);
  color: var(--btn-c);
  background-color: transparent;
  transition: background-color var(--dur-micro) var(--ease-out), color var(--dur-micro) var(--ease-out);
}
.lib-icon-btn:hover:not(:disabled) {
  background-color: var(--btn-c);
  color: var(--bg);
}
```

- [ ] **Step 8: Verify — build and visually confirm buttons/cards/inputs render correctly in both modes**

Run: `npm run build` (must succeed — Tailwind's `@apply` directives must still resolve). Then run
the dev server and check: any page with a button (Home's upload, Login's submit) shows a pill-
shaped gradient button with a visible top-edge highlight; any `.brutal-card` shows a soft-rounded
panel, not a hard-cornered box; toggle light/dark and confirm both look intentional (no
invisible/washed-out text).

- [ ] **Step 9: Commit**

```bash
git add src/index.css
git commit -m "Redesign: rebuild shared button/input/card classes as Aqua-Chrome pill system"
```

---

### Task 3: Layout.tsx — remove cyberpunk grid overlay, restyle footer

**Files:**
- Modify: `src/components/Layout.tsx` (full file, 28 lines)

**Interfaces:**
- Consumes: `--theme-grid-opacity` (already set to `0` in both themes by Task 1, so the grid
  overlay is already invisible — this task removes the dead markup entirely rather than leaving
  an inert div).
- Produces: nothing new consumed elsewhere.

- [ ] **Step 1: Remove the grid-overlay div and restyle the footer**

Current (`src/components/Layout.tsx:1-28`):
```tsx
import React from 'react';

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col relative overflow-hidden">
      {/* Grid overlay for cyberpunk feel */}
      <div className="absolute inset-0 z-0 pointer-events-none"
        style={{
          opacity: 'var(--theme-grid-opacity, 0.2)',
          backgroundImage: 'linear-gradient(var(--accent) 1px, transparent 1px), linear-gradient(90deg, var(--accent) 1px, transparent 1px)',
          backgroundSize: '40px 40px'
        }}
      />
      <div className="relative z-10 flex flex-col flex-grow">
        {children}

        {/* Ft2 inline single-line footer — a status strip, not a marketing
            footer. See design.md § Footer. */}
        <footer
          className="border-t-2 px-4 md:px-8 py-3 font-mono text-[11px] uppercase tracking-widest"
          style={{ borderTopColor: 'var(--border-muted)', color: 'var(--text-muted)' }}
        >
          imagesound · browser-based audio synthesis &amp; converter · system: online
        </footer>
      </div>
    </div>
  );
}
```

New:
```tsx
import React from 'react';

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col">
      {children}

      {/* Soft status-strip footer — see docs/superpowers/specs/2026-08-04-y2k-aqua-redesign-design.md § Footer */}
      <footer
        className="px-4 md:px-8 py-3 text-[11px] uppercase tracking-widest"
        style={{ borderTop: '1px solid var(--border)', color: 'var(--text-muted)' }}
      >
        imagesound · browser-based audio synthesis &amp; converter · system: online
      </footer>
    </div>
  );
}
```

(The cyberpunk grid overlay div, its `relative overflow-hidden`/`z-0`/`z-10` wrapper structure
that existed solely to layer content above the grid, and the `font-mono` class on the footer are
all removed — the grid has no place in the soft-glass system and the footer now inherits `Geist`
from the body's `--font-ui` instead of forcing mono.)

- [ ] **Step 2: Verify — build and check no page has a stray absolutely-positioned empty space where the grid used to be**

Run: `npm run build`, then visually check any page — layout should be identical minus the grid
lines (which were already invisible in both themes after Task 1 set `--theme-grid-opacity: 0`
anyway, so this is a pure cleanup with no visual regression risk).

- [ ] **Step 3: Commit**

```bash
git add src/components/Layout.tsx
git commit -m "Redesign: remove cyberpunk grid overlay, restyle footer"
```

---

### Task 4: Navigation.tsx — redesign to pill nav, remove CLI-flag styling

**Files:**
- Modify: `src/components/Navigation.tsx` (full file, 126 lines)

**Interfaces:**
- Consumes: `.nav-term__btn` (Task 2), `--radius-pill`, `--accent`, `--bg-card`.
- Produces: nothing new consumed elsewhere — same component signature
  (`{ theme, setTheme, physicsOn, setPhysicsOn }`), same route behavior.

- [ ] **Step 1: Replace the nav markup**

Current (`src/components/Navigation.tsx:30-125`, reproduced in full for reference — the CLI-flag
`--{r.flag}` labels, the `>` prompt glyph, and the `.nav-term__caret` blinking caret span are the
parts being removed):

```tsx
  return (
    <header
      className="border-b-2 px-4 md:px-8 py-4 sticky top-0 z-50"
      style={{ backgroundColor: 'var(--bg)', borderBottomColor: 'var(--border-muted)' }}
    >
      <pre className="m-0 font-mono flex flex-wrap items-baseline gap-x-3 gap-y-1.5 text-sm md:text-base whitespace-pre-wrap">
        <span aria-hidden="true" style={{ color: 'var(--accent)' }}>{'>'}</span>

        <Link
          to="/"
          className="glitch font-bold tracking-widest uppercase"
          data-text="imagesound"
          style={{ color: 'var(--text-heading)' }}
        >
          imagesound
        </Link>

        {ROUTES.map(r => {
          const active = location.pathname === r.path;
          return (
            <Link
              key={r.path}
              to={r.path}
              className="nav-term__btn"
              style={{
                color: active ? 'var(--accent)' : 'var(--text-muted)',
                textDecoration: active ? 'underline' : 'none',
                textUnderlineOffset: '3px',
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.color = 'var(--accent)'; }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.color = 'var(--text-muted)'; }}
            >
              --{r.flag}
            </Link>
          );
        })}

        <button
          onClick={toggleTheme}
          className="nav-term__btn inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          aria-label={`--theme:${theme}, toggle theme`}
          title="Toggle theme"
        >
          --theme:{theme}
          {theme === 'light' ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        <button
          onClick={() => setPhysicsOn(prev => !prev)}
          className="nav-term__btn inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
          style={{ color: physicsOn ? 'var(--accent)' : 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { if (!physicsOn) e.currentTarget.style.color = 'var(--text-muted)'; }}
          aria-label={`--physics:${physicsOn ? 'on' : 'off'}, toggle physics ball toy`}
          title="Toggle physics ball toy"
        >
          --physics:{physicsOn ? 'on' : 'off'}
          <Circle size={14} />
        </button>

        {username ? (
          <button
            onClick={handleLogout}
            className="nav-term__btn inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
            aria-label={`--user:${username}, log out`}
            title="Log out"
          >
            --user:{username}
            <LogOut size={14} />
          </button>
        ) : (
          <Link
            to="/login"
            className="nav-term__btn inline-flex items-center gap-1"
            style={{ color: 'var(--accent-secondary)', textDecoration: 'none' }}
            onMouseEnter={e => { e.currentTarget.style.textDecoration = 'underline'; }}
            onMouseLeave={e => { e.currentTarget.style.textDecoration = 'none'; }}
            aria-label="--login, log in"
            title="Log in"
          >
            --login
            <LogIn size={14} />
          </Link>
        )}

        <span className="nav-term__caret" aria-hidden="true" style={{ color: 'var(--accent)' }}>▮</span>
      </pre>
    </header>
  );
}
```

New:
```tsx
  return (
    <header
      className="px-4 md:px-8 py-3 sticky top-0 z-50"
      style={{ backgroundColor: 'var(--bg)', borderBottom: '1px solid var(--border)' }}
    >
      <nav className="m-0 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-sm md:text-base">
        <Link
          to="/"
          className="glitch font-bold tracking-widest uppercase mr-2"
          data-text="imagesound"
          style={{ color: 'var(--text-heading)' }}
        >
          imagesound
        </Link>

        {ROUTES.map(r => {
          const active = location.pathname === r.path;
          return (
            <Link
              key={r.path}
              to={r.path}
              className="nav-term__btn px-3 py-1.5"
              style={{
                color: active ? 'var(--selected-text)' : 'var(--text-muted)',
                backgroundColor: active ? 'var(--accent)' : 'transparent',
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.color = 'var(--accent)'; }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.color = 'var(--text-muted)'; }}
            >
              {r.flag}
            </Link>
          );
        })}

        <button
          onClick={toggleTheme}
          className="nav-term__btn inline-flex items-center gap-1.5 bg-transparent border-0 px-3 py-1.5 cursor-pointer"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          aria-label={`theme: ${theme}, toggle theme`}
          title="Toggle theme"
        >
          {theme === 'light' ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        <button
          onClick={() => setPhysicsOn(prev => !prev)}
          className="nav-term__btn inline-flex items-center gap-1.5 bg-transparent border-0 px-3 py-1.5 cursor-pointer"
          style={{ color: physicsOn ? 'var(--accent)' : 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { if (!physicsOn) e.currentTarget.style.color = 'var(--text-muted)'; }}
          aria-label={`physics toy: ${physicsOn ? 'on' : 'off'}, toggle physics ball toy`}
          title="Toggle physics ball toy"
        >
          <Circle size={14} />
        </button>

        <span className="flex-grow" />

        {username ? (
          <button
            onClick={handleLogout}
            className="nav-term__btn inline-flex items-center gap-1.5 bg-transparent border-0 px-3 py-1.5 cursor-pointer"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
            aria-label={`user: ${username}, log out`}
            title="Log out"
          >
            {username}
            <LogOut size={14} />
          </button>
        ) : (
          <Link
            to="/login"
            className="nav-term__btn inline-flex items-center gap-1.5 px-3 py-1.5"
            style={{ color: 'var(--selected-text)', backgroundColor: 'var(--accent)', textDecoration: 'none' }}
            aria-label="log in"
            title="Log in"
          >
            Log in
            <LogIn size={14} />
          </Link>
        )}
      </nav>
    </header>
  );
}
```

(The `>` prompt glyph and `.nav-term__caret` are gone entirely — no replacement needed, the nav no
longer reads as a command line. Route labels drop the `--flag` prefix and now show as plain words
via `r.flag` directly (the `ROUTES` array's `flag` field, e.g. `'upload'`, still supplies the text —
only the rendered `--{r.flag}` template literal changes to plain `{r.flag}`). Active route gets a
filled pill instead of an underline. The login link becomes a filled accent pill instead of plain
underlined text, for better visual weight as the primary unauthenticated CTA. A `flex-grow` spacer
pushes theme/physics/user controls to the right, matching a more conventional nav rhythm now that
the CLI-line metaphor is gone.)

- [ ] **Step 2: Verify — tsc and visual check**

Run: `npx tsc --noEmit` (must be clean — no prop/import changes were made, `Sun`/`Moon`/`Circle`/
`LogIn`/`LogOut` imports at the top of the file are unchanged). Visually check: nav renders on one
line at desktop, active route shows a filled pill, no leftover `>` or blinking caret anywhere.

- [ ] **Step 3: Commit**

```bash
git add src/components/Navigation.tsx
git commit -m "Redesign: rebuild nav as pill-based, remove CLI-flag/terminal styling"
```

---

### Task 5: Build the Aqua Drift ambient background component

**Files:**
- Create: `src/components/AquaDrift.tsx`

**Interfaces:**
- Consumes: `--accent` (via `oklch(from var(--accent) ...)` CSS relative-color syntax, so it
  automatically tracks whichever theme is active — no props needed for color).
- Produces: `export function AquaDrift(): JSX.Element` — a self-contained, absolutely-positioned
  background layer. Task 6 renders it as the first child inside a `position: relative` hero
  container on Home.

- [ ] **Step 1: Write the component**

```tsx
// src/components/AquaDrift.tsx
//
// The one sanctioned ambient-motion motif in the redesign — see
// docs/superpowers/specs/2026-08-04-y2k-aqua-redesign-design.md § Signature ambient motif.
// Home hero ONLY. Two soft glow blooms (abstracted from the physics-toy's circle/blob shapes)
// + three horizontal drifting wave bands (referencing the Sony PSP XMB wave background). Both
// layers share the app's single accent hue and the same blur treatment so they read as one
// atmospheric system. Purely decorative -- aria-hidden, and fully static under
// prefers-reduced-motion (this is ambient, not functional, so it's safe to just stop, same
// reasoning the old design system used for its waveform idle animation).

export function AquaDrift() {
  return (
    <div className="aqua-drift" aria-hidden="true">
      <div className="aqua-drift__bloom aqua-drift__bloom--a" />
      <div className="aqua-drift__bloom aqua-drift__bloom--b" />
      <div className="aqua-drift__wave aqua-drift__wave--a" />
      <div className="aqua-drift__wave aqua-drift__wave--b" />
      <div className="aqua-drift__wave aqua-drift__wave--c" />
    </div>
  );
}
```

- [ ] **Step 2: Add the CSS**

Append to `src/index.css` (after the `.output-bar` block near the end of the file):

```css
/* ── Aqua Drift — the one sanctioned ambient motif, Home hero only. See
   docs/superpowers/specs/2026-08-04-y2k-aqua-redesign-design.md § Signature ambient motif. ── */
.aqua-drift {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  z-index: 0;
}
.aqua-drift__bloom {
  position: absolute;
  filter: blur(22px);
  border-radius: 50%;
  background: oklch(from var(--accent) l calc(c * 0.9) h);
}
.aqua-drift__bloom--a {
  top: 8%; left: 6%;
  width: 220px; height: 220px;
  opacity: 0.35;
  animation: aqua-drift-bloom-a 10s var(--ease-in-out) infinite;
}
.aqua-drift__bloom--b {
  bottom: -4%; right: 8%;
  width: 180px; height: 180px;
  border-radius: 40% 60% 55% 45% / 50% 45% 55% 50%;
  opacity: 0.3;
  animation: aqua-drift-bloom-b 12s var(--ease-in-out) infinite;
}
.aqua-drift__wave {
  position: absolute;
  left: -10%;
  width: 120%;
  height: 90px;
  border-radius: 50%;
  filter: blur(18px);
  background: oklch(from var(--accent) l calc(c * 0.85) h);
}
.aqua-drift__wave--a { top: 20%; opacity: 0.4; animation: aqua-drift-wave-a 12s var(--ease-in-out) infinite; }
.aqua-drift__wave--b { top: 46%; opacity: 0.32; animation: aqua-drift-wave-b 15s var(--ease-in-out) infinite; }
.aqua-drift__wave--c { top: 70%; opacity: 0.26; animation: aqua-drift-wave-c 17s var(--ease-in-out) infinite; }

@keyframes aqua-drift-bloom-a { 0%, 100% { transform: translate(0, 0); } 50% { transform: translate(14px, -10px); } }
@keyframes aqua-drift-bloom-b { 0%, 100% { transform: translate(0, 0); } 50% { transform: translate(-14px, 10px); } }
@keyframes aqua-drift-wave-a { 0%, 100% { transform: translateX(-20px); } 50% { transform: translateX(20px); } }
@keyframes aqua-drift-wave-b { 0%, 100% { transform: translateX(24px); } 50% { transform: translateX(-24px); } }
@keyframes aqua-drift-wave-c { 0%, 100% { transform: translateX(-14px); } 50% { transform: translateX(14px); } }

@media (prefers-reduced-motion: reduce) {
  .aqua-drift__bloom, .aqua-drift__wave {
    animation: none;
  }
}
```

(`oklch(from var(--accent) l calc(c * 0.9) h)` uses CSS relative-color syntax to derive the bloom
fill directly from the live `--accent` token — dark mode automatically gets more visible blooms
since dark mode's `--accent` already has higher chroma per Task 1, with no separate dark-mode CSS
needed here.)

- [ ] **Step 3: Verify — render standalone**

Since this component has no props and no page wires it in yet, verify by temporarily importing it
into any page in the dev server, confirming it drifts slowly and disappears completely with the
OS "reduce motion" setting turned on, then remove the temporary import (Task 6 does the real
wiring).

- [ ] **Step 4: Commit**

```bash
git add src/components/AquaDrift.tsx src/index.css
git commit -m "Redesign: add Aqua Drift ambient background component"
```

---

### Task 6: Home.tsx — wire in Aqua Drift, apply radius, fix semantic warning/danger colors

**Files:**
- Modify: `src/pages/Home.tsx`

**Interfaces:**
- Consumes: `AquaDrift` from `src/components/AquaDrift.tsx` (Task 5).

- [ ] **Step 1: Import and wire `AquaDrift` into the hero**

Add the import (`src/pages/Home.tsx:1-7` currently ends with the `Waveform` import):
```tsx
import { Waveform } from '../components/Waveform';
import { AquaDrift } from '../components/AquaDrift';
```

Wrap the page's outer container so the hero column can host the ambient layer. Current
(`src/pages/Home.tsx:90-95`):
```tsx
  return (
    <div className="container mx-auto px-4 md:px-8 py-10 md:py-14 flex-grow">
      <div className="grid grid-cols-1 lg:grid-cols-[1.3fr_1fr] gap-10 lg:gap-16 items-start max-w-6xl mx-auto">

        {/* LEFT — the actual tool. Wider column, left-biased, not centered. */}
        <div className="flex flex-col gap-8">
```

New — add `relative` so `AquaDrift`'s `position: absolute; inset: 0` anchors to this container,
and render `<AquaDrift />` as the first child so it sits behind everything else (`z-0` from its own
CSS, page content needs `relative z-10` to sit above it):
```tsx
  return (
    <div className="container mx-auto px-4 md:px-8 py-10 md:py-14 flex-grow relative">
      <AquaDrift />
      <div className="relative z-10 grid grid-cols-1 lg:grid-cols-[1.3fr_1fr] gap-10 lg:gap-16 items-start max-w-6xl mx-auto">

        {/* LEFT — the actual tool. Wider column, left-biased, not centered. */}
        <div className="flex flex-col gap-8">
```

(Only the outer two wrapping `div`s change — everything nested inside, all the way down to the
closing tags at `src/pages/Home.tsx:291-294`, is untouched.)

- [ ] **Step 2: Add radius to the raw-bordered containers**

Five containers in this file use `border-*` directly instead of `.brutal-card` and need the panel
or chip radius added (line numbers as of this file's current state — re-grep
`border-2|border-4|data-collider` in this file before editing if Task 1-5 commits have shifted
anything):

`src/pages/Home.tsx:114-118` (in-progress warning), current:
```tsx
            <div
              className="border-4 p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4"
              style={{ borderColor: 'var(--accent-secondary)', backgroundColor: 'color-mix(in oklch, var(--accent-secondary) 6%, transparent)' }}
            >
```
New:
```tsx
            <div
              className="border-4 rounded-[var(--radius-panel)] p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4"
              style={{ borderColor: 'var(--color-warning)', backgroundColor: 'color-mix(in oklch, var(--color-warning) 6%, transparent)' }}
            >
```

`src/pages/Home.tsx:158` (the `[ IMAGE ] [ AUDIO ] [ TEXT ]` input-type toggle buttons), current:
```tsx
                className="border-2 px-4 py-2 text-sm uppercase font-bold tracking-widest transition-colors"
```
New:
```tsx
                className="border-2 rounded-[var(--radius-pill)] px-4 py-2 text-sm uppercase font-bold tracking-widest transition-colors"
```

`src/pages/Home.tsx:174-175` (text-input box container), current:
```tsx
              data-collider
              className="border-4 p-8 md:p-10 flex flex-col items-start gap-6"
```
New:
```tsx
              data-collider
              className="border-4 rounded-[var(--radius-panel)] p-8 md:p-10 flex flex-col items-start gap-6"
```

`src/pages/Home.tsx:215-216` (drag & drop zone), current:
```tsx
              data-collider
              className="relative border-4 border-dashed p-10 md:p-12 flex flex-col items-start justify-center text-left transition-colors duration-200"
```
New:
```tsx
              data-collider
              className="relative border-4 border-dashed rounded-[var(--radius-panel)] p-10 md:p-12 flex flex-col items-start justify-center text-left transition-colors duration-200"
```

`src/pages/Home.tsx:281` (right-column sticky panel), current:
```tsx
          className="lg:sticky lg:top-24 border-2 p-6 flex flex-col gap-4"
```
New:
```tsx
          className="lg:sticky lg:top-24 border-2 rounded-[var(--radius-panel)] p-6 flex flex-col gap-4"
```

- [ ] **Step 3: Point the two semantic banners at the real warning/danger tokens**

Task 1 retinted `--accent-secondary` into a soft muted-blue decorative tone (see Task 1's
"Important correction" note) — it no longer carries a warning meaning, so the two banners in this
file that are genuinely warning/error states need to move to the dedicated tokens instead.

`src/pages/Home.tsx:120-125` (the same in-progress-warning block as Step 2 — its icon and text
colors, not just the border/background already fixed above), current:
```tsx
                <AlertTriangle size={20} className="shrink-0 mt-0.5" style={{ color: 'var(--accent-secondary)' }} />
                <div>
                  <p className="font-bold uppercase tracking-widest text-sm" style={{ color: 'var(--accent-secondary)' }}>
                    // UNFINISHED_WORK — STUDIO IN PROGRESS
                  </p>
                  <p className="text-xs font-mono uppercase opacity-60 mt-1" style={{ color: 'var(--accent-secondary)' }}>
```
New (three `var(--accent-secondary)` → `var(--color-warning)`, nothing else changes):
```tsx
                <AlertTriangle size={20} className="shrink-0 mt-0.5" style={{ color: 'var(--color-warning)' }} />
                <div>
                  <p className="font-bold uppercase tracking-widest text-sm" style={{ color: 'var(--color-warning)' }}>
                    // UNFINISHED_WORK — STUDIO IN PROGRESS
                  </p>
                  <p className="text-xs font-mono uppercase opacity-60 mt-1" style={{ color: 'var(--color-warning)' }}>
```

`src/pages/Home.tsx:262-266` (upload-failed error banner — this is an actual failure, so it maps
to `--color-danger`, not `--color-warning`), current:
```tsx
              className="flex items-start gap-3 border-2 p-4"
              style={{
                borderColor: 'var(--accent-secondary)',
                color: 'var(--accent-secondary)',
                backgroundColor: 'color-mix(in oklch, var(--accent-secondary) 8%, transparent)',
              }}
```
New:
```tsx
              className="flex items-start gap-3 border-2 rounded-[var(--radius-panel)] p-4"
              style={{
                borderColor: 'var(--color-danger)',
                color: 'var(--color-danger)',
                backgroundColor: 'color-mix(in oklch, var(--color-danger) 8%, transparent)',
              }}
```

- [ ] **Step 4: Verify — build, visual check both modes, confirm reduced-motion**

Run: `npm run build`. Visually confirm: Home's hero shows the drifting blooms/waves behind the
upload UI, panels have soft rounded corners, the input-type toggle buttons are pill-shaped, the
in-progress banner reads in a warning-amber tone and the upload-failure banner reads in a danger-
red tone (not the same muted blue as everything else), and enabling the OS reduced-motion setting
freezes the background completely. Confirm Player/Library/Login do NOT show any ambient background
(they don't import `AquaDrift`, so this should hold automatically — spot-check anyway).

- [ ] **Step 5: Commit**

```bash
git add src/pages/Home.tsx
git commit -m "Redesign: wire Aqua Drift into Home hero, round remaining containers"
```

---

### Task 7: Player.tsx, GeneratePanel.tsx, ArcEditor.tsx — radius and semantic color fixes

**Files:**
- Modify: `src/pages/Player.tsx`
- Modify: `src/components/player/GeneratePanel.tsx`
- Modify: `src/components/player/ArcEditor.tsx`

**Interfaces:**
- Consumes: `--radius-panel`, `--radius-chip`, `--color-danger` (Task 1). No `.brutal-card`-based
  containers in these three files need radius touching — they already inherit the new look from
  Task 2 — but two genuinely-semantic error/destructive-confirm color sites need repointing off the
  now-decorative `--accent-secondary` (see Task 1's "Important correction" note).

- [ ] **Step 1: Player.tsx — add radius to raw-bordered containers**

`src/pages/Player.tsx:482` (waveform panel), current:
```tsx
          <div data-collider className="border-4 p-4 h-64 relative overflow-hidden flex flex-col" style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}>
```
New:
```tsx
          <div data-collider className="border-4 rounded-[var(--radius-panel)] p-4 h-64 relative overflow-hidden flex flex-col" style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}>
```

`src/pages/Player.tsx:571` (prompt-version stepper's `w-16 h-16` square), current:
```tsx
                    className="w-16 h-16 border-4 flex flex-col items-center justify-center transition-colors"
```
New:
```tsx
                    className="w-16 h-16 border-4 rounded-[var(--radius-chip)] flex flex-col items-center justify-center transition-colors"
```

(`data-collider` blocks at lines 556, 598, 768 already use `className="brutal-card"` — no change
needed, Task 2 covers them.)

Also fix the genuinely-an-error describe-failure block, same reasoning as Task 6 Step 3 (Task 1
retinted `--accent-secondary` to a decorative muted-blue tone, so a real failure state needs the
dedicated `--color-danger` token instead). `src/pages/Player.tsx:661-669`, current:
```tsx
                              <p className="text-xs" style={{ color: 'var(--accent-secondary)' }}>
                                {describeError}
                              </p>
                              <button
                                onClick={retryDescribe}
                                className="text-[10px] font-bold uppercase tracking-widest border px-2 py-0.5 shrink-0 transition-colors"
                                style={{ borderColor: 'var(--accent-secondary)', color: 'var(--accent-secondary)' }}
                                onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent-secondary)'; e.currentTarget.style.color = 'var(--bg)'; }}
                                onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent-secondary)'; }}
```
New (five `var(--accent-secondary)` → `var(--color-danger)`):
```tsx
                              <p className="text-xs" style={{ color: 'var(--color-danger)' }}>
                                {describeError}
                              </p>
                              <button
                                onClick={retryDescribe}
                                className="text-[10px] font-bold uppercase tracking-widest border px-2 py-0.5 shrink-0 transition-colors"
                                style={{ borderColor: 'var(--color-danger)', color: 'var(--color-danger)' }}
                                onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--color-danger)'; e.currentTarget.style.color = 'var(--bg)'; }}
                                onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--color-danger)'; }}
```

Leave `src/pages/Player.tsx:653` (`// AI_ANALYZING_IMAGE...`, on `--accent-tertiary`) unchanged —
that's a neutral in-progress status message, not a warning or error, so the new muted-tonal-blue
value is correct for it as-is.

- [ ] **Step 2: GeneratePanel.tsx — fix the discard-confirm warning color**

The earlier grep found no `border-2`/`border-4`/`data-collider` matches in this file, but the plan-
writing color audit found one genuine semantic case that still needs fixing: the discard
confirmation is an irreversible destructive action (same category as Library's PURGE / Player's
DISCARD, both already mapped to `--color-danger` in Task 2), so it needs the same token instead of
the now-decorative `--accent-secondary`.

`src/components/player/GeneratePanel.tsx:138-148`, current:
```tsx
                <div
                  className="border-2 p-3 flex flex-col gap-2"
                  style={{ borderColor: 'var(--accent-secondary)', backgroundColor: 'color-mix(in oklch, var(--accent-secondary) 6%, transparent)' }}
                >
                  <p
                    className="flex items-start gap-2 text-xs font-bold uppercase tracking-widest"
                    style={{ color: 'var(--accent-secondary)' }}
                  >
                    <AlertTriangle size={14} className="shrink-0 mt-[1px]" aria-hidden="true" />
                    <span>Discard this audio? This can't be undone.</span>
                  </p>
```
New:
```tsx
                <div
                  className="border-2 rounded-[var(--radius-panel)] p-3 flex flex-col gap-2"
                  style={{ borderColor: 'var(--color-danger)', backgroundColor: 'color-mix(in oklch, var(--color-danger) 6%, transparent)' }}
                >
                  <p
                    className="flex items-start gap-2 text-xs font-bold uppercase tracking-widest"
                    style={{ color: 'var(--color-danger)' }}
                  >
                    <AlertTriangle size={14} className="shrink-0 mt-[1px]" aria-hidden="true" />
                    <span>Discard this audio? This can't be undone.</span>
                  </p>
```

Leave `src/components/player/GeneratePanel.tsx:192-198` ("APPLYING EFFECTS... / BROWSER RENDERING
— EFFECTS BAKING IN", on `--accent-secondary`) unchanged — like Player's `AI_ANALYZING_IMAGE`, this
is a neutral processing indicator, not a warning, so the new muted tonal-blue is correct here too.

- [ ] **Step 3: ArcEditor.tsx — add radius to raw-bordered containers**

`src/components/player/ArcEditor.tsx:46` (root container), current:
```tsx
    <div data-collider className="border-4 p-4 flex flex-col gap-3" style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}>
```
New:
```tsx
    <div data-collider className="border-4 rounded-[var(--radius-panel)] p-4 flex flex-col gap-3" style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}>
```

`src/components/player/ArcEditor.tsx:139` (nested sub-panel), current:
```tsx
                  className="border-2 p-3 flex flex-col gap-2"
```
New:
```tsx
                  className="border-2 rounded-[var(--radius-chip)] p-3 flex flex-col gap-2"
```

- [ ] **Step 4: Verify — build and visual check**

Run: `npm run build`. Visually check the Player page in both modes: waveform panel, prompt
stepper, and arc-editor panels all show soft rounded corners consistent with the rest of the app;
no ambient background motion appears anywhere on this page (per the Home-only scope rule). Trigger
a describe-image failure (or temporarily break the network tab) to confirm the error text/retry
button read in danger-red, not the same muted blue as neutral status text. Click DISCARD on a
generated song to confirm the confirm-dialog reads in danger-red too.

- [ ] **Step 5: Commit**

```bash
git add src/pages/Player.tsx src/components/player/ArcEditor.tsx src/components/player/GeneratePanel.tsx
git commit -m "Redesign: round remaining Player/ArcEditor containers, fix semantic error colors"
```

---

### Task 8: Library.tsx — radius and semantic color fixes

**Files:**
- Modify: `src/pages/Library.tsx`

**Interfaces:**
- Consumes: `--radius-panel`, `--radius-chip`, `--color-danger`, `--color-warning` (Task 1).
  `.brutal-card` usage in the tile grid already inherits the new look from Task 2, but the
  saved/unsaved tile-accent logic and two semantic warning sites need repointing off the now-
  decorative `--accent-secondary` (see Task 1's "Important correction" note).

- [ ] **Step 1: Add radius to raw-bordered containers, fix the fetch-error banner's color**

`src/pages/Library.tsx:225-228` (fetch-error banner — this is a genuine failure, so it moves to
`--color-danger`, same reasoning as every other error banner in this plan), current:
```tsx
      {fetchError && (
        <div className="border-2 p-4 font-mono text-sm uppercase" style={{ borderColor: 'var(--accent-secondary)', color: 'var(--accent-secondary)' }}>
          ERROR: {fetchError}
        </div>
      )}
```
New (also drops `font-mono uppercase` — this is body copy, not a CLI-flag label, so it should
render in the new `Geist` UI font like everything else rather than the old terminal voice):
```tsx
      {fetchError && (
        <div className="border-2 rounded-[var(--radius-panel)] p-4 text-sm" style={{ borderColor: 'var(--color-danger)', color: 'var(--color-danger)' }}>
          ERROR: {fetchError}
        </div>
      )}
```

`src/pages/Library.tsx:232` (empty-state drop hint — purely informational, not an error, stays on
the neutral `--border-muted`/`--text-muted` tokens it already uses), current:
```tsx
        <div className="border-4 border-dashed p-16 text-center" style={{ borderColor: 'var(--border-muted)', color: 'var(--text-muted)' }}>
```
New:
```tsx
        <div className="border-4 border-dashed rounded-[var(--radius-panel)] p-16 text-center" style={{ borderColor: 'var(--border-muted)', color: 'var(--text-muted)' }}>
```

`src/pages/Library.tsx:254-256` (tile grid card — already uses `.brutal-card` via the class list,
just needs the explicit `border-2` Tailwind class kept in sync visually; no change needed here
since `.brutal-card`'s own `border` shorthand from Task 2 Step 3 already wins the cascade — leave
this line as-is):
```tsx
                className={`border-2 brutal-card p-0 flex flex-col group ${isHero ? 'md:col-span-2' : ''}`}
```
(No change needed — listed for completeness. Tailwind v4's `@import "tailwindcss"` expands to
`@layer theme, base, components, utilities`, so the generated `border-2` utility lives inside the
`utilities` layer. `.brutal-card` in this same file is plain, unlayered CSS — and per the CSS
cascade spec, unlayered rules always beat layered rules regardless of source order or specificity.
So `.brutal-card`'s `border: 1px solid var(--border)` from Task 2 definitively wins over the
`border-2` utility's 2px width; the redundant class can stay without producing a visible 2px
border.)

- [ ] **Step 2: Fix the unsaved-tile accent and quality-warning icon**

`item.saved` currently drives a binary color choice between `--accent-tertiary` (saved) and
`--accent-secondary` (unsaved). The "unsaved" state is deliberately at-risk framing elsewhere in
this redesign (Home's unfinished-work banner, GeneratePanel's "UNSAVED — SAVE TO KEEP OR IT WILL
EXPIRE" — both now on `--color-warning`), and this file's own `isExpiringSoon` check at line 243-244
confirms unsaved tiles genuinely are time-limited — so for consistency the unsaved branch moves to
`--color-warning` too. The saved branch stays on `--accent-tertiary` (now a neutral muted-blue tone
— "already secure" reads correctly as calm, not urgent).

`src/pages/Library.tsx:257-270`, current:
```tsx
                style={{
                  borderColor: item.saved ? 'var(--accent-tertiary)' : 'var(--accent-secondary)',
                  boxShadow: item.saved
                    ? '-6px 6px 0 0 var(--accent-tertiary)'
                    : '-6px 6px 0 0 var(--accent-secondary)',
                }}
              >
                {/* HEADER */}
                <div
                  className="p-2 flex justify-between items-center"
                  style={{
                    backgroundColor: item.saved ? 'var(--accent-tertiary)' : 'var(--accent-secondary)',
                    color: 'var(--selected-text)',
                  }}
                >
```
New:
```tsx
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
```

`src/pages/Library.tsx:328-330` (quality-check "unsatisfactory" icon — a genuine quality warning),
current:
```tsx
                      {!isMidi && item.fad_verdict === 'unsatisfactory' && (
                        <AlertTriangle size={12} style={{ color: 'var(--accent-secondary)' }} aria-label="Quality check: unsatisfactory" />
                      )}
```
New:
```tsx
                      {!isMidi && item.fad_verdict === 'unsatisfactory' && (
                        <AlertTriangle size={12} style={{ color: 'var(--color-warning)' }} aria-label="Quality check: unsatisfactory" />
                      )}
```

`src/pages/Library.tsx:487-488` (the SAVE button on unsaved tiles — matches its own card's new
warning accent from Step 2 above, so the button visually belongs to the card it's attached to),
current:
```tsx
                      style={{ '--btn-c': 'var(--accent-secondary)' } as React.CSSProperties}
```
New:
```tsx
                      style={{ '--btn-c': 'var(--color-warning)' } as React.CSSProperties}
```

Leave every other `--accent-tertiary`/`--accent-secondary` usage in this file unchanged (the
`SYS_ARCHIVES` header divider at line 212-213, the loading state at line 220, the MIDI-download and
format-selector `--btn-c` assignments at lines 385/432/443) — those are neutral decorative or
status-indicator usages, not warnings, so the new muted tonal-blue values from Task 1 are correct
for them as-is.

- [ ] **Step 3: Verify — build and visual check**

Run: `npm run build`. Visually check Library in both modes: tile cards show soft rounded corners
and a proper `.brutal-card` border width (confirm per the note in Step 1 above), empty-state and
error banners are rounded, the fetch-error banner reads in danger-red, unsaved tiles (and their
SAVE button) read in warning-amber while saved tiles stay muted blue, and the quality-warning
triangle icon on any unsatisfactory item reads in warning-amber.

- [ ] **Step 4: Commit**

```bash
git add src/pages/Library.tsx
git commit -m "Redesign: round remaining Library containers, fix semantic warning/danger colors"
```

---

### Task 9: Login.tsx — radius touch-up and error-color fix

**Files:**
- Modify: `src/pages/Login.tsx`

**Interfaces:**
- Consumes: `--radius-panel`, `--color-danger` (Task 1).

- [ ] **Step 1: Add radius to the form container**

`src/pages/Login.tsx:58-62`, current:
```tsx
        <div
          data-collider
          className="border-4 p-8 md:p-10 flex flex-col gap-6"
          style={{ borderColor: 'var(--accent)', backgroundColor: 'transparent' }}
        >
```
New:
```tsx
        <div
          data-collider
          className="border-4 rounded-[var(--radius-panel)] p-8 md:p-10 flex flex-col gap-6"
          style={{ borderColor: 'var(--accent)', backgroundColor: 'transparent' }}
        >
```

(The `error` alert box at `src/pages/Login.tsx:103-119` uses `border-2` with no radius, AND is a
genuine login-failure error — same reasoning as every other error banner in this plan, it moves
off the now-decorative `--accent-secondary` to `--color-danger`.)

Current (`src/pages/Login.tsx:105-111`):
```tsx
                className="flex items-start gap-3 border-2 p-4"
                style={{
                  borderColor: 'var(--accent-secondary)',
                  color: 'var(--accent-secondary)',
                  backgroundColor: 'color-mix(in oklch, var(--accent-secondary) 8%, transparent)',
                }}
```
New:
```tsx
                className="flex items-start gap-3 border-2 rounded-[var(--radius-panel)] p-4"
                style={{
                  borderColor: 'var(--color-danger)',
                  color: 'var(--color-danger)',
                  backgroundColor: 'color-mix(in oklch, var(--color-danger) 8%, transparent)',
                }}
```

- [ ] **Step 2: Verify — build and visual check, including an actual failed login attempt**

Run: `npm run build`. Visually check the login form in both modes: soft-rounded panel and button
(button already inherits Task 2's pill style via `.brutal-btn`). Submit wrong credentials once to
confirm the error banner reads in danger-red, not the same muted blue as everything else. Then
submit valid credentials to confirm the auth flow itself still works end-to-end.

- [ ] **Step 3: Commit**

```bash
git add src/pages/Login.tsx
git commit -m "Redesign: round Login form container, fix error banner color"
```

---

### Task 10: Rewrite root `design.md` to document the new locked system

**Files:**
- Modify: `design.md` (full file, 177 lines — complete rewrite)

**Interfaces:**
- Consumes: nothing (documentation task, runs last so it can describe the actually-shipped state).

- [ ] **Step 1: Replace the entire file content**

Base the new `design.md` on `docs/superpowers/specs/2026-08-04-y2k-aqua-redesign-design.md`,
condensed into the same section shape the old `design.md` used (Genre / Theme / Typography /
Spacing / Motion / Microinteractions / CTA voice / Nav / Footer / Per-page allowances / What pages
MUST share / MAY differ / Exports), so future page work keeps reading this file first per its own
opening convention ("Every page redesign reads this file before emitting code"). Pull the exact
OKLCH values, radius tokens, and font names from `src/index.css` as it exists after Tasks 1-2 (not
re-derived from the spec's draft numbers) so the documentation matches the shipped code exactly.

- [ ] **Step 2: Verify — read the new file back once and confirm every token value matches `src/index.css`**

Cross-check each `--color-*`/`--radius-*`/`--font-*` value quoted in the new `design.md`'s
`## Exports` section against the actual current contents of `src/index.css`'s `:root, body.dark`
block — these must be byte-identical, since a stale/hand-typed value here would misdirect the next
page-level change.

- [ ] **Step 3: Commit**

```bash
git add design.md
git commit -m "Redesign: rewrite design.md to document the Y2K/Aqua-Chrome system"
```

---

## Post-implementation

Once all 10 tasks are complete and committed, the user asked for a follow-up judging pass using
the design skills (impeccable-core, design-taste-frontend, emil-design-eng) covering: visual
quality, practicality/ease of use, color readability in both modes, and an explicit comparison
against the old brutalist system. That is a separate activity from this plan — do not fold it into
any task above; run it after Task 10's commit as its own review pass.
