from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    ClientThrottledError,
    PleaseWaitFewMinutes,
    ProxyAddressIsBlocked,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstagramMedia:
    pk: str
    code: str | None
    taken_at: datetime | None
    caption_text: str
    media_type: int | None
    product_type: str | None
    media_urls: list[str]

    @property
    def url(self) -> str:
        if self.code:
            if self.product_type == "clips":
                return f"https://www.instagram.com/reel/{self.code}/"
            return f"https://www.instagram.com/p/{self.code}/"
        return "https://www.instagram.com/"

    @property
    def media_type_label(self) -> str:
        if self.product_type == "clips":
            return "reel"
        labels = {
            1: "photo",
            2: "video",
            8: "album",
        }
        return labels.get(self.media_type, "post")


class InstagramLoginError(RuntimeError):
    pass


SUPPORTED_MEDIA_TYPES = {1, 2, 8}
SUPPORTED_PRODUCT_TYPES = {None, "", "feed", "clips"}


class InstagramClient:
    def __init__(
        self,
        target_username: str,
        session_file: Path,
        username: str = "",
        password: str = "",
        proxy: str | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.target_username = target_username
        self.session_file = session_file
        self.client = Client()
        self.client.delay_range = [1, 3]
        if proxy:
            self.client.set_proxy(proxy)
        self._target_user_id: int | str | None = None
        self._logged_in = False

    def login(self) -> None:
        if self._logged_in:
            return

        if self.session_file.exists():
            try:
                self.client.load_settings(str(self.session_file))
                logger.info("Đã tải Instagram session từ file %s", self.session_file)
                user_id = self.client.user_id
                if user_id:
                    logger.info("Session file còn hiệu lực (user_id=%s), bỏ qua đăng nhập lại", user_id)
                    self._logged_in = True
                    return
            except Exception:
                logger.warning("Không tải được Instagram session từ file")

        try:
            if not self.username or not self.password:
                raise InstagramLoginError("Missing IG_USERNAME or IG_PASSWORD in .env")
            logger.info("Đang đăng nhập Instagram bằng username/password")
            self.client.login(self.username, self.password)
        except TypeError as exc:
            if "NoneType" in str(exc):
                raise InstagramLoginError(
                    "Instagram did not return password-encryption public keys during login. "
                    "This usually means Instagram is throttling/challenging this account or IP. "
                    "Open Instagram manually on a trusted device, confirm any login/security prompts, "
                    "wait before retrying, and consider using a stable proxy via IG_PROXY."
                ) from exc
            raise
        except (PleaseWaitFewMinutes, ClientThrottledError) as exc:
            raise InstagramLoginError(
                "Instagram rate-limited this login attempt. Stop retrying for a while, then retry "
                "with the same session/device/IP."
            ) from exc
        except ChallengeRequired as exc:
            raise InstagramLoginError(
                "Instagram requires manual verification for this account. Complete the challenge "
                "in the official Instagram app or website, then restart the bot."
            ) from exc
        except ProxyAddressIsBlocked as exc:
            raise InstagramLoginError(
                "Instagram blocked the current proxy/IP. Use a cleaner stable proxy or the account's "
                "normal network, then restart the bot."
            ) from exc


        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.client.dump_settings(str(self.session_file))
        self._logged_in = True
        logger.info("Đăng nhập Instagram hoàn tất")

    @staticmethod
    def _is_supported_profile_media(media: object) -> bool:
        media_type = getattr(media, "media_type", None)
        product_type = getattr(media, "product_type", None)
        return media_type in SUPPORTED_MEDIA_TYPES and product_type in SUPPORTED_PRODUCT_TYPES

    @staticmethod
    def _to_instagram_media(media: object) -> InstagramMedia:
        media_urls: list[str] = []
        media_type = getattr(media, "media_type", None)
        product_type = getattr(media, "product_type", None)

        raw_resources = getattr(media, "resources", []) or []
        for resource in raw_resources:
            resource_video_url = getattr(resource, "video_url", None)
            resource_thumbnail_url = getattr(resource, "thumbnail_url", None)

            if product_type == "clips" or media_type == 2:
                if resource_video_url:
                    media_urls.append(str(resource_video_url))
            else:
                if resource_thumbnail_url:
                    media_urls.append(str(resource_thumbnail_url))

        video_url = getattr(media, "video_url", None)
        thumbnail_url = getattr(media, "thumbnail_url", None)
        if product_type == "clips" or media_type == 2:
            if video_url:
                media_urls.insert(0, str(video_url))
        else:
            if thumbnail_url:
                media_urls.insert(0, str(thumbnail_url))

        deduped_media_urls: list[str] = []
        seen_urls: set[str] = set()
        for url in media_urls:
            if url and url not in seen_urls:
                deduped_media_urls.append(url)
                seen_urls.add(url)

        return InstagramMedia(
            pk=str(getattr(media, "pk")),
            code=getattr(media, "code", None),
            taken_at=getattr(media, "taken_at", None),
            caption_text=getattr(media, "caption_text", "") or "",
            media_type=media_type,
            product_type=product_type,
            media_urls=deduped_media_urls,
        )

    def recent_medias(self) -> list[InstagramMedia]:
        self.login()

        if self._target_user_id is None:
            self._target_user_id = self.client.user_id_from_username(self.target_username)

        medias = self.client.user_medias(self._target_user_id, amount=6)
        supported_medias = [media for media in medias if self._is_supported_profile_media(media)]
        if not supported_medias:
            return []

        result = [self._to_instagram_media(media) for media in supported_medias]
        result.sort(key=lambda media: media.taken_at or datetime.min, reverse=True)
        logger.info(
            "Tìm thấy %s media Instagram gần đây cho @%s",
            len(result),
            self.target_username,
        )
        return result
