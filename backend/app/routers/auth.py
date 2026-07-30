"""
POST /auth/login, POST /auth/logout, GET /auth/me

Session-based auth backed by the single-row `users` table (see
database.py's _seed_default_user). Session data lives entirely in
Starlette's signed cookie (see main.py's SessionMiddleware) -- this router
never touches a session store, it only reads/writes request.session.
"""

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import RATE_LIMIT_LOGIN
from app.database import get_connection
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
            "SELECT username, password_hash FROM users WHERE username=?",
            (req.username,),
        ).fetchone()

    valid = row is not None and bcrypt.checkpw(req.password.encode(), row["password_hash"].encode())
    if not valid:
        # Generic message regardless of whether the username or the password
        # was wrong -- never reveal which.
        raise HTTPException(status_code=401, detail="invalid username or password")

    request.session["username"] = row["username"]
    return {"username": row["username"]}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/me")
def me(request: Request):
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="not logged in")
    return {"username": username}
