from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from config import BASE_DIR

STATE_FILE = BASE_DIR / "state.json"
FANS_STATE_FILE = BASE_DIR / "fans_state.json"


@dataclass(frozen=True)
class BotState:
    last_seen_media_pk: str | None = None
    last_seen_media_code: str | None = None


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> BotState:
        if not self.path.exists():
            return BotState()

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return BotState()

        pk = data.get("last_seen_media_pk")
        code = data.get("last_seen_media_code")
        return BotState(
            last_seen_media_pk=str(pk) if pk else None,
            last_seen_media_code=str(code) if code else None,
        )

    def save(self, state: BotState) -> None:
        payload = {
            "last_seen_media_pk": state.last_seen_media_pk,
            "last_seen_media_code": state.last_seen_media_code,
        }
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


class FansStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._seen_ids: set[str] = set()

    def load(self) -> FansStateStore:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                ids = data.get("seen_ids", [])
                self._seen_ids = set(str(i) for i in ids if i)
            except (OSError, json.JSONDecodeError):
                self._seen_ids = set()
        return self

    def save(self) -> None:
        payload = {"seen_ids": sorted(self._seen_ids)}
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
        return notif_id not in self._seen_ids

    def mark_seen(self, notif_id: str) -> None:
        self._seen_ids.add(notif_id)

    def is_initialized(self) -> bool:
        return len(self._seen_ids) > 0
