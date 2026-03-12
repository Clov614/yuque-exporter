from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .project import profile_root


def audit_file(profile: str) -> Path:
    return profile_root(profile) / "audit.log"


def append_audit(profile: str, event: Dict[str, Any]) -> Dict[str, Any]:
    target = audit_file(profile)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        **event,
    }
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload
