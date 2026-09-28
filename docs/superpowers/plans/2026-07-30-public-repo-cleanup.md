# Public Repo Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Exception: Task 1 must be executed directly by the controller, not dispatched to a subagent** — see Task 1's own note.

**Goal:** Make the ImageSound repo safe and presentable to push to GitHub publicly for the first time — scrub a leaked local username from git history, remove tracked clutter/duplication, replace the stale boilerplate README, and tidy up loose files — followed by a lower-stakes readability cleanup pass (stray scripts, a leftover package name, and splitting an oversized frontend component).

**Architecture:** Two phases, sequenced per the user's explicit request: Phase A (must-do before any public push — Tasks 1-3) lands first, Phase B (cleanup/polish — Tasks 4-7) after. Task 1 is a git-history rewrite and must run before any other new commits in this plan, since it changes every existing commit hash on the branch.

**Tech Stack:** Git (filter-branch, no external tools), Python (backend/scripts), React/TypeScript (frontend).

## Global Constraints

- This repo has **zero remotes** and has **never been pushed anywhere** (confirmed via `git remote -v`) — a history rewrite in Task 1 is safe: nothing else has cloned it, nothing to force-push, no collaborators to disrupt. This constraint is exactly why Task 1 is safe now and would not be after the first public push.
- Local git identity is already configured (repo-local only, not global) from earlier work this session — do not touch git config, local or global, beyond what's already set.
- No `.venv`/`.venv-fad` activity needed for any task in this plan — everything here is file/git operations, Python-only diagnostic scripts (no imports beyond stdlib expected), or frontend TypeScript.
- Decided by the user: **no LICENSE file** (repo stays effectively all-rights-reserved by omission; nothing to create for this). **No wake-on-request gateway work in this plan** — that's a separate, already-designed feature tracked separately.
- `backend/app/routers/audio.py`'s missing per-owner access check is a **known, deliberately deferred** gap (real login/auth is explicit future work, not part of this plan) — Task 3 documents it, does not fix it.

---

### Task 1: Purge `graphify-out/` from all git history

**Files:**
- Modify (rewrite): entire git history (all 9 existing commits get new hashes)
- Modify: `.gitignore` (add `graphify-out/`)

**Interfaces:** None — this is a repo-hygiene operation, not application code. No other task depends on specific commit hashes from before this rewrite.

**⚠️ Controller note:** Do not dispatch this task to a subagent. Execute it directly, in the main session, with the user's explicit prior approval already on record (they chose "fully purge from all git history now" when asked). This is the one task in this plan with real (if low-probability) destructive risk — do it carefully, verify each step's output before moving to the next, and stop and report if anything looks unexpected rather than pushing through.

- [ ] **Step 1: Pre-flight safety checks**

```bash
git status --short          # must be clean except the pre-existing untracked test_gemini_key.py
git remote -v                # must be empty (confirms nothing to disrupt)
git log --oneline            # note current 9 commit hashes for before/after comparison
git rev-parse --show-toplevel  # confirm cwd is the repo root before any destructive op
```

If `git status --short` shows anything unexpected beyond `?? test_gemini_key.py`, STOP and report — do not proceed with uncommitted work present.

- [ ] **Step 2: Confirm the target and current exposure**

```bash
git log --all --oneline -- graphify-out          # should show only the first commit (f86ad66 or its current hash)
git show HEAD:graphify-out/.graphify_python 2>&1 | head -5   # confirm the username string is actually there before the fix
```

- [ ] **Step 3: Run the history rewrite**

```bash
git filter-branch --force --index-filter "git rm -r --cached --ignore-unmatch graphify-out" --prune-empty --tag-name-filter cat -- --all
```

Expected: filter-branch reports rewriting all commits reachable from the current branch (9 commits), with a "WARNING: git-filter-branch has a glut of gotchas..." notice (expected, harmless — this is filter-branch's standard banner, not an error).

- [ ] **Step 4: Clean up rewrite artifacts and reclaim space**

```bash
rm -rf .git/refs/original/
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

- [ ] **Step 5: Verify the purge**

```bash
git log --all --oneline -- graphify-out                       # expect: empty output
git rev-list --objects --all | grep -i graphify                # expect: empty output
git log --oneline                                              # expect: still 9 commits, but ALL hashes changed from Step 1's note
ls graphify-out 2>&1                                            # expect: "No such file or directory" (working tree no longer has it — this is fine, it's a regenerable tool cache, not source)
git status --short                                               # expect: clean except test_gemini_key.py, same as Step 1
```

If any verification fails, STOP and report rather than proceeding to Step 6 — do not attempt further destructive operations on a rewrite that didn't verify cleanly.

- [ ] **Step 6: Prevent recurrence and commit**

Add to `.gitignore` (repo root), in the Python section or its own line:
```
graphify-out/
```

```bash
git add .gitignore
git commit -m "Ignore graphify-out/ (local tool cache, previously leaked a local username)"
```

- [ ] **Step 7: Note the SDD ledger's stale hash references**

`.superpowers/sdd/progress.md` references pre-rewrite commit hashes (e.g. `f86ad66`, `a15e353`, `0802e4a`) from the FAD quality badge feature's completed work. After this rewrite those specific hex strings no longer resolve to real commits. Do not edit the ledger's content — it's a point-in-time historical record of already-completed, already-shipped work, not a live reference. Just note this in your task report so the human knows why `git show f86ad66` will now fail if they ever try it.

- [ ] **Step 8: Report**

Report the before/after commit hash list (from Step 1's note vs. Step 5's `git log --oneline`), confirmation every verification in Step 5 passed, and the final commit hash from Step 6.

---

### Task 2: Remove `src_backup_pre_hallmark/` duplicate

**Files:**
- Delete: `src_backup_pre_hallmark/` (18 tracked files, full duplicate of `src/` from before an earlier design pass)

**Interfaces:** None — confirmed via cross-reference search that nothing in `src/`, `backend/`, or config files imports from or references this directory.

- [ ] **Step 1: Confirm nothing references it**

```bash
grep -rn "src_backup_pre_hallmark" --include="*.ts" --include="*.tsx" --include="*.json" --include="*.py" src backend pipeline *.json *.ts 2>/dev/null
```
Expected: no output. If anything shows up, STOP and report — do not delete something still referenced.

- [ ] **Step 2: Remove and commit**

```bash
git rm -r src_backup_pre_hallmark
git commit -m "Remove src_backup_pre_hallmark/ (stale duplicate; git history preserves the old version)"
```

- [ ] **Step 3: Verify**

```bash
git status --short          # clean except test_gemini_key.py
ls src_backup_pre_hallmark 2>&1   # "No such file or directory"
npx tsc --noEmit             # confirm removing it didn't somehow break a build (it shouldn't — it was never imported)
```

Report the commit hash and the `tsc` output.

---

### Task 3: Write a real README, document the known auth gap

**Files:**
- Modify: `README.md` (currently stale AI-Studio boilerplate — replace entirely)

**Interfaces:** None — documentation only, no code interfaces.

**Context for the writer:** The current `README.md` is AI-Studio-generated boilerplate that doesn't mention this project at all — it tells a reader to `npm install` + set `GEMINI_API_KEY` in `.env.local` + `npm run dev`, with no mention of the FastAPI backend, the MusicGen/audiocraft pipeline, the SQLite DB, or the `run.py` launcher that actually starts both halves of the app together. `design.md` (repo root, already accurate) is a good source for what the app actually is and how its pieces fit together — read it before writing the README, but don't just copy it wholesale; a README's job is orientation and setup instructions, not full design documentation.

- [ ] **Step 1: Read source material**

Read `design.md` (repo root) and `run.py` (repo root) in full to understand: what the app does, its two-process (backend+frontend) architecture, and the actual local setup/run steps (`python run.py` starts both; Ctrl+C or running it again stops both).

- [ ] **Step 2: Write the new README**

Replace `README.md`'s entire contents with a real README covering, at minimum:
- One paragraph: what ImageSound is (image/text/audio → AI-generated song).
- Prerequisites: Node.js, Python (with the two-venv setup — main `.venv` for the app, isolated `.venv-fad` for FAD quality scoring — briefly explain why two, referencing that `audiocraft`/`xformers` pin an older torch than the FAD scoring library needs).
- Setup steps: install frontend deps (`npm install`), set up both Python venvs and their requirements files (check what requirements files exist, e.g. `backend/requirements.txt`, `requirements-fad.txt` or similar — use the actual file names present in the repo), set `GEMINI_API_KEY` in `backend/.env` (based on `backend/.env.example`).
- Running it: `python run.py` starts backend (port 8000) + frontend together; Ctrl+C or running `python run.py` again stops both.
- **A short "Known limitations" section** noting: no authentication yet — any client that knows or guesses a file's UUID can fetch it via `/audio/{id}`, `/image/{id}`, etc. (cite `backend/app/routers/audio.py`'s own `# TODO: add AND owner_id=? here once auth exists` comment). State plainly that this is a known, deliberately-deferred gap, not an oversight, and that login/access control is planned future work — don't overstate it as "fixed" or bury it.

Keep it concise — this is a setup/orientation doc, not exhaustive documentation. Match the tone of the existing `design.md` (direct, no marketing language).

- [ ] **Step 3: Verify accuracy**

Cross-check every command/path mentioned in the new README actually exists and is spelled correctly (venv paths, requirements file names, `.env.example` path, `run.py`'s actual behavior) — read each referenced file to confirm rather than assuming.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Replace stale AI-Studio README with real project documentation"
```

Report the commit hash and a copy of the final README content in your report file for the reviewer to check against Step 3's cross-check requirement.

---

### Task 4: Delete the throwaway `test_gemini_key.py`

**Files:**
- Delete: `test_gemini_key.py` (repo root, untracked)

**Interfaces:** None.

- [ ] **Step 1: Confirm it's still untracked and still says what we think**

```bash
git status --short test_gemini_key.py   # expect: "?? test_gemini_key.py"
head -3 test_gemini_key.py               # expect to see its own "DELETE after use. Do not commit." header
```

- [ ] **Step 2: Delete it**

```bash
rm test_gemini_key.py
```

No commit needed — it was never tracked, so there's nothing for git to record. This step just removes the footgun from the working directory.

- [ ] **Step 3: Verify**

```bash
git status --short   # expect: completely clean now
ls test_gemini_key.py 2>&1   # expect: "No such file or directory"
```

Report confirmation of both.

---

### Task 5: Move diagnostic scripts to `scripts/`

**Files:**
- Move: `backend_diagnostic.py`, `mem_diag.py`, `test_autocast.py`, `run_gen_tests.py`, `prefetch_melody.py` (repo root) → `scripts/` (new directory)

**Interfaces:** None — confirmed via repo-wide grep that no other file imports or references these five scripts by path.

- [ ] **Step 1: Re-confirm no cross-references (repo state may have shifted since the original audit)**

```bash
grep -rln "backend_diagnostic\|mem_diag\|test_autocast\|run_gen_tests\|prefetch_melody" --include="*.py" --include="*.md" --include="*.json" --include="*.ts" --include="*.tsx" src backend pipeline *.py *.md *.json 2>/dev/null
```
Expected: only the five files' own filenames matching themselves (if grep matches a file's own name against its own path) — no *other* file referencing them by import or path string. If something unexpected shows up, STOP and report before moving anything.

- [ ] **Step 2: Create the directory and move the files with git mv (preserves history)**

```bash
mkdir -p scripts
git mv backend_diagnostic.py scripts/
git mv mem_diag.py scripts/
git mv test_autocast.py scripts/
git mv run_gen_tests.py scripts/
git mv prefetch_melody.py scripts/
```

- [ ] **Step 3: Commit**

```bash
git commit -m "Move standalone diagnostic scripts into scripts/"
```

- [ ] **Step 4: Verify**

```bash
ls scripts/                     # expect: all 5 files present
ls backend_diagnostic.py mem_diag.py test_autocast.py run_gen_tests.py prefetch_melody.py 2>&1   # expect: all 5 "No such file or directory" at repo root
git log --follow --oneline -- scripts/mem_diag.py   # expect: history preserved (shows commits from before the move too)
```

Report the commit hash and confirmation of all four checks.

---

### Task 6: Fix `package.json`'s leftover template name

**Files:**
- Modify: `package.json:2`

**Interfaces:** None — `name` in `package.json` isn't imported/referenced anywhere at runtime for this Vite app (confirmed: it's metadata only, not used in build config or source).

- [ ] **Step 1: Make the change**

Current:
```json
{
  "name": "react-example",
  "private": true,
```

Replace with:
```json
{
  "name": "imagesound",
  "private": true,
```

(lowercase-with-no-spaces per npm package name conventions; `metadata.json`'s `"Imagesound"` is the display name, this is the package identifier — they don't need to match case-for-case.)

- [ ] **Step 2: Verify nothing depends on the old name**

```bash
grep -rn "react-example" --include="*.json" --include="*.ts" --include="*.tsx" --include="*.js" . 2>/dev/null | grep -v node_modules
```
Expected: no output (confirms nothing referenced the old placeholder name).

```bash
npm run build
```
Expected: builds successfully (confirms the rename didn't break anything).

- [ ] **Step 3: Commit**

```bash
git add package.json
git commit -m "Fix package.json name (was leftover 'react-example' template name)"
```

Report the commit hash and the build output confirming success.

---

### Task 7: Split `Player.tsx` into subcomponents

**Files:**
- Modify: `src/pages/Player.tsx` (currently 1343 lines — read it in full first, this brief does not reproduce its contents)
- Create: `src/components/player/SourcePreview.tsx`, `src/components/player/ArcEditor.tsx`, `src/components/player/EffectsPanel.tsx`, `src/components/player/GeneratePanel.tsx` (exact prop interfaces to be determined by the implementer from reading the current file — this is a real design task, not transcription)

**Interfaces:**
- Produces: four new components, each owning one concern currently mixed together in `Player.tsx`:
  - `SourcePreview` — renders the uploaded/source image, text, or audio preview depending on input type.
  - `ArcEditor` — the drag-to-adjust intensity-arc bar UI (**note:** this is the same "some bars can't be adjusted manually" component the user reported a bug on earlier this session — do not attempt to fix that bug as part of this split; if you notice it while reading the code, leave the behavior exactly as-is and just relocate it faithfully. A behavior change here is explicitly out of scope for this task).
  - `EffectsPanel` — the EQ/compression/reverb sliders.
  - `GeneratePanel` — the generate/save/discard/download state machine and its buttons.
- `Player.tsx` itself becomes the orchestrating parent, holding shared state and passing it down as props to the four new components.

**This is real refactoring work, not mechanical extraction** — read the whole current file first, understand what state each visual region actually needs (props in) and needs to communicate back up (callbacks out), and design clean prop interfaces. Prioritize correctness (nothing about the app's behavior should change) over any particular split shape — if while reading the file a different split boundary makes more sense than the four named above, use judgment, but keep the same spirit (one component, one job) and note the deviation in your report.

- [ ] **Step 1: Read and map the current file**

Read `src/pages/Player.tsx` in full. Before writing any code, identify: what state lives in the parent vs. what's purely local to one visual region, what props/callbacks each of the four regions needs, and whether the regions share any subtle coupling (e.g. does the effects panel need to know about generation state, does the arc editor need source-preview data) that would make a clean split harder than it first looks.

- [ ] **Step 2: Extract `SourcePreview` first (smallest, most self-contained region)**

Create `src/components/player/SourcePreview.tsx`. Move its JSX and any purely-local logic out of `Player.tsx`, wire it back in via props. This is the smallest of the four — doing it first validates the split pattern before tackling the larger three.

- [ ] **Step 3: Verify after the first extraction**

```bash
npx tsc --noEmit
```
Expected: no new type errors. Then start the dev server and manually confirm in a real browser that the source preview still renders correctly for at least one input type (image or text) before proceeding — catching a broken extraction early, while the diff is still small, is much cheaper than after all four are done.

- [ ] **Step 4: Extract `ArcEditor`**

Create `src/components/player/ArcEditor.tsx`. Same pattern: move JSX + local logic, wire via props. Remember: preserve the existing bar-adjustment behavior exactly, including its known bug — do not fix it here.

- [ ] **Step 5: Extract `EffectsPanel`**

Create `src/components/player/EffectsPanel.tsx`. Same pattern.

- [ ] **Step 6: Extract `GeneratePanel`**

Create `src/components/player/GeneratePanel.tsx`. Same pattern.

- [ ] **Step 7: Full verification**

```bash
npx tsc --noEmit
```
Expected: clean, no errors.

Then, with the app running (`python run.py` or equivalent dev startup), manually exercise the Player page end-to-end in a real browser:
- Load each source type (image/text/audio) and confirm the preview renders.
- Adjust the arc editor and confirm it still behaves exactly as before (same bug, if any, still present and unchanged — not worse, not fixed).
- Adjust each effects slider and confirm playback reflects the change.
- Run through generate → save/discard/download and confirm the state machine still works end-to-end.

Take screenshots of each state as evidence, the same way Task 7 of the FAD quality badge feature did (see `.superpowers/sdd/task-7-report.md` from that feature for the pattern/format to follow, if useful as a reference — read it only for the verification-approach pattern, not its content, which is about a different feature).

- [ ] **Step 8: Commit**

```bash
git add src/pages/Player.tsx src/components/player/
git commit -m "Split Player.tsx into SourcePreview/ArcEditor/EffectsPanel/GeneratePanel"
```

Report the commit hash, the final line count of `Player.tsx` vs. before, and the browser verification evidence (screenshot paths + what each showed).

---

## Self-Review Notes

- **Spec coverage:** every "Must do before pushing publicly" item from the audit (username leak, duplicate dir, stale README, documented auth gap) maps to Tasks 1-3; every "worth cleaning up" item (throwaway script, stray diagnostics, package name, Player.tsx size) maps to Tasks 4-7. No LICENSE task — user explicitly decided to skip it.
- **Ordering:** Task 1 must run before Tasks 2-7 (history rewrite invalidates commit hashes referenced anywhere in later work if done out of order — though nothing here actually pins a hash except Task 1's own before/after check, doing it first avoids any ambiguity). Tasks 2-6 are independent of each other and of Task 7. Task 7 is the largest and riskiest — sequenced last so the simpler, lower-risk hygiene tasks land first regardless of how Task 7 goes.
- **Type consistency:** Task 7's four component names (`SourcePreview`, `ArcEditor`, `EffectsPanel`, `GeneratePanel`) are used consistently in its Interfaces and Steps sections.
- **No placeholders:** every task has concrete commands and expected output; Task 3 and Task 7 involve real writing/design judgment (a README, a component split) that can't be fully pre-written in the plan — this is called out explicitly in each task's brief rather than papered over with fake specificity.
