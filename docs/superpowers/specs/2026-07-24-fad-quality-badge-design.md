# FAD Quality Badge — Design

## Purpose

Surface an automated, genre-aware quality signal on generated songs, so the
user can see at a glance in the Library whether a song is likely to sound
"good" for its genre, without having to listen to every generation.

This is built on Fréchet Audio Distance (FAD) scoring, already validated as a
standalone tool (`check_fad.py`) across ~9 hand-labeled real songs spanning
three genre buckets. It reliably tracks broad genre-fit/quality issues (e.g.
an empty/disjointed ambient track, a jazz track "all over the place"). It
does **not** reliably catch the specific "sparkle"/ringing codec artifact
occasionally produced by MusicGen — that remains a known, unsolved gap and is
explicitly out of scope for this feature.

## Non-goals (explicitly out of scope for this iteration)

- Auto-retry / regeneration when a song scores unsatisfactory.
- Backfilling scores for songs that already exist in the Library before this
  ships — those simply show no badge.
- A persistent/warm scoring service — each song is scored via a fresh
  subprocess call. Revisit only if per-song subprocess overhead becomes a
  real problem (it currently adds a few seconds against a generation time of
  tens of seconds to minutes).
- Showing the raw numeric FAD score in the UI — pass/fail only.
- Detecting the "sparkle"/ringing artifact specifically — FAD does not catch
  this; no replacement detector exists yet (four independent DSP approaches
  were tried and abandoned during exploration — see project conversation
  history for 2026-07-23/24).

## Architecture

### Shared genre-bucket definitions

`check_fad.py`'s `GENRE_BUCKETS` dict (dense/jazz/ambient, with keyword lists
and ceilings) and `classify_genre()` function move into a small shared module
importable by both `check_fad.py` (the manual dev CLI) and the new backend
script, so bucket definitions never drift between the two call sites.
Proposed location: `fad_common.py` at repo root (same isolated-environment
constraints as `check_fad.py` — only ever imported under `.venv-fad`).

### New script: `fad_score_one.py`

Runs under `.venv-fad`. Takes a WAV path and a prompt string, and prints a
single JSON line to stdout:

```json
{"score": 103.49, "bucket": "jazz", "verdict": "satisfactory"}
```

or, on any internal failure (corrupt audio, too short, model load error):

```json
{"error": "<short description>"}
```

Internally: stages the single file into a throwaway temp directory (same
hardlink-or-copy trick `check_fad.py` uses, via `tempfile.TemporaryDirectory`
so it self-cleans), scores it against the bundled `fma_pop` baseline using
the `encodec-emb` model, classifies the genre bucket from the prompt via the
shared module, and prints the JSON result. No persistent `.fad_eval/`-style
cache — production songs are scored exactly once, so there's no repeat-run
benefit to caching embeddings here (unlike the manual dev tool).

Exit code 0 on success (even if genre is "unclassified" — that's a valid
result, not an error) — non-zero only on genuine failure to produce a score.

### Database

Add two nullable columns to `files` via the existing `_migrate_db()`
ALTER-TABLE-in-a-try/except pattern in `database.py`:

- `fad_score REAL`
- `fad_verdict TEXT` — `'satisfactory'`, `'unsatisfactory'`, or `NULL`.

An "unclassified" genre (prompt doesn't match any bucket keyword) still
produces a real score and verdict, scored against `DEFAULT_CEILING` — that's
not a failure case, so `fad_verdict` gets set normally for it. `NULL` is
reserved for the cases where scoring genuinely didn't happen: the subprocess
failed/timed out, or the song predates this feature. This avoids silently
hiding a badge just because a prompt didn't happen to contain a genre
keyword.

### Worker integration (`jobs.py`)

After the existing code that moves the generated WAV into
`storage/converted/{file_id}.wav` and before the `UPDATE files SET
job_status='done', ...` call:

1. Skip entirely for `job.input_type == "midi"` (not a music generation).
2. Call `subprocess.run([".venv-fad/Scripts/python.exe", "fad_score_one.py",
   str(dest), "--prompt", prompt_used], capture_output=True, timeout=90)`.
   90s comfortably covers cold torch-import plus scoring a single song (both
   took a few seconds each in testing) with headroom to spare; a hang past
   that is treated as a failure and falls through to the no-score path.
3. Parse stdout as JSON. On success, extract `score`/`verdict`. On any
   failure (non-zero exit, timeout, malformed JSON, missing `.venv-fad`),
   log a warning and leave both DB fields `NULL` — **this must never raise**
   and must never fail the generation job itself.
4. Fold `fad_score`/`fad_verdict` into the existing single `UPDATE ...
   job_status='done'` statement (one DB write, not two).

This matches the earlier decision: scoring happens before the job is marked
done, so the badge is present the instant the song appears in the Library.

### `/library` endpoint

Add `fad_verdict` to the row selection and the returned JSON per song.
(`fad_score` is not exposed to the frontend — pass/fail only, per the
earlier decision.)

## Frontend

### Types (`types.ts` / wherever the Library item shape is defined)

Add `fad_verdict: 'satisfactory' | 'unsatisfactory' | null` to the Library
item type.

### Library card (`Library.tsx`)

In the existing metadata row (the one currently showing duration +
input-type, e.g. "0:30" / "TEXT"), add a small icon-only badge when
`fad_verdict` is non-null:

- `satisfactory` → a checkmark icon (e.g. lucide `Check`), muted/neutral
  tone consistent with the existing pill styling.
- `unsatisfactory` → a warning icon (e.g. lucide `AlertTriangle`), matching
  the `⚠` glyph already used elsewhere in this file for "expiring soon" —
  reusing an existing visual vocabulary rather than introducing a new one.
- `null` → nothing rendered (no layout gap, no placeholder).

No score number, no tooltip required for this iteration (can be added later
without a schema change if wanted).

## Error handling summary

| Failure point | Behavior |
|---|---|
| `.venv-fad` missing / script crashes / times out | Log warning, `fad_score`/`fad_verdict` stay `NULL`, job still marked `done` normally |
| Prompt doesn't match any genre keyword | Not a failure — scored against `DEFAULT_CEILING`, verdict set normally |
| Song predates this feature | `fad_verdict` is `NULL` (column didn't exist at generation time) — no badge |
| MIDI conversion job | Skipped entirely, never scored |

## Testing

- Unit-level: `fad_score_one.py` run standalone against a couple of the
  already-labeled test songs from this session (e.g. the lofi violin/50s
  jazz pair) to confirm it reproduces the same score/verdict as
  `check_fad.py` did.
- Integration: trigger a real generation through the running app, confirm
  the DB row gets `fad_score`/`fad_verdict` populated and `/library` returns
  it, confirm the badge renders correctly in the browser for both a
  satisfactory and an unsatisfactory result (may need to force a low
  ceiling temporarily, or reuse a known-bad prompt, to exercise the
  unsatisfactory path without waiting on luck).
- Failure-path: temporarily rename/break the `.venv-fad` path and confirm a
  real generation still completes and is marked `done` with `fad_verdict`
  `NULL`, proving the never-fail-the-job guarantee actually holds.
