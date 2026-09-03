from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from cli_anything.yuque import yuque_cli


class CapturingRepoService:
    calls: list[dict[str, Any]] = []

    def __init__(self, profile: str) -> None:
        assert profile == "default"

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"status": "dry_run", "name": kwargs["name"]}


class CapturingImportService:
    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, profile: str) -> None:
        assert profile == "default"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("run", kwargs))
        return {"status": "dry_run", "items": []}

    def batch(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("batch", kwargs))
        return {"status": "dry_run", "items": []}


@pytest.fixture(autouse=True)
def reset_services(monkeypatch: pytest.MonkeyPatch) -> None:
    CapturingRepoService.calls = []
    CapturingImportService.calls = []
    monkeypatch.setattr(yuque_cli, "RepoService", CapturingRepoService)
    monkeypatch.setattr(yuque_cli, "ImportService", CapturingImportService)


def invoke(*args: str):
    return CliRunner().invoke(yuque_cli.cli, ["--json", *args])


def test_repo_create_passes_write_options() -> None:
    result = invoke(
        "repo",
        "create",
        "--name",
        "New",
        "--slug",
        "new-book",
        "--description",
        "Description",
        "--visibility",
        "private",
        "--dry-run",
    )

    assert result.exit_code == yuque_cli.EXIT_OK, result.output
    assert CapturingRepoService.calls == [
        {
            "name": "New",
            "slug": "new-book",
            "description": "Description",
            "visibility": "private",
            "confirmed": False,
            "dry_run": True,
        }
    ]


def test_import_run_requires_confirmation_unless_dry_run(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("# Note", encoding="utf-8")

    result = invoke("import", "run", "--repo", "owner/book", "--file", str(path))

    assert result.exit_code == yuque_cli.EXIT_PARAM
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "bad_parameter"
    assert CapturingImportService.calls == []


def test_import_run_passes_selector_and_file(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("# Note", encoding="utf-8")

    result = invoke(
        "import",
        "run",
        "--repo",
        "owner/book",
        "--file",
        str(path),
        "--title",
        "Custom",
        "--yes",
    )

    assert result.exit_code == yuque_cli.EXIT_OK, result.output
    assert CapturingImportService.calls == [
        (
            "run",
            {
                "repo": "owner/book",
                "file": str(path.resolve()),
                "title": "Custom",
                "confirmed": True,
                "dry_run": False,
            },
        )
    ]


def test_import_batch_preserves_file_order_and_accepts_dry_run(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    result = invoke(
        "import",
        "batch",
        "--repo-id",
        "42",
        "--file",
        str(first),
        "--file",
        str(second),
        "--dry-run",
    )

    assert result.exit_code == yuque_cli.EXIT_OK, result.output
    assert CapturingImportService.calls == [
        (
            "batch",
            {
                "repo_id": 42,
                "files": (str(first.resolve()), str(second.resolve())),
                "confirmed": False,
                "dry_run": True,
            },
        )
    ]
