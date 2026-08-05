import React from 'react';
import { Download, Zap, Save, X, Check, AlertTriangle } from 'lucide-react';
import { downloadSong, DOWNLOAD_FORMATS, DownloadFormat } from '../../api';
import { UseGenerationReturn } from '../../hooks/useGeneration';
import { useAudioEffects, effectsAreNeutral } from '../../hooks/useAudioEffects';

// The generate/save/discard/download state machine and its buttons — the
// "[ GENERATE_SONG ]" card. Renders one of four mutually-exclusive states
// (in-flight, done, failed, idle) driven entirely by `generation.phase`.
export interface GeneratePanelProps {
  generation: UseGenerationReturn;
  effects: ReturnType<typeof useAudioEffects>;
  isSaving: boolean;
  isDiscarding: boolean;
  saveConfirmed: boolean;
  discardConfirmPending: boolean;
  showFormatPicker: boolean;
  setShowFormatPicker: React.Dispatch<React.SetStateAction<boolean>>;
  downloadFormat: DownloadFormat;
  setDownloadFormat: React.Dispatch<React.SetStateAction<DownloadFormat>>;
  // Idle "GENERATE SONG" button disabled condition: describeLoading || (!history.draftText.trim() && !fileId)
  generateDisabled: boolean;
  generateDisabledTitle?: string;
  onGenerate: () => void;       // idle button + failed-state retry button
  onCancel: () => void;
  onSave: () => void;
  onDiscardClick: () => void;
  onDiscardConfirm: () => void;
  onDiscardCancel: () => void;
  onGenerateAgain: () => void;  // clearItem(); generation.reset();
}

export function GeneratePanel({
  generation, effects, isSaving, isDiscarding, saveConfirmed, discardConfirmPending,
  showFormatPicker, setShowFormatPicker, downloadFormat, setDownloadFormat,
  generateDisabled, generateDisabledTitle, onGenerate, onCancel, onSave, onDiscardClick, onDiscardConfirm,
  onDiscardCancel, onGenerateAgain,
}: GeneratePanelProps) {
  const isGenerating =
    generation.phase === 'submitting' ||
    generation.phase === 'queued' ||
    generation.phase === 'loading_model' ||
    generation.phase === 'processing';

  return (
    <div data-collider className="border-4 rounded-[var(--radius-panel)] p-4 flex flex-col gap-3" style={{ borderColor: 'var(--accent)', backgroundColor: 'var(--bg)' }}>
      <div className="flex items-center justify-between">
        <h4 className="font-bold uppercase tracking-widest text-sm" style={{ color: 'var(--accent)' }}>
          [ GENERATE_SONG ]
        </h4>
        {generation.jobId && (
          <span className="text-[10px] font-mono opacity-40" style={{ color: 'var(--accent)' }}>
            JOB:{generation.jobId.slice(0, 8)}
          </span>
        )}
      </div>

      {/* IN-FLIGHT */}
      {isGenerating && (
        <div className="flex flex-col gap-2">
          <p className="text-sm font-mono uppercase animate-pulse" style={{ color: 'var(--accent)' }}>
            {generation.phase === 'submitting'
              ? '// QUEUING...'
              : generation.phase === 'queued'
              ? generation.queueDepth != null && generation.queueDepth > 1
                ? `// QUEUED — ${generation.queueDepth} JOBS WAITING`
                : '// QUEUED — NEXT UP'
              : generation.phase === 'loading_model'
              ? '// LOADING MODEL INTO VRAM...'
              : '// GENERATING AUDIO...'}
          </p>
          <div className="flex gap-[2px] h-2 overflow-hidden">
            {Array.from({ length: 32 }).map((_, i) => {
              const hasRealProgress = generation.phase === 'processing' && generation.progress != null;
              const lit = hasRealProgress && i < Math.floor((generation.progress ?? 0) * 32);
              return (
                <div
                  key={i}
                  className={hasRealProgress ? 'flex-1' : 'flex-1 animate-pulse'}
                  style={{
                    backgroundColor: 'var(--accent)',
                    animationDelay: hasRealProgress ? undefined : `${i * 55}ms`,
                    opacity: hasRealProgress ? (lit ? 1 : 0.15) : 0.7,
                    transition: hasRealProgress ? 'opacity 150ms linear' : undefined,
                  }}
                />
              );
            })}
          </div>
          <p className="text-[10px] font-mono uppercase opacity-50" style={{ color: 'var(--accent)' }}>
            {generation.phase === 'processing'
              ? (generation.progress != null
                  ? `// SYNTHESIZING — ${Math.round(generation.progress * 100)}%`
                  : '// MUSICGEN SYNTHESIZING — APPROX 15-30s')
              : generation.phase === 'loading_model'
              ? '// WARMING UP GPU — FIRST RUN TAKES ~15s'
              : generation.queueDepth != null && generation.queueDepth > 1
              ? `// ${generation.queueDepth} JOBS IN QUEUE — WILL START WHEN WORKER IS FREE`
              : '// NEXT IN QUEUE — STARTING SOON'}
          </p>
          <button
            onClick={onCancel}
            className="brutal-btn brutal-btn-pink w-full flex items-center justify-center gap-2"
          >
            <X size={16} /> CANCEL GENERATION
          </button>
        </div>
      )}

      {/* DONE */}
      {generation.phase === 'done' && (
        <div className="flex flex-col gap-3">
          <div
            className="flex items-center gap-2 border p-3 text-xs font-mono uppercase font-bold"
            style={{ borderColor: 'var(--accent)', color: 'var(--accent)', backgroundColor: 'color-mix(in oklch, var(--accent) 6%, transparent)' }}
          >
            <Check size={14} className="shrink-0" aria-hidden="true" />
            <span>GENERATION COMPLETE — AUDIO READY</span>
          </div>
          {saveConfirmed ? (
            <div
              className="flex items-center gap-2 border p-3 text-xs font-mono uppercase font-bold"
              style={{ borderColor: 'var(--accent)', color: 'var(--accent)', backgroundColor: 'color-mix(in oklch, var(--accent) 12%, transparent)' }}
            >
              <Check size={14} className="shrink-0" aria-hidden="true" />
              <span>SAVED TO LIBRARY</span>
            </div>
          ) : (
            <>
              <p
                className="flex items-start gap-2 text-[10px] font-mono uppercase"
                style={{ color: 'var(--color-warning)', opacity: 0.9 }}
              >
                <AlertTriangle size={12} className="shrink-0 mt-[1px]" aria-hidden="true" />
                <span>UNSAVED — SAVE TO KEEP OR IT WILL EXPIRE</span>
              </p>
              {discardConfirmPending ? (
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
                  <div className="flex gap-2">
                    <button
                      onClick={onDiscardConfirm}
                      disabled={isDiscarding}
                      className="brutal-btn brutal-btn-pink flex-1 flex items-center justify-center gap-1 disabled:opacity-40 text-xs"
                    >
                      {isDiscarding ? <span className="animate-pulse">DISCARDING...</span> : <><X size={12} /> CONFIRM</>}
                    </button>
                    <button
                      onClick={onDiscardCancel}
                      className="brutal-btn flex-1 flex items-center justify-center gap-1 text-xs"
                      style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                    >
                      CANCEL
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex gap-2">
                  <button
                    onClick={onSave}
                    disabled={isSaving || isDiscarding}
                    className="brutal-btn flex-1 flex items-center justify-center gap-2 disabled:opacity-40"
                    style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                  >
                    {isSaving ? <span className="animate-pulse">SAVING...</span> : <><Save size={14} /> SAVE</>}
                  </button>
                  <button
                    onClick={onDiscardClick}
                    disabled={isSaving || isDiscarding}
                    className="brutal-btn brutal-btn-pink flex-1 flex items-center justify-center gap-2 disabled:opacity-40"
                  >
                    <X size={14} /> DISCARD
                  </button>
                </div>
              )}
            </>
          )}
          {/* Download / generate-again stay available regardless of save
              state — saving shouldn't take away the ability to play or
              download the song you just saved. */}
          {effects.isRendering ? (
            <div className="flex flex-col gap-1">
              <div
                className="border p-3 text-xs font-mono uppercase animate-pulse text-center"
                style={{ borderColor: 'var(--accent-secondary)', color: 'var(--accent-secondary)' }}
              >
                // APPLYING EFFECTS...
              </div>
              <p className="text-[10px] font-mono uppercase text-center" style={{ color: 'var(--accent-secondary)', opacity: 0.5 }}>
                // BROWSER RENDERING — EFFECTS BAKING IN
              </p>
            </div>
          ) : showFormatPicker ? (
            <div className="flex flex-col gap-1 border p-2" style={{ borderColor: 'var(--accent)' }}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[9px] font-mono uppercase tracking-widest" style={{ color: 'var(--accent)', opacity: 0.6 }}>
                  {effectsAreNeutral(effects.params) ? 'CHOOSE FORMAT' : 'CHOOSE FORMAT · EFFECTS ACTIVE'}
                </span>
                <button
                  onClick={() => setShowFormatPicker(false)}
                  aria-label="Close format picker"
                  title="Close format picker"
                  className="inline-flex items-center justify-center p-1 -m-1 transition-opacity"
                  style={{ color: 'var(--accent)', opacity: 0.5 }}
                  onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
                  onMouseLeave={e => (e.currentTarget.style.opacity = '0.5')}
                >
                  <X size={12} />
                </button>
              </div>
              <div className="flex gap-1">
                {DOWNLOAD_FORMATS.map(f => (
                  <button
                    key={f}
                    onClick={async () => {
                      if (!generation.jobId) return;
                      setDownloadFormat(f);
                      setShowFormatPicker(false);
                      if (effectsAreNeutral(effects.params)) {
                        await downloadSong(generation.jobId, generation.result?.prompt, f);
                      } else {
                        await effects.renderAndDownload(generation.jobId, generation.result?.prompt, f);
                      }
                    }}
                    className="flex-1 border py-1.5 text-[9px] font-bold uppercase tracking-wide transition-colors"
                    style={{
                      borderColor: 'var(--accent)',
                      backgroundColor: downloadFormat === f ? 'var(--accent)' : 'transparent',
                      color: downloadFormat === f ? 'var(--selected-text)' : 'var(--accent)',
                      opacity: downloadFormat === f ? 1 : 0.65,
                    }}
                  >
                    {f.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <button
              onClick={() => setShowFormatPicker(true)}
              className="brutal-btn w-full flex items-center justify-center gap-2"
              style={{ opacity: 0.75 }}
            >
              <Download size={14} /> DOWNLOAD
            </button>
          )}
          <button
            onClick={onGenerateAgain}
            className="brutal-btn w-full flex items-center justify-center gap-2"
            style={{ opacity: 0.45 }}
          >
            <Zap size={16} /> GENERATE AGAIN {saveConfirmed ? '' : '(DISCARD CURRENT)'}
          </button>
        </div>
      )}

      {/* FAILED */}
      {generation.phase === 'failed' && (
        <div className="flex flex-col gap-3">
          <p className="text-xs font-mono uppercase" style={{ color: 'var(--accent-secondary)' }}>
            ERROR: {generation.error ?? 'Unknown error.'}
          </p>
          <button onClick={onGenerate} className="brutal-btn w-full brutal-btn-pink flex items-center justify-center gap-2">
            <Zap size={16} /> RETRY
          </button>
        </div>
      )}

      {/* IDLE */}
      {generation.phase === 'idle' && (
        <button
          onClick={onGenerate}
          disabled={generateDisabled}
          title={generateDisabled ? generateDisabledTitle : undefined}
          className="brutal-btn w-full flex items-center justify-center gap-2 disabled:opacity-30"
        >
          <Zap size={16} /> GENERATE SONG
        </button>
      )}
    </div>
  );
}
