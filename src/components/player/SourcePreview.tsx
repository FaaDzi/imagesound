import React from 'react';
import { Activity } from 'lucide-react';

// Renders the "SRC_INPUT" card: the uploaded/source image, text prompt, or
// audio (melody reference) preview, depending on input type. Purely
// presentational — no local state, no side effects. Extracted verbatim from
// Player.tsx's "LEFT COL: ORIGINAL SOURCE" block.
export interface SourcePreviewProps {
  filename: string;
  // Resolved display URL: source.url, falling back to a stock photo when
  // there's no source image (matches Player.tsx's `url` variable).
  imageUrl: string;
  // Raw source URL (no fallback) — used for the <audio> melody reference
  // preview, which should show nothing rather than a stock photo when absent.
  rawUrl: string | null;
  fileId: string | null;
  isImage: boolean;
  isText: boolean;
  isAudio: boolean;
  mode: 'classic' | 'vibe';
  // Resolved text to show in the text-input preview:
  // history.draftText || state?.prompt || '--'
  textPreview: string;
}

export function SourcePreview({
  filename, imageUrl, rawUrl, fileId, isImage, isText, isAudio, mode, textPreview,
}: SourcePreviewProps) {
  return (
    <div className="col-span-1 border-2 p-4 flex flex-col relative h-[400px]" style={{ borderColor: 'var(--accent-tertiary)', backgroundColor: 'var(--bg-card)' }}>
      <div className="absolute top-0 right-0 text-xs font-bold px-2 py-1 uppercase tracking-widest" style={{ backgroundColor: 'var(--accent-tertiary)', color: 'var(--selected-text)' }}>
        SRC_INPUT
      </div>

      <h3 className="font-bold uppercase tracking-widest border-b pb-2 mb-4 truncate" title={filename} style={{ color: 'var(--accent-tertiary)', borderBottomColor: 'var(--accent-tertiary)' }}>
        {filename}
      </h3>

      <div className="flex-grow flex flex-col items-center justify-center border border-dashed overflow-hidden relative group" style={{ borderColor: 'var(--accent-tertiary)' }}>
        {isImage ? (
          <>
            <div className="flex-grow relative w-full h-full overflow-hidden border-b border-dashed" style={{ borderBottomColor: 'var(--accent-tertiary)' }}>
              <img src={imageUrl} alt="Source" className="w-full h-full object-cover filter grayscale sepia group-hover:filter-none transition-all duration-700" />
              <div className="absolute bottom-2 right-2 px-2 py-1 border text-xs uppercase tracking-widest font-bold" style={{ backgroundColor: 'var(--bg)', borderColor: 'var(--accent-tertiary)' }}>
                M:{mode}
              </div>
            </div>
            <div className="h-24 w-full shrink-0 flex items-center justify-center relative" style={{ backgroundColor: 'var(--bg)', color: 'var(--accent-tertiary)' }}>
              <Activity className="w-12 h-12" style={{ opacity: 0.5 }} />
              <span className="absolute bottom-1 right-2 text-[10px] uppercase tracking-widest">[ WAVEFORM ]</span>
            </div>
          </>
        ) : isText ? (
          <div className="flex flex-col items-start justify-start h-full w-full p-6 gap-4 overflow-hidden" style={{ color: 'var(--accent-tertiary)' }}>
            <div className="w-full border-b pb-2 shrink-0" style={{ borderBottomColor: 'var(--accent-tertiary)' }}>
              <span className="opacity-50 block mb-1 text-xs monospace uppercase">INPUT_TYPE:</span>
              <span className="font-bold text-sm uppercase tracking-widest">TEXT_PROMPT</span>
            </div>
            <div className="w-full min-h-0 flex-grow overflow-hidden">
              <span className="opacity-50 block mb-2 text-xs monospace uppercase">PROMPT:</span>
              <p className="text-xs font-mono leading-relaxed break-words overflow-y-auto" style={{ maxHeight: '180px', color: 'var(--accent-tertiary)', opacity: 0.9 }}>
                {textPreview}
              </p>
            </div>
          </div>
        ) : isAudio ? (
          <div className="flex flex-col items-start justify-center h-full w-full p-6 gap-4 monospace uppercase text-sm" style={{ color: 'var(--accent-tertiary)' }}>
            <div className="w-full border-b pb-2" style={{ borderBottomColor: 'var(--accent-tertiary)' }}>
              <span className="opacity-50 block mb-1">FILE_NAME:</span>
              <span className="font-bold truncate block">{filename}</span>
            </div>
            <div className="w-full border-b pb-2" style={{ borderBottomColor: 'var(--accent-tertiary)' }}>
              <span className="opacity-50 block mb-1">MELODY_REFERENCE:</span>
              {rawUrl ? (
                <audio controls src={rawUrl} className="w-full mt-2" />
              ) : (
                <span className="text-xs normal-case opacity-60">no preview available</span>
              )}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-start justify-center h-full w-full p-6 gap-4 monospace uppercase text-sm" style={{ color: 'var(--accent-tertiary)' }}>
            <div className="w-full border-b pb-2" style={{ borderBottomColor: 'var(--accent-tertiary)' }}>
              <span className="opacity-50 block mb-1">FILE_NAME:</span>
              <span className="font-bold truncate block">{filename}</span>
            </div>
            <div className="w-full border-b pb-2" style={{ borderBottomColor: 'var(--accent-tertiary)' }}>
              <span className="opacity-50 block mb-1">FORMAT:</span>
              <span className="font-bold truncate block">{filename.split('.').pop()?.toUpperCase() || 'RAW'}</span>
            </div>
            <div className="w-full border-b pb-2" style={{ borderBottomColor: 'var(--accent-tertiary)' }}>
              <span className="opacity-50 block mb-1">ID:</span>
              <span className="font-bold truncate block text-[10px]">{fileId ?? 'N/A'}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
