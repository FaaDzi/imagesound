# FAD Quality Badge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-validated FAD genre-bucket scoring into the real app: every generated song gets scored automatically before it's marked done, and the Library shows a pass/fail badge.

**Architecture:** A new isolated-venv script (`fad_score_one.py`) scores one WAV file and prints JSON. The backend worker calls it via subprocess right after generation finishes, storing the result in two new nullable DB columns. `/library` exposes the verdict; the Library card renders a small icon badge. Genre-bucket definitions move out of `check_fad.py` into a shared, dependency-free module so the manual dev tool and the backend path can never drift apart.

**Tech Stack:** Python (backend, `.venv` and isolated `.venv-fad`), FastAPI, SQLite, React/TypeScript (frontend), fadtk/EncodecEmb (already installed in `.venv-fad`).

## Global Constraints

- `fad_score_one.py` and `fad_common.py` must run under `.venv-fad`'s interpreter — `fadtk` requires `torch>=2.3`, which conflicts with the main app's pinned `torch==2.1.0` (needed by `audiocraft`/`xformers`). Never import `fadtk` from the main `.venv`.
- Any multiprocessing-touching script (anything calling `fadtk.fad_batch.cache_embedding_files`) MUST guard its entry point with `if __name__ == "__main__":` and keep the `torchaudio.load`/`torchaudio.save` monkeypatches at module level, outside that guard — Windows silently reruns a script's top-level code in spawned worker processes but skips the `__main__` guard; getting this wrong causes an infinite respawn loop (see `check_fad.py`'s history).
- FAD scoring must never fail a generation job. Every failure path (subprocess crash, timeout, malformed JSON, missing `.venv-fad`) must be caught and result in `fad_score`/`fad_verdict` staying `NULL` — the job still completes normally.
- No score number is exposed to the frontend — pass/fail only (`fad_verdict`), per the approved design (`docs/superpowers/specs/2026-07-24-fad-quality-badge-design.md`).
- No auto-retry, no backfill for pre-existing songs, no persistent scoring service — explicitly out of scope for this plan.

---

### Task 1: Shared genre-bucket module + `check_fad.py` refactor

**Files:**
- Create: `fad_common.py` (repo root)
- Modify: `check_fad.py:79-104` (remove inline bucket definitions, import from `fad_common` instead)
- Test: `scratch_test_fad_common.py` (repo root, deleted at the end of this task — not part of the shipped feature)

**Interfaces:**
- Produces: `fad_common.GENRE_BUCKETS: dict[str, tuple[float, list[str]]]`, `fad_common.DEFAULT_CEILING: float`, `fad_common.classify_genre(prompt: str) -> tuple[str, float]` — used by both `check_fad.py` and `fad_score_one.py` (Task 2).

- [ ] **Step 1: Write the failing test**

Create `scratch_test_fad_common.py` at repo root:

```python
from fad_common import classify_genre

cases = [
    ("187 BPM, rythm game, geometry dash, psytrance", "dense", 150),
    ("lofi piano, 50's jazz, hot coffee", "jazz", 170),
    ("Koto lead melody, deep ambient pads, subtle bass drone, mysterious and serene, slow tempo.", "ambient", 300),
    ("a completely generic prompt with no genre words at all", "unclassified", 200),
]

for prompt, expected_bucket, expected_ceiling in cases:
    bucket, ceiling = classify_genre(prompt)
    assert bucket == expected_bucket, f"{prompt!r}: expected bucket {expected_bucket!r}, got {bucket!r}"
    assert ceiling == expected_ceiling, f"{prompt!r}: expected ceiling {expected_ceiling}, got {ceiling}"

print("All classify_genre cases passed.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scratch_test_fad_common.py` (any Python works — this is pure stdlib, no venv needed)
Expected: `ModuleNotFoundError: No module named 'fad_common'` — the module doesn't exist yet.

- [ ] **Step 3: Create `fad_common.py`**

```python
"""Shared genre-bucket definitions for FAD scoring.

Pure Python, no torch/fadtk imports -- safe to import from either the main
.venv or the isolated .venv-fad. This is the single source of truth for
bucket ceilings; check_fad.py (manual dev tool) and fad_score_one.py
(backend scoring) both import from here so they can never drift apart.

GENRE BUCKETS -- first pass, calibrated from a handful of hand-labeled songs
(see project conversation history, 2026-07-23/24). Expect to retune ceilings
as more songs get labeled; these are a starting point, not settled numbers:
    dense     (EDM, rock, rhythm-game, psytrance, techno, ...)  ceiling ~150
    jazz      (multi-instrument: piano, drums, clapping, funk, ...) ceiling ~170
    ambient   (sparse, "absence of information": drone, koto, calm, ...) ceiling ~300
    unclassified (prompt didn't match any bucket keyword) ceiling ~200, flagged
"""

# (ceiling, keywords) -- prompt is lowercased before matching. Order matters:
# first bucket whose keyword appears wins, so more specific buckets go first.
GENRE_BUCKETS: dict[str, tuple[float, list[str]]] = {
    "dense": (150, [
        "edm", "rock", "rhythm game", "psytrance", "techno", "dance", "electro",
        "dubstep", "drum and bass", "dnb", "hardstyle", "trance", "house",
        "metal", "punk", "hyperpop", "synth lead", "high-energy", "high energy",
    ]),
    "jazz": (170, [
        "jazz", "piano", "clapping", "drums", "funk", "soul", "swing", "blues",
        "big band", "hot coffee",
    ]),
    "ambient": (300, [
        "ambient", "drone", "serene", "calm", "meditative", "soundscape",
        "koto", "atmospheric", "pad", "lofi", "lo-fi",
    ]),
}
DEFAULT_CEILING = 200  # unclassified prompt -- moderate fallback, flagged as such


def classify_genre(prompt: str) -> tuple[str, float]:
    p = prompt.lower()
    for bucket, (ceiling, keywords) in GENRE_BUCKETS.items():
        if any(kw in p for kw in keywords):
            return bucket, ceiling
    return "unclassified", DEFAULT_CEILING
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scratch_test_fad_common.py`
Expected: `All classify_genre cases passed.`

- [ ] **Step 5: Refactor `check_fad.py` to import from `fad_common`**

In `check_fad.py`, replace lines 79-104 (the `CACHE_DIR = ...` line stays; only the `GENRE_BUCKETS`/`DEFAULT_CEILING`/`classify_genre` block is removed):

Remove:
```python
# (ceiling, keywords) -- prompt is lowercased before matching. Order matters:
# first bucket whose keyword appears wins, so more specific buckets go first.
GENRE_BUCKETS = {
    "dense": (150, [
        "edm", "rock", "rhythm game", "psytrance", "techno", "dance", "electro",
        "dubstep", "drum and bass", "dnb", "hardstyle", "trance", "house",
        "metal", "punk", "hyperpop", "synth lead", "high-energy", "high energy",
    ]),
    "jazz": (170, [
        "jazz", "piano", "clapping", "drums", "funk", "soul", "swing", "blues",
        "big band", "hot coffee",
    ]),
    "ambient": (300, [
        "ambient", "drone", "serene", "calm", "meditative", "soundscape",
        "koto", "atmospheric", "pad", "lofi", "lo-fi",
    ]),
}
DEFAULT_CEILING = 200  # unclassified prompt -- moderate fallback, flagged as such


def classify_genre(prompt: str) -> tuple[str, float]:
    p = prompt.lower()
    for bucket, (ceiling, keywords) in GENRE_BUCKETS.items():
        if any(kw in p for kw in keywords):
            return bucket, ceiling
    return "unclassified", DEFAULT_CEILING
```

Add in its place:
```python
from fad_common import GENRE_BUCKETS, DEFAULT_CEILING, classify_genre
```

(`DEFAULT_CEILING` is imported for symmetry even though `check_fad.py` doesn't reference it directly outside `classify_genre` — keeps the import list matching what conceptually belongs together.)

- [ ] **Step 6: Smoke-test `check_fad.py` still works end-to-end after the refactor**

Run (from repo root):
```
.venv-fad\Scripts\python.exe check_fad.py pipeline/output/melody_b0791492.wav --prompt "calming lofi, rainy feel, jazz, instrumental. violin"
```
Expected: prints a `FAD scores` section ending in a line like
`melody_b0791492.wav: <some number> -- SATISFACTORY or UNSATISFACTORY (bucket=jazz, ceiling=170)`
(the exact score doesn't matter here — what matters is it runs without a Python exception and correctly resolves `bucket=jazz, ceiling=170`, proving the import refactor didn't break anything)

- [ ] **Step 7: Delete the scratch test file and commit**

```bash
rm scratch_test_fad_common.py
git add fad_common.py check_fad.py
git commit -m "Extract genre-bucket definitions into shared fad_common module"
```

---

### Task 2: Single-file backend scorer (`fad_score_one.py`)

**Files:**
- Create: `fad_score_one.py` (repo root)
- Test: manual run against an existing file (no separate test file needed — this script IS the testable unit, invoked directly)

**Interfaces:**
- Consumes: `fad_common.classify_genre` (Task 1)
- Produces: a CLI script invoked as `<venv-fad-python> fad_score_one.py <wav_path> --prompt "<text>"`, printing one JSON line to stdout — `{"score": float, "bucket": str, "verdict": "satisfactory"|"unsatisfactory"}` on success (exit 0) or `{"error": str}` on failure (exit 1). This exact invocation shape is what Task 4 (`jobs.py`) calls via `subprocess.run`.

- [ ] **Step 1: Create `fad_score_one.py`**

```python
"""Score a single generated song for the backend, printing JSON to stdout.

Must be run with the ISOLATED .venv-fad interpreter -- see check_fad.py's
module docstring for why fadtk can never share an environment with the main
app. Not meant to be run by hand for spot-checking -- use check_fad.py for
that; this script is invoked by backend/app/jobs.py after each generation.

Usage:
    .venv-fad\\Scripts\\python.exe fad_score_one.py <wav_path> --prompt "<prompt text>"

Prints exactly one JSON line to stdout and sets the exit code accordingly:
    {"score": 103.49, "bucket": "jazz", "verdict": "satisfactory"}   (exit 0)
    {"error": "<description>"}                                       (exit 1)
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import soundfile as sf
import torch
import torchaudio

# See check_fad.py's module docstring for why this monkeypatch exists (this
# venv's torchaudio routes load/save through torchcodec, which needs FFmpeg
# shared libraries not installed on this machine). Must stay at module level,
# outside any function -- Windows' spawned multiprocessing workers re-run
# this file's top-level code but skip the __main__ guard below, so a patch
# placed inside main() would silently not apply in worker processes.
def _soundfile_load(path, *args, **kwargs):
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return torch.from_numpy(data.T).contiguous(), sr


_SUBTYPE_BY_BITS = {16: "PCM_16", 24: "PCM_24", 32: "PCM_32"}


def _soundfile_save(path, src, sample_rate, encoding=None, bits_per_sample=None, **kwargs):
    data = src.detach().cpu().numpy().T
    sf.write(str(path), data, int(sample_rate), subtype=_SUBTYPE_BY_BITS.get(bits_per_sample, "PCM_16"))


torchaudio.load = _soundfile_load
torchaudio.save = _soundfile_save

from fadtk.model_loader import EncodecEmbModel
from fadtk.fad import FrechetAudioDistance
from fadtk.fad_batch import cache_embedding_files

from fad_common import classify_genre


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav_path")
    parser.add_argument("--prompt", default="")
    args = parser.parse_args()

    wav_path = Path(args.wav_path).resolve()
    if not wav_path.is_file():
        print(json.dumps({"error": f"file not found: {wav_path}"}))
        return 1

    bucket, ceiling = classify_genre(args.prompt)

    try:
        with tempfile.TemporaryDirectory(prefix="fad_score_one_") as tmp:
            eval_dir = Path(tmp)
            staged = eval_dir / wav_path.name
            try:
                os.link(wav_path, staged)
            except OSError:
                shutil.copy2(wav_path, staged)

            model = EncodecEmbModel("24k")
            cache_embedding_files(eval_dir, model, workers=1)

            fad = FrechetAudioDistance(model, audio_load_worker=1, load_model=False)
            csv_out = eval_dir / "scores.csv"
            fad.score_individual("fma_pop", eval_dir, csv_out)

            score = None
            for line in csv_out.read_text().splitlines():
                if not line.strip():
                    continue
                path_str, score_str = line.rsplit(",", 1)
                if Path(path_str).name == staged.name:
                    score = float(score_str)
                    break

            if score is None:
                print(json.dumps({"error": "no score produced"}))
                return 1

            verdict = "satisfactory" if score <= ceiling else "unsatisfactory"
            print(json.dumps({"score": score, "bucket": bucket, "verdict": verdict}))
            return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it against a real file to verify success output**

Run (from repo root):
```
.venv-fad\Scripts\python.exe fad_score_one.py pipeline/output/melody_b0791492.wav --prompt "calming lofi, rainy feel, jazz, instrumental. violin"
```
Expected: stdout is exactly one line of valid JSON with keys `score` (a float), `bucket` (`"jazz"`), `verdict` (`"satisfactory"` or `"unsatisfactory"`). Exit code 0 (check with `echo %ERRORLEVEL%` in cmd or `echo $?` in bash immediately after).

- [ ] **Step 3: Run it against a missing file to verify the failure path**

Run:
```
.venv-fad\Scripts\python.exe fad_score_one.py pipeline/output/does_not_exist.wav --prompt "anything"
```
Expected: stdout is `{"error": "file not found: <resolved path>"}`, exit code 1.

- [ ] **Step 4: Commit**

```bash
git add fad_score_one.py
git commit -m "Add single-file FAD scoring script for backend use"
```

---

### Task 3: Database migration

**Files:**
- Modify: `backend/app/database.py:40-50` (`_migrate_db`)

**Interfaces:**
- Produces: `files.fad_score REAL` (nullable), `files.fad_verdict TEXT` (nullable) — consumed by Task 4 (worker writes them) and Task 5 (`/library` reads them).

- [ ] **Step 1: Add the two ALTER TABLE statements**

In `backend/app/database.py`, modify `_migrate_db`:

```python
def _migrate_db(conn: sqlite3.Connection) -> None:
    """Apply any schema migrations that may be missing on an existing DB."""
    for stmt in [
        "ALTER TABLE files ADD COLUMN saved INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE files ADD COLUMN source_file_id TEXT",
        "ALTER TABLE files ADD COLUMN fad_score REAL",
        "ALTER TABLE files ADD COLUMN fad_verdict TEXT",
    ]:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass  # column already exists
```

- [ ] **Step 2: Run the migration against the real app DB and verify**

Run (from repo root, using the main venv — this module has no fadtk dependency):
```
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); from app.database import init_db; init_db(); print('migrated')"
```
Expected: prints `migrated` with no exceptions.

Then verify the columns actually exist:
```
.venv\Scripts\python.exe -c "import sqlite3; c = sqlite3.connect('backend/app.db'); print([r[1] for r in c.execute('PRAGMA table_info(files)')])"
```
Expected: the printed column list includes `fad_score` and `fad_verdict` alongside the existing columns (`id`, `owner_id`, `input_type`, etc.).

- [ ] **Step 3: Commit**

```bash
git add backend/app/database.py
git commit -m "Add fad_score and fad_verdict columns to files table"
```

---

### Task 4: Worker integration

**Files:**
- Modify: `backend/app/jobs.py` (imports section near the top, and the WAV-move/UPDATE block)

**Interfaces:**
- Consumes: `fad_score_one.py` (Task 2) as a subprocess; `files.fad_score`/`files.fad_verdict` columns (Task 3).
- Produces: every completed non-MIDI generation job has `fad_score`/`fad_verdict` populated in the DB (or both `NULL` if scoring failed) at the moment `job_status` becomes `'done'`.

Note: MIDI conversion jobs (`job.input_type == "midi"`) already `continue` out of the worker loop in an earlier branch (around line 137, before reaching the WAV-move section below), so no separate MIDI-skip check is needed here — by the time this code runs, `job.input_type` is always `"image"`, `"text"`, or `"audio"`.

- [ ] **Step 1: Add `subprocess` and `json` imports**

In `backend/app/jobs.py`, the current imports block reads:

```python
import logging
import queue
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
```

Change to:

```python
import json
import logging
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
```

- [ ] **Step 2: Add the `_score_song` helper function**

Add this function after the existing `_PROJECT_ROOT` computation (right after the `sys.path.insert` block, before `_queue = queue.Queue()`):

```python
def _score_song(wav_path: Path, prompt: str | None) -> tuple[float | None, str | None]:
    """Score a finished song for quality via the isolated .venv-fad environment.

    Returns (score, verdict) -- both None if scoring failed for any reason.
    Must never raise: a scoring failure must never fail the generation job.
    """
    venv_python = _PROJECT_ROOT / ".venv-fad" / "Scripts" / "python.exe"
    script = _PROJECT_ROOT / "fad_score_one.py"
    try:
        result = subprocess.run(
            [str(venv_python), str(script), str(wav_path), "--prompt", prompt or ""],
            capture_output=True, text=True, timeout=90,
        )
        data = json.loads(result.stdout.strip().splitlines()[-1])
        if "error" in data:
            log.warning("[jobs] FAD scoring failed for %s: %s", wav_path.name, data["error"])
            return None, None
        return data["score"], data["verdict"]
    except Exception:
        log.exception("[jobs] FAD scoring crashed for %s", wav_path.name)
        return None, None
```

- [ ] **Step 3: Call it before marking the job done**

Find this existing block (currently around lines 235-245):

```python
            # Move the WAV from pipeline/output/ into storage/converted/{file_id}.wav
            dest = DIR_CONVERTED / f"{job.file_id}.wav"
            shutil.move(str(wav_path), str(dest))
            converted_key = dest.name

            conn.execute(
                """UPDATE files
                      SET job_status='done', converted_key=?, prompt=?, duration=?
                    WHERE id=?""",
                (converted_key, prompt_used, float(job.duration), job.file_id),
            )
            conn.commit()
            log.info("[jobs] Job %s done -> %s", job.file_id, converted_key)
```

Replace with:

```python
            # Move the WAV from pipeline/output/ into storage/converted/{file_id}.wav
            dest = DIR_CONVERTED / f"{job.file_id}.wav"
            shutil.move(str(wav_path), str(dest))
            converted_key = dest.name

            fad_score, fad_verdict = _score_song(dest, prompt_used)

            conn.execute(
                """UPDATE files
                      SET job_status='done', converted_key=?, prompt=?, duration=?,
                          fad_score=?, fad_verdict=?
                    WHERE id=?""",
                (converted_key, prompt_used, float(job.duration), fad_score, fad_verdict, job.file_id),
            )
            conn.commit()
            log.info("[jobs] Job %s done -> %s (fad_verdict=%s)", job.file_id, converted_key, fad_verdict)
```

- [ ] **Step 4: Restart the app and run a real generation to verify scoring populates**

Start the app (`python run.py` from repo root if not already running), then submit a short text generation:
```bash
curl -s -X POST http://127.0.0.1:8000/generate -H "Content-Type: application/json" -d "{\"prompt\":\"upbeat rock guitar riff\",\"duration\":8}"
```
Note the returned `id`, then poll until done:
```bash
curl -s http://127.0.0.1:8000/status/<id>
```
Once `"status":"done"`, verify the DB directly:
```
.venv\Scripts\python.exe -c "import sqlite3; c = sqlite3.connect('backend/app.db'); print(c.execute('SELECT fad_score, fad_verdict FROM files WHERE id=?', ('<id>',)).fetchone())"
```
Expected: a tuple like `(118.2, 'satisfactory')` — a non-null float and a non-null verdict string.

- [ ] **Step 5: Verify the never-fail-the-job guarantee**

Temporarily rename `.venv-fad` to `.venv-fad-disabled` (`mv .venv-fad .venv-fad-disabled` from repo root), submit another generation the same way as Step 4, and poll it to completion.

Expected: the job still reaches `"status":"done"` normally (generation itself is unaffected), and the DB row for that job has `fad_score=None, fad_verdict=None` — proving a scoring failure never blocks or fails generation.

Restore the environment afterward: `mv .venv-fad-disabled .venv-fad`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/jobs.py
git commit -m "Score generated songs via FAD before marking jobs done"
```

---

### Task 5: Expose `fad_verdict` via `/library`

**Files:**
- Modify: `backend/app/routers/library.py`

**Interfaces:**
- Consumes: `files.fad_verdict` (Task 3/4).
- Produces: each object in the `/library` response array gains a `"fad_verdict"` key (`"satisfactory"`, `"unsatisfactory"`, or `null`) — consumed by the frontend in Task 6/7.

- [ ] **Step 1: Add the column to the SELECT and the response dict**

Current `backend/app/routers/library.py`:

```python
@router.get("/library")
def get_library():
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, input_type, prompt, duration, saved, expires_at, created_at,
                      output_format, source_file_id
                 FROM files
                WHERE job_status = 'done'
                  AND expires_at > ?
                ORDER BY created_at DESC""",
            (now_iso,),
        ).fetchall()

    return [
        {
            "id":             row["id"],
            "input_type":     row["input_type"],
            "prompt":         row["prompt"],
            "duration":       row["duration"],
            "saved":          bool(row["saved"]),
            "expires_at":     row["expires_at"],
            "created_at":     row["created_at"],
            "output_format":  row["output_format"],
            "source_file_id": row["source_file_id"],
        }
        for row in rows
    ]
```

Replace with:

```python
@router.get("/library")
def get_library():
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, input_type, prompt, duration, saved, expires_at, created_at,
                      output_format, source_file_id, fad_verdict
                 FROM files
                WHERE job_status = 'done'
                  AND expires_at > ?
                ORDER BY created_at DESC""",
            (now_iso,),
        ).fetchall()

    return [
        {
            "id":             row["id"],
            "input_type":     row["input_type"],
            "prompt":         row["prompt"],
            "duration":       row["duration"],
            "saved":          bool(row["saved"]),
            "expires_at":     row["expires_at"],
            "created_at":     row["created_at"],
            "output_format":  row["output_format"],
            "source_file_id": row["source_file_id"],
            "fad_verdict":    row["fad_verdict"],
        }
        for row in rows
    ]
```

- [ ] **Step 2: Verify via the running app**

Run:
```bash
curl -s http://127.0.0.1:8000/library
```
Expected: the JSON array's objects each include a `"fad_verdict"` key — `"satisfactory"`/`"unsatisfactory"` for songs generated after Task 4 landed, `null` for older songs generated before this feature existed.

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/library.py
git commit -m "Expose fad_verdict in /library response"
```

---

### Task 6: Frontend type

**Files:**
- Modify: `src/api.ts:104-114` (`LibraryItem` interface)

**Interfaces:**
- Produces: `LibraryItem.fad_verdict: 'satisfactory' | 'unsatisfactory' | null` — consumed by Task 7.

- [ ] **Step 1: Add the field**

Current `src/api.ts`:

```typescript
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
}
```

Replace with:

```typescript
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
```

- [ ] **Step 2: Verify the frontend still type-checks**

Run (from repo root):
```bash
npx tsc --noEmit
```
Expected: no new errors introduced (there may be pre-existing unrelated errors in the project — compare against a run on the unmodified file if any show up, but nothing referencing `LibraryItem` or `fad_verdict` should appear).

- [ ] **Step 3: Commit**

```bash
git add src/api.ts
git commit -m "Add fad_verdict to LibraryItem type"
```

---

### Task 7: Library card badge

**Files:**
- Modify: `src/pages/Library.tsx:3` (icon imports) and `:315-322` (metadata row)

**Interfaces:**
- Consumes: `LibraryItem.fad_verdict` (Task 6).

- [ ] **Step 1: Add icon imports**

Current line 3:
```typescript
import { Trash2, Music, Database, Play, Pause, Download, Save, FileMusic, Shuffle } from 'lucide-react';
```

Replace with:
```typescript
import { Trash2, Music, Database, Play, Pause, Download, Save, FileMusic, Shuffle, Check, AlertTriangle } from 'lucide-react';
```

- [ ] **Step 2: Add the badge to the metadata row**

Current (lines 315-322):
```tsx
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-xs font-bold font-mono" style={{ color: 'var(--accent)' }}>
                        {isMidi ? 'MIDI' : formatDuration(item.duration)}
                      </span>
                      <span className="text-[10px] uppercase tracking-wider opacity-50" style={{ color: 'var(--text-muted)' }}>
                        {isMidi ? 'BASIC PITCH' : item.input_type.toUpperCase()}
                      </span>
                    </div>
```

Replace with:
```tsx
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-xs font-bold font-mono" style={{ color: 'var(--accent)' }}>
                        {isMidi ? 'MIDI' : formatDuration(item.duration)}
                      </span>
                      <span className="text-[10px] uppercase tracking-wider opacity-50" style={{ color: 'var(--text-muted)' }}>
                        {isMidi ? 'BASIC PITCH' : item.input_type.toUpperCase()}
                      </span>
                      {!isMidi && item.fad_verdict === 'satisfactory' && (
                        <Check size={12} style={{ color: 'var(--accent)' }} aria-label="Quality check: satisfactory" />
                      )}
                      {!isMidi && item.fad_verdict === 'unsatisfactory' && (
                        <AlertTriangle size={12} style={{ color: 'var(--accent-secondary)' }} aria-label="Quality check: unsatisfactory" />
                      )}
                    </div>
```

- [ ] **Step 3: Verify visually in the browser**

With the app running (`python run.py`), open the Library page and confirm:
- A song with `fad_verdict: "satisfactory"` (generated after Task 4 landed) shows a small checkmark icon next to its duration/type pills.
- A song with `fad_verdict: "unsatisfactory"` shows a small warning-triangle icon instead. (If none exists yet, generate a short song with a prompt likely to score poorly, e.g. a genre-mismatched prompt, or temporarily lower a `fad_common.py` ceiling to force the unsatisfactory path, then revert the ceiling change afterward.)
- A song generated before this feature (or a MIDI entry) shows neither icon — no layout gap, no broken rendering.

- [ ] **Step 4: Commit**

```bash
git add src/pages/Library.tsx
git commit -m "Show FAD quality badge on Library cards"
```

---

## Self-Review Notes

- **Spec coverage:** shared bucket module (Task 1), scoring script (Task 2), DB columns (Task 3), worker wiring incl. never-fail guarantee (Task 4), `/library` exposure (Task 5), frontend type (Task 6), badge UI (Task 7) — all design-spec sections have a corresponding task.
- **Type consistency checked:** `fad_score_one.py`'s JSON keys (`score`, `bucket`, `verdict`, `error`) match exactly what `_score_song` in Task 4 parses (`data["score"]`, `data["verdict"]`); `LibraryItem.fad_verdict`'s three-value type (`'satisfactory' | 'unsatisfactory' | null`) matches exactly what the backend can ever produce (`_score_song` returns only those two strings or `None`, and SQLite `NULL` deserializes to JSON `null`).
- **No placeholders:** every step has literal, runnable code and an exact expected result.
