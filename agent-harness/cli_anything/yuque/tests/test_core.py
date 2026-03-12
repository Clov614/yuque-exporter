from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from cli_anything.yuque.core import audit as audit_mod
from cli_anything.yuque.core import session as session_mod
from cli_anything.yuque.utils import output as output_mod
from cli_anything.yuque.utils import validators
from cli_anything.yuque.yuque_cli import (
    EXIT_AUTH,
    EXIT_IO,
    EXIT_PARAM,
    EXIT_REMOTE,
    EXIT_UNKNOWN,
    map_exception,
)


def test_session_init_read_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_mod, "profile_root", lambda profile: tmp_path / profile)
    store = session_mod.SessionStore("default")

    initial = store.init()
    assert initial["profile"] == "default"
    assert store.session_file.exists()

    updated = store.update({"last_repo_id": 42})
    assert updated["last_repo_id"] == 42

    read_back = store.read()
    assert read_back["last_repo_id"] == 42
    assert read_back["default_export_format"] == "markdown"


def test_session_corrupt_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_mod, "profile_root", lambda profile: tmp_path / profile)
    store = session_mod.SessionStore("broken")
    store.state_dir.mkdir(parents=True, exist_ok=True)
    store.session_file.write_text("{bad-json", encoding="utf-8")

    payload = store.read()

    assert payload["profile"] == "broken"
    assert payload["default_export_format"] == "markdown"
    assert (store.state_dir / "session.json.corrupt").exists()


def test_output_success_failure_emit(capsys: pytest.CaptureFixture[str]) -> None:
    ok_payload = output_mod.success({"k": "v"})
    assert ok_payload["ok"] is True
    assert ok_payload["error"] is None
    assert "request_id" in ok_payload["meta"]

    err_payload = output_mod.failure("bad", "oops", details={"x": 1})
    assert err_payload["ok"] is False
    assert err_payload["data"] is None
    assert err_payload["error"]["code"] == "bad"

    output_mod.emit(ok_payload, as_json=True)
    stdout = capsys.readouterr().out.strip()
    parsed = json.loads(stdout)
    assert parsed["ok"] is True

    output_mod.emit(err_payload, as_json=False)
    captured = capsys.readouterr()
    assert "[bad] oops" in captured.err


def test_validators() -> None:
    assert validators.validate_profile("default_1") == "default_1"
    with pytest.raises(click.BadParameter):
        validators.validate_profile("bad profile")

    assert validators.validate_format("markdown") == "markdown"
    with pytest.raises(click.BadParameter):
        validators.validate_format("md")

    assert validators.validate_repo_id(1) == 1
    with pytest.raises(click.BadParameter):
        validators.validate_repo_id(0)

    assert validators.validate_node_values(["  abcde  ", "", "fghi"]) == ["abcde", "fghi"]
    with pytest.raises(click.BadParameter):
        validators.validate_node_values(["x"])


def test_map_exception_exit_codes() -> None:
    assert map_exception(click.BadParameter("x")).exit_code == EXIT_PARAM
    assert map_exception(click.UsageError("x")).exit_code == EXIT_PARAM
    assert map_exception(ValueError("auth failed")).exit_code == EXIT_AUTH
    assert map_exception(RuntimeError("api error")).exit_code == EXIT_REMOTE
    assert map_exception(OSError("file permission denied")).exit_code == EXIT_IO
    assert map_exception(Exception("mystery")).exit_code == EXIT_UNKNOWN


def test_append_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_mod, "profile_root", lambda profile: tmp_path / profile)
    row = audit_mod.append_audit("p1", {"event": "export.run", "repo_id": 1})
    assert row["profile"] == "p1"

    audit_file = tmp_path / "p1" / "audit.log"
    assert audit_file.exists()
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "export.run"
