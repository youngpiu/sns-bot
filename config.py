from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    webhook_url: str
    role_id: str
    ig_target: str
    ig_proxy: str | None = None
    ig_sessionid: str | None = None
    poll_interval: int = 600
    instagram_session_file: Path = BASE_DIR / "instagram_session.json"
    fans_webhook_url: str | None = None
    fans_role_id: str | None = None
    fans_token: str | None = None
    fans_client_uuid: str | None = None
    fans_guid: str | None = None
    fans_target: str | None = None
    fans_poll_interval: int = 300


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env", override=True)

    missing: list[str] = []

    def required(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            missing.append(name)
        return value

    webhook_url = required("IG_WEBHOOK")
    role_id = required("IG_ROLE")
    ig_target = required("IG_TARGET")

    ig_sessionid = os.getenv("IG_SESSIONID", "").strip() or None

    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(f"Missing required environment variables: {missing_list}")

    if not webhook_url.startswith("https://discord.com/api/webhooks/"):
        raise ValueError("IG_WEBHOOK must be a Discord webhook URL")

    poll_interval_raw = os.getenv("IG_POLL_INTERVAL", "600").strip()
    try:
        poll_interval = int(poll_interval_raw)
    except ValueError as exc:
        raise ValueError("IG_POLL_INTERVAL must be an integer number of seconds") from exc

    if poll_interval < 60:
        raise ValueError("IG_POLL_INTERVAL must be at least 60 seconds")

    fans_webhook_url = os.getenv("FANS_WEBHOOK", "").strip() or None
    fans_role_id = os.getenv("FANS_ROLE", "").strip() or None
    fans_token = os.getenv("FANS_TOKEN", "").strip() or None
    fans_client_uuid = os.getenv("FANS_CLIENT_UUID", "").strip() or None
    fans_guid = os.getenv("FANS_GUID", "").strip() or None
    fans_target = os.getenv("FANS_TARGET", "").strip() or None

    fans_poll_interval_raw = os.getenv("FANS_POLL_INTERVAL", "300").strip()
    try:
        fans_poll_interval = int(fans_poll_interval_raw)
    except ValueError as exc:
        raise ValueError("FANS_POLL_INTERVAL must be an integer number of seconds") from exc

    if fans_poll_interval < 30:
        raise ValueError("FANS_POLL_INTERVAL must be at least 30 seconds")

    return Settings(
        webhook_url=webhook_url,
        role_id=role_id,
        ig_target=ig_target,
        ig_proxy=os.getenv("IG_PROXY", "").strip() or None,
        ig_sessionid=ig_sessionid,
        poll_interval=poll_interval,
        fans_webhook_url=fans_webhook_url,
        fans_role_id=fans_role_id,
        fans_token=fans_token,
        fans_client_uuid=fans_client_uuid,
        fans_guid=fans_guid,
        fans_target=fans_target,
        fans_poll_interval=fans_poll_interval,
    )
