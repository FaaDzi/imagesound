import React from 'react';

// Drag-to-adjust intensity-arc bar UI. Renders one bar per chunk (30s each);
// dragging vertically sets that chunk's intensity, and preset buttons snap
// the whole arc to a named shape.
//
// NOTE: there is a known, reported bug here — dragging one bar can sometimes
// change a different bar instead. This split intentionally preserves that
// bug exactly as it behaved in Player.tsx; do not fix it as part of this
// component extraction. The pointer handlers (and the ref-based index-lock
// that the bug lives in) stay in Player.tsx and are passed down as props —
// only the presentational bars/labels/preset-buttons JSX moved here.

// Arc preset shapes: 4 intensity points (0–100) for 4 chunks.
// Sampled to N points when duration produces fewer than 4 chunks.
export const ARC_PRESETS: { id: string; label: string; points: [number, number, number, number] }[] = [
  { id: 'steady',          label: 'STEADY',    points: [50, 50, 50, 50] },
  { id: 'gentle_build',    label: 'GENTLE',    points: [25, 45, 68, 88] },
  { id: 'rise_and_settle', label: 'RISE+FADE', points: [30, 65, 88, 50] },
  { id: 'calm_energetic',  label: 'CALM→FULL', points: [15, 40, 70, 95] },
];

// Sample a 4-point preset curve to exactly n points via linear interpolation.
export function samplePreset(points: [number, number, number, number], n: number): number[] {
  if (n <= 1) return [points[0]];
  if (n >= 4) return [...points];
  return Array.from({ length: n }, (_, i) => {
    const t  = (i / (n - 1)) * 3;
    const lo = Math.min(3, Math.floor(t));
    const hi = Math.min(3, Math.ceil(t));
    const f  = t - lo;
    return Math.round(points[lo] * (1 - f) + points[hi] * f);
  });
}

export interface ArcEditorProps {
  segments: number[];
  numChunks: number;
  activePresetId: string;
  barRef: React.RefObject<HTMLDivElement>;
  onApplyPreset: (presetId: string) => void;
  onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => void;
  onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => void;
  onPointerUp: (e: React.PointerEvent<HTMLDivElement>) => void;
  onPointerCancel: (e: React.PointerEvent<HTMLDivElement>) => void;
}

export function ArcEditor({
  segments, numChunks, activePresetId, barRef,
  onApplyPreset, onPointerDown, onPointerMove, onPointerUp, onPointerCancel,
}: ArcEditorProps) {
  return (
    <div className="flex flex-col h-full pt-7">
      {/* Draggable segmented bars */}
      <div
        ref={barRef}
        className="flex-grow flex gap-[3px] cursor-ns-resize select-none"
        style={{ touchAction: 'none' }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
      >
        {segments.map((intensity, i) => {
          // Map 0–100 intensity to 8–95% fill height so bars are always visible.
          const fillPct = 8 + (intensity / 100) * 87;
          return (
            <div key={i} className="flex-1 relative h-full">
              {/* Track background */}
              <div className="absolute inset-0" style={{ backgroundColor: 'var(--accent)', opacity: 0.1 }} />
              {/* Filled portion */}
              <div
                className="absolute bottom-0 left-0 right-0"
                style={{ height: `${fillPct}%`, backgroundColor: 'var(--accent)', opacity: 0.82 }}
              />
            </div>
          );
        })}
      </div>

      {/* Chunk labels below bars */}
      <div className="flex gap-[3px] mt-1 shrink-0">
        {segments.map((_, i) => (
          <div key={i} className="flex-1 text-center text-[8px] font-mono" style={{ color: 'var(--accent)', opacity: 0.4 }}>
            C{i + 1}
          </div>
        ))}
      </div>

      {/* Preset buttons */}
      <div className="flex gap-1 mt-2 shrink-0">
        {ARC_PRESETS.map(preset => {
          const active = activePresetId === preset.id;
          return (
            <button
              key={preset.id}
              onClick={() => onApplyPreset(preset.id)}
              className="flex-1 border py-1 text-[9px] font-bold uppercase tracking-wide transition-colors"
              style={{
                borderColor: 'var(--accent)',
                backgroundColor: active ? 'var(--accent)' : 'transparent',
                color: active ? 'var(--selected-text)' : 'var(--accent)',
                opacity: active ? 1 : 0.5,
              }}
            >
              {preset.label}
            </button>
          );
        })}
      </div>

      {/* Info line */}
      <div className="flex justify-between items-center mt-1 shrink-0">
        <span className="text-[9px] font-mono uppercase" style={{ color: 'var(--accent)', opacity: 0.38 }}>
          drag bars · click preset · height = intensity
        </span>
        <span className="text-[9px] font-mono uppercase" style={{ color: 'var(--accent)', opacity: 0.38 }}>
          {numChunks} &times; 30s
        </span>
      </div>
    </div>
  );
}
