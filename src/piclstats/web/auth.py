"""Session-based login, password hashing, and role gates.

Accounts live in the users table (see db/users_store.py); the signed session
cookie (Starlette SessionMiddleware) holds only the user id. Roles: 'member'
unlocks the gated features, 'admin' also reaches the /admin config pages.

Access is invite-only — there is no public signup. An admin issues an invite
from /admin/users, which mints a one-time link (db/tokens_store.py) delivered
by email; the recipient sets their own password at /invite/{token}, so a
password never passes through the admin. Password resets use the same
mechanism at /forgot and /reset/{token}.
"""

from __future__ import annotations

from collections.abc import Callable

from pathlib import Path
from urllib.parse import urlparse

import bcrypt
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from piclstats.config import settings
from piclstats.db import tokens_store, users_store
from piclstats.web import mail, ratelimit
from piclstats.web.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["auth"])

# Process-wide login throttle (see ratelimit.py for the single-machine caveat).
throttle = ratelimit.LoginThrottle()

# Separate budget for /forgot: this one guards an outbound email, so the limit
# is about not letting anyone use us to spam an address, not about guessing.
reset_throttle = ratelimit.LoginThrottle(max_attempts=3, window_seconds=15 * 60)

# Long enough to resist guessing without pushing coaches into a password
# manager they may not use. Length is the only rule — composition rules push
# people toward "Password1!" and we'd rather they used a passphrase.
MIN_PASSWORD_LENGTH = 12


def password_problem(password: str, confirm: str) -> str | None:
    """Human-readable reason the password is unacceptable, or None if it's fine."""
    if password != confirm:
        return "Passwords don't match."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    return None


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


# --- roles ------------------------------------------------------------------
# Ranked: each role has everything below it.
#   coach — finish-time predictions
#   picl  — predictions + staging orders (league staff)
#   admin — everything, including this admin area
ROLES: tuple[str, ...] = ("coach", "picl", "admin")
ROLE_RANK = {role: i for i, role in enumerate(ROLES)}
ROLE_HELP = {
    "coach": "Finish-time predictions for any rider.",
    "picl": "Predictions plus staging orders and the CSV export.",
    "admin": "Everything, plus courses, users, data quality and usage.",
}
DEFAULT_ROLE = "coach"


def role_allows(role: str | None, needed: str) -> bool:
    """True if `role` is at least `needed` in the ranking. Unknown roles allow nothing."""
    return role in ROLE_RANK and ROLE_RANK[role] >= ROLE_RANK[needed]


def _forbidden(needed: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"This page needs the {needed} role. Ask a PICL admin if you should have it.",
    )


def require_member(request: Request) -> dict:
    """Page gate: any active account. Redirects anonymous users to /login."""
    user = load_user(request)
    if not user:
        raise LoginRequired(_next_path(request))
    return user


def require_role(needed: str) -> Callable[[Request], dict]:
    """Page gate for a minimum role; anonymous users go to /login, others get 403."""

    def gate(request: Request) -> dict:
        user = require_member(request)
        if not role_allows(user["role"], needed):
            raise _forbidden(needed)
        return user

    return gate


def require_role_api(needed: str) -> Callable[[Request], dict]:
    """API gate (CSV export): 401/403 instead of an HTML redirect."""

    def gate(request: Request) -> dict:
        user = load_user(request)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")
        if not role_allows(user["role"], needed):
            raise _forbidden(needed)
        return user

    return gate


require_coach = require_role("coach")
require_picl = require_role("picl")
require_picl_api = require_role_api("picl")


def require_admin(request: Request) -> dict:
    """Page gate: admin role only."""
    return require_role("admin")(request)


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


def client_ip(request: Request) -> str | None:
    """Caller's IP, trusting Fly's proxy headers ahead of the socket address.

    On Fly the socket peer is always the edge proxy, so without this every
    request would throttle under one key.
    """
    fly_ip = request.headers.get("fly-client-ip")
    if fly_ip:
        return fly_ip
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    __: None = Depends(require_same_origin),
):
    key = ratelimit.client_key(client_ip(request), email)
    wait = throttle.retry_after(key)
    if wait:
        minutes = max(1, round(wait / 60))
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next": next,
                "error": f"Too many failed attempts. Try again in {minutes} minute"
                f"{'s' if minutes != 1 else ''}.",
            },
            status_code=429,
            headers={"Retry-After": str(wait)},
        )

    user = users_store.get_user_by_email(email)
    if not user or not user["is_active"] or not verify_password(password, user["password_hash"]):
        throttle.record_failure(key)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": "Invalid email or password."},
            status_code=401,
        )
    throttle.record_success(key)
    request.session["user_id"] = user["id"]
    users_store.touch_last_login(user["id"])
    return RedirectResponse(_safe_next(next), status_code=303)


# --- invite acceptance ------------------------------------------------------


def build_link(request: Request, path: str) -> str:
    """Absolute URL for an emailed link.

    Prefers the configured public base URL. Falling back to the request's own
    base URL is only safe because these links are built on admin-authenticated
    or self-initiated requests — never from a value an attacker supplies.
    """
    base = settings.public_base_url.rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return f"{base}{path}"


@router.get("/invite/{token}", response_class=HTMLResponse)
def invite_form(request: Request, token: str):
    invite = tokens_store.get_valid(token, tokens_store.INVITE)
    if not invite:
        return templates.TemplateResponse(
            "invite.html",
            {"request": request, "token": token, "invite": None, "error": None},
            status_code=404,
        )
    return templates.TemplateResponse(
        "invite.html", {"request": request, "token": token, "invite": invite, "error": None}
    )


@router.post("/invite/{token}")
def invite_accept(
    request: Request,
    token: str,
    name: str = Form(""),
    password: str = Form(...),
    confirm: str = Form(...),
    __: None = Depends(require_same_origin),
):
    invite = tokens_store.get_valid(token, tokens_store.INVITE)
    if not invite:
        return templates.TemplateResponse(
            "invite.html",
            {"request": request, "token": token, "invite": None, "error": None},
            status_code=404,
        )

    def _reject(message: str, status_code: int = 400):
        return templates.TemplateResponse(
            "invite.html",
            {"request": request, "token": token, "invite": invite, "error": message},
            status_code=status_code,
        )

    problem = password_problem(password, confirm)
    if problem:
        return _reject(problem)

    # The address was bound at invite time, so an account already existing means
    # the invite is stale (they signed up via an earlier one).
    if users_store.get_user_by_email(invite["email"]):
        tokens_store.consume(invite["id"])
        return _reject(
            "An account already exists for this address. Sign in, or use "
            "“Forgot password?” if you can't get in.",
            status_code=409,
        )

    user_id = users_store.create_user(
        email=invite["email"],
        name=name.strip() or None,
        password_hash=hash_password(password),
        role=invite["role"] or DEFAULT_ROLE,
    )
    tokens_store.consume(invite["id"])

    # Sign them straight in — bouncing to a login form right after they chose a
    # password is the step where people give up.
    request.session["user_id"] = user_id
    users_store.touch_last_login(user_id)
    return RedirectResponse("/staging", status_code=303)


# --- password reset ---------------------------------------------------------

# One wording for every outcome, so the page can't be used to test which
# addresses have accounts.
_RESET_SENT_MESSAGE = (
    "If that address has an account, a reset link is on its way. "
    "Check your spam folder if it doesn't arrive in a few minutes."
)


@router.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request):
    return templates.TemplateResponse(
        "forgot.html", {"request": request, "error": None, "sent": False}
    )


@router.post("/forgot")
def forgot_submit(
    request: Request,
    email: str = Form(...),
    __: None = Depends(require_same_origin),
):
    key = ratelimit.client_key(client_ip(request), email)
    wait = reset_throttle.retry_after(key)
    if wait:
        minutes = max(1, round(wait / 60))
        return templates.TemplateResponse(
            "forgot.html",
            {
                "request": request,
                "sent": False,
                "error": f"Too many reset requests. Try again in {minutes} minute"
                f"{'s' if minutes != 1 else ''}.",
            },
            status_code=429,
            headers={"Retry-After": str(wait)},
        )
    reset_throttle.record_failure(key)

    user = users_store.get_user_by_email(email)
    if user and user["is_active"]:
        # Retire older links so a stale email can't be replayed later.
        tokens_store.invalidate_outstanding(user["email"], tokens_store.RESET)
        token = tokens_store.create(
            purpose=tokens_store.RESET, email=user["email"], user_id=user["id"]
        )
        hours = max(1, int(tokens_store.RESET_TTL.total_seconds() // 3600))
        mail.send_password_reset(user["email"], build_link(request, f"/reset/{token}"), hours)

    # Same response either way, whether or not the account exists.
    return templates.TemplateResponse(
        "forgot.html", {"request": request, "error": None, "sent": True}
    )


@router.get("/reset/{token}", response_class=HTMLResponse)
def reset_form(request: Request, token: str):
    entry = tokens_store.get_valid(token, tokens_store.RESET)
    return templates.TemplateResponse(
        "reset.html",
        {"request": request, "token": token, "valid": entry is not None, "error": None},
        status_code=200 if entry else 404,
    )


@router.post("/reset/{token}")
def reset_submit(
    request: Request,
    token: str,
    password: str = Form(...),
    confirm: str = Form(...),
    __: None = Depends(require_same_origin),
):
    entry = tokens_store.get_valid(token, tokens_store.RESET)
    if not entry:
        return templates.TemplateResponse(
            "reset.html",
            {"request": request, "token": token, "valid": False, "error": None},
            status_code=404,
        )

    problem = password_problem(password, confirm)
    if problem:
        return templates.TemplateResponse(
            "reset.html",
            {"request": request, "token": token, "valid": True, "error": problem},
            status_code=400,
        )

    user_id = entry["user_id"]
    users_store.set_password(user_id, hash_password(password))
    tokens_store.consume(entry["id"])
    # Whoever just proved control of the mailbox gets a clean slate on the
    # throttle, so a locked-out coach isn't still locked out after resetting.
    throttle.record_success(ratelimit.client_key(client_ip(request), entry["email"]))

    request.session["user_id"] = user_id
    users_store.touch_last_login(user_id)
    return RedirectResponse("/staging", status_code=303)


@router.post("/logout")
def logout(request: Request, __: None = Depends(require_same_origin)):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# --- account: change your own password ----------------------------------------


def _account_page(
    request: Request,
    user: dict,
    error: str | None = None,
    saved: bool = False,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        "account.html",
        {
            "request": request,
            "account": user,
            "role_help": ROLE_HELP,
            "roles": ROLES,
            "error": error,
            "saved": saved,
        },
        status_code=status_code,
    )


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, saved: str = "", user: dict = Depends(require_member)):
    return _account_page(request, user, saved=saved == "1")


@router.post("/account/password", response_class=HTMLResponse)
def account_password(
    request: Request,
    current: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    user: dict = Depends(require_member),
    __: None = Depends(require_same_origin),
):
    full = users_store.get_user_by_id(user["id"])
    if not full or not verify_password(current, full["password_hash"]):
        return _account_page(request, user, error="Current password is wrong.", status_code=400)
    problem = password_problem(password, confirm)
    if problem:
        return _account_page(request, user, error=problem, status_code=400)
    if verify_password(password, full["password_hash"]):
        return _account_page(request, user, error="That is already your password.", status_code=400)
    users_store.set_password(user["id"], hash_password(password))
    return RedirectResponse("/account?saved=1", status_code=303)
