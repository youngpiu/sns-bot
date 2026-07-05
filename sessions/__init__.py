from __future__ import annotations

import json
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile

from config import BASE_DIR

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
