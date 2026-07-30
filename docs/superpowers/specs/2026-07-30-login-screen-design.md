# Login Screen — Design

## Purpose

Let anyone browse the app (Home, Library, the Player/Studio page) to see how
it works and what it produces, but require a valid login before they can
actually *use* it — upload/type a prompt, generate a song, play or download
any audio, or interact with the Player's generation controls. This is
explicitly a stopgap: one seeded user account for now, with real multi-user
registration planned as separate future work. `backend/app/routers/audio.py`'s
existing `# TODO: add AND owner_id=? here once auth exists` comment (and the
`owner_id` column already sitting unused in the `files` table) both
anticipated exactly this — this feature is what makes that TODO actionable,
though actually wiring `owner_id` into every router's queries is out of scope
for this iteration (see Non-goals).

**Revision note:** the first pass of this design gated the entire app behind
a hard redirect-to-`/login` wall. That's been revised (still pre-implementation)
to the "browse freely, block actions" model described above and in the
Architecture/Frontend sections below — a curious visitor can see the UI and
the library's contents, but every state-changing or content-serving action
still requires login, enforced independently by the backend regardless of
what the frontend shows.

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
  otherwise. This is what the frontend polls once on load to populate
  `AuthContext`, which every page then reads to decide what's interactive
  (see Frontend section) — it doesn't gate navigation to any page.

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

### Enforcement: a new auth-gate middleware, allowlisting *public* paths

Unlike the original all-or-nothing design, most GET endpoints that only
*display* things (not stream/serve actual audio, or mutate anything) are
public — this is what lets a visitor browse Home/Library/Player without
logging in. Everything else requires a valid session. A small middleware
(added in `main.py`, after CORS) checks every incoming request against an
explicit allowlist of public paths/methods; anything not on the allowlist
requires a valid session (same check `GET /auth/me` uses) or returns 401.

**Public (no login required):**
- `GET /library` — song metadata (id, prompt, duration, thumbnail
  reference, `fad_verdict`, etc.) so the Library page can render real cards
  for a logged-out visitor.
- `GET /image/{id}` — thumbnails referenced by those cards (source images,
  not generated audio) — needed for the cards to look like the real thing,
  not empty boxes.
- `GET /health`
- `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`

**Protected (login required):**
- `POST /upload`, `POST /generate`, `POST /describe` — the actual
  input/generation actions.
- `POST /cancel/{id}`, `POST /save/{id}`, `POST /discard/{id}` — job
  mutation.
- `GET /audio/{id}` — actual audio streaming/playback. This is the specific
  endpoint that makes "cannot look up [i.e. listen to] the library of
  sound" true even though the library *list* itself is public.
- `GET /download/{id}` — file download.
- `POST /midi/convert/{id}`, `GET /midi/preview/{id}` — MIDI conversion.
- `GET /status/{id}` — job status polling; moot for a logged-out visitor
  anyway since they can't start a job, but gated for consistency (no
  legitimate use of this endpoint exists without a job to check on).
- Everything else not explicitly listed above, including FastAPI's own
  auto-generated `/docs`/`/openapi.json` — deny-by-default, not an
  allowlist of things to block, so a future new endpoint is protected
  unless someone deliberately adds it to the public list.

This split is what makes the frontend's "visible but disabled" treatment
*true* rather than cosmetic: even if someone bypasses the UI entirely and
calls `POST /generate` or `GET /audio/{id}` directly with curl/Postman and
no session cookie, the backend independently rejects it. The frontend's
disabled buttons and Player overlay (see Frontend section) are a UX nicety
on top of a real, independently-enforced backend gate — not a substitute
for one.

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

### No route-level gate — Home, Library, and Player all render for everyone

All three existing routes (`/`, `/player`, `/library`) stay reachable
without login; `AuthContext`'s `username`/`loading` state is read *within*
each page to decide what's interactive, not to block navigation to the page
itself.

- **Home** (`src/pages/Home.tsx`): the text-prompt input, image/audio
  upload dropzone, and "PROCEED TO STUDIO" button are all rendered
  normally but `disabled` when logged out (grayed out via existing styling
  conventions), with an `onClick`/`onFocus` handler that shows a brief
  "login required" inline message instead of performing the real action.
- **Library** (`src/pages/Library.tsx`): song cards render fully (title,
  duration, thumbnail, `fad_verdict` badge — same `GET /library` data
  either way, since that endpoint is public). Play and Download buttons on
  each card are `disabled` when logged out, with the same "login required"
  message on click instead of calling `GET /audio/{id}`/`GET
  /download/{id}`.
- **Player** (`src/pages/Player.tsx`): the functional area (everything
  Task 7 of the repo-cleanup plan split into `SourcePreview`, `ArcEditor`,
  `EffectsPanel`, `GeneratePanel`) gets a full-coverage opaque black overlay
  when logged out, with a centered lock icon/message and a link to
  `/login` — a stronger treatment than Home/Library's per-field disabling,
  since this is explicitly the "main" feature curious visitors would want
  to poke at. The page's own chrome (nav, page title) stays visible above
  the overlay.

### Persistent login indicator (Navigation)

`src/components/Navigation.tsx` gains a persistent element (top right,
matching the existing nav bar's other status indicators like the theme
toggle): a "LOGIN" link when logged out, or the username + a logout button
when logged in. This is the one always-visible, unambiguous path to
actually log in — the per-page disabled states don't need to each duplicate
a full login form, they just need to make clear *that* login is required
and let the persistent nav element be where it actually happens.

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
| No/invalid/expired session, calling a protected endpoint directly (curl/Postman/devtools) | 401 from the API — enforced independently of whatever the frontend shows |
| No/invalid/expired session, using the frontend UI | Relevant controls render disabled with a "login required" message (Home/Library) or the Player's functional area is covered by an opaque overlay; navigation between pages is never blocked |
| Calling a public endpoint (`GET /library`, `GET /image/{id}`, `GET /health`) with no session | Succeeds normally — these are intentionally open regardless of auth state |
| `/auth/login`/`/auth/logout`/`/auth/me`/`/health` | Always reachable regardless of session state (login must be reachable to log in; health check must stay unauthenticated for monitoring) |
| Session cookie present but tampered/forged | Signature verification fails → treated identically to "no session" |

## Testing approach

- Real login attempt with correct credentials against the live backend,
  confirm the session cookie is set and `GET /auth/me` then succeeds.
- Real login attempt with wrong password, confirm generic 401 and no cookie
  set.
- With no session cookie: confirm `GET /library` and `GET /image/{id}`
  succeed (public), and confirm `POST /generate`, `POST /upload`, `GET
  /audio/{id}`, `GET /download/{id}`, `POST /save/{id}` all return 401 when
  called directly (curl/Postman, no cookie) — proving the gate is real at
  the API level, not just a frontend visual state.
- Browser, logged out: confirm Home/Library/Player all render and are
  navigable (no redirect-to-login), confirm Home's inputs and Library's
  play/download buttons are visibly disabled and show a login prompt on
  click instead of performing the real action, confirm Player's functional
  area is covered by the opaque overlay.
- Browser, logged in: confirm the same three pages are fully interactive —
  overlay gone, buttons enabled, a real generation/upload/play/download all
  work end-to-end.
- Confirm logout clears the session (subsequent `/auth/me` call fails, the
  frontend reverts to the logged-out disabled/overlay state without
  needing a page reload, if reasonable to verify; a reload-based check is
  acceptable if not).
- Browser screenshots: the login page in both light and dark theme, the
  failed-attempt error state, Home/Library logged-out (disabled controls),
  Player logged-out (overlay), and Player logged-in (overlay gone).
