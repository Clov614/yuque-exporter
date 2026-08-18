from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from cli_anything.yuque import yuque_cli


@pytest.mark.parametrize(
    ("command", "expected_call"),
    [
        (
            ["export", "run", "--repo-id", "1", "--all", "--download-images"],
            (
                "run",
                {
                    "repo_id": 1,
                    "fmt": "markdown",
                    "all_docs": True,
                    "node_uuids": [],
                    "download_images": True,
                },
            ),
        ),
        (
            [
                "export",
                "batch",
                "--repo-id",
                "1",
                "--repo-id",
                "2",
                "--all",
                "--download-images",
            ],
            (
                "batch",
                {
                    "repo_ids": [1, 2],
                    "fmt": "markdown",
                    "all_docs": True,
                    "node_uuids": [],
                    "download_images": True,
                },
            ),
        ),
    ],
)
def test_export_commands_forward_download_images_option(
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    expected_call: tuple[str, dict[str, Any]],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class CapturingExportService:
        def __init__(self, profile: str, output_dir: str | None) -> None:
            assert profile == "default"
            assert output_dir is None

        def run(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("run", kwargs))
            return {"requested": 1, "success": 1, "items": []}

        def batch(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("batch", kwargs))
            return {"count": 2, "results": []}

    monkeypatch.setattr(yuque_cli, "ExportService", CapturingExportService)

    result = CliRunner().invoke(yuque_cli.cli, ["--json", *command])

    assert result.exit_code == yuque_cli.EXIT_OK, result.output
    assert calls == [expected_call]


def test_download_images_rejects_non_markdown_format() -> None:
    result = CliRunner().invoke(
        yuque_cli.cli,
        [
            "--json",
            "export",
            "run",
            "--repo-id",
            "1",
            "--all",
            "--format",
            "pdf",
            "--download-images",
        ],
    )

    assert result.exit_code == yuque_cli.EXIT_PARAM
    assert "requires --format markdown" in result.output
