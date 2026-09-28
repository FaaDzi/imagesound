# run.py — launcher for ImageSound. Starts backend + frontend together and,
# by default, a Cloudflare tunnel so the site is reachable publicly (the
# status page picks up the URL). `python run.py --local` skips the tunnel.
# Press Ctrl+C once to stop everything, OR just run `python run.py` again from
# any terminal — it detects the already-running instance and stops it instead
# of starting a second one (a start/stop switch).
import json
import re
import shutil
import subprocess
import sys
import os
import signal
import threading
import time
import urllib.request
import urllib.error
import http.client
from pathlib import Path

# Windows-only: rebuild THIS process's PATH from the registry instead of
# trusting whatever PATH was inherited from the terminal/session that
# launched this script. Installing something (Node.js, Python, cloudflared,
# ...) updates the registry-stored PATH immediately, but any already-running
# process -- including the terminal this script was started from, and
# everything descended from a shell opened before the install -- keeps its
# OLD PATH until a fresh logon/reboot. That silently breaks the frontend's
# `shell=True` subprocess lookup of `npm` (see start()) with no clearer
# symptom than "site can't be reached" -- the frontend process just fails to
# launch. Reading PATH straight from the registry sidesteps the staleness
# for this process and everything it spawns, without requiring the user to
# log off. Mirrors the same problem _resolve_cloudflared() already works
# around for cloudflared specifically; this covers it at the source instead.
if sys.platform == "win32":
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as _key:
            _machine_path, _ = winreg.QueryValueEx(_key, "Path")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as _key:
            _user_path, _ = winreg.QueryValueEx(_key, "Path")
        os.environ["PATH"] = _machine_path + ";" + _user_path
    except OSError:
        pass  # degrade to the inherited PATH if the registry layout is ever different

# Windows-only convenience relaunch: python-dotenv and the backend's other
# dependencies only live in the project's own .venv. If this script gets
# invoked with a different Python -- easy to do by accident, since the
# header above just says "run python run.py" -- .env values like
# SESSION_SECRET_KEY become invisible to this process, which then makes
# --tunnel's secret check further down refuse to start even when a real
# secret IS set in .env, just not visible here. Re-exec under the venv's
# python transparently instead of leaving that as a recurring footgun.
# No-op (falls through) if the venv doesn't exist yet (e.g. a fresh clone
# before dependencies are installed) or if we're already running under it.
#
# Not os.execv: on Windows that spawns a new process and exits this one, so
# the terminal gets its prompt back while the server is still running and
# competes with it for keystrokes (Ctrl+C included). Run the venv copy as a
# child and wait instead. Ctrl+C reaches every process in the console, so
# this parent ignores it and lets the child run its own shutdown.
_VENV_PYTHON_PATH = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
if (
    sys.platform == "win32"
    and _VENV_PYTHON_PATH.exists()
    and Path(sys.executable).resolve() != _VENV_PYTHON_PATH.resolve()
):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    sys.exit(subprocess.call([str(_VENV_PYTHON_PATH), str(Path(__file__).resolve())] + sys.argv[1:]))

# Force line-buffered stdout. Without this, Python block-buffers its own
# print() output whenever stdout isn't a real interactive console (common
# in VS Code's integrated terminal / task runners on Windows) -- so every
# "[launcher] ..." line (including the tunnel URL) sits in a buffer and
# only appears all at once, out of order, whenever the buffer happens to
# flush. reconfigure() only affects THIS process's own prints; the
# backend/frontend subprocesses' output is unaffected either way.
sys.stdout.reconfigure(line_buffering=True)

# Load the project's .env file (if present) so SESSION_SECRET_KEY and other
# values set there are visible via os.environ -- mirrors backend/app/config.py,
# which already calls load_dotenv(). Without this, a user who follows this
# launcher's own advice to "set SESSION_SECRET_KEY in your .env file" would
# get a permanent false refusal, since this process never saw it otherwise.
# No-arg load_dotenv() searches upward from the current working directory --
# since run.py is invoked from the project root, this finds the same root
# .env the backend already uses.
#
# python-dotenv is only guaranteed to be installed in the project's .venv
# (it's a backend dependency) -- this file's own header comment says to just
# run `python run.py`, which may resolve to a system Python that never had
# it installed. Import optionally so a missing dotenv degrades to "can't see
# .env-only values" (still works via real shell env vars) instead of a hard
# crash that breaks the launcher for ALL usage, tunnel or not.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[launcher] note: python-dotenv not installed in this interpreter -- "
          "values set only in .env (not your shell environment) won't be visible "
          "to run.py. Run with the project's .venv Python, or set values like "
          "SESSION_SECRET_KEY directly in your shell, to avoid this.")

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


def _resolve_cloudflared() -> str:
    """Find cloudflared.exe even when it's not on THIS process's PATH.

    On Windows, installing cloudflared (e.g. via winget) updates the
    machine/user PATH in the registry, but any terminal/process already
    running keeps the PATH it started with -- it won't see the new entry
    until a fresh process is spawned. That made 'python run.py --tunnel'
    fail with WinError 2 even right after a successful install, in a
    terminal that was simply opened before the install ran. Falling back
    to cloudflared's well-known winget install location sidesteps that
    stale-PATH trap instead of requiring the user to reopen their terminal.
    """
    # Preferred: the standalone exe kept inside the project (tools/, not in
    # git) so nothing is installed on C:. Download it from Cloudflare's GitHub
    # releases as cloudflared-windows-amd64.exe, renamed to cloudflared.exe.
    local = Path(__file__).resolve().parent / "tools" / "cloudflared.exe"
    if local.is_file():
        return str(local)
    found = shutil.which("cloudflared")
    if found:
        return found
    for candidate in (
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
    ):
        if os.path.isfile(candidate):
            return candidate
    return "cloudflared"  # not found anywhere; let the OSError path report it


TUNNEL_CMD = [_resolve_cloudflared(), "tunnel", "--url", "http://127.0.0.1:4000"]
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


# Excludes the literal "api" subdomain: cloudflared's real assigned quick-
# tunnel hostnames are always multi-word (e.g. "immediate-harbor-tampa"),
# never a single bare word -- so a match on "api.trycloudflare.com" is
# never a real tunnel address (seen once during heavy back-to-back testing,
# likely an artifact of hitting the free anonymous service's rate limit).
_TUNNEL_URL_RE = re.compile(r'https://(?!api\.)[a-zA-Z0-9-]+\.trycloudflare\.com')
_tunnel_url = {"value": None}


def _read_tunnel_output(p: subprocess.Popen, url_holder: dict, url_found: threading.Event) -> None:
    """Background reader thread: keeps draining cloudflared's combined
    stdout/stderr (so the pipe never fills and blocks the subprocess) and
    captures the quick-tunnel URL the first time it appears in the output.
    Takes its own url_holder/url_found per attempt (rather than shared
    globals) so a retry in start_tunnel() gets a clean slate instead of
    reacting to a stale match from a previous, abandoned attempt.
    """
    for line in iter(p.stdout.readline, ''):
        if not line:
            break
        match = _TUNNEL_URL_RE.search(line)
        if match and url_holder["value"] is None:
            url_holder["value"] = match.group(0)
            url_found.set()


def start_tunnel(max_attempts: int = 3) -> subprocess.Popen:
    """Launch cloudflared, retrying if it doesn't report a URL in time.

    Cloudflare's free, account-less "quick tunnel" registration is known to
    occasionally fail or hang with no error -- observed directly during
    development: three back-to-back manual invocations of the exact same
    command produced a URL in ~7s, then silently never produced one at all,
    then worked again. A single 20s attempt is not reliable enough on its
    own; retrying a fresh cloudflared process (new registration attempt)
    resolves it most of the time without any user action.
    """
    p = None
    for attempt in range(1, max_attempts + 1):
        print(f"[launcher] starting tunnel (attempt {attempt}/{max_attempts})...")
        p = subprocess.Popen(
            TUNNEL_CMD,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        url_holder = {"value": None}
        url_found = threading.Event()
        threading.Thread(target=_read_tunnel_output, args=(p, url_holder, url_found), daemon=True).start()
        if url_found.wait(timeout=20):
            procs.append(("tunnel", p))
            _tunnel_url["value"] = url_holder["value"]
            # Matches Vite's own "->  Local:"/"->  Network:" banner column
            # width so this reads as one more line in that same list instead
            # of a separate, easy-to-miss [launcher] message. Plain ASCII
            # "->" (not Vite's Unicode arrow) on purpose -- Python's stdout
            # on some Windows consoles/interpreters is still the legacy
            # cp1252 codepage, which can't encode "➜" and crashes print()
            # with UnicodeEncodeError.
            print(f"  ->  Public:  {_tunnel_url['value']}/  (via Cloudflare Tunnel)")
            print("[launcher] share that URL -- it stops working the moment you stop run.py")
            return p
        # This attempt's cloudflared process didn't produce a URL in time --
        # stop it (killing the whole tree, matching _kill_tree's reasoning)
        # before retrying, so a stuck/zombie attempt doesn't linger.
        _kill_tree(p, f"tunnel (attempt {attempt})")
        if attempt < max_attempts:
            print(f"[launcher] tunnel attempt {attempt} did not report a URL within 20s -- retrying...")
    print(f"[launcher] WARNING: tunnel did not report a URL after {max_attempts} attempts. Check "
          "that cloudflared is installed (winget install --id Cloudflare.cloudflared) and that "
          "you have an internet connection. Backend/frontend are still running locally regardless.")
    return p


def _wait_for_frontend_ready(timeout_seconds: float = 45.0) -> bool:
    """Poll the frontend's /api/health proxy path until it responds correctly,
    or the timeout elapses. Uses the SAME 127.0.0.1 target cloudflared's
    tunnel points at (see TUNNEL_CMD) rather than 'localhost', so there's no
    ambiguity from Windows' IPv4/IPv6 resolution order routing to an
    unrelated process instead of our actual Vite dev server (a real failure
    mode --strictPort alone can't catch). Also confirms the frontend didn't
    crash on startup. Any non-2xx/garbage response (URLError, OSError,
    HTTPException from a non-HTTP listener on the port, or a ValueError from
    a malformed body) is treated as "not ready yet" and retried until the
    timeout, rather than crashing the launcher."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:4000/api/health", timeout=2) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status == 200 and '"status":"ok"' in body.replace(" ", ""):
                    return True
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
            pass
        time.sleep(1)
    return False


def _check_tunnel_secret() -> bool:
    """Refuse to tunnel with the insecure default session secret -- anyone who
    knows it (it's hardcoded in backend/app/config.py) could forge a valid
    login session and bypass the login gate entirely. Returns True if safe
    to proceed."""
    secret = os.environ.get("SESSION_SECRET_KEY", "")
    if not secret or secret == "dev-only-insecure-secret-change-me" or len(secret) < 32:
        print("[launcher] REFUSING to start tunnel: SESSION_SECRET_KEY is unset, still the")
        print("[launcher] insecure default, or too short (< 32 chars) to be a real random")
        print("[launcher] value. Anyone could forge a login session and bypass the password")
        print("[launcher] gate. Set a real random value first, e.g.:")
        print("[launcher]   (PowerShell) $env:SESSION_SECRET_KEY = python -c \"import secrets; print(secrets.token_hex(32))\"")
        print("[launcher] then set it in your .env file so it's picked up on every future run.")
        return False
    return True


def _check_default_password() -> bool:
    """Refuse to tunnel while the seeded login (test/admin1234, hardcoded in
    backend/app/database.py and so public in the source) still works -- it
    would let anyone who reads the repo straight in. Returns True if safe."""
    try:
        import sqlite3
        import bcrypt
    except ImportError:
        print("[launcher] note: bcrypt not importable here -- skipping the default-password check.")
        return True
    db_path = Path(os.environ.get("DATABASE_PATH") or Path(__file__).resolve().parent / "backend" / "app.db")
    if not db_path.exists():
        # Fresh install: the backend seeds the default user on first start.
        print("[launcher] REFUSING to start tunnel: no database yet, so the first start would")
        print("[launcher] seed the public default login. Run `python run.py --local` once,")
        print("[launcher] stop it, then run `python scripts/set_password.py`.")
        return False
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT password_hash FROM users").fetchall()
    if any(bcrypt.checkpw(b"admin1234", h.encode()) for (h,) in rows):
        print("[launcher] REFUSING to start tunnel: the login still uses the default password")
        print("[launcher] (admin1234), which is written in the public source code. Change it first:")
        print("[launcher]   python scripts/set_password.py")
        return False
    return True


# --- Public status page (Option B) ---------------------------------------
# The quick tunnel's URL changes on every start, so the public status page
# (a separate GitHub Pages site) can't hardcode it. Instead this publishes
# the current URL to a GitHub Gist the page reads. Needs GIST_ID and
# GIST_TOKEN (classic token, `gist` scope only) in .env; without them the
# tunnel still works, the status page just isn't updated.
_GIST_FILE = "status.json"


def _publish_status(online: bool, url: str | None = None) -> None:
    gist_id = os.environ.get("GIST_ID", "").strip()
    token = os.environ.get("GIST_TOKEN", "").strip()
    if not gist_id or not token:
        print("[launcher] note: GIST_ID / GIST_TOKEN not set -- public status page not updated.")
        return
    status = {"online": online, "url": url, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    body = json.dumps({"files": {_GIST_FILE: {"content": json.dumps(status, indent=2)}}}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/gists/{gist_id}",
        data=body,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "imagesound-run.py",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
        print(f"[launcher] status page updated: {'online' if online else 'offline'}")
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        # Never let the status page take the server down with it.
        print(f"[launcher] WARNING: could not update the status gist ({e}).")


def _check_ports_free() -> bool:
    """Refuse to start if something already listens on the backend/frontend
    ports. A stray server from an earlier session (not in the pidfile, so the
    stop switch can't see it) once kept port 8000 on 0.0.0.0 while run.py's
    backend bound 127.0.0.1 alongside it on Windows: requests were split
    between old and new code, and each backend's one-generation-at-a-time
    guard only saw its own jobs, so two models could load on the GPU at once."""
    import socket
    busy = []
    for port, name in ((8000, "backend"), (4000, "frontend")):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                busy.append((port, name))
    if not busy:
        return True
    for port, name in busy:
        owner = ""
        if sys.platform == "win32":
            try:
                out = subprocess.check_output(["netstat", "-ano", "-p", "TCP"], text=True, timeout=10)
                pids = {line.split()[-1] for line in out.splitlines()
                        if f":{port} " in line and "LISTENING" in line}
                owner = "  ".join(f"pid {pid}: {_process_cmdline(int(pid))[:120]}" for pid in pids)
            except Exception:
                pass
        print(f"[launcher] REFUSING to start: port {port} ({name}) is already in use. {owner}")
    print("[launcher] Something from an earlier session is still running. Stop it (Task Manager,")
    print("[launcher] or `taskkill /F /T /PID <pid>`) and run this again.")
    return False


def stop_all(*_):
    print("\n[launcher] shutting down — killing full process trees to prevent GPU-memory orphans...")
    for name, p in procs:
        _kill_tree(p, name)
    _remove_pidfile()
    if any(name == "tunnel" for name, _ in procs):
        _publish_status(False)
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
    # taskkill /F gives the other run.py no chance to run its own shutdown,
    # so mark the status page offline from here instead.
    if stopped_any and "tunnel" in recorded:
        _publish_status(False)
    return stopped_any


if __name__ == "__main__":
    # Public by default: the tunnel was too easy to forget. `--local` keeps it
    # on this machine only. `--tunnel` is still accepted (it's the default now).
    TUNNEL_MODE = "--local" not in sys.argv

    # The stop-switch must always take priority over any flag-specific gating
    # (e.g. the tunnel secret check below) -- running this script again to
    # stop a previous instance is documented/expected behavior regardless of
    # what flags are passed or what the environment currently looks like.
    if _try_stop_previous_instance():
        print("[launcher] previous instance stopped. Run again to start it back up.")
        sys.exit(0)

    if not _check_ports_free():
        sys.exit(1)

    # Never go public with a forgeable session or the public default login --
    # but don't block local use over it either: fall back to local-only.
    if TUNNEL_MODE and not (_check_tunnel_secret() and _check_default_password()):
        print("[launcher] starting LOCAL ONLY (no public link) until that's fixed.")
        TUNNEL_MODE = False

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    # Public HTTPS via the tunnel -> mark the session cookie Secure (see main.py).
    start("backend", BACKEND_CMD, env={"SESSION_HTTPS_ONLY": "1"} if TUNNEL_MODE else None)
    frontend_env = {
        "VITE_API_BASE": "/api",
    } if TUNNEL_MODE else None
    start("frontend", FRONTEND_CMD, cwd=FRONTEND_DIR, env=frontend_env)
    # Write the pidfile immediately once backend+frontend are started, BEFORE
    # the readiness probe / tunnel start below -- that way the stop-switch
    # can always find and clean them up even if something after this point
    # raises unexpectedly, instead of orphaning them with no pidfile to find.
    _write_pidfile()
    if TUNNEL_MODE:
        if _wait_for_frontend_ready():
            try:
                start_tunnel()
                # Re-write the pidfile so it also includes the tunnel entry.
                _write_pidfile()
                if _tunnel_url["value"]:
                    _publish_status(True, _tunnel_url["value"])
            except OSError as e:
                print(f"[launcher] WARNING: could not start cloudflared ({e}). Backend/frontend are "
                      "still running locally on :8000/:4000. If cloudflared was just installed, open "
                      "a NEW terminal (PATH needs refreshing) and try 'python run.py' again "
                      "after stopping this instance with 'python run.py'.")
        else:
            print("[launcher] WARNING: frontend did not become healthy within 45s -- skipping "
                  "tunnel start. Backend/frontend may still be usable locally; check for errors above.")
    print("[launcher] both running. Backend on :8000, frontend on its dev port.")
    print("[launcher] Ctrl+C to stop both, or run `python run.py` again (even from another terminal) to stop them.")
    if TUNNEL_MODE and _tunnel_url["value"]:
        # Repeat the URL down here, after all the startup noise above, so
        # it's the last thing printed and easy to spot/copy without
        # scrolling back up through backend/frontend/tunnel startup logs.
        print()
        print("=" * 60)
        print(f"  Public URL: {_tunnel_url['value']}")
        print("=" * 60)
    elif TUNNEL_MODE:
        # The reason was printed further up, but easily lost in the startup
        # noise -- repeat the outcome last so it can't be missed.
        print()
        print("=" * 60)
        print("  NO PUBLIC LINK -- the tunnel didn't start (see the WARNING above).")
        print("  Running on this machine only; the status page still says offline.")
        print("=" * 60)
    # wait for either to exit
    for name, p in procs:
        p.wait()
    _remove_pidfile()
    if TUNNEL_MODE and _tunnel_url["value"]:
        _publish_status(False)
