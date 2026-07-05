from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from config import BASE_DIR

STATES_DIR = BASE_DIR / "states"
IG_STATE_FILE = STATES_DIR / "ig_state.json"
FANS_STATE_FILE = STATES_DIR / "fans_state.json"
YT_STATE_FILE = STATES_DIR / "yt_state.json"


class InstagramStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._last_seen_pk: str | None = None

    def load(self) -> InstagramStateStore:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._last_seen_pk = str(data["last_seen_pk"]) if data.get("last_seen_pk") else None
            except (OSError, json.JSONDecodeError):
                self._last_seen_pk = None
        return self

    def save(self) -> None:
        payload = {"last_seen_pk": self._last_seen_pk}
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

    def is_new(self, pk: str) -> bool:
        return True

    def mark_seen(self, pk: str) -> None:
        self._last_seen_pk = pk

    def is_initialized(self) -> bool:
        return self._last_seen_pk is not None

    @property
    def last_seen_pk(self) -> str | None:
        return self._last_seen_pk


class FansStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._last_seen_id: str | None = None
        self._refresh_token: str | None = None
        self._client_uuid: str | None = None
        self._guid: str | None = None

    def load(self) -> FansStateStore:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._last_seen_id = str(data["last_seen_id"]) if data.get("last_seen_id") else None
                self._refresh_token = str(data["refresh_token"]) if data.get("refresh_token") else None
                self._client_uuid = str(data["client_uuid"]) if data.get("client_uuid") else None
                self._guid = str(data["guid"]) if data.get("guid") else None
            except (OSError, json.JSONDecodeError):
                pass
        return self

    def save(self) -> None:
        payload: dict[str, str] = {}
        if self._last_seen_id is not None:
            payload["last_seen_id"] = self._last_seen_id
        if self._refresh_token is not None:
            payload["refresh_token"] = self._refresh_token
        if self._client_uuid is not None:
            payload["client_uuid"] = self._client_uuid
        if self._guid is not None:
            payload["guid"] = self._guid
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

    def is_new(self, notif_id: str) -> bool:
        return True

    def mark_seen(self, notif_id: str) -> None:
        self._last_seen_id = notif_id

    def is_initialized(self) -> bool:
        return self._last_seen_id is not None

    @property
    def last_seen_id(self) -> str | None:
        return self._last_seen_id

    @property
    def refresh_token(self) -> str | None:
        return self._refresh_token

    @property
    def client_uuid(self) -> str | None:
        return self._client_uuid

    @property
    def guid(self) -> str | None:
        return self._guid

    def set_refresh_token(self, token: str) -> None:
        self._refresh_token = token

    def set_client_uuid(self, uuid: str) -> None:
        self._client_uuid = uuid

    def set_guid(self, guid: str) -> None:
        self._guid = guid


class YouTubeStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._last_seen_id: str | None = None

    def load(self) -> YouTubeStateStore:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._last_seen_id = str(data["last_seen_id"]) if data.get("last_seen_id") else None
            except (OSError, json.JSONDecodeError):
                self._last_seen_id = None
        return self

    def save(self) -> None:
        payload = {"last_seen_id": self._last_seen_id}
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

    def is_new(self, video_id: str) -> bool:
        return True

    def mark_seen(self, video_id: str) -> None:
        self._last_seen_id = video_id

    def is_initialized(self) -> bool:
        return self._last_seen_id is not None

    @property
    def last_seen_id(self) -> str | None:
        return self._last_seen_id
