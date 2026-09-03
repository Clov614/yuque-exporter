from __future__ import annotations

from pathlib import Path

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path

ensure_src_on_path()

from cli_anything.yuque.core import importer as importer_mod  # noqa: E402
from cli_anything.yuque.core import repo as repo_mod  # noqa: E402
from cli_anything.yuque.core.importer import ImportService  # noqa: E402
from cli_anything.yuque.core.repo import RepoService  # noqa: E402
from core.models import Repository  # type: ignore  # noqa: E402
from core.mutation_errors import (  # type: ignore  # noqa: E402
    MutationConfirmationRequired,
    MutationProtocolError,
)


REPOSITORY = Repository(
    id=42,
    name="Existing",
    slug="existing-book",
    user_login="tester",
)


class FakeAuth:
    def load_cookies(self, _page: object) -> bool:
        return True


class FakeManager:
    starts = 0
    quits = 0

    def start(self, headless: bool = True) -> object:
        assert headless is True
        type(self).starts += 1
        return object()

    def quit(self) -> None:
        type(self).quits += 1


class FakeProfileAuth:
    def __init__(self, _profile: str) -> None:
        pass

    def auth(self) -> FakeAuth:
        return FakeAuth()

    def browser_manager(self) -> FakeManager:
        return FakeManager()


class FakeClient:
    def __init__(self, _page: object, auth: object = None) -> None:
        pass

    def get_repository(self, reference: object) -> Repository:
        assert getattr(reference, "canonical") == "tester/existing-book"
        return REPOSITORY


class FakeWriter:
    imports: list[tuple[str, str]] = []
    creates: list[dict[str, str | None]] = []

    def __init__(self, _page: object) -> None:
        pass

    def import_markdown(self, repository: Repository, document) -> str:
        self.imports.append((repository.slug, document.title))
        return f"{repository.url}/{document.title.lower()}"

    def create_repository(self, **kwargs: str | None) -> str:
        self.creates.append(kwargs)
        return "tester/new-book"


@pytest.fixture(autouse=True)
def reset_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeManager.starts = 0
    FakeManager.quits = 0
    FakeWriter.imports = []
    FakeWriter.creates = []
    monkeypatch.setattr(importer_mod, "ProfileAuth", FakeProfileAuth)
    monkeypatch.setattr(importer_mod, "YuqueClient", FakeClient)
    monkeypatch.setattr(importer_mod, "YuqueBrowserWriter", FakeWriter)
    monkeypatch.setattr(importer_mod, "append_audit", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(repo_mod, "ProfileAuth", FakeProfileAuth)
    monkeypatch.setattr(repo_mod, "YuqueClient", FakeClient)
    monkeypatch.setattr(repo_mod, "YuqueBrowserWriter", FakeWriter)
    monkeypatch.setattr(repo_mod, "append_audit", lambda *_args, **_kwargs: {})


def test_import_requires_explicit_confirmation_before_browser_start(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("# Note", encoding="utf-8")

    with pytest.raises(MutationConfirmationRequired):
        ImportService("default").run(repo="tester/existing-book", file=path)

    assert FakeManager.starts == 0


def test_import_run_reads_file_and_releases_browser(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("# Note", encoding="utf-8")

    result = ImportService("default").run(
        repo="tester/existing-book", file=path, confirmed=True
    )

    assert result["status"] == "created"
    assert result["title"] == "Note"
    assert result["repo"]["id"] == 42
    assert FakeWriter.imports == [("existing-book", "Note")]
    assert FakeManager.starts == 1
    assert FakeManager.quits == 1


def test_import_batch_validates_all_files_before_starting_browser(tmp_path: Path) -> None:
    good = tmp_path / "good.md"
    good.write_text("good", encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("bad", encoding="utf-8")

    with pytest.raises(Exception):
        ImportService("default").batch(
            repo="tester/existing-book", files=[good, bad], confirmed=True
        )

    assert FakeManager.starts == 0
    assert FakeWriter.imports == []


def test_repo_create_returns_resolved_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class CreatedClient(FakeClient):
        def get_repository(self, reference: object) -> Repository:
            assert getattr(reference, "canonical") == "tester/new-book"
            return Repository(
                id=99,
                name="New",
                slug="new-book",
                user_login="tester",
            )

    monkeypatch.setattr(repo_mod, "YuqueClient", CreatedClient)
    result = RepoService("default").create(
        name="New",
        slug="new-book",
        description="Description",
        visibility="private",
        confirmed=True,
    )

    assert result["repo"]["id"] == 99
    assert FakeWriter.creates == [
        {
            "name": "New",
            "slug": "new-book",
            "description": "Description",
            "visibility": "private",
        }
    ]
    assert FakeManager.quits == 1


def test_repo_create_rejects_protocol_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenWriter(FakeWriter):
        def create_repository(self, **_kwargs):
            raise MutationProtocolError("page controls changed")

    monkeypatch.setattr(repo_mod, "YuqueBrowserWriter", BrokenWriter)

    with pytest.raises(MutationProtocolError, match="page controls"):
        RepoService("default").create(
            name="New", slug="new-book", visibility="private", confirmed=True
        )
    assert FakeManager.quits == 1
