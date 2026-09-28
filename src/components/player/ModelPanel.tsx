import React from 'react';
import type { ModelInfo, ModelOption } from '../../api';
import { optionApplies, type ModelSelection } from '../../hooks/useModelSelection';
import { Hint, Muted, splitFirstSentence } from './Hint';
import { useAuth } from '../../context/AuthContext';

// Model picker + everything that depends on the selected model: valid song
// lengths, how a reference song is used, and the model's own options. Renders
// purely from the /models response (see useModelSelection) -- adding a model
// to pipeline/models.json needs no change here.

// Display text for reference modes the app knows about. An unrecognised mode a
// future model declares still works: it just shows its raw id.
const REFERENCE_MODE_TEXT: Record<string, { label: string; hint: string }> = {
  cover: { label: 'REMIX', hint: 'Keeps the melody and structure of the reference, restyled by your description.' },
  style: { label: 'STYLE ONLY', hint: "Borrows the reference's sound; the melody is new." },
};

const DURATION_CANDIDATES = [5, 8, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 600];
const MAX_DURATION_BUTTONS = 8;

// Preset lengths inside the model's range, thinned evenly if there are too
// many to fit on one row.
function durationPresets(min: number, max: number): number[] {
  const inRange = DURATION_CANDIDATES.filter(d => d >= min && d <= max);
  if (inRange.length <= MAX_DURATION_BUTTONS) return inRange;
  const picked = Array.from({ length: MAX_DURATION_BUTTONS }, (_, i) =>
    inRange[Math.round((i * (inRange.length - 1)) / (MAX_DURATION_BUTTONS - 1))]);
  return [...new Set(picked)];
}

const ACCENT = 'var(--accent-secondary)';

function ToggleButton({ active, onClick, disabled, title, children }: {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="flex-1 border py-1.5 min-h-[40px] sm:min-h-0 text-xs font-bold uppercase tracking-wider transition-colors"
      style={{
        borderColor: ACCENT,
        backgroundColor: active && !disabled ? ACCENT : 'transparent',
        color: active && !disabled ? 'var(--bg)' : ACCENT,
        opacity: disabled ? 0.35 : active ? 1 : 0.55,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >
      {children}
    </button>
  );
}

function SectionHeader({ title, aside }: { title: string; aside?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between mb-2">
      <span className="text-xs font-bold uppercase tracking-widest" style={{ color: ACCENT }}>{title}</span>
      {aside && <Muted>{aside}</Muted>}
    </div>
  );
}

function OptionControl({ option, value, onChange }: {
  option: ModelOption;
  value: boolean | number | string;
  onChange: (v: boolean | number | string) => void;
}) {
  return (
    <div className="border-t pt-5" style={{ borderTopColor: ACCENT }}>
      <SectionHeader
        title={option.label}
        aside={option.type === 'number' ? String(value) : undefined}
      />
      {option.type === 'bool' && (
        <div className="flex gap-1">
          <ToggleButton active={value === true} onClick={() => onChange(true)}>on</ToggleButton>
          <ToggleButton active={value === false} onClick={() => onChange(false)}>off</ToggleButton>
        </div>
      )}
      {option.type === 'number' && (
        <input
          type="range"
          className="w-full"
          style={{ accentColor: ACCENT }}
          min={option.min}
          max={option.max}
          step={option.step ?? 1}
          value={Number(value)}
          onChange={e => onChange(Number(e.target.value))}
          aria-label={option.label}
        />
      )}
      {option.type === 'choice' && (
        <div className="flex gap-1">
          {option.choices.map(c => {
            const locked = option.locked_choices?.[c];
            return (
              <ToggleButton key={c} active={value === c} disabled={!!locked} title={locked} onClick={() => onChange(c)}>
                {c}
                {locked && (
                  <span className="block text-[8px] font-normal normal-case tracking-normal leading-tight mt-0.5 opacity-80">
                    locked
                  </span>
                )}
              </ToggleButton>
            );
          })}
        </div>
      )}
      {/* For a choice, what the *selected* option does matters more than the
          general note, so it goes first and the general note is the tail. */}
      {option.type === 'choice' && option.choice_help?.[String(value)] && (() => {
        const [first, rest] = splitFirstSentence(option.choice_help[String(value)]);
        const more = [rest, option.help].filter(Boolean).join(' ');
        return (
          <Hint more={more || undefined}>
            <strong className="uppercase">{String(value)}</strong> — {first}
          </Hint>
        );
      })()}
      {option.help && !(option.type === 'choice' && option.choice_help?.[String(value)]) && (() => {
        const [first, rest] = splitFirstSentence(option.help);
        return <Hint more={rest || undefined}>{first}</Hint>;
      })()}
    </div>
  );
}

function modelUnusableReason(m: ModelInfo, wantsReference: boolean): string | null {
  if (!m.available) return m.unavailable_reason ?? 'Not available.';
  if (wantsReference && !(m.capabilities.reference?.length)) return "Can't remix an existing song.";
  return null;
}

export function ModelPanel({ selection, wantsReference }: { selection: ModelSelection; wantsReference: boolean }) {
  const { loading, error, models, model, usable, selectModel, duration, setDuration,
          referenceMode, setReferenceMode, optionValues, setOptionValue } = selection;

  // Logged out, /models is refused and the page sits under the login overlay:
  // no point showing that failure through it.
  const { username } = useAuth();
  const range = model?.capabilities.duration;
  const presets = range ? durationPresets(range.min, range.max) : [];
  const referenceModes = model?.capabilities.reference ?? [];
  const allOptions = model?.options ?? [];
  const visibleOptions = allOptions.filter(o => optionApplies(o, referenceMode, optionValues, allOptions));

  return (
    <>
      {/* GENERATION LENGTH — range comes from the selected model */}
      <div>
        <SectionHeader title="GENERATION LENGTH" aside={`${duration}s selected`} />
        <div className="flex gap-1">
          {presets.map(d => (
            <ToggleButton key={d} active={duration === d} onClick={() => setDuration(d)}>{d}s</ToggleButton>
          ))}
        </div>
        {range && <Hint>{range.min}–{range.max}s for {model?.label} · longer = slower</Hint>}
      </div>

      {/* MODEL */}
      <div className="border-t pt-5" style={{ borderTopColor: ACCENT }}>
        <SectionHeader title="MODEL" aside={wantsReference ? 'Remix-capable models only' : undefined} />
        {loading && <Hint>loading models…</Hint>}
        {error && username && (
          <p className="text-xs font-mono" style={{ color: 'var(--color-danger)' }}>{error}</p>
        )}
        {!loading && !error && (
          <>
            <div className="flex gap-1 flex-wrap">
              {models.map(m => {
                const reason = modelUnusableReason(m, wantsReference);
                return (
                  <ToggleButton
                    key={m.id}
                    active={model?.id === m.id}
                    disabled={!usable(m)}
                    title={reason ?? m.description}
                    onClick={() => selectModel(m.id)}
                  >
                    {m.label}
                    {reason && (
                      <span className="block text-[8px] font-normal normal-case tracking-normal leading-tight mt-0.5 opacity-80">
                        {m.available ? 'no remix' : 'not installed'}
                      </span>
                    )}
                  </ToggleButton>
                );
              })}
            </div>
            {model
              ? <Hint>{model.description}</Hint>
              : <p className="text-xs font-mono mt-2" style={{ color: 'var(--color-danger)' }}>
                  No usable model. {models[0] ? modelUnusableReason(models[0], wantsReference) : 'None are configured.'}
                </p>}
          </>
        )}
      </div>

      {/* REFERENCE MODE — only when the source is an existing song */}
      {wantsReference && referenceModes.length > 0 && (
        <div className="border-t pt-5" style={{ borderTopColor: ACCENT }}>
          <SectionHeader title="HOW TO USE THE REFERENCE" />
          <div className="flex gap-1">
            {referenceModes.map(mode => (
              <ToggleButton key={mode} active={referenceMode === mode} onClick={() => setReferenceMode(mode)}>
                {REFERENCE_MODE_TEXT[mode]?.label ?? mode}
              </ToggleButton>
            ))}
          </div>
          {referenceMode && REFERENCE_MODE_TEXT[referenceMode] && <Hint>{REFERENCE_MODE_TEXT[referenceMode].hint}</Hint>}
        </div>
      )}

      {/* MODEL-SPECIFIC OPTIONS */}
      {visibleOptions.map(o => (
        <OptionControl
          key={o.key}
          option={o}
          value={optionValues[o.key] ?? o.default}
          onChange={v => setOptionValue(o.key, v)}
        />
      ))}
    </>
  );
}
