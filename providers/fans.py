from __future__ import annotations

import base64
import json
import logging
import time
import uuid
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from config import BASE_DIR


logger = logging.getLogger(__name__)


API_URL = "https://api.app.fans/graphql"
REFRESH_URL = "https://api.app.fans/account/auth/refresh"

# NOTIFICATION_CATEGORIES - Các loại thông báo từ API
# Community:
#   POST_CREATED_BY_ARTIST   - Artist tạo bài post mới
#   COMMENT_CREATED_BY_ARTIST - Artist comment vào post của fan
#   POST_LIKE_CREATED_BY_ARTIST - Artist like post của fan
# Shop:
#   NOTICE                   - Thông báo shop (shipping, thanh toán...)
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


GRAPHQL_QUERIES = {
    "SendEmailCode": """
        mutation SendEmailCode($email: String!) {
          sendEmailVerificationCode(email: $email) {
            ok errors { code title messages isExpected __typename }
            __typename
          }
        }
    """,
    "ConfirmEmail": """
        mutation ConfirmEmail($email: String!, $code: String!) {
          confirmEmailVerificationCode(email: $email, code: $code) {
            ok errors { code title messages isExpected __typename }
            emailToken userExists __typename
          }
        }
    """,
    "LoginEmailToken": """
        mutation LoginEmailToken($clientUuid: String, $emailToken: String) {
          login(clientUuid: $clientUuid, emailToken: $emailToken, grantType: JWT) {
            ok errors { code title messages isExpected __typename }
            token accessToken refreshToken __typename
          }
        }
    """,
}


def _graphql_request(query: str, variables: dict[str, Any], guid: str) -> dict[str, Any]:
    import http.client

    payload = json.dumps({"query": query, "variables": variables})
    parsed = urllib.parse.urlparse(API_URL)
    headers = {
        "j-guid": guid,
        "j-operation-type": "mutation",
        "j-context": "web",
        "j-language": "en",
        "Content-Type": "application/json",
        "Origin": "https://app.fans",
        "Referer": "https://app.fans/",
    }
    conn = http.client.HTTPSConnection(parsed.hostname, timeout=30)
    try:
        conn.request("POST", parsed.path or "/", body=payload, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        return json.loads(body)
    finally:
        conn.close()


def send_verification_code(email: str, guid: str) -> bool:
    result = _graphql_request(GRAPHQL_QUERIES["SendEmailCode"], {"email": email}, guid)
    return bool(result.get("data", {}).get("sendEmailVerificationCode", {}).get("ok"))


def confirm_login(email: str, code: str, client_uuid: str, guid: str) -> tuple[str, str]:
    result = _graphql_request(GRAPHQL_QUERIES["ConfirmEmail"], {"email": email, "code": code}, guid)
    confirm = result.get("data", {}).get("confirmEmailVerificationCode", {})
    if not confirm.get("ok"):
        errors = confirm.get("errors") or []
        raise FansAuthError(f"Email confirmation failed: {errors}")

    email_token = confirm.get("emailToken")
    if not email_token:
        raise FansAuthError("No emailToken in confirm response")

    result2 = _graphql_request(
        GRAPHQL_QUERIES["LoginEmailToken"],
        {"clientUuid": client_uuid, "emailToken": email_token},
        guid,
    )
    login_data = result2.get("data", {}).get("login", {})
    if not login_data.get("ok"):
        errors = login_data.get("errors") or []
        raise FansAuthError(f"Login failed: {errors}")

    access_token = login_data.get("accessToken")
    refresh_token = login_data.get("refreshToken")
    if not access_token or not refresh_token:
        raise FansAuthError("No access/refresh token in login response")

    return access_token, refresh_token


class FansAuth:
    def __init__(self, session: FansSessionStore) -> None:
        if not session.token:
            raise FansAuthError("No token in Fans session. Run: python -m providers.fans login")
        self._session = session
        self._token = session.token
        self._refresh_token = session.refresh_token
        self._guid = session.ensure_guid()
        self._decoded: dict[str, Any] = {}
        self._decode_token()
        ucu = self._decoded.get("ucu")
        self._client_uuid = session.ensure_client_uuid(ucu)

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

    def get_refresh_token(self) -> str | None:
        return self._refresh_token

    def set_tokens(self, access_token: str, refresh_token: str) -> None:
        self._token = access_token
        self._refresh_token = refresh_token
        self._decode_token()

    def refresh(self) -> str:
        if not self._refresh_token:
            raise FansAuthError("No refresh token available — login via email first")

        import http.client

        parsed = urllib.parse.urlparse(REFRESH_URL)
        conn = http.client.HTTPSConnection(parsed.hostname, timeout=30)
        headers = {
            "Cookie": f"refreshToken:web={self._refresh_token}",
            "j-context": "web",
            "j-language": "en",
            "j-guid": self._guid,
        }
        try:
            conn.request("POST", parsed.path or "/", headers=headers)
            resp = conn.getresponse()
            data = json.loads(resp.read())
            conn.close()

            new_token = (data or {}).get("accessToken")
            if not new_token:
                err = data.get("error", "unknown") if isinstance(data, dict) else "unknown"
                raise FansAuthError(f"Token refresh returned no accessToken (server: {err})")

            new_refresh = (data or {}).get("refreshToken", self._refresh_token)
            self._token = new_token
            self._refresh_token = new_refresh
            self._session.set_tokens(new_token, new_refresh)
            self._decode_token()
            logger.info("Fans JWT đã refresh thành công")
            return self._token
        except FansAuthError:
            raise
        except Exception as exc:
            raise FansAuthError(f"Token refresh failed: {exc}") from exc

    def ensure_token(self) -> str:
        try:
            if self.is_expired():
                logger.info("Fans JWT hết hạn, đang refresh...")
                return self.refresh()
            if self.should_refresh():
                return self.refresh()
        except FansAuthError:
            logger.warning("Fans JWT refresh thất bại, dùng token hiện tại")
        return self._token


class FansClient:
    def __init__(self, session: FansSessionStore, target_group: str = "") -> None:
        self.auth = FansAuth(session)
        self._session = session
        self._target_group = self._resolve_group(target_group)

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
                    logger.info("Fans API trả về 401, đang refresh token và thử lại...")
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
            "page": {"first": 3},
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
        result.sort(key=lambda n: n.created_at, reverse=True)
        logger.info(
            "Tìm thấy %s thông báo Fans cho %s",
            len(result),
            self._target_group["name"],
        )
        return result


FANS_SESSION_FILE = BASE_DIR / "sessions" / "fans_session.json"


class FansSessionStore:
    def __init__(self, path: Path = FANS_SESSION_FILE) -> None:
        self.path = path
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._client_uuid: str | None = None
        self._guid: str | None = None
        self._email: str | None = None

    def load(self) -> FansSessionStore:
        if self.path.exists() and self.path.stat().st_size > 0:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._token = str(data["token"]) if data.get("token") else None
                self._refresh_token = str(data["refresh_token"]) if data.get("refresh_token") else None
                self._client_uuid = str(data["client_uuid"]) if data.get("client_uuid") else None
                self._guid = str(data["guid"]) if data.get("guid") else None
                self._email = str(data["email"]) if data.get("email") else None
            except (OSError, json.JSONDecodeError):
                pass
        return self

    def save(self) -> None:
        payload: dict[str, str] = {}
        if self._token is not None:
            payload["token"] = self._token
        if self._refresh_token is not None:
            payload["refresh_token"] = self._refresh_token
        if self._client_uuid is not None:
            payload["client_uuid"] = self._client_uuid
        if self._guid is not None:
            payload["guid"] = self._guid
        if self._email is not None:
            payload["email"] = self._email
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
        ) as temp_file:
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)

        temp_path.replace(self.path)

    def ensure_guid(self) -> str:
        if not self._guid:
            self._guid = str(uuid.uuid4())
            self.save()
        return self._guid

    def ensure_client_uuid(self, fallback: str | None = None) -> str:
        if not self._client_uuid:
            self._client_uuid = fallback or f"web-{uuid.uuid4()}"
            self.save()
        return self._client_uuid

    def set_tokens(self, token: str, refresh_token: str) -> None:
        self._token = token
        self._refresh_token = refresh_token
        self.save()

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def refresh_token(self) -> str | None:
        return self._refresh_token

    @property
    def client_uuid(self) -> str | None:
        return self._client_uuid

    @property
    def guid(self) -> str | None:
        return self._guid

    @property
    def email(self) -> str | None:
        return self._email

    def set_email(self, email: str) -> None:
        self._email = email
        self.save()


def main() -> None:
    import argparse
    import sys
    import uuid

    parser = argparse.ArgumentParser(description="FANS provider utilities")
    sub = parser.add_subparsers(dest="command")

    login_parser = sub.add_parser("login", help="Login via email verification code")
    login_parser.add_argument("--email", default=None, nargs="?", help="Email address (optional, prompts if absent)")

    args = parser.parse_args()

    if args.command == "login":
        if not args.email:
            existing = FansSessionStore().load()
            args.email = existing.email
        if not args.email:
            args.email = input("Email: ").strip()
            if not args.email:
                print("No email provided")
                sys.exit(1)

        session = FansSessionStore().load()
        guid = session.guid or str(uuid.uuid4())
        client_uuid = session.client_uuid or f"web-{uuid.uuid4()}"

        print(f"Sending verification code to {args.email}...")
        ok = send_verification_code(args.email, guid)
        if not ok:
            print("Failed to send verification code")
            sys.exit(1)
        print("Code sent! Check your email.")
        code = input("Enter verification code: ").strip()
        if not code:
            print("No code entered")
            sys.exit(1)
        access_token, refresh_token = confirm_login(args.email, code, client_uuid, guid)

        session = FansSessionStore()
        session._token = access_token
        session._refresh_token = refresh_token
        session._client_uuid = client_uuid
        session._guid = guid
        session._email = args.email
        session.save()
        print(f"\nSaved to {session.path}")
        print("Done! You can now run: python main.py")


if __name__ == "__main__":
    main()
