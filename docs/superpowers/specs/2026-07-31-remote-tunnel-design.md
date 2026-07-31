# Remote Tunnel Access — Design

## Goal

Let the owner share a public URL, on demand, that lets other people use the
real running app (upload/generate/library — everything the login-gated app
already does), backed by the owner's own machine and GPU. Off by default;
only live when the owner explicitly starts it, and it goes down the moment
they stop the app, exactly like `localhost` does today.

## Non-goals

- No custom domain. Cloudflare's free "quick tunnel" gives a random
  `https://<random-words>.trycloudflare.com` URL each time it starts — good
  enough for sharing with specific people on demand. A stable custom
  hostname is a later upgrade (would need a Cloudflare account + owned
  domain), not needed now.
- No IP whitelist. The existing login gate (username/password, session
  cookie, deny-by-default backend middleware) is the access control. Adding
  IP filtering on top would need a way to see/manage an allow-list that
  doesn't exist yet, for marginal benefit over "only people who have both
  the URL and the password can do anything" — skipped per YAGNI.
- No always-on tunnel. Manual, on-demand only — the owner starts it right
  before sharing and stops it when done. Nothing is exposed by default.
- No production/cloud hosting. This is "expose my laptop for a while," not
  a deployment target. If ImageSound is ever hosted permanently somewhere
  else, that's a separate, later design.
- No changes to the idle-auto-shutdown idea discussed earlier — still a
  separate, deferred feature, not bundled into this one.
- No HTTPS-provisioning or Secure-cookie hardening beyond what already
  exists — see "Known limitation" below.

## Architecture

**One tunnel, not two.** Tunneling the frontend (port 3000) and backend
(port 8000) as two independent quick tunnels would produce two unrelated
random `*.trycloudflare.com` hostnames. Because `trycloudflare.com` is on
the public suffix list, those count as fully separate "sites" for cookie
purposes — the login session cookie the backend sets would never be sent
back on API calls made from the frontend's tunnel origin. Two tunnels also
means sharing two confusing links with whoever the owner is showing this
to.

Instead, only the **frontend (port 3000)** is tunneled. Vite's dev server
gains a `/api/*` proxy that forwards to the backend on `localhost:8000`
internally, and the frontend's existing `VITE_API_BASE` override (already
built into `src/api.ts` for exactly this kind of environment-specific
base URL) is set to `/api` in tunnel mode instead of the default
`http://localhost:8000`.

From the visitor's browser, everything — the page load, the login call,
audio playback, uploads — appears to come from one origin (the tunnel URL).
This sidesteps CORS and cross-site cookie issues entirely: the request the
browser sees is same-origin; Vite forwards it to the backend as a
server-side hop the browser never observes directly. No changes are needed
to `backend/app/config.py`'s CORS allowlist or to the session cookie's
`same_site` setting for this to work.

```
Visitor's browser
      │  https://<random>.trycloudflare.com
      ▼
 cloudflared (tunnel, TLS termination at Cloudflare's edge)
      │  plain http, forwarded to localhost:3000
      ▼
 Vite dev server (port 3000)
      │  page/asset requests served directly
      │  /api/* requests proxied internally ──────┐
      ▼                                            ▼
  (rendered page)                    FastAPI backend (localhost:8000)
```

## Components

**1. `vite.config.ts`** — add a dev-server proxy entry:
```ts
server: {
  proxy: {
    '^/api/': {
      target: 'http://localhost:8000',
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ''),
    },
  },
  // ...existing hmr/watch config unchanged
}
```
The proxy key uses Vite's regex form (`^/api/`, not the plain string
`/api`) so only genuine `/api/*` paths match — a plain-string key does a
prefix match, which would also (mis)route a hypothetical `/apidocs` through
the `/^\/api/` rewrite. This only affects requests that actually hit
`/api/*` — nothing else about local dev changes. When `VITE_API_BASE` is
unset (today's normal local flow), `src/api.ts` still defaults to
`http://localhost:8000` directly and never touches this proxy at all.

`vite.config.ts` also sets `server.fs.deny` to block Vite's static file
server from ever serving sensitive paths (`backend/**`, `*.py`, `*.db*`,
`*.log`, `*.csv`, `.superpowers/**`, `docs/**`, etc.) — this matters on
plain `localhost:3000` too, not just when tunneled. **Important:** setting
`fs.deny` at all *replaces* Vite's own built-in denylist rather than
extending it — Vite does not merge config arrays. An earlier version of
this file set `fs.deny` to only the project-specific entries, which
silently discarded Vite's built-in defaults (`.env`, `.env.*`,
`*.{crt,pem}`, `**/.git/**`) and re-exposed `.env` and `.git/` over the
dev server. The fix is to always include those four Vite-default patterns
explicitly alongside the project-specific entries whenever `fs.deny` is
touched.

**2. `run.py`** — add a `--tunnel` CLI flag.
- Plain `python run.py` behaves exactly as it does today — unaffected.
- `python run.py --tunnel`:
  - Calls `load_dotenv()` (via `python-dotenv`, already a dependency) early
    at startup, mirroring `backend/app/config.py`, so a `SESSION_SECRET_KEY`
    set in the project's `.env` file is visible to `run.py` itself, not
    just the backend subprocess.
  - Refuses to start (hard precondition check, before anything else spins
    up) unless `SESSION_SECRET_KEY` is set in the environment to something
    other than the hardcoded insecure default in `backend/app/config.py` —
    without this, anyone could forge a valid login session and bypass the
    password gate on a publicly tunneled instance.
  - Sets `VITE_API_BASE=/api` in the frontend subprocess's environment
    before launching it (so the browser bundle resolves API calls to the
    relative `/api` path instead of `http://localhost:8000`).
  - Before starting the tunnel, actively polls
    `http://localhost:3000/api/health` (the same hostname/URL a real tunnel
    request would use) for up to ~20s, waiting for an HTTP 200 with
    `"status":"ok"` in the body. This replaces relying on `--strictPort`
    alone to assume port 3000 is occupied by our own Vite process —
    `--strictPort` only proves *something* bound the port, not that it's
    our server reachable via the same DNS/socket path cloudflared will use
    (Windows' IPv4/IPv6 dual-stack preference can otherwise let an
    unrelated process on the same port win). If the check doesn't pass
    within the timeout, the tunnel is skipped with a warning; backend and
    frontend keep running locally regardless.
  - Starts a third managed subprocess:
    `cloudflared tunnel --url http://localhost:3000`.
  - Captures the tunnel process's output, extracts the assigned
    `https://*.trycloudflare.com` URL once cloudflared prints it (a few
    seconds after starting), and prints it prominently to the console —
    this is the link the owner shares.
  - Registers the tunnel process in the existing PID-file /
    `_NAME_HINTS` mechanism (`"tunnel": ["cloudflared"]`) so the current
    start/stop-switch behavior (`python run.py` a second time = stop
    everything) also tears down the tunnel, exactly like it already does
    for backend/frontend.
  - If `cloudflared` isn't installed or fails to start, the backend and
    frontend still come up normally for local use — a clear warning is
    printed, but a broken tunnel doesn't take down local access.

**3. One-time setup** — `cloudflared` isn't installed on this machine.
Install via `winget install Cloudflare.cloudflared` (confirmed available).
This is a one-time step, not something `run.py` installs automatically.

## Data flow / request lifecycle

1. Owner runs `python run.py --tunnel`. Backend, frontend (with
   `VITE_API_BASE=/api`), and `cloudflared` all start.
2. Console prints the tunnel URL once cloudflared reports it.
3. Owner shares that URL with someone.
4. Visitor opens it → Cloudflare's edge terminates TLS, forwards to the
   Vite dev server on the owner's machine.
5. Visitor loads the page (browsable without login, per the existing
   login-screen design), then logs in via `/login`. The `POST
   /api/auth/login` call is same-origin from the browser's perspective;
   Vite proxies it to the real backend; the `Set-Cookie` response header
   passes through untouched, so the browser stores the session cookie
   scoped to the tunnel's own origin.
6. Every subsequent app action (upload, generate, library, playback) is a
   same-origin `/api/*` call from the visitor's browser, proxied the same
   way, cookie included automatically like any other same-origin request.
7. Owner stops the app (`python run.py` again, or Ctrl+C) → backend,
   frontend, and the tunnel all shut down together. The URL stops
   resolving; nothing is reachable from the internet anymore.

## Known limitation (accepted, not fixed)

The session cookie's `Secure` flag is set by Starlette automatically only
when the request it sees arrives over HTTPS. Because `cloudflared`
terminates TLS at Cloudflare's edge and forwards plain HTTP to
`localhost:3000`, the backend never sees an HTTPS request directly, so the
cookie won't carry `Secure` even though the public-facing connection is in
fact HTTPS. Net effect: the cookie is marked sendable over both HTTP and
HTTPS instead of HTTPS-only — broader, not narrower, so it does not block
anything working. Tightening this (e.g. trusting `X-Forwarded-Proto`) is
unnecessary complexity for a manually-toggled, short-lived demo link, and
is explicitly out of scope here, consistent with the login design spec's
existing "no HTTPS provisioning" non-goal.

`vite.config.ts`'s `server.allowedHosts: ['.trycloudflare.com']` turned out
to be required, not anticipated in the original design above. Vite 6 ships
DNS-rebinding Host-header protection that rejects any request whose `Host`
header isn't `localhost` or explicitly allowlisted, which otherwise 403s
every request that arrives through the tunnel's random
`*.trycloudflare.com` hostname.

`backend/app/limiter.py`'s rate limits (login, generate, upload, etc.) key
on the requesting IP address. Because only the frontend is tunneled and the
Vite proxy forwards `/api/*` requests to the backend without setting an
`X-Forwarded-For` header, every remote visitor's request arrives at the
backend as a plain `localhost` hop — so every tunnel visitor is seen by the
backend as `127.0.0.1` and shares ONE rate-limit bucket per endpoint (e.g.
the login endpoint's 10/minute limit is shared across everyone using the
tunnel at once, not applied per-visitor). This is accepted as a known
limitation, not fixed: a naive fix (trusting a client-supplied
`X-Forwarded-For` header) would let a malicious visitor spoof their
apparent IP and evade rate limiting entirely. A real fix would need to
distinguish "IP set by Cloudflare's own edge" from "IP merely claimed by
the client," which is more engineering than this feature's scope warrants
right now.

## Testing approach

Manual, live verification (no automated test suite for a launcher script
that mainly shells out to two other processes):
- `python run.py --tunnel` prints a working `https://*.trycloudflare.com`
  URL within a few seconds.
- Opening that URL in a real browser (ideally from a different network,
  e.g. a phone on mobile data, to genuinely prove it's not just localhost)
  shows the app; Home/Library/Player all browse without logging in, per
  the existing gate design.
- Logging in via the tunnel URL succeeds, and subsequent actions
  (generate, library actions, playback) all work — proving the `/api`
  proxy and cookie forwarding work end-to-end, not just the static page.
- Stopping the app (`python run.py` again) kills all three processes
  (confirm via Task Manager / process list) and the tunnel URL stops
  responding.
- Plain `python run.py` (no `--tunnel`) is unaffected — confirms no
  regression to the existing local-only workflow.
