from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from config import BASE_DIR

STATES_DIR = BASE_DIR / "states"
IG_STATE_FILE = STATES_DIR / "ig_state.json"
FANS_STATE_FILE = STATES_DIR / "fans_state.json"
YT_STATE_FILE = STATES_DIR / "yt_state.json"
TWITTER_STATE_FILE = STATES_DIR / "twitter_state.json"


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

    def load(self) -> FansStateStore:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._last_seen_id = str(data["last_seen_id"]) if data.get("last_seen_id") else None
            except (OSError, json.JSONDecodeError):
                pass
        return self

    def save(self) -> None:
        payload: dict[str, str] = {}
        if self._last_seen_id is not None:
            payload["last_seen_id"] = self._last_seen_id
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


class YouTubeStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        # Lưu theo từng kênh: { channel_id: last_video_id }
        self._seen: dict[str, str] = {}

    def load(self) -> YouTubeStateStore:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                # Hỗ trợ backward-compat: file cũ chỉ có last_seen_id (string)
                if isinstance(data, dict):
                    if "channels" in data:
                        self._seen = {k: str(v) for k, v in data["channels"].items()}
                    elif "last_seen_id" in data and data["last_seen_id"]:
                        # Migrate: gán vào key đặc biệt "__legacy__"
                        self._seen = {"__legacy__": str(data["last_seen_id"])}
            except (OSError, json.JSONDecodeError):
                self._seen = {}
        return self

    def save(self) -> None:
        payload = {"channels": self._seen}
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

    def is_new(self, channel_id: str, video_id: str) -> bool:
        return self._seen.get(channel_id) != video_id

    def mark_seen(self, channel_id: str, video_id: str) -> None:
        self._seen[channel_id] = video_id

    def is_initialized(self, channel_id: str) -> bool:
        return channel_id in self._seen


class TwitterStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._seen: dict[str, str] = {}

    def load(self) -> TwitterStateStore:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if "targets" in data:
                        self._seen = {k: str(v) for k, v in data["targets"].items()}
                    elif "last_seen_id" in data and data["last_seen_id"]:
                        self._seen = {"__legacy__": str(data["last_seen_id"])}
            except (OSError, json.JSONDecodeError):
                self._seen = {}
        return self

    def save(self) -> None:
        payload = {"targets": self._seen}
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

    def is_new(self, target: str, tweet_id: str) -> bool:
        return True

    def mark_seen(self, target: str, tweet_id: str) -> None:
        self._seen[target] = tweet_id

    def is_initialized(self, target: str) -> bool:
        return target in self._seen or "__legacy__" in self._seen

    def get_last_seen_id(self, target: str) -> str | None:
        return self._seen.get(target) or self._seen.get("__legacy__")
