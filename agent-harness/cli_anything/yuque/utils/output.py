from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def make_meta(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta = {
        "request_id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    return {**meta, **(extra or {})}


def success(data: Any, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "ok": True,
        "data": data,
        "error": None,
        "meta": make_meta(meta),
    }


def failure(code: str, message: str, details: Optional[Any] = None, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {
        "ok": False,
        "data": None,
        "error": err,
        "meta": make_meta(meta),
    }


def emit(payload: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return

    if payload.get("ok"):
        sys.stdout.write(json.dumps(payload.get("data"), ensure_ascii=False, indent=2) + "\n")
        return

    err = payload.get("error") or {}
    sys.stderr.write(f"[{err.get('code', 'error')}] {err.get('message', 'unknown error')}\n")
