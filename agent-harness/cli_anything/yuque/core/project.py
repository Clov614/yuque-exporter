from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict


def project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def src_root() -> Path:
    return project_root() / "src"


def ensure_src_on_path() -> Path:
    src = src_root()
    if src.exists():
        src_str = str(src)
        if src_str not in sys.path:
            sys.path.insert(0, src_str)
        return src

    try:
        import core  # type: ignore

        package_root = Path(core.__file__).resolve().parent
        return package_root.parent
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(f"src directory not found: {src}") from exc


def profile_root(profile: str) -> Path:
    return Path.home() / ".yuque_harness" / profile


def project_info() -> Dict[str, str]:
    return {
        "name": "yuque-exporter",
        "root": str(project_root()),
        "src": str(src_root()),
        "harness": str(project_root() / "agent-harness"),
    }


def project_paths(profile: str) -> Dict[str, str]:
    state_dir = profile_root(profile)
    return {
        "profile": profile,
        "state_dir": str(state_dir),
        "cookies_file": str(state_dir / "cookies.json"),
        "session_file": str(state_dir / "session.json"),
        "audit_file": str(state_dir / "audit.log"),
        "default_output_dir": str(project_root() / "yuque_export"),
    }
