# Remote Tunnel Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner run `python run.py --tunnel` to get a public, shareable `https://*.trycloudflare.com` URL that lets other people use the real running app (login-gated, same as local), with the tunnel going down the instant the app is stopped.

**Architecture:** Only the frontend (port 3000) is tunneled via Cloudflare's free "quick tunnel." Vite's dev server proxies `/api/*` to the backend (`localhost:8000`) internally, so the visitor's browser sees one same-origin URL for everything — no CORS or cross-site cookie issues. `run.py` gains a `--tunnel` flag that starts `cloudflared` as a third managed subprocess, tied into the launcher's existing start/stop-switch machinery.

**Tech Stack:** Vite dev server proxy (`server.proxy`), Cloudflare Tunnel (`cloudflared` quick tunnel, no account/domain needed), Python `subprocess`/`threading` (already used in `run.py`).

## Global Constraints

- No custom domain — only the random `https://<random-words>.trycloudflare.com` quick-tunnel URL. No Cloudflare account or DNS setup.
- No IP whitelist — the existing login gate is the only access control layer.
- No always-on tunnel — `--tunnel` is opt-in per run; plain `python run.py` must behave exactly as it does today, unaffected.
- Only the frontend (port 3000) is ever tunneled directly; the backend (port 8000) is reachable from the internet only via the Vite `/api` proxy, never tunneled on its own.
- No changes to `backend/app/config.py` CORS settings or the session cookie's `same_site` value — the proxy design means none are needed.
- No IP whitelist, no idle-auto-shutdown changes, no HTTPS/Secure-cookie hardening, no production hosting changes — all explicitly out of scope per the design spec.

---

### Task 1: Vite `/api` proxy

**Files:**
- Modify: `vite.config.ts`

**Interfaces:**
- Produces: a working `http://localhost:3000/api/*` → `http://localhost:8000/*` proxy (prefix stripped) — consumed by Task 2, which points the tunneled frontend's `VITE_API_BASE` at `/api`.

- [ ] **Step 1: Add the proxy to `vite.config.ts`**

Current `vite.config.ts`:
```ts
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // Set DISABLE_HMR=true in the environment to turn off hot module
      // reloading and file watching below — useful when an automated tool
      // (e.g. a coding agent) is making bulk edits and you do not want the
      // dev server flickering/reloading mid-edit.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      // Watch the frontend only. Without these excludes, every DB read/write
      // (backend runs SQLite in WAL mode, so even a plain SELECT touches
      // app.db-wal/-shm) and every generated audio file gets picked up as a
      // "source change" and forces a full page reload mid-action (playing,
      // saving, or generating a song).
      watch: process.env.DISABLE_HMR === 'true' ? null : {
        ignored: [
          '**/backend/**',
          '**/pipeline/**',
          '**/.venv/**',
          '**/.venv-fad/**',
          '**/graphify-out/**',
          '**/__pycache__/**',
          '**/*.db',
          '**/*.db-wal',
          '**/*.db-shm',
          '**/*.db-journal',
        ],
      },
    },
  };
});
```

Replace with:
```ts
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // Set DISABLE_HMR=true in the environment to turn off hot module
      // reloading and file watching below — useful when an automated tool
      // (e.g. a coding agent) is making bulk edits and you do not want the
      // dev server flickering/reloading mid-edit.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      // Watch the frontend only. Without these excludes, every DB read/write
      // (backend runs SQLite in WAL mode, so even a plain SELECT touches
      // app.db-wal/-shm) and every generated audio file gets picked up as a
      // "source change" and forces a full page reload mid-action (playing,
      // saving, or generating a song).
      watch: process.env.DISABLE_HMR === 'true' ? null : {
        ignored: [
          '**/backend/**',
          '**/pipeline/**',
          '**/.venv/**',
          '**/.venv-fad/**',
          '**/graphify-out/**',
          '**/__pycache__/**',
          '**/*.db',
          '**/*.db-wal',
          '**/*.db-shm',
          '**/*.db-journal',
        ],
      },
      // Lets a single tunneled origin (see run.py's --tunnel flag) reach both
      // the frontend and the backend through one URL: any request to /api/*
      // is forwarded to the backend with the /api prefix stripped, so the
      // browser never needs to know about localhost:8000 directly. Local
      // dev without --tunnel is unaffected -- src/api.ts talks to
      // http://localhost:8000 directly unless VITE_API_BASE overrides it.
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  };
});
```

- [ ] **Step 2: Verify the proxy works, and that normal (non-proxied) dev is unaffected**

Start the backend and frontend the normal way (two terminals, or `python run.py` from the repo root — do NOT pass `--tunnel`, that flag doesn't exist yet until Task 2):
```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend
```
```bash
npm run dev
```

With both running, verify the proxy forwards correctly:
```bash
curl -s http://localhost:3000/api/health
```
Expected: `{"status":"ok"}` — this is the backend's own `/health` response, reached by Vite stripping `/api` and forwarding to `localhost:8000/health`. If you get a Vite 404 or HTML instead, the proxy isn't matching — recheck the `proxy` block's placement under `server`.

Then confirm nothing about *normal* usage changed: open `http://localhost:3000` in a browser, confirm the Home page loads, and that the existing direct-to-backend path still works (e.g. the Library page still loads its list, which uses `src/api.ts`'s default `API_BASE = http://localhost:8000` since `VITE_API_BASE` is not set in this step).

- [ ] **Step 3: Commit**

```bash
git add vite.config.ts
git commit -m "Add Vite /api proxy for single-origin tunnel access"
```

---

### Task 2: `run.py --tunnel` flag

**Files:**
- Modify: `run.py`

**Interfaces:**
- Consumes: the `/api` proxy from Task 1 (this task sets `VITE_API_BASE=/api` for the tunneled frontend, which only resolves correctly because Task 1's proxy exists).

This task is not split further because the tunnel subprocess, the `VITE_API_BASE` env wiring, and the stop-switch integration all only make sense as one working whole — a partial version (e.g. tunnel process started but not registered with the stop-switch) would leave orphaned `cloudflared` processes after `python run.py` is used to stop the app, which is exactly the bug this plan exists to avoid.

- [ ] **Step 1: Install `cloudflared`**

```powershell
winget install --id Cloudflare.cloudflared --accept-source-agreements --accept-package-agreements -e
```

This is a one-time host machine setup step, not something `run.py` does automatically. After installing, `cloudflared` may not be on `PATH` in your *current* terminal session yet (winget updates the registry, not an already-open shell's environment) — open a **new** terminal window, or refresh `PATH` in the current one, before verifying:
```bash
cloudflared --version
```
Expected: prints a version string (e.g. `cloudflared version 2026.7.3 ...`). If `cloudflared` is not found, close and reopen your terminal and try again.

- [ ] **Step 2: Read the current `run.py` in full**

Read `F:\Project\VSCODE\ImageSound\run.py` before editing — confirm it matches the structure described below (it may have changed since this plan was written). It currently defines: `VENV_PYTHON`, `BACKEND_CMD`, `FRONTEND_CMD`, `FRONTEND_DIR`, `_PIDFILE`, `_NAME_HINTS`, `procs` (list), `_BACKEND_FLAGS`, and these functions: `_kill_tree`, `start`, `stop_all`, `_write_pidfile`, `_remove_pidfile`, `_process_cmdline`, `_is_our_process`, `_kill_tree_by_pid`, `_try_stop_previous_instance`, plus the `if __name__ == "__main__":` block at the bottom.

- [ ] **Step 3: Add the tunnel command constant and extend `_NAME_HINTS`**

Find this line near the top of `run.py`:
```python
FRONTEND_CMD = ["npm", "run", "dev"]
FRONTEND_DIR = "."   # set to your frontend folder if it's not the project root
# -------------------------------------------------------------
```

Replace with:
```python
FRONTEND_CMD = ["npm", "run", "dev"]
FRONTEND_DIR = "."   # set to your frontend folder if it's not the project root
TUNNEL_CMD = ["cloudflared", "tunnel", "--url", "http://localhost:3000"]
# -------------------------------------------------------------
```

Find this line:
```python
_NAME_HINTS = {"backend": ["uvicorn"], "frontend": ["npm", "vite", "node"]}
```

Replace with:
```python
_NAME_HINTS = {"backend": ["uvicorn"], "frontend": ["npm", "vite", "node"], "tunnel": ["cloudflared"]}
```

- [ ] **Step 4: Add `import re` and `import threading`**

Find the import block at the top of `run.py`:
```python
import json
import subprocess
import sys
import os
import signal
from pathlib import Path
```

Replace with:
```python
import json
import re
import subprocess
import sys
import os
import signal
import threading
from pathlib import Path
```

- [ ] **Step 5: Add the tunnel URL reader and `start_tunnel()`**

Find the `start` function:
```python
def start(name, cmd, cwd=None):
    print(f"[launcher] starting {name}...")
    flags = _BACKEND_FLAGS if name == "backend" else 0
    # shell=True on Windows helps find 'npm'; backend uses the venv's python directly.
    p = subprocess.Popen(cmd, cwd=cwd, shell=(name == "frontend"), creationflags=flags)
    procs.append((name, p))
    if name == "backend":
        print(f"[launcher] backend pid={p.pid}  "
              f"(if you restart, check Task Manager — kill any lingering python.exe at this PID first)")
    return p
```

Replace with (adds an `env` parameter so the frontend can be launched with `VITE_API_BASE` set, and adds the tunnel-specific helpers right after):
```python
def start(name, cmd, cwd=None, env=None):
    print(f"[launcher] starting {name}...")
    flags = _BACKEND_FLAGS if name == "backend" else 0
    proc_env = None
    if env:
        proc_env = os.environ.copy()
        proc_env.update(env)
    # shell=True on Windows helps find 'npm'; backend uses the venv's python directly.
    p = subprocess.Popen(cmd, cwd=cwd, shell=(name == "frontend"), creationflags=flags, env=proc_env)
    procs.append((name, p))
    if name == "backend":
        print(f"[launcher] backend pid={p.pid}  "
              f"(if you restart, check Task Manager — kill any lingering python.exe at this PID first)")
    return p


_TUNNEL_URL_RE = re.compile(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com')
_tunnel_url = {"value": None}
_tunnel_url_found = threading.Event()


def _read_tunnel_output(p: subprocess.Popen) -> None:
    """Background reader thread: keeps draining cloudflared's combined
    stdout/stderr (so the pipe never fills and blocks the subprocess) and
    captures the quick-tunnel URL the first time it appears in the output.
    """
    for line in iter(p.stdout.readline, ''):
        if not line:
            break
        match = _TUNNEL_URL_RE.search(line)
        if match and _tunnel_url["value"] is None:
            _tunnel_url["value"] = match.group(0)
            _tunnel_url_found.set()


def start_tunnel() -> subprocess.Popen:
    print("[launcher] starting tunnel...")
    p = subprocess.Popen(
        TUNNEL_CMD,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    procs.append(("tunnel", p))
    threading.Thread(target=_read_tunnel_output, args=(p,), daemon=True).start()
    if _tunnel_url_found.wait(timeout=20):
        print(f"[launcher] tunnel ready: {_tunnel_url['value']}")
        print("[launcher] share that URL -- it stops working the moment you stop run.py")
    else:
        print("[launcher] WARNING: tunnel did not report a URL within 20s. Check that "
              "cloudflared is installed (winget install --id Cloudflare.cloudflared) and "
              "that you have an internet connection. Backend/frontend are still running "
              "locally regardless.")
    return p
```

- [ ] **Step 6: Wire `--tunnel` into the `__main__` block**

Find:
```python
if __name__ == "__main__":
    if _try_stop_previous_instance():
        print("[launcher] previous instance stopped. Run again to start it back up.")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    start("backend", BACKEND_CMD)
    start("frontend", FRONTEND_CMD, cwd=FRONTEND_DIR)
    _write_pidfile()
    print("[launcher] both running. Backend on :8000, frontend on its dev port.")
    print("[launcher] Ctrl+C to stop both, or run `python run.py` again (even from another terminal) to stop them.")
    # wait for either to exit
    for name, p in procs:
        p.wait()
    _remove_pidfile()
```

Replace with:
```python
if __name__ == "__main__":
    TUNNEL_MODE = "--tunnel" in sys.argv

    if _try_stop_previous_instance():
        print("[launcher] previous instance stopped. Run again to start it back up.")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    start("backend", BACKEND_CMD)
    frontend_env = {"VITE_API_BASE": "/api"} if TUNNEL_MODE else None
    start("frontend", FRONTEND_CMD, cwd=FRONTEND_DIR, env=frontend_env)
    if TUNNEL_MODE:
        start_tunnel()
    _write_pidfile()
    print("[launcher] both running. Backend on :8000, frontend on its dev port.")
    print("[launcher] Ctrl+C to stop both, or run `python run.py` again (even from another terminal) to stop them.")
    # wait for either to exit
    for name, p in procs:
        p.wait()
    _remove_pidfile()
```

- [ ] **Step 7: Verify locally (no `--tunnel`) is unaffected — regression check**

Stop anything already running (run `python run.py` if something's up, confirm it stops). Then:
```bash
python run.py
```
Expected: identical behavior to before this task — backend + frontend start, no mention of a tunnel, `VITE_API_BASE` unset. Open `http://localhost:3000`, confirm login/generate/library still work exactly as before. Stop with `python run.py` again; confirm both processes are gone (Task Manager or `tasklist`).

- [ ] **Step 8: Verify `--tunnel` end-to-end**

```bash
python run.py --tunnel
```
Expected within ~20 seconds: a `[launcher] tunnel ready: https://<random-words>.trycloudflare.com` line.

With that URL (call it `$TUNNEL_URL`), verify the full chain works, not just that a URL was printed:
```bash
curl -s "$TUNNEL_URL/api/health"
```
Expected: `{"status":"ok"}` — proves Cloudflare's edge → cloudflared → Vite → the `/api` proxy → the backend all work together.

Then verify the login+cookie chain through the tunnel specifically (this is the part most likely to silently break if the proxy or env wiring is wrong):
```bash
curl -s -c cookies.txt -w "\n%{http_code}\n" -X POST "$TUNNEL_URL/api/auth/login" -H "Content-Type: application/json" -d "{\"username\":\"test\",\"password\":\"admin1234\"}"
curl -s -b cookies.txt -w "\n%{http_code}\n" "$TUNNEL_URL/api/auth/me"
```
Expected: login `200` with `{"username":"test"}`; `/auth/me` `200` with the same — proving the session cookie set via the tunnel URL round-trips correctly.

Also open `$TUNNEL_URL` in a real browser (ideally on a different network — e.g. phone on mobile data — to prove this isn't just resolving to localhost some other way) and confirm: the page loads, you can log in via the UI, and the Library page loads its list. This is the check that proves the whole feature works the way an actual visitor would experience it, not just via curl.

- [ ] **Step 9: Verify stopping tears everything down**

```bash
python run.py
```
(no `--tunnel` needed to stop — the existing stop-switch stops whatever was last started, regardless of flags)

Expected: `[launcher] previous instance stopped.` Confirm via Task Manager / `tasklist` that no `uvicorn`, `node`/`npm`, or `cloudflared` processes remain. Then confirm the tunnel URL from Step 8 no longer responds:
```bash
curl -s -o /dev/null -w "%{http_code}\n" "$TUNNEL_URL/api/health"
```
Expected: connection failure or a Cloudflare-generated error page (not `200`) — the tunnel is gone.

- [ ] **Step 10: Commit**

```bash
git add run.py
git commit -m "Add --tunnel flag to run.py for on-demand public access via cloudflared"
```
