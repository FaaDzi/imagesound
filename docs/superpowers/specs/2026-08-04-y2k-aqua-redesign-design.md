# Design — ImageSound Y2K/Aqua-Chrome Redesign

> **Status:** Approved direction, pending user review of this document before implementation planning.
> **Supersedes:** `design.md` at the project root (the current brutalist-terminal system). On
> implementation, `design.md` should be rewritten to reflect this file — it is the new locked
> design system going forward, per the same "every page reads this file before emitting code"
> convention the old one established.
> **Scope:** Redesign — Overhaul (per impeccable-core / design-taste-frontend's redesign protocol).
> New visual language; existing routes, IA, copy voice, and functionality are preserved untouched.
> **App mode:** Operate (impeccable-core framework) — this is a task-completion tool (upload →
> generate → save), not a marketing site. Scanability and native-feeling consistency outrank
> decorative expression; the new identity lives in material/motion craft, not big display copy.

## Why this system, in one paragraph

The brutalist-terminal identity is being fully retired — user's own words: "brutalist... might not
stay." The replacement direction: semi-clean **Y2K aesthetic**, specifically an **"Aqua Chrome"**
material language (early Mac OS X-era glossy glass/chrome — cool blue-grey, pillowy buttons with a
bevel highlight), with the app's existing physics-toy shapes (circle/square/triangle) reimagined as
an ambient **motion background** rather than a structural/iconography system. This was arrived at
through several rounds of visual iteration (documented in `.superpowers/brainstorm/` for this
session) and cross-checked against three anti-slop skills at each step — see "Anti-slop discipline
applied" below for what specifically got caught and fixed.

## Genre

Still **atmospheric** in the sense of "one deliberate signature visual moment," but the *material*
changes completely — from CRT-terminal hard-edged to Aqua-Chrome soft-glass. Genre classification
stays Operate-mode per impeccable-core: brand lives in precise component-level details (button
bevels, glow treatment, corner-radius consistency), not in landing-page-style hero rhetoric.

## Anti-slop discipline applied

Cross-checked against design-taste-frontend, Hallmark (`anti-patterns.md`, `color.md`, `motion.md`),
and emil-design-eng at each iteration. What got caught and fixed during brainstorming, so the
implementer doesn't reintroduce these:

- **First pass had literal "floating-orb decoration"** (Hallmark's named tell — generic ambient
  shapes "for depth," no semantic role). Fixed by giving the motif real meaning (same shape family
  as the actual physics toy) and hard restraint (2 shapes max, confined to one sanctioned surface —
  see "Signature ambient motif" below), not by cutting the idea the user explicitly asked for.
- **"Holo Pop" direction (iridescent pink/lavender/mint) was rejected early** — too close to
  Hallmark's banned "aurora-blob" pattern (purple-to-pink-to-cyan mesh blobs). Aqua Chrome's cool
  blue-grey doesn't have this problem.
- **Dark mode does NOT swap accent hue.** Hallmark's color.md is explicit: "Never switch the hue
  between modes... Only lightness and chroma move." Dark mode instead gets more chroma + an actual
  glow/bloom treatment on the same hue — moodier, not different. This also happens to be more
  accurate to the real PSP-wave reference (glowing blue on black) than an arbitrary second color
  would have been.
- **Shapes were originally crisp cutouts sitting on top of the soft wave background** — a
  materiality mismatch (two different edge languages in one frame) that read as "background +
  stickers pasted on it," not one system. Fixed by blurring the shapes into the same soft-glow
  family as the waves (see "Signature ambient motif").
- **Motion stayed capped even after the "more character" request** — glossy highlight/chrome depth
  was added, but shape count and drift speed were deliberately NOT increased at the same time (that
  wasn't the complaint). Speed was bumped later, once, by request (~35%), and stays inside Hallmark's
  motion.md guidance: infinite ambient loops are banned as a *scattered default*, not as a single,
  restrained, `prefers-reduced-motion`-gated signature motif confined to one surface.
- **Corner-radius, typography, and one-accent-hue rules** (Hallmark color.md "one accent, max two" /
  "Shape Consistency Lock" from design-taste-frontend) are treated as hard constraints in every
  section below, not just the hero.

## Theme — palette

OKLCH throughout. Single anchor hue **230°** (cool blue) for both modes — never switched, only
lightness/chroma move between light and dark, per the discipline above.

```css
:root {
  /* Light mode */
  --color-paper:        oklch(97% 0.008 230);
  --color-paper-2:       oklch(94% 0.010 230);
  --color-paper-elevated: oklch(99% 0.006 230);  /* elevation = lighter, not shadowed */
  --color-rule:          oklch(85% 0.008 230);
  --color-ink:            oklch(20% 0.012 230);
  --color-ink-muted:      oklch(45% 0.010 230);
  --color-accent:         oklch(58% 0.17 230);
  --color-accent-glow:    oklch(58% 0.17 230 / 0.35);  /* button/shape bevel glow, light mode */
  --color-focus:          oklch(55% 0.19 230);
  --color-warning:        oklch(70% 0.16 70);   /* kept off the main hue for a real semantic signal */
  --color-danger:         oklch(58% 0.20 25);
}

[data-theme="dark"] {
  --color-paper:        oklch(13% 0.014 230);
  --color-paper-2:       oklch(17% 0.016 230);   /* elevation = +~3% lightness per step */
  --color-paper-elevated: oklch(21% 0.018 230);
  --color-rule:          oklch(30% 0.012 230);
  --color-ink:            oklch(94% 0.008 230);
  --color-ink-muted:      oklch(68% 0.010 230);
  --color-accent:         oklch(55% 0.19 230);   /* +chroma, glow-driven, same hue as light */
  --color-accent-glow:    oklch(55% 0.19 230 / 0.5);
  --color-focus:          oklch(65% 0.19 230);
  --color-warning:        oklch(72% 0.15 70);
  --color-danger:         oklch(62% 0.20 25);
}
```

**Contrast targets** (Hallmark color.md, WCAG 2.1): body text 4.5:1 minimum / 7:1 target against
`--color-paper`; large text 3:1 minimum / 4.5:1 target. `--color-ink` / `--color-ink-muted` against
both paper values must be verified at implementation time with the browser's contrast checker before
shipping — this is the user's explicit hard requirement ("readable text color and easy on the eyes"
in both modes), not a nice-to-have.

**Bans carried from the anti-slop skills:** no pure `#000`/`#fff` anywhere (both paper values are
tinted); one accent hue only, never more than ~5% of any viewport as a background fill (buttons are
the only large accent-filled elements, and they're small); no purple-to-cyan/purple-to-pink
gradients; no gradient-fill text on headings.

## Typography

New pairing — the old system's Geist Mono (body/UI) was specifically chosen for terminal/brutalist
voice and now clashes with the soft-glass aesthetic.

- **Display:** Syne — kept. It's a distinctive geometric face, not inherently brutalist; no reason
  to change what already works.
- **Body / UI:** replaces Geist Mono. Use **Geist Sans** (same type family as the kept mono, so the
  transition is a within-family swap, not a totally new vendor) — clean rounded-humanist, reads
  naturally at UI sizes, pairs cleanly with Syne's geometric display voice. If Geist Sans isn't
  already available as a project dependency, confirm the install at implementation time
  (`next/font` or self-hosted `@font-face`, per design-taste-frontend's font-loading discipline —
  never a runtime Google Fonts `<link>`).
- **2+1 rule maintained:** two families total (Syne + Geist Sans), monospace usage (if any survives
  for genuinely tabular data, e.g. duration timestamps) is a deliberate third-family exception, not
  a general UI voice anymore.

## Shape / corner-radius system

**Soft throughout — one consistent scale, zero sharp corners anywhere.** Full break from the old
system's `--radius: 0px`.

```css
:root {
  --radius-pill: 999px;   /* buttons, chips, pills, toggle controls */
  --radius-panel: 14px;   /* cards, panels, containers, modals */
  --radius-chip: 9px;     /* small tags, badges, inline controls */
}
```

Shape-Consistency-Lock discipline (design-taste-frontend): every interactive/container element maps
to exactly one of these three, no ad-hoc radius values anywhere in the implementation.

## Signature ambient motif — "Aqua Drift"

The one sanctioned enrichment element, following the same restraint discipline the old design.md
used for its waveform motif ("appears twice, not used anywhere else").

**Composition:** 2 large soft-blurred glow blooms (abstracted from the physics-toy's circle/blob
shapes — deliberately NOT crisp/recognizable as literal shapes here, since blurring them enough to
match the wave layer's softness dissolves sharp edges anyway) + 3 horizontal drifting wave bands
(referencing the Sony PSP XMB wave background — a specific, real, named reference, not a generic
effect). Both layers share the same accent hue and a `filter: blur()` treatment so they read as one
atmospheric system, not background-plus-stickers.

**Where it appears — Home hero ONLY.** Per the anti-slop "signature motif, not everywhere" rule and
explicit user confirmation: the Studio (Player) and Library are task screens where ambient motion
would become visual noise during actual work; Login is a quick functional gate. Only Home's hero
gets the full treatment.

**Reference implementation (light mode; dark mode mirrors with the dark tokens above and higher
opacity/glow per the palette section):**

```css
.aqua-drift-panel {
  position: relative;
  overflow: hidden;
}
.aqua-drift-bloom {
  position: absolute;
  filter: blur(22px);
  opacity: 0.45;
  animation: aqua-drift 10s var(--ease-in-out) infinite;
}
.aqua-drift-wave {
  position: absolute;
  left: -10%;
  width: 120%;
  height: 70px;
  border-radius: 50%;
  filter: blur(18px);
  opacity: 0.5;
  animation: aqua-wave 12s var(--ease-in-out) infinite;
}
@keyframes aqua-drift {
  0%, 100% { transform: translate(0, 0); }
  50%      { transform: translate(14px, -10px); }
}
@keyframes aqua-wave {
  0%, 100% { transform: translateX(-20px); }
  50%      { transform: translateX(20px); }
}
@media (prefers-reduced-motion: reduce) {
  .aqua-drift-bloom, .aqua-drift-wave { animation: none; }
}
```

Two bloom instances + three wave instances, staggered durations (10s/12s for blooms;
12s/15s/17s for waves) so they don't move in visible lockstep. `prefers-reduced-motion: reduce`
disables all motion outright (this is ambient/decorative, not functional — safe to fully stop, same
reasoning the old design.md used for its waveform idle animation).

## Buttons — Aqua Chrome material

Primary CTA voice, replacing `brutal-btn` (hard border, offset shadow, translate-on-press, zero
radius, uppercase).

- Shape: `--radius-pill` (999px), never uppercase-mandatory anymore (soft/glass voice doesn't need
  the shouty CTA voice the brutalist system used).
- Fill: linear-gradient from a lighter tint of `--color-accent` to `--color-accent` itself
  (~160deg), giving the bevel/chrome look without a second hue.
- Highlight: `inset 0 1px 0 <light tint>` for the specular glass-edge detail — a crafted, positioned
  detail (period-accurate to real Aqua UI), not a generic gradient wash. This is the one place a
  "glossy" treatment is intentional and skill-approved, per the anti-slop cross-check during
  brainstorming.
- Press/hover states: still need full interaction-state coverage per emil-design-eng (`:active`
  scale ~0.97, `:hover` subtle lift/brighten, `:focus-visible` ring at ≥3:1 contrast appearing
  instantly — never animated in) — exact values to be finalized in the implementation plan, not
  guessed here.

## Nav

Full redesign, replacing the "N8 Terminal Command" CLI-flag nav (`> imagesound --upload --studio
--library▮`, monospace, blinking caret) per explicit user confirmation that it should match the new
soft system rather than stay as a contrast piece.

- Wordmark stays (glitch effect, Syne, unchanged — the one identity element that survives untouched).
- Route items become pill-shaped nav controls (`--radius-pill`), active route indicated by a soft
  filled/glowing pill state using `--color-accent`, not underline+CLI-flag styling.
- No more blinking caret, no more `>` prompt glyph, no more `--flag` naming convention for routes.
- Height and single-line-at-desktop constraints carry over unchanged (existing responsive
  discipline, not something this redesign touches).

## Footer

The old "Ft2 Inline single line" terminal-styled status strip should get the same soft treatment as
Nav — thin hairline rule using `--color-rule`, body text in Geist Sans instead of mono. Not a
priority surface (low visual weight already); exact treatment can be finalized at implementation
time following the same token system as everything else.

## Motion — interactive (non-ambient)

Standard emil-design-eng / Hallmark motion discipline applies everywhere outside the ambient motif:
`transform`/`opacity` only, exponential ease-out for enters
(`cubic-bezier(0.16, 1, 0.3, 1)`), 100-300ms for UI-scale interactions, `prefers-reduced-motion`
respected throughout. This is unchanged from the existing project convention (already established in
`index.css`'s `--ease-out`/`--dur-*` tokens) — the new system reuses those, doesn't reinvent them.

## Per-page allowances

- **Home** — gets the full Aqua Drift ambient hero treatment (the one sanctioned surface).
- **Player (Studio)** — clean Aqua-Chrome material (soft panels, pill buttons, new palette/type) but
  NO ambient background motion — it's a task screen, motion would compete with the waveform's own
  functional animation.
- **Library** — same material system, static (matches the old system's "Library MUST NOT use
  enrichment" rule — the content carries the page, not decoration).
- **Login** — clean functional gate, same material system, no ambient motion.

## What pages MUST share

- The wordmark (unchanged).
- The palette tokens above (one accent hue, tinted paper, never pure black/white).
- Geist Sans / Syne pairing.
- The three-value corner-radius scale.
- The new pill-button CTA voice.
- Nav and footer (shared shell, redesigned per above).

## What pages MAY differ on

- Whether the Aqua Drift ambient motif is present (Home only, per above).
- Content-specific layout (Library's tile grid vs. Player's panel layout) — unchanged from the
  existing macrostructure choices; this redesign is a material/visual-language change, not an IA
  change.

## Open items for the implementation plan (not decided here)

- Exact hover/active/focus pixel values for the new pill-button system.
- Whether any monospace usage survives for genuinely tabular content (timestamps, durations).
- Full component-by-component token audit (every hardcoded color/radius currently in the codebase
  needs to be found and mapped to the new tokens — mirrors the original design.md's implementation
  scope).
- Confirming Geist Sans is available as a dependency or needs installing.
