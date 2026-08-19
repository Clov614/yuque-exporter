from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner

from cli_anything.yuque import yuque_cli
from core.repository_resolver import (
    RepositoryAuthenticationError,
    RepositoryTransportError,
)


class CapturingRepoService:
    calls: list[dict[str, Any]] = []
    list_calls: list[str] = []

    def __init__(self, profile: str) -> None:
        assert profile == "default"

    def list_repos(self, source: str = "common") -> list[dict[str, Any]]:
        self.list_calls.append(source)
        return []

    def tree(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"repo": {"id": 42}, "nodes": []}


class CapturingExportService:
    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, profile: str, output_dir: str | None) -> None:
        assert profile == "default"
        assert output_dir is None

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("run", kwargs))
        return {"requested": 0, "success": 0, "items": []}

    def batch(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("batch", kwargs))
        return {"count": 0, "results": []}


@pytest.fixture(autouse=True)
def reset_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    CapturingRepoService.calls = []
    CapturingRepoService.list_calls = []
    CapturingExportService.calls = []
    monkeypatch.setattr(yuque_cli, "RepoService", CapturingRepoService)
    monkeypatch.setattr(yuque_cli, "ExportService", CapturingExportService)


def invoke(*args: str):
    return CliRunner().invoke(yuque_cli.cli, ["--json", *args])


@pytest.mark.parametrize(
    ("args", "expected_source"),
    [
        (("repo", "list"), "common"),
        (("repo", "list", "--source", "favorites"), "favorites"),
    ],
)
def test_repo_list_passes_explicit_source(
    args: tuple[str, ...],
    expected_source: str,
) -> None:
    result = invoke(*args)

    assert result.exit_code == yuque_cli.EXIT_OK
    assert CapturingRepoService.list_calls == [expected_source]


def test_repo_list_rejects_unknown_source() -> None:
    result = invoke("repo", "list", "--source", "unknown")

    assert result.exit_code == yuque_cli.EXIT_PARAM


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("owner/repo", {"repo": "owner/repo"}),
        ("https://www.yuque.com/owner/repo/?tab=docs", {"repo": "owner/repo"}),
    ],
)
def test_repo_tree_accepts_namespace_or_url(
    target: str,
    expected: dict[str, Any],
) -> None:
    result = invoke("repo", "tree", "--repo", target)

    assert result.exit_code == yuque_cli.EXIT_OK, result.output
    assert CapturingRepoService.calls == [expected]


def test_export_run_accepts_repository_reference() -> None:
    result = invoke("export", "run", "--repo", "owner/repo", "--all")

    assert result.exit_code == yuque_cli.EXIT_OK, result.output
    assert CapturingExportService.calls == [
        (
            "run",
            {
                "repo": "owner/repo",
                "fmt": "markdown",
                "all_docs": True,
                "node_uuids": [],
                "download_images": False,
            },
        )
    ]


@pytest.mark.parametrize(
    "args",
    [
        ("repo", "tree"),
        ("repo", "tree", "--repo-id", "1", "--repo", "owner/repo"),
        ("export", "run", "--all"),
        ("export", "run", "--repo-id", "1", "--repo", "owner/repo", "--all"),
    ],
)
def test_single_repository_commands_require_exactly_one_selector(
    args: tuple[str, ...],
) -> None:
    result = invoke(*args)

    assert result.exit_code == yuque_cli.EXIT_PARAM
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "bad_parameter"


def test_export_run_rejects_unknown_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingExportService(CapturingExportService):
        def run(self, **_kwargs: Any) -> dict[str, Any]:
            raise ValueError("unknown node UUID(s): missing")

    monkeypatch.setattr(yuque_cli, "ExportService", FailingExportService)
    result = invoke("export", "run", "--repo-id", "1", "--node", "missing")

    assert result.exit_code == yuque_cli.EXIT_UNKNOWN
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["message"] == "unknown node UUID(s): missing"


@pytest.mark.parametrize("command", ["run", "batch"])
def test_export_commands_reject_all_and_node_together(command: str) -> None:
    result = invoke(
        "export",
        command,
        "--repo-id",
        "1",
        "--all",
        "--node",
        "doc-uuid",
    )

    assert result.exit_code == yuque_cli.EXIT_PARAM
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "bad_parameter"


def test_export_batch_accepts_mixed_repository_selectors() -> None:
    result = invoke(
        "export",
        "batch",
        "--repo-id",
        "1",
        "--repo",
        "owner/repo",
        "--repo",
        "https://www.yuque.com/another/book",
        "--all",
    )

    assert result.exit_code == yuque_cli.EXIT_OK, result.output
    assert CapturingExportService.calls == [
        (
            "batch",
            {
                "repo_ids": [1],
                "repos": ["owner/repo", "another/book"],
                "fmt": "markdown",
                "all_docs": True,
                "node_uuids": [],
                "download_images": False,
            },
        )
    ]


def test_export_batch_requires_a_repository_selector() -> None:
    result = invoke("export", "batch", "--all")

    assert result.exit_code == yuque_cli.EXIT_PARAM
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "bad_parameter"


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_exit"),
    [
        (RepositoryAuthenticationError("expired"), "auth_error", yuque_cli.EXIT_AUTH),
        (RepositoryTransportError("server failed"), "remote_error", yuque_cli.EXIT_REMOTE),
    ],
)
def test_repo_tree_maps_catalog_failures_to_json_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_code: str,
    expected_exit: int,
) -> None:
    class FailingRepoService:
        def __init__(self, _profile: str) -> None:
            pass

        def tree(self, **_kwargs: Any) -> dict[str, Any]:
            raise error

    monkeypatch.setattr(yuque_cli, "RepoService", FailingRepoService)

    result = invoke("repo", "tree", "--repo-id", "42")

    assert result.exit_code == expected_exit
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == expected_code
