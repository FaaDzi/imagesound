import React from 'react';
import { Link } from 'react-router-dom';
import { Upload } from 'lucide-react';
import { AudioPreview } from './AudioPreview';

// Renders the "SRC_INPUT" card: the uploaded/source image, text prompt, or
// audio (reference track) preview, depending on input type. Purely
// presentational — no local state, no side effects. Extracted verbatim from
// Player.tsx's "LEFT COL: ORIGINAL SOURCE" block.
export interface SourcePreviewProps {
  filename: string;
  // Resolved display URL: source.url, falling back to a stock photo when
  // there's no source image (matches Player.tsx's `url` variable).
  imageUrl: string;
  // Raw source URL (no fallback) — used for the <audio> reference track
  // preview, which should show nothing rather than a stock photo when absent.
  rawUrl: string | null;
  fileId: string | null;
  isImage: boolean;
  isText: boolean;
  isAudio: boolean;
  // Resolved text to show in the text-input preview:
  // history.draftText || state?.prompt || '--'
  textPreview: string;
}

export function SourcePreview({
  filename, imageUrl, rawUrl, isImage, isText, isAudio, textPreview,
}: SourcePreviewProps) {
  return (
    <div className={`col-span-1 border-2 rounded-[var(--radius-panel)] overflow-hidden p-4 flex flex-col relative ${isImage || isText || isAudio ? 'h-[400px]' : 'min-h-[260px]'}`} style={{ borderColor: 'var(--accent-tertiary)', backgroundColor: 'var(--bg-card)' }}>
      <div className="absolute top-0 right-0 rounded-tr-[var(--radius-chip)] rounded-bl-[var(--radius-chip)] text-xs font-bold px-2 py-1 uppercase tracking-widest" style={{ backgroundColor: 'var(--accent-tertiary)', color: 'var(--selected-text)' }}>
        SRC_INPUT
      </div>

      <h3 className="font-bold uppercase tracking-widest border-b pb-2 mb-4 truncate" title={filename} style={{ color: 'var(--accent-tertiary)', borderBottomColor: 'var(--accent-tertiary)' }}>
        {filename}
      </h3>

      <div className="flex-grow flex flex-col items-center justify-center border border-dashed rounded-[var(--radius-chip)] overflow-hidden relative group" style={{ borderColor: 'var(--accent-tertiary)' }}>
        {isImage ? (
          // The image fills the card. There used to be a 96px "[ WAVEFORM ]"
          // strip below it holding a static icon -- it showed nothing about the
          // image or the audio and never changed, so it was costing a quarter of
          // the panel to say nothing.
          <div className="flex-grow relative w-full h-full overflow-hidden">
            <img src={imageUrl} alt="Source" className="w-full h-full object-cover filter grayscale sepia group-hover:filter-none transition-all duration-700" />
          </div>
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
              <span className="opacity-50 block mb-1">REFERENCE_TRACK:</span>
              {rawUrl ? (
                <AudioPreview src={rawUrl} />
              ) : (
                <span className="text-xs normal-case opacity-60">no preview available</span>
              )}
            </div>
          </div>
        ) : (
          // Nothing loaded yet: say so plainly and point at the one place a
          // source comes from, instead of N/A fields for a file that isn't there.
          <div className="flex flex-col items-center justify-center text-center h-full w-full p-6 gap-3">
            <Upload size={28} aria-hidden="true" style={{ color: 'var(--accent-tertiary)' }} />
            <p className="text-sm font-bold uppercase tracking-widest" style={{ color: 'var(--accent-tertiary)' }}>
              No source yet
            </p>
            <p className="text-xs font-mono max-w-[16rem]" style={{ color: 'var(--text-primary)' }}>
              Start from an image, a song to remix, or a written description.
            </p>
            <Link to="/" className="brutal-btn text-xs min-h-[44px] inline-flex items-center">
              GO TO UPLOAD
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
