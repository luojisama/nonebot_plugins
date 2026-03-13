import json
from pathlib import Path

from pydantic import BaseModel, Field


class Config(BaseModel):
    touchgal_priority: int = Field(default=5)
    search_limit: int = Field(default=15, ge=1, le=99)
    enable_nsfw: bool = Field(default=False)
    request_timeout: int = Field(default=20, ge=5, le=60)
    state_path: str = Field(default="data/touchgal/state.json")


class StateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.data: dict[str, bool] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = {str(k): bool(v) for k, v in raw.items()}
        except Exception:
            self.data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.data.get(key, default))

    def set_bool(self, key: str, value: bool) -> None:
        self.data[key] = bool(value)
        self._save()
