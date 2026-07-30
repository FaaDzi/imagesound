# Login Screen — Design

## Purpose

Gate the whole app behind a login, ahead of the first public GitHub push, so the
hosted instance is only usable by people who know a valid username/password —
not the general public. This is explicitly a stopgap: one seeded user account
for now, with real multi-user registration planned as separate future work.
`backend/app/routers/audio.py`'s existing `# TODO: add AND owner_id=? here
once auth exists` comment (and the `owner_id` column already sitting unused in
the `files` table) both anticipated exactly this — this feature is what makes
that TODO actionable, though actually wiring `owner_id` into every router's
queries is out of scope for this iteration (see Non-goals).

## Non-goals (explicitly out of scope for this iteration)

- A registration UI, self-service signup, or any way to create additional
  users through the app itself — there is exactly one seeded account.
- Password reset / forgot-password flows.
- Per-user file scoping (`owner_id` filtering in `audio.py` and friends) —
  this feature only establishes *that* a request is authenticated, not that
  different logged-in users see different files. With one user, that
  distinction is moot for now; revisit when a second real user exists.
- Role-based permissions (admin vs. non-admin) — one flat login, no roles.
- HTTPS/TLS setup — the cookie is written to work correctly today over local
  HTTP and to upgrade automatically (`Secure` flag) whenever this is ever
  served over HTTPS, but provisioning TLS itself is separate infrastructure
  work, not part of this feature.

## Architecture

### Database: `users` table

New table, added via the existing `_migrate_db()`-adjacent init pattern in
`backend/app/database.py`:

```sql
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
)
```

On startup, if `users` is empty, seed exactly one row: username `test`,
password `admin1234` hashed with `bcrypt`. This check-then-seed step must be
idempotent and must never overwrite an existing row — once seeded (or once
the password is ever changed some other way), startup leaves it alone. This
mirrors the project's existing idempotent-migration style rather than
introducing a new pattern.

### New dependency: `bcrypt`

Nothing in the project hashes passwords today. Add `bcrypt` to
`backend/requirements.txt` — a small, purpose-built library, not the heavier
`passlib` abstraction, since there's exactly one hashing algorithm in use
here.

### New router: `backend/app/routers/auth.py`

- `POST /auth/login` — body `{"username": str, "password": str}`. Looks up
  the user, verifies the password against the stored bcrypt hash. On success,
  writes `{"username": ...}` into the Starlette session (see below) and
  returns `{"username": ...}`. On failure (unknown username OR wrong
  password), returns a generic 401 `{"error": "invalid username or
  password"}` — never reveals which part was wrong. Rate-limited via the
  project's existing `slowapi` limiter (already wired up in `main.py`) to
  make brute-forcing the known username `test` impractical.
- `POST /auth/logout` — clears the session, returns 200.
- `GET /auth/me` — returns `{"username": ...}` if the session is valid, 401
  otherwise. This is what the frontend polls once on load to decide whether
  to render the app or redirect to `/login`.

### Session mechanism: Starlette `SessionMiddleware`

Uses Starlette's built-in signed-cookie session support (ships with FastAPI's
underlying Starlette, needs `itsdangerous` as a small added dependency for
the signing). Session data (just `{"username": ...}`) lives inside the
signed cookie itself — no server-side session store to build or manage,
since the signature makes the cookie unforgeable without the app's secret
key. Cookie settings:

- `httponly=True` — not readable by JavaScript (mitigates XSS token theft,
  the main reason this was chosen over a localStorage token).
- `same_site="lax"` — cookies aren't port-scoped, so this works correctly for
  cross-port local dev (frontend on `:3000`, backend on `:8000`, same host)
  without needing the looser and HTTPS-only `same_site="none"`.
- `max_age` — 30 days, so a logged-in browser doesn't need to re-authenticate
  constantly. `secure=True` is set whenever the request arrives over HTTPS
  (Starlette handles this automatically based on the request scheme) so this
  degrades safely to plain HTTP for local dev without any code branching.
- Secret key: a new `SESSION_SECRET_KEY` env var, read the same way every
  other config value is (`os.getenv(...)` in `backend/app/config.py`,
  following the exact style of `DATABASE_PATH`/`UNSAVED_EXPIRY_SECONDS`),
  with a dev-only fallback default and a code comment making clear that
  fallback must never be relied on for anything beyond local dev.

### Enforcement: a new global auth-gate middleware

A small middleware (added in `main.py`, after CORS) checks every incoming
request: if the path is `/auth/login`, `/auth/logout`, or `/health`, let it
through unconditionally; otherwise, require a valid session (same check
`GET /auth/me` uses) or return 401. This is what makes the gate apply to the
*entire* app, including the API directly — not just the frontend's own
page-level redirect, which alone could be bypassed by hitting the API
straight from curl/Postman. (Everything not explicitly listed is gated by
default, including FastAPI's own auto-generated `/docs`/`/openapi.json` —
consistent with "select people only," not just "select people can see the
generated songs.")

**Middleware ordering matters**: this auth-gate middleware must let CORS
preflight `OPTIONS` requests through unconditionally (they carry no cookie
and browsers expect them to succeed regardless of auth state) — otherwise
every cross-origin request from the frontend would fail at the preflight
step with a 401 before the browser ever sends the real request, breaking
the entire frontend against the backend. Either exempt the `OPTIONS` method
explicitly in this middleware, or ensure `CORSMiddleware` is registered so
it handles preflight before this middleware runs. Get this right and verify
it directly (a real cross-origin fetch, not just curl) — it's the kind of
mistake that looks fine in isolated backend testing and only breaks once
the frontend's own port is actually different from the backend's.

### CORS change

`allow_credentials` flips from `False` to `True` in `main.py` — required for
the browser to actually send the session cookie on cross-port requests from
the frontend. `allow_origins` must stay non-wildcarded for this to be valid
per the CORS spec (a wildcard origin can't be combined with credentials) —
it already is (explicit `http://localhost:3000` default, or whatever
`ALLOWED_ORIGINS` is configured to), so no further change needed there.

## Frontend

### New route: `/login`

A new `Login.tsx` page (styled to match the app's existing terminal
aesthetic — see "Visual design" below): username + password fields, a submit
button, and an inline error message shown on a failed attempt (generic
wording, matching the backend's generic 401).

### New `AuthContext`

Modeled on the existing `InProgressContext` pattern (`src/context/`). On
mount, calls `GET /auth/me` once to determine whether a valid session
exists; exposes `{ username, loading, login(), logout() }` to the rest of
the app.

### New `ProtectedRoute` wrapper

Wraps the three existing routes (`/`, `/player`, `/library`) in
`src/App.tsx`. While `AuthContext` is still resolving the initial `/auth/me`
check, renders nothing (or a minimal loading state) rather than
flashing the real app before redirecting. Once resolved: renders the route
normally if logged in, or redirects to `/login` if not.

### `src/api.ts` change

Every `fetch(...)` call gets `credentials: 'include'` added, so the session
cookie is actually attached to cross-port requests to the backend. This is
the one required change to existing API call code — everything else is
additive (new files, new route).

### Logout

A simple logout affordance added to `src/components/Navigation.tsx` (calls
`AuthContext.logout()`, which hits `POST /auth/logout` then redirects to
`/login`).

## Visual design

The login page must match the app's existing "Brutalist Cyberpunk" visual
system (neon green/pink accents, monospace type, bordered boxes, `//
SECTION_HEADER`-style captions — as already established across
Home/Player/Library). Rather than designing this from scratch here, the
Hallmark design skill will be invoked at implementation time specifically
for the Login page's markup/styling, so it inherits the existing design
tokens rather than introducing a new look.

## Error handling summary

| Case | Behavior |
|---|---|
| Wrong username or wrong password | 401, generic "invalid username or password" — never reveals which |
| Too many rapid login attempts | Rate-limited (429) via existing `slowapi` limiter |
| No/invalid/expired session cookie, any protected route | 401 from the API; frontend redirects to `/login` |
| Valid session, `/auth/login`/`/auth/logout`/`/health` | Always reachable regardless of session state (login must be reachable to log in; health check must stay unauthenticated for monitoring) |
| Session cookie present but tampered/forged | Signature verification fails → treated identically to "no session" |

## Testing approach

- Real login attempt with correct credentials against the live backend,
  confirm the session cookie is set and `GET /auth/me` then succeeds.
- Real login attempt with wrong password, confirm generic 401 and no cookie
  set.
- Confirm hitting a protected endpoint directly (e.g. `curl
  http://127.0.0.1:8000/library` with no cookie) returns 401 — proving the
  gate isn't just a frontend redirect that the API itself ignores.
- Confirm the frontend redirects an unauthenticated browser to `/login`
  before rendering Home/Player/Library, and that a logged-in browser reaches
  the real app normally.
- Confirm logout clears the session (subsequent `/auth/me` call fails, next
  protected-page load redirects to `/login` again).
- Browser screenshots of the login page in both light and dark theme, plus
  the failed-attempt error state.
