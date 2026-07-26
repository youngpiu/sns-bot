from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from config import BASE_DIR

try:
    from tweety import TwitterAsync
    import tweety.transaction as tweety_tx
except ImportError:
    TwitterAsync = None
    tweety_tx = None


logger = logging.getLogger(__name__)


TWITTER_SESSION_FILE = BASE_DIR / "sessions" / "twitter_session"


class TwitterLoginError(RuntimeError):
    pass


@dataclass(frozen=True)
class TwitterTweet:
    id: str
    text: str
    url: str
    created_on: datetime | None
    media_urls: list[str]


# ---------------------------------------------------------------------------
# Monkey-patch TransactionGenerator so it doesn't crash on HTML parsing
# ---------------------------------------------------------------------------
if tweety_tx is not None:

    _orig_tx_init = tweety_tx.TransactionGenerator.__init__

    def _patched_tx_init(self, home_page_html):
        try:
            _orig_tx_init(self, home_page_html)
        except Exception:
            import base64
            import math
            import random

            self.DEFAULT_ROW_INDEX = 0
            self.DEFAULT_KEY_BYTES_INDICES = [0, 1, 2]
            self.key = "AAAA"
            self.key_bytes = list(base64.b64decode(b"AAAA"))
            self.animation_key = "0" * 64

    tweety_tx.TransactionGenerator.__init__ = _patched_tx_init


class TwitterClient:
    def __init__(
        self,
        auth_token: str | None = None,
    ) -> None:
        if TwitterAsync is None:
            raise TwitterLoginError("tweety chưa được cài đặt (pip install tweety-ns)")
        self.auth_token = auth_token
        self.app = TwitterAsync(str(TWITTER_SESSION_FILE))
        self._authenticated = False

    async def authenticate(self) -> None:
        if self._authenticated:
            return

        try:
            await self.app.connect()
            if self.app.user is not None:
                self._authenticated = True
                logger.info("Đã tải Twitter session từ %s.tw_session", TWITTER_SESSION_FILE)
                return
        except Exception:
            pass

        logger.info("Không tìm thấy Twitter session, thử auth_token...")

        if self.auth_token:
            try:
                await self.app.load_auth_token(self.auth_token)
                self._authenticated = True
                logger.info("Đã đăng nhập Twitter bằng auth_token")
                return
            except Exception as exc:
                raise TwitterLoginError(f"Twitter auth_token thất bại: {exc}") from exc

        raise TwitterLoginError(f"Twitter chưa được xác thực")

    async def recent_tweets(self, target_username: str) -> list[TwitterTweet]:
        await self.authenticate()

        user_id = await self.app.get_user_id(target_username)
        await self.app.enable_user_notification(user_id)

        notifications = await self.app.get_tweet_notifications(pages=1)
        tweets = [t for t in notifications if str(t.author.id) == str(user_id)]
        tweets = [t for t in tweets if not getattr(t, "is_retweet", False)]

        result: list[TwitterTweet] = []
        for t in tweets[:20]:
            media_urls: list[str] = []
            media_list = getattr(t, "media", [])
            if media_list:
                for m in media_list:
                    url = getattr(m, "media_url_https", None)
                    if url:
                        media_urls.append(url)
            created = getattr(t, "created_on", None) or getattr(t, "date", None)
            text = getattr(t, "text", "") or ""
            urls = getattr(t, "urls", [])
            for u in urls:
                url_str = getattr(u, "url", None)
                expanded_str = getattr(u, "expanded_url", None)
                if url_str and expanded_str:
                    text = text.replace(url_str, expanded_str)
                    
            result.append(TwitterTweet(
                id=str(t.id),
                text=text,
                url=getattr(t, "url", ""),
                created_on=created,
                media_urls=media_urls,
            ))

        result.sort(key=lambda tw: tw.created_on or datetime.min, reverse=True)
        logger.info("Tìm thấy %s tweet Twitter cho @%s", len(result), target_username)
        return result
