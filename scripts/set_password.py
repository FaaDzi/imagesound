# set_password.py — manage ImageSound logins: change a password, rename an
# account, or create a new one.
#
# Two roles: `admin` (you: sees everything, can stop other people's
# generations) and `user` (a shared public account with limits; see
# backend/app/access.py). The backend seeds a default admin
# (test/admin1234) that is written in the public source, and
# `run.py --tunnel` refuses to start until its password is changed.
#
# Run from the project root with the server STOPPED:
#     python scripts/set_password.py
# Passwords are read with getpass, so they never show on screen or land in
# shell history. Changes don't log anyone out (sessions are tied to the
# account, not its name or password) -- rotate SESSION_SECRET_KEY in .env to
# log every device out.
import getpass
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# bcrypt only lives in the project's .venv. Re-run under it when invoked with
# some other Python. Not os.execv: on Windows that spawns a new process and
# exits this one, so the shell prints its prompt and competes with the
# password prompt for keystrokes. Run as a child and wait instead.
_VENV_PYTHON = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"
if (
    sys.platform == "win32"
    and _VENV_PYTHON.exists()
    and Path(sys.executable).resolve() != _VENV_PYTHON.resolve()
):
    import subprocess
    sys.exit(subprocess.call([str(_VENV_PYTHON), str(Path(__file__).resolve())] + sys.argv[1:]))

import bcrypt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.database import get_connection, init_db  # noqa: E402

# Admin passwords guard everything, so they must be real. The `user` account
# is shared publicly on purpose, so any non-empty password is allowed there.
ADMIN_MIN_LENGTH = 12


def ask_password(role: str) -> str | None:
    minimum = ADMIN_MIN_LENGTH if role == "admin" else 1
    password = getpass.getpass(f"New password (min {minimum} chars, hidden while typing): ")
    if len(password) < minimum:
        print("Too short, nothing changed.")
        return None
    if role == "admin" and password == "admin1234":
        print("That's the public default, nothing changed.")
        return None
    if getpass.getpass("Repeat password: ") != password:
        print("Passwords don't match, nothing changed.")
        return None
    return password


def main() -> int:
    # Creates the database if needed and applies migrations (e.g. the role
    # column), so this works even before the server has run on new code.
    init_db()

    with get_connection() as conn:
        users = conn.execute("SELECT id, username, role FROM users ORDER BY created_at").fetchall()
        print("Accounts:")
        for u in users:
            print(f"  {u['username']}  ({u['role']})")
        print()

        name = input("Account to change, or a new name to create it: ").strip()
        if not name:
            print("No name given, nothing changed.")
            return 1
        existing = conn.execute("SELECT id, username, role FROM users WHERE username=?", (name,)).fetchone()

        if existing is None:
            role = input("New account. Role, admin or user [user]: ").strip().lower() or "user"
            if role not in ("admin", "user"):
                print("Role must be 'admin' or 'user', nothing changed.")
                return 1
            password = ask_password(role)
            if password is None:
                return 1
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at, role) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), name, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                 datetime.now(timezone.utc).isoformat(), role),
            )
            conn.commit()
            print(f"Created '{name}' ({role}).")
            return 0

        new_name = input(f"Username [{existing['username']}]: ").strip() or existing["username"]
        if new_name != existing["username"] and conn.execute(
            "SELECT 1 FROM users WHERE username=?", (new_name,)
        ).fetchone():
            print(f"'{new_name}' is already taken, nothing changed.")
            return 1
        password = ask_password(existing["role"])
        if password is None:
            return 1
        conn.execute(
            "UPDATE users SET username=?, password_hash=? WHERE id=?",
            (new_name, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(), existing["id"]),
        )
        conn.commit()

    print(f"Updated '{new_name}' ({existing['role']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
