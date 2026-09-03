"""Tests for incremental export orchestration in ExportService."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import pytest
from click.testing import CliRunner

from cli_anything.yuque import yuque_cli
from cli_anything.yuque.core.export import ExportService
from core.incremental_state import resolve_state_file


@dataclass
class FakeDoc:
    id: int
    title: str
    slug: str
    uuid: str
    parent_uuid: str
    type: str = "DOC"
    level: int = 0
    doc_id: int = 0
    book_id: int = 1
    created_at: str = ""
    updated_at: str = ""
    word_count: int = 0
    children: List["FakeDoc"] = field(default_factory=list)


@dataclass
class FakeRepo:
    id: int
    name: str
    slug: str
    user_login: str


class FakePage:
    pass


class FakeBrowserManager:
    def start(self, headless: bool = True):
        return FakePage()

    def quit(self):
        return None


class FakeYuqueClient:
    """Fake client with controllable detail timestamps and export behavior."""

    timestamps: Dict[str, str] = {}
    export_calls: List[str] = []
    export_results: Dict[str, str] = {}

    def __init__(self, _page, auth=None):
        self.repo = FakeRepo(id=1, name="RepoA", slug="repo-a", user_login="u")
        self.nodes = [
            FakeDoc(id=10, title="Group", slug="group", uuid="root", parent_uuid="", type="TITLE", book_id=1),
            FakeDoc(id=11, title="Doc1", slug="doc1", uuid="doc1", parent_uuid="root", type="DOC", doc_id=11, book_id=1),
            FakeDoc(id=12, title="Doc2", slug="doc2", uuid="doc2", parent_uuid="root", type="DOC", doc_id=12, book_id=1),
        ]

    def get_repository(self, _reference):
        return self.repo

    def get_catalog_nodes(self, _repo):
        return self.nodes

    def get_document_updated_at(self, doc):
        return self.timestamps.get(doc.uuid)

    def export_document(self, doc, _export_type):
        self.export_calls.append(doc.uuid)
        return self.export_results.get(doc.uuid, "https://download/x")

    def download_file(self, _url: str, save_path: str):
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        Path(save_path).write_text("content", encoding="utf-8")
        return True

    def download_external_image(self, _url: str, save_path: str | Path) -> bool:
        return False


class FakeExporter:
    def __init__(self, output_dir=None):
        self.output_dir = Path(output_dir or Path.cwd() / "out")
        self.metadata_docs: List[Any] = []

    def get_save_path(self, doc, repo_name: str, extension: str = ".md", relative_path: str = ""):
        base = self.output_dir / repo_name
        if relative_path:
            base = base / relative_path
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{doc.title}{extension}"

    def localize_images(self, _filepath: Path, _download_image):
        from types import SimpleNamespace

        return SimpleNamespace(found_count=0, downloaded_count=0, failed_urls=(), skipped_count=0)

    def add_metadata(self, filepath: Path, doc):
        self.metadata_docs.append(doc)


class FakeProfileAuth:
    def __init__(self, _profile: str):
        pass

    def browser_manager(self):
        return FakeBrowserManager()

    def auth(self):
        class FakeAuth:
            @staticmethod
            def load_cookies(_page):
                return True

        return FakeAuth()


@pytest.fixture(autouse=True)
def patch_service(monkeypatch: pytest.MonkeyPatch):
    FakeYuqueClient.timestamps = {}
    FakeYuqueClient.export_calls = []
    FakeYuqueClient.export_results = {}
    monkeypatch.setattr("cli_anything.yuque.core.export.ProfileAuth", FakeProfileAuth)
    monkeypatch.setattr("cli_anything.yuque.core.export.YuqueClient", FakeYuqueClient)
    monkeypatch.setattr("cli_anything.yuque.core.export.DocumentExporter", FakeExporter)
    monkeypatch.setattr("cli_anything.yuque.core.export.append_audit", lambda *_a, **_k: {})


def run_service(tmp_path: Path, **kwargs) -> Dict[str, Any]:
    svc = ExportService(profile="default", output_dir=str(tmp_path))
    params = {"repo_id": 1, "fmt": "markdown", "all_docs": True, "node_uuids": []}
    params.update(kwargs)
    return svc.run(**params)


def test_first_incremental_run_exports_everything_and_records_state(tmp_path: Path) -> None:
    result = run_service(tmp_path, incremental=True)

    assert result["incremental"] is True
    assert result["requested"] == 3
    assert result["skipped"] == 0
    assert result["success"] == 3
    assert set(FakeYuqueClient.export_calls) == {"doc1", "doc2"}
    state_file = Path(result["state_file"])
    assert state_file == resolve_state_file(str(tmp_path), 1)
    assert state_file.exists()


def test_second_run_skips_unchanged_documents(tmp_path: Path) -> None:
    FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2"}
    first = run_service(tmp_path, incremental=True)
    assert first["skipped"] == 0

    FakeYuqueClient.export_calls = []
    second = run_service(tmp_path, incremental=True)

    assert second["skipped"] == 2
    assert FakeYuqueClient.export_calls == []
    assert second["success"] == 1  # only the TITLE directory entry
    skipped = [item for item in second["items"] if item["status"] == "skipped"]
    assert {item["doc"]["uuid"] for item in skipped} == {"doc1", "doc2"}
    assert second["stale_files"] == []


def test_changed_document_is_reexported(tmp_path: Path) -> None:
    FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2"}
    run_service(tmp_path, incremental=True)

    FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2-changed"}
    FakeYuqueClient.export_calls = []
    result = run_service(tmp_path, incremental=True)

    assert result["skipped"] == 1
    assert FakeYuqueClient.export_calls == ["doc2"]


def test_detail_failure_falls_back_to_reexport(tmp_path: Path) -> None:
    FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2"}
    run_service(tmp_path, incremental=True)

    FakeYuqueClient.timestamps = {"doc1": "t1"}  # doc2 detail now fails
    FakeYuqueClient.export_calls = []
    result = run_service(tmp_path, incremental=True)

    assert result["skipped"] == 1
    assert FakeYuqueClient.export_calls == ["doc2"]


def test_missing_local_file_is_reexported(tmp_path: Path) -> None:
    FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2"}
    run_service(tmp_path, incremental=True)

    exported_file = tmp_path / "RepoA" / "Group" / "Doc1.md"
    assert exported_file.exists()
    exported_file.unlink()

    FakeYuqueClient.export_calls = []
    result = run_service(tmp_path, incremental=True)

    assert result["skipped"] == 1
    assert FakeYuqueClient.export_calls == ["doc1"]


def test_failed_export_is_not_recorded(tmp_path: Path) -> None:
    FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2"}
    FakeYuqueClient.export_results = {"doc2": ""}
    result = run_service(tmp_path, incremental=True)
    assert result["skipped"] == 0

    statuses = {item["doc"]["uuid"]: item["status"] for item in result["items"]}
    assert statuses["doc2"] == "failed"

    FakeYuqueClient.export_calls = []
    FakeYuqueClient.export_results = {}
    second = run_service(tmp_path, incremental=True)

    assert second["skipped"] == 1
    assert FakeYuqueClient.export_calls == ["doc2"]


def test_title_nodes_never_enter_state(tmp_path: Path) -> None:
    import json

    FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2"}
    result = run_service(tmp_path, incremental=True)

    payload = json.loads(Path(result["state_file"]).read_text(encoding="utf-8"))
    assert set(payload["docs"]) == {"11", "12"}


def test_incremental_refills_fresh_updated_at_into_metadata(tmp_path: Path) -> None:
    captured: Dict[str, FakeExporter] = {}
    import cli_anything.yuque.core.export as export_mod

    real_exporter = export_mod.DocumentExporter

    def capturing_exporter(output_dir=None):
        exporter = real_exporter(output_dir)
        captured["exporter"] = exporter
        return exporter

    import pytest as _pytest

    _monkey = _pytest.MonkeyPatch()
    _monkey.setattr(export_mod, "DocumentExporter", capturing_exporter)
    try:
        FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2"}
        svc = ExportService(profile="default", output_dir=str(tmp_path))
        svc.run(repo_id=1, fmt="markdown", all_docs=True, node_uuids=[], incremental=True)
    finally:
        _monkey.undo()

    seen = {doc.uuid: doc.updated_at for doc in captured["exporter"].metadata_docs}
    assert seen == {"doc1": "t1", "doc2": "t2"}


def test_incremental_rejects_non_markdown_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="incremental export requires markdown"):
        run_service(tmp_path, incremental=True, fmt="pdf")


def test_batch_forwards_incremental(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: List[Dict[str, Any]] = []
    real_run = ExportService.run

    def capturing_run(self, **kwargs):
        calls.append(dict(kwargs))
        kwargs.pop("incremental", None)
        return real_run(self, **kwargs)

    monkeypatch.setattr(ExportService, "run", capturing_run)
    FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2"}
    svc = ExportService(profile="default", output_dir=str(tmp_path))
    result = svc.batch(repo_ids=[1], fmt="markdown", all_docs=True, incremental=True)

    assert result["count"] == 1
    assert calls[0]["incremental"] is True


def test_stale_docs_are_reported_not_deleted(tmp_path: Path) -> None:
    FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2"}
    run_service(tmp_path, incremental=True)

    FakeYuqueClient.timestamps = {"doc1": "t1", "doc2": "t2"}
    original_nodes = FakeYuqueClient.get_catalog_nodes

    def catalog_without_doc2(self, _repo):
        return [node for node in original_nodes(self, _repo) if node.uuid != "doc2"]

    FakeYuqueClient.get_catalog_nodes = catalog_without_doc2
    try:
        result = run_service(tmp_path, incremental=True)
    finally:
        FakeYuqueClient.get_catalog_nodes = original_nodes

    assert result["stale_files"] == ["doc_id=12"]


def test_export_run_cli_forwards_incremental(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[Dict[str, Any]] = []

    class CapturingExportService:
        def __init__(self, _profile: str, _output_dir: str | None) -> None:
            pass

        def run(self, **kwargs: Any) -> Dict[str, Any]:
            calls.append(kwargs)
            return {"requested": 1, "success": 1, "items": []}

    monkeypatch.setattr(yuque_cli, "ExportService", CapturingExportService)
    result = CliRunner().invoke(
        yuque_cli.cli,
        ["--json", "export", "run", "--repo-id", "1", "--all", "--incremental"],
    )

    assert result.exit_code == yuque_cli.EXIT_OK, result.output
    assert calls[0]["incremental"] is True


def test_export_run_cli_rejects_incremental_for_pdf() -> None:
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
            "--incremental",
        ],
    )

    assert result.exit_code == yuque_cli.EXIT_PARAM
    assert "requires --format markdown" in result.output
