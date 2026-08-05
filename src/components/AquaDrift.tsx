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
