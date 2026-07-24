import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, Image as ImageIcon, Music, AlertCircle, AlertTriangle, Type } from 'lucide-react';
import { uploadFile } from '../api';
import { useInProgress } from '../context/InProgressContext';

export function Home() {
  const navigate = useNavigate();
  const { item, setItem, clearItem } = useInProgress();
  const [dragActive, setDragActive] = useState(false);
  const [inputType, setInputType] = useState<'image' | 'audio' | 'text'>('image');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [textInput, setTextInput] = useState('');

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    handleFileRegistration(e.dataTransfer.files);
  };

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      handleFileRegistration(e.target.files);
    }
  };

  const handleFileRegistration = async (files: FileList) => {
    if (files.length === 0) return;
    const file = files[0];
    setUploadError(null);
    setUploading(true);

    const localUrl = (file.type.startsWith('image/') || file.type.startsWith('audio/')) ? URL.createObjectURL(file) : null;

    try {
      const result = await uploadFile(file);
      setItem({
        fileId: result.id,
        inputType: result.input_type,
        filename: file.name,
        url: localUrl,
        prompt: null,
      });
      navigate('/player', {
        state: {
          fileId: result.id,
          filename: file.name,
          type: result.input_type,
          url: localUrl,
        },
      });
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed.');
      setUploading(false);
    }
  };

  const handleTextSubmit = () => {
    const prompt = textInput.trim();
    if (!prompt) return;
    setItem({ fileId: null, inputType: 'text', filename: null, url: null, prompt });
    navigate('/player', { state: { type: 'text', prompt } });
  };

  const handleTextKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleTextSubmit();
    }
  };

  return (
    <div className="container mx-auto p-4 md:p-8 flex-grow flex flex-col items-center justify-center">

      <div className="text-center mb-12 border-b-4 pb-6 inline-block" style={{ borderBottomColor: 'var(--accent)' }}>
        <h2 className="text-4xl md:text-6xl font-display font-bold uppercase tracking-[0.2em] mb-4" style={{ color: 'var(--text-heading)' }}>
          Data &gt; Audio
        </h2>
        <p className="monospace uppercase text-xs md:text-sm tracking-widest max-w-2xl opacity-60" style={{ color: 'var(--accent-secondary)' }}>
          // BROWSER-BASED AUDIO SYNTHESIS & CONVERTER.<br />
          // UPLOAD AN IMAGE FOR SPECTROGRAM REVERSAL OR RAW AUDIO FOR FORMAT TRANSLATION.
        </p>
      </div>

      <div className="w-full max-w-4xl flex flex-col gap-8">

        {/* IN-PROGRESS WARNING */}
        {item && (
          <div
            className="border-4 p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4"
            style={{ borderColor: 'var(--accent-secondary)', backgroundColor: 'rgba(255,45,120,0.06)' }}
          >
            <div className="flex items-start gap-3">
              <AlertTriangle size={20} className="shrink-0 mt-0.5" style={{ color: 'var(--accent-secondary)' }} />
              <div>
                <p className="font-bold uppercase tracking-widest text-sm" style={{ color: 'var(--accent-secondary)' }}>
                  // UNFINISHED_WORK — STUDIO IN PROGRESS
                </p>
                <p className="text-xs font-mono uppercase opacity-60 mt-1" style={{ color: 'var(--accent-secondary)' }}>
                  {item.inputType === 'text'
                    ? (item.prompt
                        ? `"${item.prompt.slice(0, 40)}${item.prompt.length > 40 ? '...' : ''}"`
                        : 'TEXT_INPUT')
                    : (item.filename ?? 'UNTITLED')
                  } · {item.inputType?.toUpperCase() ?? 'UNKNOWN'}
                </p>
              </div>
            </div>
            <div className="flex gap-2 shrink-0">
              <button
                onClick={() => navigate('/player')}
                className="brutal-btn text-xs"
              >
                RESUME IN STUDIO
              </button>
              <button
                onClick={clearItem}
                className="brutal-btn-pink text-xs"
              >
                DISCARD
              </button>
            </div>
          </div>
        )}

        {/* INPUT TYPE TOGGLE — three equal buttons, slightly smaller than the old two-button layout */}
        <div className="flex justify-center gap-3 mb-4">
          {(['image', 'audio', 'text'] as const).map(t => (
            <button
              key={t}
              onClick={() => setInputType(t)}
              className="border-2 px-4 py-2 text-sm uppercase font-bold tracking-widest transition-colors"
              style={{
                borderColor: 'var(--accent)',
                backgroundColor: inputType === t ? 'var(--selected-bg)' : 'transparent',
                color: inputType === t ? 'var(--selected-text)' : 'var(--accent)',
                opacity: inputType === t ? 1 : 0.5,
              }}
            >
              [ {t.toUpperCase()} ]
            </button>
          ))}
        </div>

        {/* TEXT INPUT BOX */}
        {inputType === 'text' && (
          <div
            data-collider
            className="border-4 p-10 flex flex-col items-center gap-6"
            style={{ borderColor: 'var(--accent)', backgroundColor: 'transparent' }}
          >
            <div className="flex flex-col items-center gap-3 pointer-events-none" style={{ color: 'var(--accent)' }}>
              <Type className="w-16 h-16" strokeWidth={1} />
              <p className="text-2xl font-bold uppercase tracking-widest">DESCRIBE YOUR MUSIC</p>
            </div>

            <textarea
              className="brutal-input w-full font-mono text-sm resize-none"
              rows={4}
              maxLength={300}
              value={textInput}
              onChange={e => setTextInput(e.target.value)}
              onKeyDown={handleTextKeyDown}
              placeholder="Describe the music you want… e.g. 'slow ambient piano, rainy and calm'"
              autoFocus
            />

            <div className="flex items-center justify-between w-full gap-2">
              <span className="text-xs font-mono uppercase opacity-40" style={{ color: 'var(--accent)' }}>
                {textInput.length}/300 · Enter=submit · Shift+Enter=newline
              </span>
              <button
                onClick={handleTextSubmit}
                disabled={!textInput.trim()}
                className="brutal-btn flex items-center gap-2 disabled:opacity-30"
              >
                PROCEED TO STUDIO
              </button>
            </div>
          </div>
        )}

        {/* DRAG & DROP ZONE — image and audio modes */}
        {inputType !== 'text' && (
          <div
            data-collider
            className="relative border-4 border-dashed p-12 flex flex-col items-center justify-center text-center transition-colors duration-200"
            style={{
              borderColor: dragActive ? 'var(--accent-secondary)' : 'var(--accent)',
              backgroundColor: dragActive ? 'var(--bg-card)' : 'transparent',
              opacity: uploading ? 0.6 : 1,
              pointerEvents: uploading ? 'none' : 'auto',
            }}
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
          >
            <input
              type="file"
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
              onChange={handleFileInput}
              accept={inputType === 'image' ? 'image/*' : 'audio/*'}
              disabled={uploading}
            />

            <div className="flex flex-col items-center gap-4 mb-8 pointer-events-none" style={{ color: 'var(--accent)' }}>
              {uploading ? (
                <UploadCloud className="w-16 h-16 animate-bounce" strokeWidth={1} />
              ) : inputType === 'image' ? (
                <ImageIcon className="w-16 h-16" strokeWidth={1} />
              ) : (
                <Music className="w-16 h-16" strokeWidth={1} />
              )}
            </div>

            <div className="pointer-events-none">
              <p className="text-2xl font-bold uppercase tracking-widest mb-2">
                {uploading ? 'UPLOADING...' : inputType === 'image' ? 'DROP IMAGE HERE' : 'DROP AUDIO HERE'}
              </p>
              <p className="text-sm opacity-70 uppercase tracking-widest">
                {uploading
                  ? 'Please wait...'
                  : `Drag & Drop ${inputType === 'image' ? 'image' : 'audio'} file or click to browse`}
              </p>
            </div>
          </div>
        )}

        {/* ERROR MESSAGE — file upload modes only */}
        {uploadError && inputType !== 'text' && (
          <div
            className="flex items-start gap-3 border-2 p-4"
            style={{
              borderColor: 'var(--accent-secondary)',
              color: 'var(--accent-secondary)',
              backgroundColor: 'rgba(255,45,120,0.08)',
            }}
          >
            <AlertCircle size={20} className="shrink-0 mt-0.5" />
            <div>
              <p className="font-bold uppercase tracking-widest text-sm">UPLOAD FAILED</p>
              <p className="text-xs mt-1 opacity-80">{uploadError}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
