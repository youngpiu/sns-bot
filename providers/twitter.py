from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from config import BASE_DIR

try:
    from tweety import TwitterAsync
except ImportError:
    TwitterAsync = None


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


class TwitterClient:
    def __init__(
        self,
        target_username: str,
        auth_token: str | None = None,
    ) -> None:
        if TwitterAsync is None:
            raise TwitterLoginError("tweety chưa được cài đặt (pip install tweety-ns)")
        self.target_username = target_username
        self.auth_token = auth_token
        self.app = TwitterAsync(str(TWITTER_SESSION_FILE))
        self._authenticated = False

    async def authenticate(self) -> None:
        if self._authenticated:
            return

        try:
            await self.app.connect()
            self._authenticated = True
            logger.info("Đã tải Twitter session từ %s.tw_session", TWITTER_SESSION_FILE)
            return
        except Exception:
            logger.info("Không tìm thấy Twitter session, thử auth_token...")

        if self.auth_token:
            try:
                await self.app.load_auth_token(self.auth_token)
                self._authenticated = True
                logger.info("Đã đăng nhập Twitter bằng auth_token")
                return
            except Exception as exc:
                raise TwitterLoginError(f"Twitter auth_token thất bại: {exc}") from exc

        raise TwitterLoginError(f"Twitter chưa được xác thực. Chạy: python -m login")

    async def recent_tweets(self) -> list[TwitterTweet]:
        await self.authenticate()

        tweets_data = await self.app.get_tweets(self.target_username, replies=False)

        tweets: list[TwitterTweet] = []
        for item in tweets_data:
            if hasattr(item, "tweets"):
                for t in item.tweets:
                    tweets.append(t)
            elif hasattr(item, "id"):
                tweets.append(item)

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
            result.append(TwitterTweet(
                id=str(t.id),
                text=getattr(t, "text", "") or "",
                url=getattr(t, "url", ""),
                created_on=created,
                media_urls=media_urls,
            ))

        result.sort(key=lambda tw: tw.created_on or datetime.min, reverse=True)
        logger.info("Tìm thấy %s tweet Twitter cho @%s", len(result), self.target_username)
        return result
