# run.py — dev launcher for ImageSound. Starts backend + frontend together.
# Press Ctrl+C once to stop both, OR just run `python run.py` again from any
# terminal — it detects the already-running instance and stops it instead of
# starting a second one (a start/stop switch). For local testing only.
import json
import re
import subprocess
import sys
import os
import signal
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from dotenv import load_dotenv

# Load the project's .env file (if present) so SESSION_SECRET_KEY and other
# values set there are visible via os.environ -- mirrors backend/app/config.py,
# which already calls load_dotenv(). Without this, a user who follows this
# launcher's own advice to "set SESSION_SECRET_KEY in your .env file" would
# get a permanent false refusal, since this process never saw it otherwise.
# No-arg load_dotenv() searches upward from the current working directory --
# since run.py is invoked from the project root, this finds the same root
# .env the backend already uses.
load_dotenv()

# --- adjust these to match what you currently type by hand ---
VENV_PYTHON = os.path.join(".venv", "Scripts", "python.exe")  # Windows path

# --reload-dir scopes the file watcher to backend/ only. Without it, uvicorn
# defaults to watching the process's cwd (the whole repo) — node_modules and
# .venv alone are 100k+ files, which made StatReload's poll loop slow and,
# worse, could trigger a spurious mid-request backend restart from a totally
# unrelated write (an npm cache touch, a wav file, a DB commit).
BACKEND_CMD = [
    VENV_PYTHON, "-m", "uvicorn", "app.main:app",
    "--reload", "--reload-dir", "backend", "--app-dir", "backend"
]
FRONTEND_CMD = ["npm", "run", "dev"]
FRONTEND_DIR = "."   # set to your frontend folder if it's not the project root
TUNNEL_CMD = ["cloudflared", "tunnel", "--url", "http://localhost:3000"]
# -------------------------------------------------------------

_PIDFILE = Path(__file__).resolve().parent / ".run.pid"
# Rough command-line fingerprints used to confirm a recorded PID still refers
# to a process we actually started — PIDs get reused by the OS over time, so
# a bare "does this PID exist" check isn't enough to trust before killing it.
_NAME_HINTS = {"backend": ["uvicorn"], "frontend": ["npm", "vite", "node"], "tunnel": ["cloudflared"]}

procs = []

# Windows: start the backend at HIGH priority so Windows Power Throttling
# doesn't slow ML inference to a crawl when the terminal isn't in the foreground.
_BACKEND_FLAGS = getattr(subprocess, 'HIGH_PRIORITY_CLASS', 0)  # 0x80 on Windows, 0 elsewhere


def _kill_tree(p: subprocess.Popen, name: str) -> None:
    """Kill a process and ALL its descendants.

    On Windows, uvicorn --reload spawns a CHILD worker process.  A plain
    p.terminate() only kills the reloader (parent); the child survives as an
    orphan holding GPU memory.  taskkill /F /T kills the entire tree.
    """
    if p.poll() is not None:
        return  # already dead
    print(f"[launcher] stopping {name} (pid={p.pid}) + children...")
    if sys.platform == "win32":
        subprocess.call(
            ["taskkill", "/F", "/T", "/PID", str(p.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        p.terminate()
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            print(f"[launcher] {name} didn't exit in 8s — force killing...")
            p.kill()


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


def _wait_for_frontend_ready(timeout_seconds: float = 20.0) -> bool:
    """Poll the frontend's /api/health proxy path until it responds correctly,
    or the timeout elapses. This goes through the SAME hostname resolution a
    real tunnel request uses, so it correctly detects the case where Windows'
    IPv4/IPv6 preference routes 'localhost' to an unrelated process instead of
    our actual Vite dev server (a real failure mode --strictPort alone can't
    catch), and also confirms the frontend didn't crash on startup."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:3000/api/health", timeout=2) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status == 200 and '"status":"ok"' in body.replace(" ", ""):
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    return False


def _check_tunnel_secret() -> bool:
    """Refuse to tunnel with the insecure default session secret -- anyone who
    knows it (it's hardcoded in backend/app/config.py) could forge a valid
    login session and bypass the login gate entirely. Returns True if safe
    to proceed."""
    secret = os.environ.get("SESSION_SECRET_KEY", "")
    if not secret or secret == "dev-only-insecure-secret-change-me":
        print("[launcher] REFUSING to start tunnel: SESSION_SECRET_KEY is unset or still the")
        print("[launcher] insecure default. Anyone could forge a login session and bypass the")
        print("[launcher] password gate. Set a real random value first, e.g.:")
        print("[launcher]   (PowerShell) $env:SESSION_SECRET_KEY = python -c \"import secrets; print(secrets.token_hex(32))\"")
        print("[launcher] then set it in your .env file so it's picked up on every future run.")
        return False
    return True


def stop_all(*_):
    print("\n[launcher] shutting down — killing full process trees to prevent GPU-memory orphans...")
    for name, p in procs:
        _kill_tree(p, name)
    _remove_pidfile()
    sys.exit(0)


def _write_pidfile() -> None:
    _PIDFILE.write_text(json.dumps({name: p.pid for name, p in procs}))


def _remove_pidfile() -> None:
    _PIDFILE.unlink(missing_ok=True)


def _process_cmdline(pid: int) -> str:
    """Return a running process's full command line (lowercased), or ''
    if no such process exists. Uses PowerShell — bundled with every
    supported Windows version — instead of a third-party package, so
    `python run.py` works no matter which Python (venv or system) runs it.
    """
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'],
                stderr=subprocess.DEVNULL, text=True, timeout=5,
            )
            return out.strip().lower()
        except Exception:
            return ""
    else:
        try:
            with open(f"/proc/{pid}/cmdline") as f:
                return f.read().replace("\0", " ").lower()
        except OSError:
            return ""


def _is_our_process(pid: int, name: str) -> bool:
    """Best-effort check that `pid` still refers to a process we plausibly
    started (guards against a recycled PID landing on an unrelated process)."""
    cmdline = _process_cmdline(pid)
    if not cmdline:
        return False
    return any(hint in cmdline for hint in _NAME_HINTS.get(name, []))


def _kill_tree_by_pid(pid: int, name: str) -> None:
    print(f"[launcher] stopping {name} (pid={pid}) + children...")
    if sys.platform == "win32":
        subprocess.call(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _try_stop_previous_instance() -> bool:
    """If a previous run.py-launched server is still alive, stop it and
    return True (this is what makes re-running the script act as a
    start/stop switch instead of failing on a port already in use).

    Returns False when there's nothing to stop — either no pid file exists,
    or the recorded processes are already gone (crashed, or killed manually
    via Task Manager) — in which case any stale pid file is discarded and
    the caller should proceed to start fresh.
    """
    if not _PIDFILE.exists():
        return False
    try:
        recorded = json.loads(_PIDFILE.read_text())
    except Exception:
        _remove_pidfile()
        return False

    stopped_any = False
    for name, pid in recorded.items():
        if _is_our_process(pid, name):
            _kill_tree_by_pid(pid, name)
            stopped_any = True

    _remove_pidfile()
    return stopped_any


if __name__ == "__main__":
    TUNNEL_MODE = "--tunnel" in sys.argv

    if TUNNEL_MODE and not _check_tunnel_secret():
        sys.exit(1)

    if _try_stop_previous_instance():
        print("[launcher] previous instance stopped. Run again to start it back up.")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    start("backend", BACKEND_CMD)
    frontend_env = {
        "VITE_API_BASE": "/api",
    } if TUNNEL_MODE else None
    start("frontend", FRONTEND_CMD, cwd=FRONTEND_DIR, env=frontend_env)
    if TUNNEL_MODE:
        if _wait_for_frontend_ready():
            try:
                start_tunnel()
            except OSError as e:
                print(f"[launcher] WARNING: could not start cloudflared ({e}). Backend/frontend are "
                      "still running locally on :8000/:3000. If cloudflared was just installed, open "
                      "a NEW terminal (PATH needs refreshing) and try 'python run.py --tunnel' again "
                      "after stopping this instance with 'python run.py'.")
        else:
            print("[launcher] WARNING: frontend did not become healthy within 20s -- skipping "
                  "tunnel start. Backend/frontend may still be usable locally; check for errors above.")
    _write_pidfile()
    print("[launcher] both running. Backend on :8000, frontend on its dev port.")
    print("[launcher] Ctrl+C to stop both, or run `python run.py` again (even from another terminal) to stop them.")
    # wait for either to exit
    for name, p in procs:
        p.wait()
    _remove_pidfile()
