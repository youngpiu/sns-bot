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
    instagram_session_file: Path = BASE_DIR / "sessions" / "instagram_session.json"
    fans_webhook_url: str | None = None
    fans_role_id: str | None = None
    fans_target: str | None = None
    fans_poll_interval: int = 300
    ig_thread_id: str | None = None
    fans_thread_id: str | None = None
    yt_webhook_url: str | None = None
    yt_role_id: str | None = None
    yt_targets: tuple[str, ...] = ()
    yt_thread_id: str | None = None
    ngrok_token: str | None = None
    twitter_webhook_url: str | None = None
    twitter_role_id: str | None = None
    twitter_target: str | None = None
    twitter_poll_interval: int = 300
    twitter_thread_id: str | None = None
    twitter_auth_token: str | None = None


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

    if poll_interval < 10:
        raise ValueError("IG_POLL_INTERVAL must be at least 10 seconds")

    fans_webhook_url = os.getenv("FANS_WEBHOOK", "").strip() or None
    fans_role_id = os.getenv("FANS_ROLE", "").strip() or None
    fans_target = os.getenv("FANS_TARGET", "").strip() or None

    fans_poll_interval_raw = os.getenv("FANS_POLL_INTERVAL", "300").strip()
    try:
        fans_poll_interval = int(fans_poll_interval_raw)
    except ValueError as exc:
        raise ValueError("FANS_POLL_INTERVAL must be an integer number of seconds") from exc

    if fans_poll_interval < 10:
        raise ValueError("FANS_POLL_INTERVAL must be at least 10 seconds")

    ig_thread_id = os.getenv("IG_THREAD_ID", "").strip() or None
    fans_thread_id = os.getenv("FANS_THREAD_ID", "").strip() or None

    yt_webhook_url = os.getenv("YT_WEBHOOK", "").strip() or None
    yt_role_id = os.getenv("YT_ROLE", "").strip() or None

    yt_targets_raw = os.getenv("YT_TARGETS", "").strip()
    yt_targets: tuple[str, ...] = ()
    if yt_targets_raw:
        yt_targets = tuple(t.strip() for t in yt_targets_raw.split(",") if t.strip())

    yt_thread_id = os.getenv("YT_THREAD_ID", "").strip() or None

    ngrok_token = os.getenv("NGROK_TOKEN", "").strip() or None

    twitter_webhook_url = os.getenv("TWITTER_WEBHOOK", "").strip() or None
    twitter_role_id = os.getenv("TWITTER_ROLE", "").strip() or None
    twitter_target = os.getenv("TWITTER_TARGET", "").strip() or None

    twitter_poll_interval_raw = os.getenv("TWITTER_POLL_INTERVAL", "300").strip()
    try:
        twitter_poll_interval = int(twitter_poll_interval_raw)
    except ValueError as exc:
        raise ValueError("TWITTER_POLL_INTERVAL must be an integer number of seconds") from exc

    if twitter_poll_interval < 10:
        raise ValueError("TWITTER_POLL_INTERVAL must be at least 10 seconds")

    twitter_thread_id = os.getenv("TWITTER_THREAD_ID", "").strip() or None
    twitter_auth_token = os.getenv("TWITTER_AUTH_TOKEN", "").strip() or None

    return Settings(
        webhook_url=webhook_url,
        role_id=role_id,
        ig_target=ig_target,
        ig_proxy=os.getenv("IG_PROXY", "").strip() or None,
        ig_sessionid=ig_sessionid,
        poll_interval=poll_interval,
        fans_webhook_url=fans_webhook_url,
        fans_role_id=fans_role_id,
        fans_target=fans_target,
        fans_poll_interval=fans_poll_interval,
        ig_thread_id=ig_thread_id,
        fans_thread_id=fans_thread_id,
        yt_webhook_url=yt_webhook_url,
        yt_role_id=yt_role_id,
        yt_targets=yt_targets,
        yt_thread_id=yt_thread_id,
        ngrok_token=ngrok_token,
        twitter_webhook_url=twitter_webhook_url,
        twitter_role_id=twitter_role_id,
        twitter_target=twitter_target,
        twitter_poll_interval=twitter_poll_interval,
        twitter_thread_id=twitter_thread_id,
        twitter_auth_token=twitter_auth_token,
    )
