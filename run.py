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
from pathlib import Path

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
        try:
            start_tunnel()
        except OSError as e:
            print(f"[launcher] WARNING: could not start cloudflared ({e}). Backend/frontend are "
                  "still running locally on :8000/:3000. If cloudflared was just installed, open "
                  "a NEW terminal (PATH needs refreshing) and try 'python run.py --tunnel' again "
                  "after stopping this instance with 'python run.py'.")
    _write_pidfile()
    print("[launcher] both running. Backend on :8000, frontend on its dev port.")
    print("[launcher] Ctrl+C to stop both, or run `python run.py` again (even from another terminal) to stop them.")
    # wait for either to exit
    for name, p in procs:
        p.wait()
    _remove_pidfile()
