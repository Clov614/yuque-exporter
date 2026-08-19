from __future__ import annotations

from dataclasses import asdict

import pytest

from cli_anything.yuque.core import export as export_mod
from cli_anything.yuque.core import repo as repo_mod
from cli_anything.yuque.core.export import ExportService
from cli_anything.yuque.core.repo import RepoService
from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

from core.models import Document, Repository  # type: ignore  # noqa: E402


class FakeProfileAuth:
    def __init__(self, _profile: str) -> None:
        pass

    def browser_manager(self) -> "FakeBrowserManager":
        return FakeBrowserManager()

    def auth(self) -> "FakeAuth":
        return FakeAuth()


class FakeAuth:
    def load_cookies(self, _page: object) -> bool:
        return True


class FakeBrowserManager:
    def start(self, headless: bool = True) -> object:
        assert headless is True
        return object()

    def quit(self) -> None:
        return None


class FakeYuqueClient:
    def __init__(self, _page: object, auth: object = None) -> None:
        self.repo = Repository(
            id=42,
            name="Favorite Repo",
            slug="favorite-repo",
            user_login="owner",
        )

    def get_repositories(self) -> list[Repository]:
        raise AssertionError("direct flows must not list repositories")

    def get_repository(self, reference: object) -> Repository:
        assert getattr(reference, "canonical") == "owner/favorite-repo"
        return self.repo

    def get_catalog_nodes(self, repo: Repository) -> list[Document]:
        assert repo.id == 42
        return [
            Document(
                id=7,
                title="Doc",
                slug="doc",
                uuid="doc-uuid",
                book_id=repo.id,
            )
        ]


class ForbiddenBrowserManager:
    def __init__(self) -> None:
        raise AssertionError("invalid service inputs must fail before browser startup")


def test_export_service_rejects_invalid_format_before_browser_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    with pytest.raises(ValueError, match="unsupported export format"):
        ExportService("default").run(repo_id=42, fmt="invalid", all_docs=True)


def test_export_service_rejects_empty_batch() -> None:
    with pytest.raises(ValueError, match="at least one repository"):
        ExportService("default").batch()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"repo_id": 42, "all_docs": False, "node_uuids": []},
        {"repo_id": 42, "all_docs": True, "node_uuids": ["doc"]},
    ],
)
def test_export_service_requires_exactly_one_range_before_browser_start(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
) -> None:

    with pytest.raises(ValueError, match="exactly one"):
        ExportService("default").run(**kwargs)


def test_repo_service_lists_requested_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class ListingClient(FakeYuqueClient):
        def get_repositories(self):
            calls.append("common")
            return [self.repo]

        def get_favorite_repositories(self):
            calls.append("favorites")
            return [self.repo]

    monkeypatch.setattr(repo_mod, "ProfileAuth", FakeProfileAuth)
    monkeypatch.setattr(repo_mod, "YuqueClient", ListingClient)

    common = RepoService("default").list_repos()
    favorites = RepoService("default").list_repos(source="favorites")

    assert [row["id"] for row in common] == [42]
    assert [row["id"] for row in favorites] == [42]
    assert calls == ["common", "favorites"]


def test_repo_service_rejects_unknown_source_before_browser_start() -> None:
    with pytest.raises(ValueError, match="unsupported repository source"):
        RepoService("default").list_repos(source="unknown")


def test_repo_tree_resolves_directly_without_repository_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo_mod, "ProfileAuth", FakeProfileAuth)
    monkeypatch.setattr(repo_mod, "YuqueClient", FakeYuqueClient)

    result = RepoService("default").tree(repo="owner/favorite-repo")

    assert result["repo"]["id"] == 42
    assert result["nodes"] == [
        asdict(
            Document(
                id=7,
                title="Doc",
                slug="doc",
                uuid="doc-uuid",
                book_id=42,
            )
        )
    ]
