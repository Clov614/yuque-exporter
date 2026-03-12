from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .project import profile_root


DEFAULT_SESSION: Dict[str, Any] = {
    "default_export_format": "markdown",
    "default_output_dir": None,
    "last_repo_id": None,
    "last_run_at": None,
}


@dataclass(frozen=True)
class SessionStore:
    profile: str

    @property
    def state_dir(self) -> Path:
        return profile_root(self.profile)

    @property
    def session_file(self) -> Path:
        return self.state_dir / "session.json"

    def init(self) -> Dict[str, Any]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.session_file.exists():
            return self.read()
        payload = {**DEFAULT_SESSION, "profile": self.profile, "updated_at": _now_iso()}
        self.write(payload)
        return payload

    def read(self) -> Dict[str, Any]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self.session_file.exists():
            return self.init()
        try:
            with self.session_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("session payload must be object")
            return {**DEFAULT_SESSION, **data, "profile": self.profile}
        except (json.JSONDecodeError, ValueError, TypeError):
            backup = self.session_file.with_suffix(".json.corrupt")
            self.session_file.replace(backup)
            return self.init()

    def write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {**DEFAULT_SESSION, **data, "profile": self.profile, "updated_at": _now_iso()}
        _atomic_write_json(self.session_file, payload)
        return payload

    def update(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        current = self.read()
        merged = {**current, **updates}
        return self.write(merged)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        Path(tmp_name).replace(path)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
