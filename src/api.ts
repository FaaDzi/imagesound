import { triggerDownload } from './utils/audioUtils';

export const API_BASE = (import.meta.env.VITE_API_BASE ?? 'http://localhost:8000').replace(/\/$/, '');

// fetch() rejects with a bare TypeError ("Failed to fetch") when the server
// can't be reached at all -- offline, restarting, or the tunnel down -- and
// that text used to reach the screen as-is. Said plainly here, once, for
// every API call. An aborted request (AbortError) passes through untouched.
export const SERVER_UNREACHABLE = "Can't reach the server. It may be offline or restarting. Try again in a moment.";

export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (err) {
    if (err instanceof TypeError) throw new Error(SERVER_UNREACHABLE);
    throw err;
  }
}

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
  const res = await apiFetch(dlUrl, { credentials: 'include' });
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
  const res = await apiFetch(`${API_BASE}/upload`, { method: 'POST', credentials: 'include', body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Upload failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Upload failed.');
  }
  return res.json() as Promise<UploadResult>;
}

// ---------- Generation ----------

export type JobStatus = 'queued' | 'loading_model' | 'processing' | 'done' | 'failed';

export interface GenerateResult {
  id: string;
  status: 'queued';
  warning?: string;  // soft heads-up, e.g. the host is low on RAM; the job still runs
}

export interface StatusResult {
  id: string;
  input_type: 'image' | 'audio' | 'text' | 'midi';
  status: JobStatus;
  created_at: string;
  expires_at: string;
  converted_key?: string;
  prompt?: string;
  duration?: number;
  queue_depth?: number;
  progress?: number;
  stage?: string;        // what a running job is doing in words (MIDI conversion)
}

// Per-model option values (e.g. { cover_strength: 0.8 }). Keys and ranges come
// from that model's manifest entry -- see ModelOption -- and the server
// rejects anything the model didn't declare.
export type ModelOptionValues = Record<string, boolean | number | string>;

export async function generateSong(opts: {
  id?: string;
  reference_id?: string;
  reference_mode?: string;
  prompt?: string;
  duration?: number;
  model?: string;
  options?: ModelOptionValues;
  lyrics?: string;               // sung with options.vocals === 'lyrics'; omitted = drafted server-side
}): Promise<GenerateResult> {
  const res = await apiFetch(`${API_BASE}/generate`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Generate failed.' })) as { detail?: string; active_job_id?: string };
    throw new GenerateError(err.detail ?? 'Generate failed.', res.status === 409, err.active_job_id);
  }
  return res.json() as Promise<GenerateResult>;
}

/** Thrown by generateSong. `busy` means another generation holds the GPU;
 *  `activeJobId` is that job, sent only to the admin, who may cancel it. */
export class GenerateError extends Error {
  constructor(message: string, readonly busy: boolean, readonly activeJobId?: string) {
    super(message);
  }
}

export async function getStatus(id: string): Promise<StatusResult> {
  const res = await apiFetch(`${API_BASE}/status/${encodeURIComponent(id)}`, { credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Status check failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Status check failed.');
  }
  return res.json() as Promise<StatusResult>;
}

/** One of the caller's jobs that is still queued or running. */
export interface ActiveJob {
  id: string;
  input_type: 'image' | 'audio' | 'text' | 'midi';
  status: JobStatus;
  source_file_id: string | null;   // MIDI jobs: the song being converted
  created_at: string;
  queue_depth?: number;
  progress?: number;
  stage?: string;
}

/** The caller's jobs still in flight, oldest first. The server is the source
 *  of truth here: a phone that discarded the tab, a reload or another device
 *  has no memory of what it started, and without asking it would offer the
 *  Generate / Convert button again for a job that is still running. */
export async function getActiveJobs(): Promise<ActiveJob[]> {
  const res = await apiFetch(`${API_BASE}/jobs/active`, { credentials: 'include' });
  if (!res.ok) throw new Error('Could not check for running jobs.');
  return ((await res.json()) as { jobs: ActiveJob[] }).jobs;
}

// ---------- Models ----------
// The server is the single source of truth for which music models exist and
// what each can do (GET /models, backed by pipeline/models.json). The UI
// renders from this and hardcodes nothing about any particular model.

// Conditions an option can carry. `when_option` ({ vocals: ['lyrics'] }) means
// "only while that other option has one of these values"; the server drops an
// option whose condition fails, so the UI hides it and leaves it out too.
interface OptionConditions { when_reference_mode?: string; when_option?: Record<string, string[]> }

export type ModelOption = OptionConditions & (
  | { key: string; label: string; type: 'bool'; default: boolean; help?: string }
  | { key: string; label: string; type: 'number'; min: number; max: number; step?: number; default: number; help?: string }
  // choice_help holds one line per choice, shown for whichever is selected —
  // a `help` that only described one of them left the others unexplained.
  // locked_choices: listed but not selectable yet, each with the reason (the
  // server refuses them too) -- e.g. lyrics languages that are not tested.
  | { key: string; label: string; type: 'choice'; choices: string[]; default: string; help?: string; choice_help?: Record<string, string>; locked_choices?: Record<string, string> });

export interface ModelInfo {
  id: string;
  label: string;
  tier: string;
  description: string;
  available: boolean;
  unavailable_reason: string | null;
  capabilities: {
    duration: { min: number; max: number; default: number };
    reference?: string[];          // reference-audio modes this model supports; absent/empty = none
  };
  options: ModelOption[];
}

export interface ModelsResponse {
  default: string;
  models: ModelInfo[];
}

export async function getModels(): Promise<ModelsResponse> {
  const res = await apiFetch(`${API_BASE}/models`, { credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to load models.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Failed to load models.');
  }
  return res.json() as Promise<ModelsResponse>;
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
  model_id: string | null;         // which model generated it; null = pre-registry song or non-generated entry
}

export async function getLibrary(): Promise<LibraryItem[]> {
  const res = await apiFetch(`${API_BASE}/library`, { credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to load library.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Failed to load library.');
  }
  return res.json() as Promise<LibraryItem[]>;
}

// ---------- Cancel ----------

export async function cancelJob(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/cancel/${encodeURIComponent(id)}`, { method: 'POST', credentials: 'include' });
  if (!res.ok && res.status !== 409) {
    // 409 = already in terminal state (failed/cancelled) — treat as no-op.
    const err = await res.json().catch(() => ({ detail: 'Cancel failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Cancel failed.');
  }
}

// ---------- Save / Discard ----------

export async function saveJob(id: string): Promise<{ id: string; saved: boolean; expires_at: string }> {
  const res = await apiFetch(`${API_BASE}/save/${encodeURIComponent(id)}`, { method: 'POST', credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Save failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Save failed.');
  }
  return res.json() as Promise<{ id: string; saved: boolean; expires_at: string }>;
}

export async function discardJob(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/discard/${encodeURIComponent(id)}`, { method: 'POST', credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Discard failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Discard failed.');
  }
}

// ---------- MIDI ----------

export async function convertToMidi(sourceId: string): Promise<{ id: string; status: string }> {
  const res = await apiFetch(`${API_BASE}/midi/convert/${encodeURIComponent(sourceId)}`, {
    method: 'POST',
    credentials: 'include',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'MIDI conversion failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'MIDI conversion failed.');
  }
  return res.json() as Promise<{ id: string; status: string }>;
}

/** `full` is every transcribed stem; `lead` is the melody/chords/bass/drums
 *  reduction. Entries converted before the reduction existed only have `full`,
 *  and the server falls back to it rather than failing. */
export type MidiView = 'full' | 'lead';

/** Sonified preview of a MIDI entry. `track` solos one part by name. */
export function midiPreviewUrl(id: string, track?: string | null, view: MidiView = 'full'): string {
  const q = new URLSearchParams();
  if (track) q.set('track', track);
  if (view !== 'full') q.set('view', view);
  const qs = q.toString();
  return `${API_BASE}/midi/preview/${encodeURIComponent(id)}${qs ? `?${qs}` : ''}`;
}

export interface MidiTrack {
  name: string;
  notes: number;
  is_drum: boolean;
}

/** The parts a MIDI entry contains, for the solo buttons. `view` says which
 *  file actually answered — asking for `lead` on an older entry returns
 *  `full`, and the caller should say so rather than mislabel it. */
export async function getMidiTracks(
  id: string, view: MidiView = 'full',
): Promise<{ view: MidiView; tracks: MidiTrack[] }> {
  const res = await apiFetch(
    `${API_BASE}/midi/tracks/${encodeURIComponent(id)}?view=${view}`,
    { credentials: 'include' },
  );
  if (!res.ok) throw new Error('Could not load MIDI tracks.');
  return res.json() as Promise<{ view: MidiView; tracks: MidiTrack[] }>;
}

export async function downloadMidi(id: string, prompt?: string | null): Promise<void> {
  const dlUrl = `${API_BASE}/download/${encodeURIComponent(id)}?format=midi`;
  const res = await apiFetch(dlUrl, { credentials: 'include' });
  if (!res.ok) throw new Error('MIDI download failed.');
  const blob = await res.blob();
  triggerDownload(blob, `${slugFilename(prompt, id)}.mid`);
}

// ---------- Strudel ----------

/** Render a completed MIDI entry as Strudel (https://strudel.cc) pattern code. */
export type StrudelMode = 'chords' | 'notes';

/** `chords` names the harmony once and gives bass/melody as scale degrees.
 *  `notes` keeps every transcribed pitch — longer, but shows the transcription. */
export async function getStrudelCode(
  id: string, mode: StrudelMode = 'chords', view: MidiView = 'full',
): Promise<string> {
  const res = await apiFetch(
    `${API_BASE}/midi/strudel/${encodeURIComponent(id)}?mode=${mode}&view=${view}`,
    { credentials: 'include' },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Strudel conversion failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Strudel conversion failed.');
  }
  const data = (await res.json()) as { code: string };
  return data.code;
}

// ---------- Describe ----------

export async function describeImage(id: string, signal?: AbortSignal, model?: string): Promise<string> {
  const res = await apiFetch(`${API_BASE}/describe`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, model }),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Describe failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Describe failed.');
  }
  const data = (await res.json()) as { prompt: string };
  return data.prompt;
}

/** Draft lyrics for a vocal song (admin only). Written to fit `prompt`, and
 *  inspired by the image when `id` is an uploaded image. One describer request. */
export async function writeLyrics(opts: {
  id?: string;
  prompt?: string;
  language: string;
  duration: number;
  structure?: string;   // the user's own sections to write into
}, signal?: AbortSignal): Promise<{ lyrics: string; bpm: number }> {
  const res = await apiFetch(`${API_BASE}/lyrics`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Writing lyrics failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Writing lyrics failed.');
  }
  // bpm: the tempo the writer suggests. Vocal songs always run at a locked
  // tempo -- left free, the model drifted into half time mid-song.
  return (await res.json()) as { lyrics: string; bpm: number };
}

/** Invent a music prompt with no image to work from (the "surprise me" button).
 *  Runs on the local model, so it is free to press repeatedly. */
export async function randomTheme(signal?: AbortSignal): Promise<string> {
  const res = await apiFetch(`${API_BASE}/theme`, {
    method: 'POST',
    credentials: 'include',
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Theme generation failed.' }));
    throw new Error((err as { detail?: string }).detail ?? 'Theme generation failed.');
  }
  const data = (await res.json()) as { prompt: string };
  return data.prompt;
}

// ---------- Auth ----------

export interface MeResult {
  username: string;
  role: 'admin' | 'user';
}

export async function getMe(): Promise<MeResult> {
  const res = await apiFetch(`${API_BASE}/auth/me`, { credentials: 'include' });
  if (!res.ok) throw new Error('Not logged in.');
  return res.json() as Promise<MeResult>;
}

export async function loginRequest(username: string, password: string): Promise<MeResult> {
  const res = await apiFetch(`${API_BASE}/auth/login`, {
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
  await apiFetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'include' });
}
