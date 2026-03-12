from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _resolve_cli(name: str) -> list[str]:
    force_installed = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED") == "1"
    if force_installed:
        return [name]
    return [sys.executable, "-m", "cli_anything.yuque.yuque_cli"]


def _run(args: list[str]):
    cmd = _resolve_cli("cli-anything-yuque") + args
    env = os.environ.copy()
    project_root = Path(__file__).resolve().parents[4]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{project_root / 'agent-harness'}{os.pathsep}{existing}" if existing else str(project_root / "agent-harness")
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def test_project_info_json() -> None:
    proc = _run(["--json", "project", "info"])
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["data"]["name"] == "yuque-exporter"


def test_project_paths_json() -> None:
    proc = _run(["--json", "project", "paths", "--profile", "default"])
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["data"]["profile"] == "default"


def test_param_error_json_and_exit_code() -> None:
    proc = _run(["--json", "export", "run", "--repo-id", "1"])  # missing --all / --node
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] in {"bad_parameter", "usage_error"}
