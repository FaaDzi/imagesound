import React from 'react';
import { useAudioEffects } from '../../hooks/useAudioEffects';

// Local slider helper — only used within the effects panel.
function EffectSlider({
  label, value, min, max, step, display, onChange,
}: {
  label:   string;
  value:   number;
  min:     number;
  max:     number;
  step:    number;
  display: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-0.5">
        <span className="text-[10px] font-bold uppercase tracking-wide" style={{ color: 'var(--accent-secondary)' }}>
          {label}
        </span>
        <span className="text-[10px] font-mono" style={{ color: 'var(--accent-secondary)', opacity: 0.65 }}>
          {display(value)}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full"
        style={{ accentColor: 'var(--accent-secondary)' }}
      />
    </div>
  );
}

// EQ/compression/reverb sliders — the live Web Audio effects chain UI.
// Purely presentational; all state and Web Audio wiring lives in the
// useAudioEffects hook, whose full return value is passed straight through.
export interface EffectsPanelProps {
  effects: ReturnType<typeof useAudioEffects>;
}

export function EffectsPanel({ effects }: EffectsPanelProps) {
  return (
    <div className="border-t pt-4" style={{ borderTopColor: 'var(--accent-secondary)' }}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent-secondary)' }}>
          EFFECTS
        </span>
        {effects.effectsAvailable ? (
          <button
            onClick={effects.resetEffects}
            className="text-[9px] font-bold uppercase px-1.5 py-0.5 border transition-colors"
            style={{ borderColor: 'var(--accent-secondary)', color: 'var(--accent-secondary)' }}
            onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--accent-secondary)'; e.currentTarget.style.color = 'var(--bg)'; }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = 'var(--accent-secondary)'; }}
          >
            RESET
          </button>
        ) : (
          <span className="text-[9px] font-mono uppercase" style={{ color: 'var(--accent-secondary)', opacity: 0.4 }}>
            UNAVAILABLE
          </span>
        )}
      </div>

      {!effects.effectsAvailable ? (
        <p className="text-[10px] font-mono uppercase" style={{ color: 'var(--accent-secondary)', opacity: 0.5 }}>
          // WEB AUDIO SETUP FAILED — CHECK BROWSER CONSOLE
        </p>
      ) : (
        <div className="flex flex-col gap-2.5">
          {/* GAIN */}
          <EffectSlider
            label="GAIN"
            value={effects.params.gain}
            min={0} max={2} step={0.01}
            display={v => `${Math.round(v * 100)}%`}
            onChange={v => effects.updateParam('gain', v)}
          />

          {/* EQ */}
          <div>
            <p className="text-[9px] font-bold uppercase tracking-widest mb-1.5" style={{ color: 'var(--accent-secondary)', opacity: 0.6 }}>EQ</p>
            <div className="flex flex-col gap-1.5">
              <EffectSlider label="LOW"  value={effects.params.eqLow}  min={-12} max={12} step={0.5}
                display={v => `${v > 0 ? '+' : ''}${v.toFixed(1)}dB`} onChange={v => effects.updateParam('eqLow', v)} />
              <EffectSlider label="MID"  value={effects.params.eqMid}  min={-12} max={12} step={0.5}
                display={v => `${v > 0 ? '+' : ''}${v.toFixed(1)}dB`} onChange={v => effects.updateParam('eqMid', v)} />
              <EffectSlider label="HIGH" value={effects.params.eqHigh} min={-12} max={12} step={0.5}
                display={v => `${v > 0 ? '+' : ''}${v.toFixed(1)}dB`} onChange={v => effects.updateParam('eqHigh', v)} />
            </div>
          </div>

          {/* COMPRESSION */}
          <div>
            <p className="text-[9px] font-bold uppercase tracking-widest mb-1.5" style={{ color: 'var(--accent-secondary)', opacity: 0.6 }}>COMP</p>
            <div className="flex flex-col gap-1.5">
              <EffectSlider label="THRESH" value={effects.params.compThreshold} min={-60} max={0} step={1}
                display={v => `${v}dB`} onChange={v => effects.updateParam('compThreshold', v)} />
              <EffectSlider label="RATIO"  value={effects.params.compRatio}     min={1}   max={20} step={0.5}
                display={v => `${v.toFixed(1)}:1`} onChange={v => effects.updateParam('compRatio', v)} />
            </div>
          </div>

          {/* REVERB */}
          <EffectSlider
            label="REVERB"
            value={effects.params.reverbMix}
            min={0} max={1} step={0.01}
            display={v => `${Math.round(v * 100)}%`}
            onChange={v => effects.updateParam('reverbMix', v)}
          />
        </div>
      )}

      <p className="text-[9px] font-mono uppercase mt-2" style={{ color: 'var(--accent-secondary)', opacity: 0.4 }}>
        {effects.effectsAvailable
          ? '// live preview · same engine renders the download'
          : '// effects activate when you play a song'}
      </p>
    </div>
  );
}
