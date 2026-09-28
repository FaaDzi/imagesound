"""
POST /auth/login, POST /auth/logout, GET /auth/me

Session-based auth backed by the `users` table: an `admin` account and a
shared `user` account (see app/access.py for what each may do). Session data lives entirely in
Starlette's signed cookie (see main.py's SessionMiddleware) -- this router
never touches a session store, it only reads/writes request.session.
"""

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import RATE_LIMIT_LOGIN
from app.database import get_connection, get_user
from app.limiter import limiter

router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
@limiter.limit(RATE_LIMIT_LOGIN)
def login(request: Request, req: LoginRequest):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, role, password_hash FROM users WHERE username=?",
            (req.username,),
        ).fetchone()

    valid = row is not None and bcrypt.checkpw(req.password.encode(), row["password_hash"].encode())
    if not valid:
        # Generic message regardless of whether the username or the password
        # was wrong -- never reveal which.
        raise HTTPException(status_code=401, detail="invalid username or password")

    # Only the id is authoritative; main.py's auth_gate re-reads the account
    # on every request. The username stays for older log readers.
    request.session.clear()
    request.session["user_id"] = row["id"]
    request.session["username"] = row["username"]
    return {"username": row["username"], "role": row["role"]}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/me")
def me(request: Request):
    # Public route (the gate doesn't load the account for it), so look it up here.
    user_id = request.session.get("user_id")
    user = get_user(user_id) if user_id else None
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="not logged in")
    return {"username": user["username"], "role": user["role"]}
