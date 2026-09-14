"""Transactional email via Resend.

Only two messages exist: an invite link and a password-reset link. Both are
short, both are plain enough to read as text in any client.

With no API key configured, send() logs the message and reports success instead
of posting anywhere. That keeps local dev and CI working without credentials,
and means a missing key degrades to "the admin copies the link out of the logs"
rather than a 500 in the middle of inviting someone. The admin UI shows the link
on screen either way, so a silent email failure never strands an invite.

API shape per https://resend.com/docs/api-reference/emails/send-email —
POST https://api.resend.com/emails with a Bearer key and {from, to, subject,
html, text}, returning {"id": ...}.
"""

from __future__ import annotations

import logging

import httpx

from piclstats.config import settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 10.0


def is_configured() -> bool:
    return bool(settings.resend_api_key and settings.email_from)


def send(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Send one message. Returns False if delivery failed.

    Never raises: an email problem must not take down the request that
    triggered it, because the caller always has the link to fall back on.
    """
    if not is_configured():
        logger.warning(
            "Email not configured (PICLSTATS_RESEND_API_KEY / PICLSTATS_EMAIL_FROM); "
            "would have sent to %s — %s\n%s",
            to,
            subject,
            text,
        )
        return True

    payload: dict = {
        "from": settings.email_from,
        "to": to,
        "subject": subject,
        "text": text,
    }
    if html:
        payload["html"] = html

    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.exception("Email to %s failed to send", to)
        return False

    if response.status_code >= 400:
        # Body may name the problem (unverified domain, bad key) — worth logging,
        # and it contains no secret beyond what we sent.
        logger.error("Resend rejected mail to %s: %s %s", to, response.status_code, response.text)
        return False

    logger.info("Sent %r to %s", subject, to)
    return True


# ── Messages ─────────────────────────────────────────────────────────


def send_invite(to: str, link: str, inviter_name: str | None, days_valid: int) -> bool:
    who = f"{inviter_name} has" if inviter_name else "You've been"
    text = (
        f"{who} invited you to PICL Stats.\n\n"
        f"PICL Stats holds race results, staging speed-ratings, and category "
        f"predictions for the PA Interscholastic Cycling League.\n\n"
        f"Set your password and sign in:\n{link}\n\n"
        f"This link works once and expires in {days_valid} days.\n"
        f"If you weren't expecting this, ignore it — no account is created "
        f"until you use the link."
    )
    return send(to, "Your PICL Stats invitation", text, _html(text, link, "Set your password"))


def send_password_reset(to: str, link: str, hours_valid: int) -> bool:
    text = (
        "Someone asked to reset the password for this PICL Stats account.\n\n"
        f"Set a new password:\n{link}\n\n"
        f"This link works once and expires in {hours_valid} hour"
        f"{'s' if hours_valid != 1 else ''}.\n"
        "If this wasn't you, ignore it — your current password still works."
    )
    return send(to, "Reset your PICL Stats password", text, _html(text, link, "Set a new password"))


def _html(text: str, link: str, cta: str) -> str:
    # Deliberately plain: inline styles only, no images, no tracking. Renders
    # the same everywhere and won't trip spam filters on a new sending domain.
    from html import escape

    body = "".join(
        f"<p style='margin:0 0 12px'>{escape(line)}</p>"
        for line in text.split("\n\n")
        if link not in line
    )
    return (
        "<div style='font-family:system-ui,-apple-system,sans-serif;font-size:15px;"
        "line-height:1.5;color:#1f2937;max-width:480px'>"
        f"{body}"
        f"<p style='margin:20px 0'><a href='{escape(link, quote=True)}' "
        "style='background:#1d4ed8;color:#fff;padding:10px 18px;border-radius:6px;"
        f"text-decoration:none;display:inline-block'>{escape(cta)}</a></p>"
        "<p style='margin:0;font-size:13px;color:#6b7280'>Or paste this into your browser:<br>"
        f"<span style='word-break:break-all'>{escape(link)}</span></p>"
        "</div>"
    )
