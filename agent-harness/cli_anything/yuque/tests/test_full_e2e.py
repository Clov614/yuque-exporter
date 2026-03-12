from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from cli_anything.yuque.core.export import ExportService


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
    description: str = ""
    doc_count: int = 0
    public: int = 0
    cover: str = ""


class FakePage:
    pass


class FakeBrowserManager:
    def __init__(self):
        self.page = FakePage()

    def start(self, headless: bool = True):
        return self.page

    def quit(self):
        return None


class FakeYuqueClient:
    def __init__(self, _page):
        self.repo = FakeRepo(id=1, name="RepoA", slug="repo-a", user_login="u")
        self.nodes = [
            FakeDoc(id=10, title="Group", slug="group", uuid="root", parent_uuid="", type="TITLE", book_id=1),
            FakeDoc(id=11, title="Doc1", slug="doc1", uuid="doc1", parent_uuid="root", type="DOC", doc_id=11, book_id=1),
            FakeDoc(id=12, title="Doc2", slug="doc2", uuid="doc2", parent_uuid="root", type="DOC", doc_id=12, book_id=1),
        ]

    def get_repositories(self):
        return [self.repo]

    def get_catalog_nodes(self, _repo):
        return self.nodes

    def export_document(self, doc, _export_type):
        if doc.uuid == "doc1":
            return "https://download/doc1"
        return "EMPTY_DOC"

    def download_file(self, _url: str, save_path: str):
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        Path(save_path).write_text("content", encoding="utf-8")
        return True


class FakeExporter:
    def __init__(self, output_dir=None):
        self.output_dir = Path(output_dir or Path.cwd() / "out")

    def get_save_path(self, doc, repo_name: str, extension: str = ".md", relative_path: str = ""):
        base = self.output_dir / repo_name
        if relative_path:
            base = base / relative_path
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{doc.title}{extension}"

    def add_metadata(self, filepath: Path, _doc):
        if filepath.exists():
            original = filepath.read_text(encoding="utf-8") if filepath.stat().st_size else ""
            filepath.write_text("---\nmeta: yes\n---\n" + original, encoding="utf-8")


class FakeProfileAuth:
    def __init__(self, _profile: str):
        pass

    def _sync_profile_to_legacy(self):
        return None


def test_export_service_run_all(monkeypatch, tmp_path: Path) -> None:
    captured: Dict[str, object] = {}

    def fake_append_audit(profile: str, event: Dict[str, object]):
        captured["profile"] = profile
        captured["event"] = event
        return {"profile": profile, **event}

    monkeypatch.setattr("cli_anything.yuque.core.export.ProfileAuth", FakeProfileAuth)
    monkeypatch.setattr("cli_anything.yuque.core.export.BrowserManager", FakeBrowserManager)
    monkeypatch.setattr("cli_anything.yuque.core.export.YuqueClient", FakeYuqueClient)
    monkeypatch.setattr("cli_anything.yuque.core.export.DocumentExporter", FakeExporter)
    monkeypatch.setattr("cli_anything.yuque.core.export.append_audit", fake_append_audit)

    svc = ExportService(profile="default", output_dir=str(tmp_path))
    result = svc.run(repo_id=1, fmt="markdown", all_docs=True, node_uuids=[])

    assert result["requested"] == 3
    assert result["success"] == 3
    assert captured["profile"] == "default"
    assert captured["event"]["event"] == "export.run"


def test_export_service_run_node_filter(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("cli_anything.yuque.core.export.ProfileAuth", FakeProfileAuth)
    monkeypatch.setattr("cli_anything.yuque.core.export.BrowserManager", FakeBrowserManager)
    monkeypatch.setattr("cli_anything.yuque.core.export.YuqueClient", FakeYuqueClient)
    monkeypatch.setattr("cli_anything.yuque.core.export.DocumentExporter", FakeExporter)
    monkeypatch.setattr("cli_anything.yuque.core.export.append_audit", lambda *_a, **_k: {})

    svc = ExportService(profile="default", output_dir=str(tmp_path))
    result = svc.run(repo_id=1, fmt="markdown", all_docs=False, node_uuids=["doc1"])

    assert result["requested"] == 1
    assert result["success"] == 1
    assert result["items"][0]["doc"]["uuid"] == "doc1"
