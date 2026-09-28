"""
Who is asking, and which songs they may touch.

Two roles: `admin` sees and manages everything; `user` (the shared public
account) only sees songs made on that account. main.py's auth_gate loads the
account into request.state.user on every non-public request, so routes read
it from here instead of the session directly.

Rows a caller doesn't own are reported as 404, never 403 -- a 403 would
confirm the id exists.
"""

from fastapi import Request

from app.config import USER_MAX_DURATION_SECONDS


def current_user(request: Request) -> dict:
    return request.state.user


def is_admin(request: Request) -> bool:
    return current_user(request)["role"] == "admin"


def owner_filter(request: Request) -> tuple[str, tuple]:
    """SQL to append to a `files` WHERE clause, plus its parameters."""
    if is_admin(request):
        return "", ()
    return " AND owner_id=?", (current_user(request)["id"],)


def max_duration_for(request: Request) -> int | None:
    """Extra cap on song length for this caller, or None for no extra cap."""
    return None if is_admin(request) else USER_MAX_DURATION_SECONDS
