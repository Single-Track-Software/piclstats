"""Session-based login, password hashing, and role gates.

Replaces the old single-user HTTP Basic admin. Accounts live in the users table
(see db/users_store.py); the signed session cookie (Starlette SessionMiddleware)
holds only the user id. Roles: 'member' unlocks the gated features, 'admin' also
reaches the /admin config pages.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import bcrypt
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from piclstats.db import users_store
from piclstats.web.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["auth"])


# --- password hashing -------------------------------------------------------

def hash_password(password: str) -> str:
    # bcrypt caps input at 72 bytes; encode then truncate to stay within it.
    digest = bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt())
    return digest.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- user loading + gates ---------------------------------------------------

class LoginRequired(Exception):
    """Raised by page gates; the app turns it into a redirect to /login."""

    def __init__(self, next_path: str = "/"):
        self.next_path = next_path


def load_user(request: Request) -> dict | None:
    """Resolve the signed-in, active user from the session, or None."""
    user_id = request.session.get("user_id") if "session" in request.scope else None
    if not user_id:
        return None
    user = users_store.get_user_by_id(user_id)
    if not user or not user["is_active"]:
        return None
    return user


def _next_path(request: Request) -> str:
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    return path


def require_member(request: Request) -> dict:
    """Page gate: any active account. Redirects anonymous users to /login."""
    user = load_user(request)
    if not user:
        raise LoginRequired(_next_path(request))
    return user


def require_admin(request: Request) -> dict:
    """Page gate: admin role only."""
    user = load_user(request)
    if not user:
        raise LoginRequired(_next_path(request))
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only")
    return user


def require_member_api(request: Request) -> dict:
    """API gate (e.g. CSV export): 401 instead of an HTML redirect."""
    user = load_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required"
        )
    return user


def require_same_origin(request: Request) -> None:
    # CSRF mitigation for state-changing POSTs: the request's Origin/Referer host
    # must match our Host, so a third-party page with a cached session cookie
    # cannot drive writes.
    host = request.headers.get("host", "")
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        raise HTTPException(status_code=403, detail="Missing Origin/Referer")
    try:
        source_host = urlparse(source).netloc
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid Origin/Referer")
    if source_host != host:
        raise HTTPException(status_code=403, detail="Cross-origin request blocked")


# --- routes -----------------------------------------------------------------

def _safe_next(next_path: str) -> str:
    # Only allow same-site relative redirects (must start with a single '/').
    if next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return "/"


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", error: str = ""):
    if load_user(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(
        "login.html", {"request": request, "next": next, "error": error}
    )


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    __: None = Depends(require_same_origin),
):
    user = users_store.get_user_by_email(email)
    if not user or not user["is_active"] or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": "Invalid email or password."},
            status_code=401,
        )
    request.session["user_id"] = user["id"]
    users_store.touch_last_login(user["id"])
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/logout")
def logout(request: Request, __: None = Depends(require_same_origin)):
    request.session.clear()
    return RedirectResponse("/", status_code=303)
