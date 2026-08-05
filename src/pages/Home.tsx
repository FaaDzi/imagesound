import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, Image as ImageIcon, Music, AlertCircle, AlertTriangle, Type } from 'lucide-react';
import { uploadFile } from '../api';
import { useInProgress } from '../context/InProgressContext';
import { useAuth } from '../context/AuthContext';
import { Waveform } from '../components/Waveform';
import { AquaDrift } from '../components/AquaDrift';

export function Home() {
  const navigate = useNavigate();
  const { item, setItem, clearItem } = useInProgress();
  const { username } = useAuth();
  const loggedIn = !!username;
  const [dragActive, setDragActive] = useState(false);
  const [inputType, setInputType] = useState<'image' | 'audio' | 'text'>('image');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [textInput, setTextInput] = useState('');

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!loggedIn || uploading) return;
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!loggedIn || uploading) return;
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
    <div className="container mx-auto px-4 md:px-8 py-10 md:py-14 flex-grow relative">
      <AquaDrift />
      <div className="relative z-10 grid grid-cols-1 lg:grid-cols-[1.3fr_1fr] gap-10 lg:gap-16 items-start max-w-6xl mx-auto">

        {/* LEFT — the actual tool. Wider column, left-biased, not centered. */}
        <div className="flex flex-col gap-8">
          <div className="border-b-2 pb-4" style={{ borderBottomColor: 'var(--accent)' }}>
            <p
              className="font-mono text-xs uppercase tracking-widest opacity-70 mb-1"
              style={{ color: 'var(--accent-tertiary)' }}
            >
              // image &#8646; sound, entirely in the browser
            </p>
            <h2
              className="text-2xl md:text-3xl font-display font-bold uppercase tracking-wide"
              style={{ color: 'var(--text-heading)' }}
            >
              Data &gt; Audio
            </h2>
          </div>

          {/* IN-PROGRESS WARNING */}
          {item && (
            <div
              className="border-4 rounded-[var(--radius-panel)] p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4"
              style={{ borderColor: 'var(--color-warning)', backgroundColor: 'color-mix(in oklch, var(--color-warning) 6%, transparent)' }}
            >
              <div className="flex items-start gap-3">
                <AlertTriangle size={20} className="shrink-0 mt-0.5" style={{ color: 'var(--color-warning)' }} />
                <div>
                  <p className="font-bold uppercase tracking-widest text-sm" style={{ color: 'var(--color-warning)' }}>
                    // UNFINISHED_WORK — STUDIO IN PROGRESS
                  </p>
                  <p className="text-xs font-mono uppercase opacity-60 mt-1" style={{ color: 'var(--color-warning)' }}>
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

          {/* INPUT TYPE TOGGLE — left-aligned, not centered */}
          <div className="flex flex-wrap gap-3">
            {(['image', 'audio', 'text'] as const).map(t => (
              <button
                key={t}
                onClick={() => setInputType(t)}
                className="border-2 rounded-[var(--radius-pill)] px-4 py-2 text-sm uppercase font-bold tracking-widest transition-colors"
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
              className="border-4 rounded-[var(--radius-panel)] p-8 md:p-10 flex flex-col items-start gap-6"
              style={{ borderColor: 'var(--accent)', backgroundColor: 'transparent' }}
            >
              <div className="flex items-center gap-3 pointer-events-none" style={{ color: 'var(--accent)' }}>
                <Type className="w-10 h-10" strokeWidth={1} />
                <p className="text-xl font-bold uppercase tracking-widest">Describe your music</p>
              </div>

              <textarea
                className="brutal-input w-full font-mono text-sm resize-none disabled:opacity-40"
                rows={4}
                maxLength={300}
                value={textInput}
                onChange={e => setTextInput(e.target.value)}
                onKeyDown={handleTextKeyDown}
                placeholder="Describe the music you want… e.g. 'slow ambient piano, rainy and calm'"
                autoFocus
                disabled={!loggedIn}
                title={!loggedIn ? 'Login required' : undefined}
              />

              <div className="flex items-center justify-between w-full gap-2">
                <span className="text-xs font-mono uppercase opacity-40" style={{ color: 'var(--accent)' }}>
                  {textInput.length}/300 · Enter=submit · Shift+Enter=newline
                </span>
                <button
                  onClick={handleTextSubmit}
                  disabled={!textInput.trim() || !loggedIn}
                  className="brutal-btn flex items-center gap-2 disabled:opacity-30"
                  title={!loggedIn ? 'Login required' : undefined}
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
              className="relative border-4 border-dashed rounded-[var(--radius-panel)] p-10 md:p-12 flex flex-col items-start justify-center text-left transition-colors duration-200"
              style={{
                borderColor: dragActive ? 'var(--accent-secondary)' : 'var(--accent)',
                backgroundColor: dragActive ? 'var(--bg-card)' : 'transparent',
                opacity: (uploading || !loggedIn) ? 0.6 : 1,
                pointerEvents: (uploading || !loggedIn) ? 'none' : 'auto',
              }}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
              title={!loggedIn ? 'Login required' : undefined}
            >
              <input
                type="file"
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
                onChange={handleFileInput}
                accept={inputType === 'image' ? 'image/*' : 'audio/*'}
                disabled={uploading || !loggedIn}
              />

              <div className="flex items-center gap-4 mb-6 pointer-events-none" style={{ color: 'var(--accent)' }}>
                {uploading ? (
                  <UploadCloud className="w-12 h-12 animate-bounce" strokeWidth={1} />
                ) : inputType === 'image' ? (
                  <ImageIcon className="w-12 h-12" strokeWidth={1} />
                ) : (
                  <Music className="w-12 h-12" strokeWidth={1} />
                )}
                <div>
                  <p className="text-xl font-bold uppercase tracking-widest">
                    {uploading ? 'UPLOADING...' : inputType === 'image' ? 'DROP IMAGE HERE' : 'DROP AUDIO HERE'}
                  </p>
                  <p className="text-sm opacity-70 uppercase tracking-widest mt-1">
                    {uploading
                      ? 'Please wait...'
                      : `Drag & Drop ${inputType === 'image' ? 'image' : 'audio'} file or click to browse`}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* ERROR MESSAGE — file upload modes only */}
          {uploadError && inputType !== 'text' && (
            <div
              className="flex items-start gap-3 border-2 rounded-[var(--radius-panel)] p-4"
              style={{
                borderColor: 'var(--color-danger)',
                color: 'var(--color-danger)',
                backgroundColor: 'color-mix(in oklch, var(--color-danger) 8%, transparent)',
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

        {/* RIGHT — the signature visual. Sticky so it stays in view while the
            left column (which can grow, e.g. the text input) scrolls past it. */}
        <div
          className="lg:sticky lg:top-24 border-2 rounded-[var(--radius-panel)] p-6 flex flex-col gap-4"
          style={{ borderColor: 'var(--border-muted)', backgroundColor: 'var(--bg-card)' }}
        >
          <p className="font-mono text-[10px] uppercase tracking-widest opacity-50" style={{ color: 'var(--text-muted)' }}>
            spectrogram &rarr; waveform
          </p>
          <Waveform variant="ambient" />
          <p className="font-mono text-xs leading-relaxed opacity-70" style={{ color: 'var(--text-muted)' }}>
            Every upload gets reversed into sound — an image's pixel data becomes a spectrogram, and the
            spectrogram becomes the waveform you hear.
          </p>
        </div>
      </div>
    </div>
  );
}
