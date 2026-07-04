from __future__ import annotations

import base64
import json
import logging
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)


API_URL = "https://api.app.fans/graphql"
REFRESH_URL = "https://api.app.fans/account/auth/refresh"

NOTIFICATION_CATEGORIES: list[str] = [
    "POST_CREATED_BY_ARTIST",
]

GROUPS: dict[str, dict[str, str]] = {
    "nmixx": {"id": "14", "name": "NMIXX"},
    "twice": {"id": "9", "name": "TWICE"},
    "itzy": {"id": "11", "name": "ITZY"},
    "straykids": {"id": "10", "name": "Stray Kids"},
    "day6": {"id": "8", "name": "DAY6"},
    "twopm": {"id": "3", "name": "2PM"},
    "jypark": {"id": "2", "name": "J.Y. Park"},
    "niziu": {"id": "12", "name": "NiziU"},
    "xdinaryheroes": {"id": "13", "name": "Xdinary Heroes"},
    "kickflip": {"id": "67", "name": "KickFlip"},
    "nexz": {"id": "34", "name": "NEXZ"},
}


class FansAuthError(RuntimeError):
    pass


class FansAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class FansNotification:
    id: str
    category: str
    message: str
    link_url: str
    group_name: str
    group_code: str
    created_at: datetime
    thumbnail_url: str | None = None

    @property
    def url(self) -> str:
        if self.link_url and not self.link_url.startswith("http"):
            return f"https://app.fans/{self.link_url}"
        return self.link_url or "https://app.fans/"


class FansAuth:
    def __init__(self, token: str, client_uuid: str, guid: str) -> None:
        self._token = token
        self._client_uuid = client_uuid
        self._guid = guid
        self._decoded: dict[str, Any] = {}
        self._decode_token()

    def _decode_token(self) -> None:
        try:
            parts = self._token.split(".")
            if len(parts) != 3:
                raise FansAuthError("Invalid JWT: expected 3 parts")
            payload = parts[1]
            padded = payload + "=" * (4 - len(payload) % 4)
            self._decoded = json.loads(base64.urlsafe_b64decode(padded))
        except Exception as exc:
            raise FansAuthError(f"Failed to decode JWT: {exc}") from exc

    def is_expired(self) -> bool:
        exp = self._decoded.get("exp", 0)
        return time.time() > exp

    def should_refresh(self) -> bool:
        exp = self._decoded.get("exp", 0)
        return exp - time.time() < 300

    def get_token(self) -> str:
        return self._token

    def refresh(self) -> str:
        import http.client
        import io
        import random
        import string

        boundary = "----" + "".join(random.choices(string.ascii_letters + string.digits, k=16))

        def _encode_field(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()

        body = _encode_field("accessToken", self._token) + _encode_field("clientUuid", self._client_uuid) + f"--{boundary}--\r\n".encode()

        parsed = urllib.parse.urlparse(REFRESH_URL)
        conn = http.client.HTTPSConnection(parsed.hostname, timeout=30)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "j-context": "web",
            "j-language": "en",
            "j-guid": self._guid,
        }
        try:
            conn.request("POST", parsed.path or "/", body=body, headers=headers)
            resp = conn.getresponse()
            data = json.loads(resp.read())
            conn.close()

            new_token = (data or {}).get("accessToken")
            if not new_token:
                raise FansAuthError("Token refresh returned no accessToken")
            self._token = new_token
            self._decode_token()
            logger.info("Fans JWT refreshed successfully")
            return self._token
        except FansAuthError:
            raise
        except Exception as exc:
            raise FansAuthError(f"Token refresh failed: {exc}") from exc

    def ensure_token(self) -> str:
        if self.is_expired():
            logger.info("Fans JWT expired, refreshing...")
            return self.refresh()
        if self.should_refresh():
            try:
                return self.refresh()
            except FansAuthError:
                logger.warning("Fans JWT soft refresh failed, using current token")
                return self._token
        return self._token


class FansClient:
    def __init__(
        self,
        token: str,
        client_uuid: str,
        guid: str,
        target_group: str,
    ) -> None:
        self.auth = FansAuth(token, client_uuid, guid)
        self._target_group = self._resolve_group(target_group)
        self._seen_ids: set[str] = set()

    @staticmethod
    def _resolve_group(code_or_name: str) -> dict[str, str]:
        key = code_or_name.strip().lower()
        if key in GROUPS:
            return GROUPS[key]
        for info in GROUPS.values():
            if info["name"].lower() == key:
                return info
        raise FansAuthError(f"Unknown Fans group: {code_or_name}. Available: {', '.join(GROUPS)}")

    @staticmethod
    def list_groups() -> dict[str, dict[str, str]]:
        return dict(GROUPS)

    def _build_headers(self) -> dict[str, str]:
        token = self.auth.ensure_token()
        return {
            "Authorization": f"Bearer {token}",
            "j-guid": self.auth._guid,
            "j-operation-type": "query",
            "j-context": "web",
            "j-client-version": "2.2627.1",
            "j-language": "en",
            "j-marketplace": "KR",
            "j-currency": "USD",
            "j-timezone": "Asia/Saigon",
            "Content-Type": "application/json",
            "Origin": "https://app.fans",
            "Referer": "https://app.fans/",
        }

    def _request(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        import http.client

        payload = json.dumps({
            "query": query,
            "variables": variables,
        })

        parsed = urllib.parse.urlparse(API_URL)

        for attempt in range(2):
            headers = self._build_headers()
            try:
                conn = http.client.HTTPSConnection(parsed.hostname, timeout=30)
                conn.request("POST", parsed.path or "/", body=payload, headers=headers)
                resp = conn.getresponse()
                body = resp.read()
                conn.close()

                if resp.status == 401 and attempt == 0:
                    logger.info("Fans API returned 401, refreshing token and retrying...")
                    self.auth.refresh()
                    continue

                if resp.status >= 400:
                    raise FansAPIError(f"Fans API returned {resp.status}: {body.decode(errors='replace')[:500]}")

                result: dict[str, Any] = json.loads(body)
                return result

            except FansAPIError:
                raise
            except Exception as exc:
                if attempt == 0 and isinstance(exc, (OSError, ConnectionError)):
                    continue
                raise FansAPIError(f"Fans API request failed: {exc}") from exc

        raise FansAPIError("Fans API request failed after retry")

    def get_notifications(
        self,
        group_ids: list[str] | None = None,
        categories: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        query = """
        query NotificationsForNotificationList($filter: NotificationFilterInput, $sort: [NotificationSortInput], $page: PageInput) {
          notifications(filter: $filter, sort: $sort, page: $page) {
            objects {
              id
              category
              createdAt
              updatedAt
              message
              linkUrl
              classification
              group { id name code }
              globalShop { id name }
              targetMedia {
                key
                ... on Image { thumbnailUrl: thumbnailUrl(mode: THUMBNAIL, width: 300) }
                ... on Video { thumbnailUrl: thumbnailUrl(mode: THUMBNAIL, width: 300) }
              }
              actor { id }
            }
          }
        }
        """

        variables: dict[str, Any] = {
            "sort": [{"type": "UPDATED_AT", "direction": "DESC"}],
            "page": {"first": 100},
            "filter": {
                "classification_Overlap": ["COMMUNITY"],
            },
        }

        if group_ids:
            variables["filter"]["group_Overlap"] = group_ids
        if categories:
            variables["filter"]["category_Overlap"] = categories

        result = self._request(query, variables)
        objects: list[dict[str, Any]] = []
        try:
            objects = result["data"]["notifications"]["objects"] or []
        except (KeyError, TypeError):
            pass
        return objects

    def get_post_detail(self, slug: str) -> dict[str, Any] | None:
        query = """
        query CommunityPostDetail($filter: PostFilterInput!) {
          post(filter: $filter) {
            id
            slug
            body
            likeCount
            commentCount
            firstActivatedAt
            member { nickname artist { code } }
            bodyBlocks {
              category
              text { content }
              sticker { imageUrl }
            }
            attachments {
              key
              url
              ... on Image {
                url
                thumbnailUrl: thumbnailUrl(mode: THUMBNAIL, width: 630)
              }
              ... on Video {
                url
                thumbnailUrl: thumbnailUrl(mode: THUMBNAIL, width: 630)
              }
            }
          }
        }
        """

        result = self._request(query, {"filter": {"slug_Exact": slug}})
        try:
            return result["data"]["post"]
        except (KeyError, TypeError):
            return None

    def recent_notifications(self) -> list[FansNotification]:
        self.auth.ensure_token()

        raw = self.get_notifications(
            group_ids=[self._target_group["id"]],
            categories=NOTIFICATION_CATEGORIES,
        )
        if not raw:
            return []

        result: list[FansNotification] = []
        for item in raw:
            group = item.get("group") or {}
            target_media = item.get("targetMedia") or {}
            created = item.get("createdAt") or ""
            created_dt = datetime.now(timezone.utc)
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

            notification = FansNotification(
                id=str(item.get("id", "")),
                category=item.get("category", ""),
                message=item.get("message", "") or "(no message)",
                link_url=item.get("linkUrl", "") or "",
                group_name=group.get("name", ""),
                group_code=group.get("code", ""),
                created_at=created_dt,
                thumbnail_url=target_media.get("thumbnailUrl"),
            )
            result.append(notification)

        result = [n for n in result if n.category in NOTIFICATION_CATEGORIES]
        result.sort(key=lambda n: n.created_at)
        logger.info(
            "Resolved %s Fans notification(s) for %s",
            len(result),
            self._target_group["name"],
        )
        return result
