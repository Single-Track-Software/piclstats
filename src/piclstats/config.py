import os
import re

from pydantic import Field
from pydantic_settings import BaseSettings


def _default_database_url() -> str:
    # Fall back to Fly's DATABASE_URL (translated to the psycopg driver) when
    # PICLSTATS_DATABASE_URL isn't set. Lets `release_command` / one-off jobs
    # that don't run start.sh still reach the DB. PICLSTATS_DATABASE_URL, if
    # set, still wins (pydantic checks the env var before this default).
    raw = os.environ.get("DATABASE_URL")
    if raw:
        return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", raw)
    return "postgresql+psycopg://localhost:5432/piclstats"


class Settings(BaseSettings):
    database_url: str = Field(default_factory=_default_database_url)
    scrape_delay_seconds: float = 1.5
    request_timeout_seconds: float = 30.0
    log_level: str = "INFO"
    admin_password: str = ""
    # Bootstrap admin login and the key that signs session cookies. Set both
    # admin_email and admin_password to seed the first admin on startup.
    admin_email: str = ""
    session_secret: str = ""
    # Mark the session cookie Secure (https-only). Keep True in production (Fly
    # serves https); set False for local http dev or the cookie won't be sent.
    session_https_only: bool = True

    # Transactional email (Resend) for invite and password-reset links. With no
    # API key the app logs the link instead of sending it, so local dev and CI
    # work unchanged — see web/mail.py.
    resend_api_key: str = ""
    # Must be on a domain verified in Resend, e.g. "PICL Stats <noreply@example.org>".
    email_from: str = ""
    # Absolute base for links in emails, e.g. "https://piclstats.fly.dev". Unset,
    # links are built from the incoming request's own base URL.
    public_base_url: str = ""

    # extra="ignore" so unrelated env vars (Fly's DATABASE_URL, shell exports,
    # stray .env lines) don't crash startup — only PICLSTATS_-prefixed keys bind.
    model_config = {"env_prefix": "PICLSTATS_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
