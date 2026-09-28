# ImageSound

Turns an image, a text prompt, or an existing song into an AI-generated song.
An image is first described by Gemini into a text music prompt; that prompt
(typed directly, or produced from the image) drives a text-to-music model. An
existing song can be used as a reference to remix. Generated songs can also be
converted to MIDI (Basic Pitch) and downloaded in several audio formats.

The music model is **pluggable**: the app doesn't know any particular model.
Which models exist, what each can do, and which options it exposes are all
declared in one manifest (see [Models](#models)). Currently shipped:
**ACE-Step 1.5**, in two variants (Turbo: fast, SFT: higher quality).

## Architecture

Two processes, run together by `run.py`:

- **Backend** — FastAPI + SQLite (`backend/`). Handles uploads, queues
  generation jobs on a background worker, and serves generated audio/images.
- **Frontend** — React + Vite + TypeScript (`src/`), three routes: Home
  (upload/prompt entry), Player (generation controls + playback), Library
  (saved songs).

Generation itself runs in a **third, short-lived process per song**:

```
backend job worker ─► pipeline/generate_song.py ─► pipeline/runner.py ─► <model's own venv> worker script
   (queue, DB)          Gemini image→prompt          spawns/monitors,        loads weights, generates,
                        + validation                 cancel + timeout        exits (frees all VRAM)
```

Each model runs in its own virtualenv, so models with conflicting dependencies
(different `torch`, `transformers`, …) never have to share a Python. A worker
talks to the runner over a tiny JSON-lines protocol
(`pipeline/workers/protocol.py`). Because the model lives in a process, a
cancel simply kills it (VRAM is returned within about a second) and a hung
generation times out.

The pipeline is also usable without the backend:

```
python -m pipeline.generate_song --prompt "warm lo-fi hip hop, mellow Rhodes piano" --duration 15
```

See [`docs/how-it-works.docx`](docs/how-it-works.docx) for the end-to-end
walkthrough — image → prompt → audio → MIDI → Strudel, with the maths each
stage runs and where the measured constants came from. It is generated, not
hand-edited: the text lives in `scripts/build_how_it_works_docx.py`, so
rebuild it with

```
.venv\Scripts\python.exe scripts\build_how_it_works_docx.py
```

See `design.md` for the frontend's visual design system (not reproduced
here — this file is setup/orientation only).

## Prerequisites

- Node.js 18+ (frontend)
- Python 3.10 for the main environment (built against 3.10.11)
- [`uv`](https://docs.astral.sh/uv/) and `git`, used to install ACE-Step (uv
  fetches ACE-Step's own Python 3.12 itself)
- An NVIDIA GPU with 8 GB+ VRAM for ACE-Step. It runs on 8 GB with its CPU
  offload and INT8 quantization settings (see `pipeline/models.json`); a
  15 s song takes about 30 s end to end on an RTX 4060 Laptop, most of it
  model loading.
- Windows: `run.py` currently assumes Windows (hardcoded `.venv\Scripts\`
  paths, `taskkill` for process cleanup, PowerShell for process inspection).
  Running the two halves manually (`uvicorn` + `npm run dev`) should still
  work on other platforms; the launcher script itself won't.

## Setup

### 1. Frontend dependencies

```
npm install
```

### 2. Environment variables

```
copy .env.example .env
```

Edit the new `.env` (repo root) and set `GEMINI_API_KEY`. This file is read
directly by `pipeline/generate_song.py` (the Gemini image-to-prompt step)
and also doubles as Vite's env file for the frontend (`VITE_API_BASE`,
default `http://localhost:8000`). `ALLOWED_ORIGINS` (backend CORS) and
`VITE_API_BASE` already default to the right values for local dev — you
only need to touch `GEMINI_API_KEY`.

**Which Gemini model reads the image.** Pinned to a Lite model
(`GEMINI_MODEL` in `.env` overrides it). It used to be `gemini-flash-latest`,
which is an alias that moves onto whatever the newest Flash is — the worst
target for a free key, because the newest Flash models carry the smallest free
quotas (reported ~20 requests/day against ~500/day for Lite; Google no longer
publishes per-model numbers, so check
[your own dashboard](https://aistudio.google.com/rate-limit)) and the alias
moves without notice. Measured back to back on one image,
`gemini-flash-latest` returned 503 twice and needed 40.1s to answer, where both
Lite models answered first try in ~3s. On a paid key the quota and the 503s
stop mattering and a full Flash model is the better pick — set `GEMINI_MODEL`.

**Local fallback (Ollama).** `pipeline/describers.py` runs a chain of image
describers, set by `DESCRIBER_ORDER` (default `gemini,ollama`): the first that
answers wins, and a backend that fails transiently falls through to the next, so
a quota wall degrades the result instead of failing the request. The local
backend talks to Ollama over HTTP — no Python dependencies. Weights live
wherever `OLLAMA_MODELS` points, deliberately outside this repo.

Measured on two images, three runs each, against the ACE-Step prompt rules:

| describer | descriptors (rule 8–16) | within rule | repeats | median |
|---|---|---|---|---|
| `gemma3:4b` (local) | 8.8 | **6/6** | 0.17 | **0.6s** |
| `gemini-3.1-flash-lite` | 8.3 | 5/6 | 0.00 | ~3s |
| `gemini-3.5-flash-lite` | 6.7 | 2/6 | 0.33 | ~3s |

Those columns measure *rule compliance*, not whether the caption suits the
image — the local captions are markedly terser (14.7 words against Gemini's
23.7), which the table cannot see. Gemini stays first in the chain for that
reason, not because of these numbers.

**Pick a local model that does not reason.** `qwen3-vl:4b` scores better on
paper and is unusable here: it deliberates before answering and does not
reliably stop, spending its whole token budget and returning nothing in 6 of 6
attempts, with Ollama 0.34's `"think": false` and Qwen's own `/no_think` both
ignored. Shortening the instruction (see `compact=True` in
`pipeline/prompting.py`, 34% the length) raised that to 1 success in 4 — better,
still unusable. `gemma3:4b` cannot fail this way. Small models need the compact
wording regardless; the full instruction is written for a hosted model.

First call after idle costs a model load (36.7s measured); later calls are
~0.5s. `OLLAMA_KEEP_ALIVE` (default 60s) then releases the ~3GB so it is not
still resident when music generation starts on the same 8GB card.

**The "surprise me" button** (`POST /theme`) invents a prompt when there is no
image to read — remixing an existing song starts with an empty box. It is
local-only on purpose: it costs nothing and has no quota, which suits a button
pressed repeatedly while hunting for an idea. The *genre* is chosen in Python
from `_SEED_GENRES` (121 of them, grouped by family) and the model only builds a
coherent prompt around it; asked to "pick any genre" a 4B model returns a
variant of "cinematic ambient" nearly every time. Spot-checked across the
awkward corners of the list, the instrumentation comes back idiomatic — gamelan
gets metallophone and gong, klezmer gets clarinet and accordion, bebop gets a
walking upright bass and brushed drums.

**When image description fails.** Gemini's free tier fails two transient ways
and the app now reports which: `429` means the per-minute request quota is
spent (5/min per model at the time of writing), `5xx` means the model is
overloaded. Both come back as a 503 from `/describe` with a message saying
which it was and whether waiting helps. The retry loop has a wall-clock budget
(`GEMINI_MAX_TOTAL_SEC`) that the route's timeout is derived from, so a quota
wall that would need a 60s wait fails immediately with the real reason instead
of being retried past the route's patience and reported as a timeout.

The SDK's *"Direct use of automatic function calling (AFC) in
Models.generate_content is not recommended"* warning is unrelated to any of
that. It fires once per process whenever `generate_content` is called with no
`config`, because AFC then defaults to on; we declare no tools, so the AFC path
only added a deep copy and a one-iteration loop. The calls now pass a config
with AFC explicitly disabled, so it no longer appears. Seeing it next to a 503
was a coincidence of both happening on the first call of the process.

Optionally also:

```
copy backend\.env.example backend\.env
```

This only overrides `DATABASE_PATH`/`STORAGE_ROOT`, both of which have
working defaults (`backend/app.db`, `backend/storage/`) — skip it unless you
need to point them elsewhere.

### 3. Main Python environment (`.venv`)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt
pip install -r pipeline\requirements.txt
```

This environment has no music model in it.

If you want audio-to-MIDI conversion, also:

```
pip install -r pipeline\requirements-midi.txt
```

That adds Demucs (stem separation) and Basic Pitch (transcription), both
CPU-only here. They fetch ~150 MB of weights on first conversion, into
`.hf-cache/` and `.torch-cache/` inside the project.

### 3b. Rhythm analysis (`.venv-beat`) — optional

Tempo *and* time signature, via [Beat This](https://github.com/CPJKU/beat_this):

```
python -m venv .venv-beat
.venv-beat\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
.venv-beat\Scripts\python -m pip install -r pipeline\requirements-beat.txt
```

Its own virtualenv because it needs a newer `torch` than the main environment
pins for Demucs; `pipeline/beat_track.py` calls it as a subprocess. Skip it
and MIDI conversion still works — it falls back to librosa, which gives a
tempo but no downbeats, so the meter is assumed to be 4/4.

### 4. ACE-Step environment

```
powershell -ExecutionPolicy Bypass -File scripts\setup_ace_step.ps1
```

Add `-Sft` to also download the higher-quality SFT model. This clones ACE-Step
into `third_party/`, creates its own virtualenv and downloads the weights
(several GB). **Everything is kept inside the project folder** — uv's Python
and cache in `.uv/`, the Hugging Face cache in `.hf-cache/`, weights in
`models/` — so nothing is written to your system drive. The script is safe to
re-run and resumes interrupted downloads.

### 5. FAD scoring environment (`.venv-fad`) — optional

`fad_score_one.py` can score songs with FAD (Frechet Audio Distance) in a
fully isolated venv, because `fadtk` needs a newer `torch` than other parts of
the app:

```
python -m venv .venv-fad
.venv-fad\Scripts\activate
pip install fadtk soundfile
```

Scoring is off for ACE-Step (see Known limitations), so this is only needed
if you enable `quality_scoring` for a model. If scoring fails to run it's
caught and the song's `fad_score`/`fad_verdict` are left `null`; it never
fails a generation job.

## Running it

```
python run.py
```

Starts the backend (`:8000`) and frontend (`:4000`) together. Press Ctrl+C
once to stop both, or just run `python run.py` again (even from a different
terminal) — it detects the running instance and stops it instead of starting
a second one, so it doubles as a start/stop switch.

If a terminal says `python` isn't found right after installing Python,
Windows hasn't refreshed that terminal's PATH yet; run
`.venv\Scripts\python.exe run.py` instead. `run.py` rebuilds its own PATH from
the registry, so it finds `npm` even from such a terminal.

## Models

`pipeline/models.json` is the single source of truth. Each entry declares:

| Field | Meaning |
|---|---|
| `worker` | the model's Python (its own venv), the worker script, env vars, timeout, and the arguments the worker reads |
| `requires` | files that must exist for the model to count as installed (use real weight files — a folder can be half-downloaded) |
| `capabilities` | `duration` (min/max/default seconds) and `reference` (the reference-song modes it supports, e.g. `cover`, `style`; omit for none) |
| `options` | user-facing settings (`bool`, `number`, `choice`), optionally tied to one reference mode |
| `prompt_style` | which Gemini prompt wording suits the model (`pipeline/prompting.py`) |
| `quality_scoring` | whether the FAD thresholds are calibrated for this model |

The backend serves this at `GET /models` (with live availability), validates
every `/generate` request against it, and the Player renders the model picker,
length buttons, remix controls and options from it — none of those know a
model by name.

**Adding a model** (e.g. Stable Audio Open):

1. Create its environment (its own venv, any Python/torch it needs).
2. Write `pipeline/workers/<name>_worker.py` following the contract in
   `pipeline/workers/protocol.py`: read the request, emit `status` /
   `progress` events, write a WAV to `output_path`, emit `result`.
3. Add an entry to `pipeline/models.json`. Restart the backend.

No route, job, database or frontend change is needed.

## Known limitations

**Remix follows the reference loosely.** ACE-Step's `cover` mode measurably
pulls the result toward the reference song, but it is not a strict
re-rendering of it, and with a large style change the influence is hard to
detect. Measured on single samples with crude chroma/energy similarity, so
judge by ear: use *Remix strength* (higher keeps more of the original) and keep
the requested style close to the source for the closest results.

**Audio artifacts ("crackle") are reduced, not eliminated.** ACE-Step can put
impulsive clicks and graininess into a track, worst on dense, loud material
(rock, electronic). Two things in `models.json` cut this measurably:

| | SFT | Turbo |
|---|---|---|
| `guidance_scale` | `5.5` (ACE-Step's default is `7.0`) | ignored by Turbo — it has no CFG |
| `velocity_norm_threshold` | `2.0` | `2.0` |
| `velocity_ema_factor` | — | `0.1` |

**Treat the numbers below with suspicion.** They were measured on single 20 s
samples, and later multi-seed runs showed that *seed-to-seed variance dwarfs
every setting here*: the same config on three seeds produced 1,936 / 12,289 /
23,153 impulsive jumps. Worse, re-running an identical config with identical
seeds did not reproduce — generation is not deterministic despite `seed`
(GPU kernels and INT8 quantization both introduce it). So these figures show
a direction at best, not an effect size, and the settings they justify are
kept because they are harmless rather than because they are proven.

Measured on single 20 s samples, counting sample-to-sample jumps (`>0.10`) —
which is what crackle is — and the share of energy above 12 kHz, as a proxy
for how bright the result still is:

| Config | jumps | above 12 kHz |
|---|---|---|
| SFT before | 2246 | 3.57 % |
| SFT after | **165** | 3.36 % |
| SFT at `guidance_scale` 4.5 | 2 | 1.08 % |
| Turbo before | 902 | 4.28 % |
| Turbo after | **655** | 4.01 % |

Dropping `guidance_scale` to `4.5` removes nearly every click but also cuts
treble energy by two thirds, which is why `5.5` is the default instead — the
metric can't tell "fewer artifacts" apart from "duller mix", so that call was
made by keeping the brightness figure near its original value. Judge by ear:
`pipeline/output/crackle_ab/` holds before/after pairs plus the 4.5 version.
Raising `quantization` off `int8_weight_only` was also tried and measured
*worse*, so INT8 stays.

(The separate upstream distortion bug — an experimental `std * 5.0`
normalization before VAE decode, ACE-Step PR #299 — is already fixed in the
pinned clone.)

**Dense electronic material is the model's weak spot, and it is inconsistent
rather than uniformly bad.** Across three seeds each, an EDM prompt produced
roughly 8,000–35,000 impulsive jumps against ~900 for a rock prompt, with
individual runs ranging from 1,913 to 80,105 on *identical settings*. Neither
Turbo nor SFT won reliably: SFT had fewer abrupt level drops (mean 1.7 vs 7.3
per 20 s) but more high-frequency noise, and both orderings flipped between
runs. There is no setting here that fixes it — the practical answer for
electronic styles is to generate several takes and keep one, which is also why
the seed is left random rather than pinned. Samples in
`pipeline/output/edm_variance/` show the spread on one prompt.

The one tuning knob that moved this measurably is the **Solver** option
(`sampler_mode`). Heun adds a corrector step per diffusion step, halving the
integration error that surfaces as noise. Over four seeds on an EDM prompt it
cut impulsive jumps ~28% (mean 21,738 → 15,666) and, more usefully, more than
halved the spread between runs (7,345–40,372 → 9,718–23,163) at no extra time.
It also showed *more* abrupt level drops (mean 1.8 → 19.5), driven mostly by
one outlier run. That trade-off is unresolved at n=4 against this much
variance, so it ships as a user choice defaulting to `euler` — the existing
behaviour — rather than as a new default. Judge it by ear.

**Locking the BPM is the one change that clearly works.** ACE-Step estimates
its own tempo when `bpm` is unset, and that estimate wanders over a long
generation — which is what makes a beat-driven style feel like it drifts. The
*Lock BPM* option pins it. Three runs each on a drift phonk prompt, measuring
the spread of local tempo estimates across the track:

| | run 1 | run 2 | run 3 | mean |
|---|---|---|---|---|
| Free (previous behaviour) | 16.7 | 10.7 | 25.2 | **17.5 BPM** |
| Locked to 140 | 4.5 | 3.7 | 3.8 | **4.0 BPM** |

No overlap between the groups, unlike every other knob measured here, and the
locked runs land near the requested tempo (mean ~141 BPM) where the free ones
wandered to 112–129 regardless of what the prompt asked for. `0` keeps the old
behaviour.

**Where the harshness lives.** Measuring a generated drift phonk against
calmer styles put it in the top octaves: 8–14 kHz carried 17.1% of the track's
magnitude against 3.1% / 4.0% / 9.5% for three tracks that sound fine, with
spectral flatness there at 0.79–0.81, i.e. much closer to noise than to tone.
The high shelf in `useAudioEffects.ts` sits at 8 kHz for that reason.

An earlier pass also blamed a sustained 3.5–3.6 kHz resonance. A follow-up
scan measuring each track's largest peak against a running-median baseline
over 2–9 kHz does **not** support that: the buzzy track peaked at +6.1 dB
against +5.0, +6.9 and +8.1 dB for the clean ones. Every track has such a
resonance and the buzzy one's is unremarkable, so the mid band at 3.2 kHz is a
general bite control, not a fix for a model defect. Only the high shelf is
evidence-backed.

Note the metric: band shares are of summed **magnitude**, not power. A power
spectrum is so bass-dominated that all four tracks land at 73–87% low and the
bands that carry harshness disappear into rounding — magnitude shares separate
them cleanly. `src/utils/audioAnalysis.ts` implements this in the browser and
reproduces the offline NumPy figures to the decimal.

**Auto-EQ.** `src/utils/autoEq.ts` matches the generation prompt to one of
three tiers (bright electronic / general / soft acoustic), each a ceiling on
what share of top end that style should carry, then cuts by
`10·log10(measured/target) × 2.5 dB`, clamped to 8 dB with a 1 dB deadzone.
The 2.5 factor is calibrated so the drift phonk lands on −5 dB, which is where
the hand-tuned DE-BUZZ preset independently ended up. Of the four saved tracks
only that one triggers a cut; the other three measure inside their targets and
are left untouched. Tiers are ceilings chosen just above the clean group — they
were not measured against commercial releases.

Worth noting what the same analysis ruled *out*: that track had 6 abrupt
loudness steps in three minutes against the EDM track's 702, and no spectral
flux spikes at all. Its "jumping" was tempo drift and short 2-second sections,
not clicks or level jumps — which is why EQ and limiting do nothing for it and
locking the BPM does.

**Transcribed MIDI is thinned by polyphony, not by note length.** Basic Pitch
transcribes pitch, and on a bass stem it reports the fundamental *and* its
overtones as separate simultaneous notes. Measured on a 180s track, the bass
peaked at 5 voices and 29 distinct pitches for what is played as one line.
`_thin_polyphony` caps each stem at the number of voices its instrument
actually has (bass and vocals 1, guitar 3, piano and other 4), dropping the
highest pitch where the limit is 1 — overtones sit above the fundamental — and
the quietest otherwise:

| track | notes | max polyphony | distinct pitches |
|---|---|---|---|
| Bass | 552 → 258 | 5 → 1 | 29 → 25 |
| Vocals | 88 → 70 | 2 → 1 | 31 → 25 |
| Other | 1111 → 1036 | 7 → 4 | 39 → 37 |
| Guitar | 204 → 196 | 5 → 3 | 32 → 32 |
| Piano | 220 → 220 | 3 → 3 | 29 → 29 |

Pitch variety barely moves while polyphony halves, which is the signature of
removing doubled overtones rather than melody. Piano was already inside its
limit and is untouched.

Note *length* was the obvious thing to filter and would have achieved nothing:
0% of pitched notes in that file were under 80ms and none overlapped a repeat
of the same pitch, because Basic Pitch's own 127ms minimum already handles it.

**Drum hits are labelled by spectral flux, not by energy.** A phonk track
transcribed to 15 kicks against 357 snares — visibly wrong for a kick-led
genre, and it made the Strudel `bd` line silent for most of the song.

The cause was the baseline. Each onset's band energy was scored against that
band's *average over the whole stem*, which fails on exactly the music this app
generates: a sustained 808 holds the low band above its own average at all
times, so a kick attack can never beat it.

There was no labelled drum stem to test against, so
`scripts/drum_bench.py` builds one — synthesised kicks, snares and hats at
known times, rendered to a WAV the real `_transcribe_drums()` reads back, over
4 patterns × dry/sustained-808 × 2 tempos. Hits are placed non-coincident on
purpose: a real kit puts a hat on every kick, and one onset can then only carry
one of the two labels, which would cap the score at a number that says nothing
about which band was chosen. The old code scored **63.5%** and lost *every kick
in all eight 808 cases* (0 of 31, 0 of 63, 0 of 47).

Positive spectral flux fixes it, because a drone contributes energy but almost
no flux — it only grows at its own attacks. Bands are tried low-to-high rather
than taking the largest, because a snare has real high-frequency content: under
argmax, a pattern with no hats had 0 of 32 snares labelled correctly. Together:
**63.5% → 94.8%**, with 8 of 16 cases perfect.

| | old | new |
|---|---|---|
| mean label accuracy | 63.5% | **94.8%** |
| kicks found, all 8 sustained-808 cases | 0 / 344 | 323 / 344 |
| snares found, all 4 no-hat cases | 0 / 128 | 128 / 128 |

`_DRUM_FLUX_MIN` sits at 0.32 because accuracy is flat within a percent across
0.25–0.40 and falls away outside it — the middle of a plateau, not a value that
happened to score well.

On a real separated stem there is no ground truth, so the check is instead how
tightly each part lands on a few positions in the bar, measured against
librosa's *tracked* beats (`t % (60/bpm)` is useless here — over three minutes a
0.5% tempo error drifts by several beats and scatters a perfectly regular part):

| part | old | new |
|---|---|---|
| kick | 0.210 | **0.290** |
| snare | 0.317 | **0.492** |
| hat | 0.146 | 0.095 |

Kick and snare get substantially cleaner; hats get worse, and that is a
deliberate trade. Onsets matching no band fall through to hat, which makes it a
catch-all (397 → 573 hits). Routing the residue by nearest band instead was
tried and moved kick back to 0.254 and snare to 0.393 for hat 0.113 — paying
from the two parts that carry the groove to tidy the one that ornaments it.

The remaining benchmark errors are almost entirely an 808 attack landing
exactly on a snare: moving it off takes that case from 78.5% to 100% and its
snares from 0/16 to 16/16. One label per onset simply cannot represent two
instruments hitting together.

**The lead sheet.** `pipeline/lead_sheet.py` reduces the full transcription to
the four parts a person would write down — MELODY (the highest line of whichever
stem carries the lead), CHORDS (block voicings from the per-bar progression,
merged while a chord holds), BASS (the lowest line), DRUMS unchanged. Written
alongside the full file as `_lead.mid`, reachable through `?view=lead`, and
offered as a FULL/LEAD toggle rather than a replacement.

Parts are chosen by **register, not stem name**: a separator's labels say where
a sound came from, not what job it does in the arrangement.

**It does not measure more accurate, and that is worth stating plainly:**

| track | version | notes | support gain | chroma gain |
|---|---|---|---|---|
| pop punk | full | 1706 | +16.4pp | **+0.078** |
| pop punk | lead | 1287 | **+19.1pp** | +0.073 |
| j-pop | full | 3732 | **+13.1pp** | **+0.076** |
| j-pop | lead | 2348 | +10.0pp | +0.052 |

A reduction drops notes by design and both metrics reward coverage, so this is
close to the expected result rather than a surprise. What the metrics cannot see
is legibility, which is the entire reason for the feature.

One thing they did not see, found by comparing against the Songscription score:
the reduction **fixes the key estimate**. On the pop-punk track the full
transcription gives *A minor* (no sharps) and the lead sheet gives *E minor*
(one sharp) — and Songscription's key signature is one sharp. Cutting the
overtone and leakage notes leaves a pitch-class distribution clean enough to
read correctly (B rises from 7% to 13%), and since `.scale()` drives every
degree in the Strudel output, that propagates.

**Overtones have to be removed before the melody is taken, not after.**
`skyline` assumes the melody is the highest thing sounding. Basic Pitch reports
a tone's partials as real simultaneous notes, and a partial is by definition
*above* the note that produced it — so whenever that happens, taking the top
line takes the artifact instead of the tune.

`scripts/melody_bench.py` measures this on synthetic material: a known melody,
plus the accompaniment and the overtone notes a transcriber adds around it
(octave, octave-and-fifth, two octaves, quieter and shorter, as observed on real
output). `skyline` alone:

| overtone rate | F1, melody only | F1, with accompaniment | octave errors |
|---|---|---|---|
| 0% | 1.00 | 0.88 | 0% |
| 30% | 0.25 | 0.24 | 38% |
| 60% | 0.05 | 0.02 | 41% |
| 90% | **0.00** | **0.00** | 41% |

With `strip_overtones` first, every one of those becomes **1.00** (melody only)
or **0.89** (with accompaniment), octave errors 0%, and recall stays 1.00 — no
real melody notes are lost. A note is dropped when another note sounding at the
same time, *at least as loud*, sits exactly a harmonic interval below it; the
velocity test is what protects a genuine melody note an octave above quiet
accompaniment, since a partial is always weaker than its fundamental.

On the pop-punk track the melody line's median pitch fell from 64 to 59 and its
note count from 416 to 377 — the line stopped being dragged upward by partials.

The same filter applied before *chord* detection was tried and not kept: it
moved chord-tone overlap against the Songscription reference from 55% to 59%
but cost a root match (12/17 to 11/17), and `Am9` stayed `Am9`. Those extensions
come from genuine notes across different stems, not from one note's partials.

`skyline` is a sweep over note boundaries with a max-heap, not the obvious
incremental walk. The incremental version — truncate each note against whatever
was appended last — is wrong in a way that is easy to miss: a note appended to
cover the tail of a long low note *starts later* than notes still to be
processed, so "last appended" stops meaning "currently sounding". It produced
overlapping output on 1334 of 2000 random inputs. The sweep is monophonic by
construction and tests 2000/2000 on both monophony and picking the genuinely
highest note. `_groundline` mirrors the pitches and reuses it, so the two lines
cannot drift apart.

**Why jazz converted badly: the tempo was half.** The obvious suspect was swing
— jazz eighths land near 0 and 2/3 of a beat and every grid here is straight —
so that was tested first, by scoring each track's onsets against straight,
swung and triplet grids (mean distance to the nearest grid line, divided by what
random onsets would score, so grids of different density are comparable).

Swing lost. What the table showed instead was one Cool Jazz track sitting at
**60.0 BPM against librosa's 117.5** on the same audio, and another at 187.5
against 95.7. The beat tracker in `midi_convert.py` halves and doubles, and
`_estimate_bpm` trusted its header outright whenever it was inside the 60–180
range. A half-speed tempo is not a small error: every bar then covers two real
bars, so the chord detected for it pools two different chords, and the Strudel
`setcpm` is out by a factor of two.

The header is now trusted for its *value* but not its octave — half, actual and
double are each scored against the onset autocorrelation (weighted by the same
log-normal prior) and the best wins. It can only ever move the answer by a
factor of two, so a correct header stays correct: verified on six synthetic
cases covering both halving and doubling, 6/6, with the four real tracks going
60→**120**, 187.5→**94**, and the two already-correct ones untouched.

With the tempo fixed, swing turns out to be real after all — it was simply
invisible underneath the octave error. On the corrected track a swung grid fits
better than a straight one (pitched 0.73 against 0.83, drums 0.26 against 0.29).
It is a modest effect on one of four tracks and grid selection is not yet
per-track, so nothing has been changed for it.

Not everything is fixable this way. The J-pop track scores **0.97–0.99 on every
grid** — indistinguishable from random onsets — and its tempo estimates disagree
irreconcilably (158 from the onsets, 108 from librosa, not a factor of two
apart). Dense 8-bit arpeggios genuinely have no beat to find, and no choice of
grid recovers one.

**Why the tempo used to read 139.6 instead of 140.** The autocorrelation
fallback in `_estimate_bpm` runs on a 100 Hz onset grid, so only whole lags
exist — and in BPM terms they are far apart at fast tempos. Near 140 the only
reachable values are 142.86 (lag 42) and **139.53** (lag 43); 140 itself cannot
be produced at all. The peak is now refined between samples by fitting a
parabola through it and its neighbours, and the result rounded to a whole number
when it is within 0.75 of one, because neither tempo source here resolves
fractions of a BPM and `setcpm(139.6/4)` claims a precision that does not exist.

That fallback also picked the wrong metrical level in **9 of 13** synthetic kit
patterns at known tempos (60→120, 174→87, 160→80): a rhythm repeats at its beat
*and* at every multiple and division of it, so raw autocorrelation is nearly as
happy with half or double. A log-normal tempo prior around 120 BPM (Ellis 2007,
as librosa's own estimator uses) takes that to 6 of 13 — better, still weak.
It matters less than it sounds: our own files carry a beat-tracked tempo in the
MIDI header, which `_estimate_bpm` prefers, and the fallback only runs for
foreign MIDI or a header pinned at pretty_midi's default 120.

**The MIDI preview.** Two things were wrong with it beyond note accuracy.

`pretty_midi.Instrument.synthesize` **returns zeros for any `is_drum` track**,
so every preview had been pitched parts only — the drums were not quiet, they
were absent. They are now rendered as noise bursts shaped per drum key.

And the GM programs written into the file never reach the preview at all:
pretty_midi ignores programs and renders whatever waveform it is handed. So
each track is now synthesized separately with its own waveshape (round for bass,
odd-harmonic for guitar, saw-like for a lead, harmonic stack for piano) and the
results mixed. Parts are levelled to equal RMS rather than equal peak — drums
are almost entirely transient, so peak levelling left them at 0.042 RMS against
the bass's 0.275. Levelling happens *after* normalising each part to unit peak,
because pretty_midi sums overlapping notes without normalising and a dense track
comes back at RMS 30 where a sparse one is near 1.

`GET /midi/preview/{id}?track=<name>` solos one part, rendered on demand
(~0.2s) and cached beside the MIDI; `GET /midi/tracks/{id}` lists them. Several
parts at once is right for judging a transcription as a whole and useless for
checking whether one line is correct.

An earlier attempt made every track Acoustic Grand Piano, reasoning that a
transcription is read rather than performed. Listening disproved it: three piano
tracks at once are *harder* to follow than three different instruments, because
nothing cues which line is which.

**Measuring a transcription with no ground truth.** Generated audio has no
reference MIDI, so transcription quality cannot be scored by comparison.
`scripts/amt_eval.py` instead asks two questions of the audio itself:

- **note support** — for each transcribed note, is there energy at that pitch,
  at that time, in the recording? Scored as the note's rank among semitone CQT
  bins in its frames, so it asks whether the pitch was *prominent* rather than
  merely audible.
- **chroma agreement** — per-frame cosine similarity between the MIDI's pitch
  class profile and the recording's.

Both are reported against three nulls, and **the nulls are the whole point**: a
similarity figure alone means nothing, because any two pieces of tonal music
already share most of their pitch content. Randomly reshuffled pitches score
**63.6% note support** on the track they came from. The nulls are the same notes
transposed a tritone, the same notes slid 30s through the song, and pitches
redrawn from the track's own distribution.

Validated at both ends before being used: a MIDI scored against its own
synthesis gives **+36.0pp support / +0.254 chroma** over the best null, and the
same MIDI against an unrelated song gives **−3.1pp / −0.059** — the metric says
"this is not a transcription of this song" when that is true.

One correction worth recording: chroma agreement originally averaged only over
frames where *both* sides sounded, which made deleting notes free — the frames
they covered simply left the average, and a transcription of the bass alone
scored far above a complete one. It now averages over every frame where the
*recording* has energy, scoring zero where the transcription is silent, so
coverage costs something.

The `note support` metric has a limit worth stating: separation leakage and
overtones *are* present in the audio, so they pass it. It measures whether a
written note is real, not whether it is musically meaningful; chroma agreement
is the discriminating one when judging leakage.

**Leakage stems are dropped.** `htdemucs_6s` emits all six stems whether or not
the song contains the instrument, so a phonk track with no piano still gets a
piano stem of bleed — which Basic Pitch transcribes into confident notes. On one
measured track that was 604 piano and 940 guitar notes out of 3996, from stems
26 dB and 11 dB below the bass. Stems more than `_LEAKAGE_DB` (20 dB) below the
loudest are now skipped, with the loudest *pitched* stem always kept so a quiet
mix under loud drums cannot transcribe to percussion only.

Measured across three tracks, chroma gain over null:

| track | all stems | leakage gate |
|---|---|---|
| b65ed284 | +0.114 | **+0.129** |
| 003fe159 | +0.217 | **+0.232** |
| 9c16f4d0 | +0.077 | +0.076 |

A modest gain, and gating harder is not supported by the evidence: past this
point the two metrics move in opposite directions, and on one track a tighter
gate deleted the entire harmony.

Two things that sounded like they would help and did not, both measured:
per-instrument frequency bounds on Basic Pitch (bass 30–400 Hz, vocals
80–1100 Hz and so on) moved chroma gain by −0.001, and raising
`onset_threshold` to 0.6 or `minimum_note_length` to 200 ms made it worse. The
transcriber's settings were not the problem; what it was being fed was.

**A transformer transcriber was tried and rejected, on measurements.**
MT3 is the obvious upgrade from Basic Pitch — a multi-instrument transformer
that reads the mix directly, so separation artifacts never enter the picture.
`scripts/mt3_transcribe.py` runs it via
[mt3-infer](https://github.com/openmirlab/mt3-infer). **The environment is not
installed** — it was measured, rejected, and its 1.3 GB deleted; the script is
kept so the experiment can be repeated:

```
uv venv third_party/mt3/.venv --python 3.11
uv pip install --no-cache --python third_party/mt3/.venv/Scripts/python.exe \
    "mt3-infer[torch]" "transformers<5"
PYTHONIOENCODING=utf-8 third_party/mt3/.venv/Scripts/python.exe \
    scripts/mt3_transcribe.py <audio.wav> <out.mid> mr_mt3
```

Two install notes, both costing an hour to find: mt3-infer pulls
`transformers` 5.x, which has dropped `T5Stack.get_extended_attention_mask` and
fails the forward pass, and its checkpoint downloader prints a `✓` that crashes
on a cp1252 Windows console. It also needs torch 2.14 against the main
environment's 2.1, hence the separate venv.

On a generated track MR-MT3 scored **+3.1pp support / −0.015 chroma** — the same
level as a transcription of a *different song* (−3.1pp / −0.059). It took
**58 minutes** on CPU to produce it.

Before blaming the model, the wrapper was checked against audio whose every note
is known (a synthesised scale and triads, 38 notes, in
`scripts/amt_sanity.py`). MT3 is fine:

| on clean, known-note audio | detected | precision | recall | F1 |
|---|---|---|---|---|
| MR-MT3 | 37 / 38 | 0.84 | 0.82 | **0.83** |
| Basic Pitch | 49 / 38 | 0.71 | 0.92 | 0.80 |

So MT3 is *more* precise than Basic Pitch on clean material — 37 detections for
38 notes against Basic Pitch's 49, which is the overtone spam showing up. The
ranking reverses completely on generated music: Basic Pitch on separated stems
gets +21.9pp where MT3 on the mix gets +3.1pp.

The conclusion is not "MT3 is bad" but that **separation-then-transcribe is the
right architecture for this material**, and the transcriber is not the
bottleneck. Generated music is far outside MT3's training distribution
(Slakh/MAESTRO-style clean multitrack renders); splitting the problem into
stems is what makes it tractable at all. The MT3 path is left in place, unused
by the pipeline, since it is the better transcriber on clean input.

**Checked against a commercial transcription.**
[Songscription](https://www.songscription.ai/) transcribed one of our generated
tracks, which gives a reference for the same audio. Note what it produces: you
choose *one instrument* and it returns that instrument from the mix — the
reference here is a **Bass Guitar** part, not the whole song. It is a narrower
job than ours, so this is not like-for-like.

Comparing the chord symbols printed above its first 17 bars:

| | ours | Songscription |
|---|---|---|
| tempo | 136.4 BPM | 134 BPM (1.8% off) |
| chord root agreement | **13 / 17 bars (76%)** | — |
| mean chord-tone overlap | 58% | — |

The 76% is generous to us: their bars carry up to four chord changes where we
emit one per bar, and our chord was credited against whichever of theirs it
matched best.

**The comparison found a real bug.** The key came back *A major* with a
confidence of **0.007** — indistinguishable from a guess — and `.scale("A:major")`
was written into the Strudel regardless, because only `pcs` respected
`_KEY_CONFIDENCE_MIN` and the scale name did not. A wrong scale makes every
emitted degree wrong. Two fixes: the mode is now settled on whichever third is
actually sounding (the Krumhansl profiles weight all seven degrees, so on a
bass-heavy transcription the thirds barely register and major/minor is a coin
flip — here the major third was 1% of duration against the minor third's 4%,
with the leading tone absent), and a weak estimate is now labelled `(uncertain)`
in the header instead of asserted. Regression-checked on unambiguous C major,
C minor and A minor, all still correct.

The residual disagreement is systematic and was *not* fixed: we name `Am9`,
`Gmaj9`, `C6` where the reference says `Am`, `G`, `C` — `Jaccard(Am9, Am)` is
exactly 3/5, which is most of the 60% scores. Two simplicity rules were tried
and neither shipped. Preferring simpler chords globally made it **worse**
(58% → 55%), because a simpler chord on a *different root* can win. Fixing the
root first and simplifying only the quality reached 67% overlap at one specific
margin, at the cost of a root match — not enough to justify tuning a constant on
17 bars of a single song. The extra ninths are genuinely present in our
transcribed notes (distortion harmonics and overtones); the reference lacks them
because it transcribed one clean instrument, which points at the input rather
than at the chord namer.

Also measured and rejected: pYIN monophonic pitch tracking for the bass stem,
on the theory that a polyphonic model reporting overtones as voices is the wrong
tool for a single line. It came out a wash — support +8.5pp against Basic
Pitch's +7.3, chroma +0.175 against +0.189 — at 14x the runtime (38s vs 2.7s).

**Strudel output is written the way Strudel is written.** The emitter used to
print every step of every bar as a literal token, which capped it at 8 bars
(~14s of a 3-minute song) because past that it was unreadable. Hand-written
Strudel stays short by *naming* things, so this does the same: a chord symbol
instead of its notes, `.struct()` per drum sound instead of one string mixing
them, `!4` instead of four repeats, `<a b>` alternation instead of `.slow(n)`.

The snippet is laid out as **drums / harmony / bass / melody**, not one part per
separated stem. Stems are what a source separator produced; they are not the
parts of the music, and the first version emitted six chord tracks all playing
the same progression — including the bass and the vocal, which are monophonic.
The harmony is detected once from every pitched note pooled, since a song has
one harmony and each stem only sees part of it.

Two modes, chosen in the UI: `chords` names the harmony and gives bass and
melody as scale degrees; `notes` keeps every transcribed pitch.

**Rest runs are compacted, not printed.** A quantized bar arrives as a fixed
grid — sixteen slots whether or not anything is in them — and written out
literally that is what it read like: `x ~ ~ ~ x ~ ~ ~ x ~ ~ ~ x ~ ~ ~`. Nobody
writes Strudel that way, and the rests were most of the snippet's bulk.
`compact_steps` applies four mini-notation rewrites, in this order:

| rewrite | grid | emitted |
|---|---|---|
| halve while odd slots are empty | `x ~ ~ ~ x ~ ~ ~ …` | (recurses) |
| euclidean `(k,n)` | `x ~ ~ ~ ~ ~ x ~ ~ ~ ~ ~ x ~ ~ ~` | `x(3,8)` |
| `@n` absorbs the rest run after an event | `~ ~ ~ ~ x ~ ~ ~ ~ ~ ~ ~ x ~ ~ ~` | `~ x@2 x` |
| `*n` for one token repeated evenly | `x ~ ~ ~ x ~ ~ ~ x ~ ~ ~ x ~ ~ ~` | `x*4` |

Every one is lossless: a property test expands the emitted notation back to
onset positions and compares against the grid it came from — 20000/20000 random
grids round-trip to the exact same events at the exact same times
(`_bjorklund` is verified against the published E(3,8), E(5,8), E(2,5) and
E(7,16) necklaces). Euclidean rhythms are emitted **un-rotated only**: Strudel
takes a third rotation argument, but which way it turns is not something the
docs pin down, and the `@n` path is always correct.

Compaction alone still left one 424-character line, so a pattern over 72
characters is emitted as a backtick template with one alternation entry per
line — inside `<>` a newline is just whitespace. Longest line is now 91 (a
comment). On a 180s drift phonk: **1.6 KB `chords` / 2.6 KB `notes`**, from
2.6/4.4 KB before compaction and 6.6 KB before the role restructure.

One bug worth recording: `.scale()` takes **scale steps, not semitones**, so
`n("7")` on a seven-note scale is an octave, not a fifth. Emitting semitones
transposes everything wildly — a bass note came out at −36, meaning 36 scale
steps down rather than three octaves.

**Why some styles convert to MIDI/Strudel better than others.** Scoring each
part's mean distance to the nearest grid line against what uniformly random
note times would score (1.0 = no better than noise):

| part | straight 8ths | straight 16ths |
|---|---|---|
| drums (our onset detection) | **0.29** | 0.51 |
| any pitched part (Basic Pitch) | 0.83–0.99 | 0.82–0.99 |

The drum track carries effectively all the rhythm that survives conversion, so
styles with clear percussive transients convert well and styles carried by
sustained or heavily processed pitched material do not — the dark cabaret track
has no drum track at all, which is why it converts into something rhythmically
vague. Measured and rejected: finer grids (32nds score *worse* against noise,
0.73 — they fit everything and so discriminate nothing), dropping
low-confidence notes (moved pitched parts by 0.01–0.06, drums not at all), and
triplet/swung grids (fit worse than straight on every part). What did help was
grid phase: the converter assumed the first step lands at t=0, and fitting the
offset instead improved the phonk drum track from 0.67 to **0.51** and most
pitched parts slightly. Fixing this properly needs a better transcriber than
Basic Pitch for pitched material, not a better grid.

**Long generations loop, and structure tags did not fix it.** Measured on four
180s tracks, each repeats on a 6–64s cycle and the first and last 15s are
indistinguishable from the middle — they start and stop rather than opening and
closing. ACE-Step's documented mechanism for this is the `lyrics` field, which
is a *temporal* description, not just words: for instrumental music it takes
structure tags (`[Intro - sparse]`, `[Climax - powerful]`, `[Outro - fade
out]`). The app only ever sent a bare `[Instrumental]`, so the *Song structure*
option now sends a real plan, thinned to fit short durations.

It does not measurably work. Five runs each of `off` and `full` at 120s, same
prompt and locked BPM:

| | short-lag loop | long-lag loop | segments | intro dB | outro dB |
|---|---|---|---|---|---|
| off | 0.06 | 0.05 | 187 | −1.0 | −10.5 |
| full | 0.04 | 0.05 | 150 | −2.3 | −7.3 |

Every one of those overlaps between the groups. At n=2 the intro and segment
counts separated cleanly and it looked like a win; at n=5 that vanished
entirely, which is the same lesson as the sampler A/B earlier in this file.
Loop strength is split by lag deliberately — a short-lag repeat is a riff
cycling, a long-lag one is a section returning, and a metric that adds them
together calls a working song structure a defect.

**Listening disagreed, and listening wins.** On the first try with `full` on a
3-minute drift phonk the difference was obvious by ear -- "much more variable
and still clean" -- against metrics that said nothing had changed. So the
measurement was inadequate, not the feature. Worth being precise about what
failed: segment counts and lag-split self-similarity capture whether a track
*repeats*, and nothing here captures whether it *develops* -- whether an idea
arrives, grows and resolves. That is most of what makes three minutes hold up,
and none of these numbers see it. Treat the table above as "these particular
statistics did not move", not as evidence of no effect.

The option is kept and the untested lead is `shift`,
which the docs describe as exactly this knob — "larger shift → more effort
spent on early denoising (building large structure from pure noise) … clearer
overall framework". Turbo already runs at 3.0, but SFT runs at 1.0, so the
higher-quality model is currently the one told to care least about structure.

Knobs still untested: `cfg_interval_start`/`cfg_interval_end` (non-Turbo only),
`use_adg`, DCW on SFT, step counts above 50, and ACE-Step's `keyscale` /
`timesignature`, which the app also never sets.

**ACE-Step ends songs with about 3 s of silence.** The worker asks for 3 s
extra and trims back to the requested length with a short fade-out
(`tail_pad_sec` / `fade_out_sec` in the manifest). Remix mode is not padded.

**Quality scoring is off for ACE-Step.** The FAD verdict thresholds
(`fad_common.py`) were calibrated on a different model's output and flag every
ACE-Step song "unsatisfactory". Library entries from before this change keep
their old verdicts. Set `"quality_scoring": true` on a model only after
recalibrating the ceilings against its output.

**Strudel export is a quantized sketch, not a transcript.** A MIDI entry's
*STRUDEL* button renders it as [Strudel](https://strudel.cc) pattern code
(`GET /midi/strudel/{id}`, derived on demand by `pipeline/strudel_convert.py`
— nothing is stored). Strudel is cycle-based, so notes are snapped to a 1/16
grid: swing and triplets are lost, and notes shorter than a step can collide.
Only the first 8 bars are emitted, to keep the snippet readable.

Each track gets a sound and a short effect chain (`.lpf()`, `.room()`,
`.gain()`, `.bank("RolandTR909")` on drums) so the snippet plays as something
musical rather than a bare list of pitches. Those are per-instrument starting
points, **not** anything detected from the audio — they are the first thing
worth changing.

**Off-key notes are corrected against the detected key.** Basic Pitch reports
overtones and octave errors as real notes; individually they pass, but once
tracks stack they are what sounds sour. The key is estimated from a
duration-weighted pitch-class histogram (Krumhansl-Schmuckler), and notes
outside that scale are moved to the nearest scale tone — at most one semitone,
never dropped. On two test clips this moved 5.1% and 3.6% of notes and left
nothing off-key.

Correction is skipped when the key estimate is weak, since a bad guess would
drag good notes out of tune to fix a handful of bad ones. Confidence compares
the winner against the best *harmonically different* key, because a key and
its relative minor share every pitch class and so correct identically.
Calibration: an atonal chromatic run scores 0.000, a clear C major arpeggio
0.117, and real songs land at 0.03–0.07 against a 0.03 threshold. When the
estimate falls short, the header says so instead of silently leaving the
clashes in.

An alternative worth knowing: Strudel's own `n("0 2 4").scale("C:minor")`
works in scale degrees, which makes off-key notes unrepresentable. The
literal note names used here stay closer to the transcription, at the cost of
needing this correction step.

**MIDI conversion is a transcription, not a transfer.** Demucs splits the song
into six stems; each non-silent one becomes a General MIDI track. Stems
quieter than -50 dBFS are dropped, so a sparse song yields fewer tracks.

The two transcription paths are deliberately different:

| | Method | Result |
|---|---|---|
| Pitched stems (bass, guitar, keys) | Basic Pitch | notes, reasonable |
| Drums | onset detection + band classification | `bd`/`sd`/`hh` on real GM keys |

Basic Pitch estimates *pitch*, which percussion does not have — pointed at a
drum stem it returned about a dozen arbitrary notes with no rhythm in them. So
drums are instead onset-detected and each hit classified by which frequency
band carries it, scored against that band's own average across the stem
(comparing raw magnitudes labels nearly everything a kick, since bass
frequencies carry far more energy than cymbals). On a 20 s rock clip that took
the drum track from 16 notes to 89 hits split 41/34/14 across kick/snare/hat.
The split stops at those three: toms, and ride versus crash, are not reliably
separable this way.

**Tempo and time signature come from the audio, not the notes.** Both are
measured during conversion and written into the MIDI header, rather than
inferred afterwards from already-quantized notes — `pretty_midi.estimate_tempo()`
read a 90 BPM lo-fi track as 178.

With `.venv-beat` installed, [Beat This](https://github.com/CPJKU/beat_this)
tracks beats *and downbeats*, so the meter is measured rather than assumed;
the Strudel export sizes its bars and its `setcpm()` from it. Without it,
librosa supplies a tempo only and 4/4 is assumed. The two disagree more than
you might expect — on one clip librosa said 123 BPM where Beat This said 93.8
with perfectly consistent four-beat bars.

A meter is only trusted when the detected bar lengths agree (≥60% of bars);
below that it falls back to 4/4, since a confidently wrong 5/4 is worse than
an assumed 4/4. One test clip hit exactly that case at 0.50 agreement.

All of this is still estimation — correct `setcpm()` by ear.

Measured on a 20 s rock clip: 4 tracks, ~580 notes, about 14 s once the models
are cached (35 s on the first run, which downloads them).

**Vocals are wordless — there is no lyrics input.** ACE-Step is a
lyrics-conditioned model (up to 4096 characters, structure tags like
`[verse]`/`[chorus]`, multilingual), but the app never sends any. The
*Instrumental only* option just switches the model's `lyrics` field between
`"[Instrumental]"` and the empty string, so turning it off asks for singing
with nothing to sing — expect vocalise or gibberish, not words. Getting real
lyrics needs either a text input wired through `options` (the manifest's
option types are currently `bool`/`number`/`choice`, so this would add a
`text` type) or ACE-Step's LM writing them from the caption, which needs the
0.6B LM checkpoint — only the 1.7B is downloaded, and that one wants more VRAM
than an 8 GB card has.

**Playback effects never touch generation.** The effects panel is a Web Audio
chain (EQ, compressor, reverb, gain) applied to *playback*, and to downloads
only when its settings are non-neutral. It cannot change what the model
produces, and every parameter defaults to transparent, so it does nothing at
all until moved. A fixed safety limiter sits after the user's gain and is not
exposed: the model peak-normalises to -1 dBFS, so a boosted EQ band or a gain
above unity would otherwise clip hard.

**No per-file ownership check.** There is a login gate (username `test`,
seeded password, session cookie) — the app is browsable without logging in,
but generating/uploading/saving requires signing in first. What's still
missing is per-owner authorization: every generated file is addressed by a
UUID (`/audio/{id}`, `/image/{id}`, `/download/{id}`, etc.), and once
logged in, the backend does not check who owns which file — any
authenticated client that knows or can guess an id can fetch that file.
`backend/app/routers/audio.py` marks the gap explicitly:

```python
# DB gate — only serve files that finished successfully.
# TODO: add `AND owner_id=?` here once auth exists.
```

This is a known, deliberately deferred gap rather than an oversight — with
only a single shared login, there's no per-user owner to check against yet.
Adding real multi-user accounts (and the corresponding `owner_id` filtering
across the audio/image/download endpoints) is planned future work, not
something already mitigated.
