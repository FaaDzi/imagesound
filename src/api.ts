import { triggerDownload } from './utils/audioUtils';

export const API_BASE = (import.meta.env.VITE_API_BASE ?? 'http://localhost:8000').replace(/\/$/, '');

export function audioUrl(id: string): string {
  return `${API_BASE}/audio/${encodeURIComponent(id)}`;
}

export function imageUrl(id: string): string {
  return `${API_BASE}/image/${encodeURIComponent(id)}`;
}

export const DOWNLOAD_FORMATS = ['mp3', 'wav', 'flac', 'm4a', 'ogg'] as const;
export type DownloadFormat = typeof DOWNLOAD_FORMATS[number];

// Shared by downloadSong/downloadMidi: prompt-derived filename, falling back to
// the id's first 8 chars when there's no prompt to slugify.
function slugFilename(prompt: string | null | undefined, id: string): string {
  return prompt ? prompt.slice(0, 40).replace(/[^a-z0-9]+/gi, '_').toLowerCase() : id.slice(0, 8);
}

export async function downloadSong(id: string, prompt?: string | null, format: DownloadFormat = 'mp3'): Promise<void> {
  const dlUrl = `${API_BASE}/download/${encodeURIComponent(id)}?format=${format}`;
  const res = await fetch(dlUrl, { credentials: 'include' });
  if (!res.ok) throw new Error('Download failed.');
  const blob = await res.blob();
  triggerDownload(blob, `${slugFilename(prompt, id)}.${format}`);
}

export interface UploadResult {
  id: string;
  input_type: 'image' | 'audio';
  original_key: string;
}

export async function uploadFile(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${API_BASE}/upload`, { method: 'POST', credentials: 'include', body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Upload failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Upload failed.');
  }
  return res.json() as Promise<UploadResult>;
}

// ---------- Generation ----------

export type JobStatus = 'queued' | 'processing' | 'done' | 'failed';

export interface GenerateResult {
  id: string;
  status: 'queued';
}

export interface StatusResult {
  id: string;
  input_type: 'image' | 'audio' | 'text';
  status: JobStatus;
  created_at: string;
  expires_at: string;
  converted_key?: string;
  prompt?: string;
  duration?: number;
  queue_depth?: number;
  progress?: number;
}

export type ArcPreset = 'steady' | 'gentle_build' | 'rise_and_settle' | 'calm_energetic';

export async function generateSong(opts: {
  id?: string;
  melody_source_id?: string;
  prompt?: string;
  duration?: number;
  model?: 'medium' | 'small';
  arc_preset?: ArcPreset;
  arc_segments?: number[];
  filter_mode?: 'raw' | 'filtered';
}): Promise<GenerateResult> {
  const res = await fetch(`${API_BASE}/generate`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Generate failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Generate failed.');
  }
  return res.json() as Promise<GenerateResult>;
}

export async function getStatus(id: string): Promise<StatusResult> {
  const res = await fetch(`${API_BASE}/status/${encodeURIComponent(id)}`, { credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Status check failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Status check failed.');
  }
  return res.json() as Promise<StatusResult>;
}

// ---------- Library ----------

export interface LibraryItem {
  id: string;
  input_type: 'image' | 'audio' | 'text' | 'midi';
  prompt: string | null;
  duration: number | null;
  saved: boolean;
  expires_at: string;
  created_at: string;
  output_format: string | null;    // 'midi' for MIDI entries, null for audio entries
  source_file_id: string | null;   // for MIDI entries: the audio entry this was derived from
  fad_verdict: 'satisfactory' | 'unsatisfactory' | null;  // quality signal; null = not scored (pre-feature song, MIDI entry, or scoring failed)
}

export async function getLibrary(): Promise<LibraryItem[]> {
  const res = await fetch(`${API_BASE}/library`, { credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to load library.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Failed to load library.');
  }
  return res.json() as Promise<LibraryItem[]>;
}

// ---------- Cancel ----------

export async function cancelJob(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/cancel/${encodeURIComponent(id)}`, { method: 'POST', credentials: 'include' });
  if (!res.ok && res.status !== 409) {
    // 409 = already in terminal state (failed/cancelled) — treat as no-op.
    const err = await res.json().catch(() => ({ detail: 'Cancel failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Cancel failed.');
  }
}

// ---------- Save / Discard ----------

export async function saveJob(id: string): Promise<{ id: string; saved: boolean; expires_at: string }> {
  const res = await fetch(`${API_BASE}/save/${encodeURIComponent(id)}`, { method: 'POST', credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Save failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Save failed.');
  }
  return res.json() as Promise<{ id: string; saved: boolean; expires_at: string }>;
}

export async function discardJob(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/discard/${encodeURIComponent(id)}`, { method: 'POST', credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Discard failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Discard failed.');
  }
}

// ---------- MIDI ----------

export async function convertToMidi(sourceId: string): Promise<{ id: string; status: string }> {
  const res = await fetch(`${API_BASE}/midi/convert/${encodeURIComponent(sourceId)}`, {
    method: 'POST',
    credentials: 'include',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'MIDI conversion failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'MIDI conversion failed.');
  }
  return res.json() as Promise<{ id: string; status: string }>;
}

export function midiPreviewUrl(id: string): string {
  return `${API_BASE}/midi/preview/${encodeURIComponent(id)}`;
}

export async function downloadMidi(id: string, prompt?: string | null): Promise<void> {
  const dlUrl = `${API_BASE}/download/${encodeURIComponent(id)}?format=midi`;
  const res = await fetch(dlUrl, { credentials: 'include' });
  if (!res.ok) throw new Error('MIDI download failed.');
  const blob = await res.blob();
  triggerDownload(blob, `${slugFilename(prompt, id)}.mid`);
}

// ---------- Describe ----------

export async function describeImage(id: string, signal?: AbortSignal): Promise<string> {
  const res = await fetch(`${API_BASE}/describe`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Describe failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Describe failed.');
  }
  const data = (await res.json()) as { prompt: string };
  return data.prompt;
}

// ---------- Auth ----------

export interface MeResult {
  username: string;
}

export async function getMe(): Promise<MeResult> {
  const res = await fetch(`${API_BASE}/auth/me`, { credentials: 'include' });
  if (!res.ok) throw new Error('Not logged in.');
  return res.json() as Promise<MeResult>;
}

export async function loginRequest(username: string, password: string): Promise<MeResult> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Invalid username or password.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Invalid username or password.');
  }
  return res.json() as Promise<MeResult>;
}

export async function logoutRequest(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'include' });
}
