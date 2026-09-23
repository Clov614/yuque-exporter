from __future__ import annotations

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

from core.favorite_document import FavoriteDocument  # type: ignore  # noqa: E402
from core.favorite_document_provider import (  # type: ignore  # noqa: E402
    FavoriteDocumentProvider,
    FavoriteRepositoryUnavailableError,
)
from core.models import Repository  # type: ignore  # noqa: E402
from core.repository_resolver import (  # type: ignore  # noqa: E402
    RepositoryAuthenticationError,
    RepositoryHttpResult,
)
from main import Application  # type: ignore  # noqa: E402
from ui.console import UI  # type: ignore  # noqa: E402


def doc_action(
    *,
    doc_id: int = 101,
    book_id: int = 8,
    title: str = "Doc A",
    slug: str = "doc-a",
    created_at: str = "2026-09-03 21:05",
    namespace: str = "owner/book-b",
) -> dict[str, object]:
    return {
        "target_id": doc_id,
        "_url": f"/{namespace}/{slug}",
        "created_at": created_at,
        "target": {
            "type": "Doc",
            "id": doc_id,
            "title": title,
            "slug": slug,
            "book_id": book_id,
            "book": {
                "id": book_id,
                "name": "Book B",
                "slug": "book-b",
                "namespace": namespace,
                "user": {"login": "owner"},
            },
        },
    }


def book_action(repo_id: int = 7) -> dict[str, object]:
    return {
        "target": {
            "id": repo_id,
            "type": "Book",
            "name": "Book A",
            "slug": "book-a",
            "namespace": "owner/book-a",
            "items_count": 2,
            "user": {"login": "owner"},
        }
    }


def provider(payload: object, *, status: int = 200) -> FavoriteDocumentProvider:
    def requester(_url: str) -> RepositoryHttpResult:
        return RepositoryHttpResult(status, payload, "application/json")

    return FavoriteDocumentProvider(requester)


def test_provider_returns_doc_favorites_with_book_and_time() -> None:
    payload = {"data": {"actions": [doc_action()]}}

    documents = provider(payload).list_documents()

    assert len(documents) == 1
    document = documents[0]
    assert document.doc_id == 101
    assert document.title == "Doc A"
    assert document.book_id == 8
    assert document.book_display == "owner/book-b"
    assert document.favorite_time == "2026-09-03 21:05"


def test_provider_ignores_book_actions() -> None:
    payload = {"data": {"actions": [book_action(), doc_action()]}}

    documents = provider(payload).list_documents()

    assert [document.doc_id for document in documents] == [101]


def test_provider_deduplicates_same_doc() -> None:
    payload = {"data": {"actions": [doc_action(), doc_action()]}}

    documents = provider(payload).list_documents()

    assert len(documents) == 1


def test_provider_skips_doc_without_id() -> None:
    action = doc_action()
    assert isinstance(action["target"], dict)
    action["target"].pop("id")
    action.pop("target_id")
    payload = {"data": {"actions": [action]}}

    assert provider(payload).list_documents() == []


def test_provider_paginates_until_short_page() -> None:
    calls: list[str] = []
    page_one_actions = [doc_action(doc_id=i, slug=f"doc-{i}") for i in range(1, 21)]
    page_one = {"data": {"actions": page_one_actions, "totalItems": 21}}
    page_two = {"data": {"actions": [doc_action(doc_id=21)], "totalItems": 21}}

    def requester(url: str) -> RepositoryHttpResult:
        calls.append(url)
        if "offset=0" in url:
            return RepositoryHttpResult(200, page_one, "application/json")
        return RepositoryHttpResult(200, page_two, "application/json")

    documents = FavoriteDocumentProvider(requester).list_documents()

    assert [document.doc_id for document in documents] == [*range(1, 21), 21]
    assert any("offset=20" in call for call in calls)


def test_provider_rejects_invalid_payload() -> None:
    with pytest.raises(FavoriteRepositoryUnavailableError):
        provider({"data": {"unknown": []}}).list_documents()


def test_provider_maps_authentication_error() -> None:
    with pytest.raises(RepositoryAuthenticationError):
        provider({}, status=401).list_documents()


def test_favorite_document_to_document_keeps_book_and_uuid() -> None:
    favorite = FavoriteDocument(doc_id=101, title="Doc A", slug="doc-a", book_id=8)

    document = favorite.to_document()

    assert document.id == 101
    assert document.doc_id == 101
    assert document.book_id == 8
    assert document.uuid == "favorite-8-101"


class FakeFavoriteClient:
    def __init__(self, documents: list[FavoriteDocument] | None = None) -> None:
        self.documents = documents or []
        self.calls = 0

    def get_favorite_documents(self) -> list[FavoriteDocument]:
        self.calls += 1
        return list(self.documents)


class FakeProgress:
    def __enter__(self) -> "FakeProgress":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def add_task(self, *args: object, **kwargs: object) -> int:
        return 1

    def update(self, *args: object, **kwargs: object) -> None:
        return None


def application_with(client: FakeFavoriteClient) -> Application:
    application = Application()
    application.client = client  # type: ignore[assignment]
    return application


def test_select_from_favorite_documents_uses_checkbox_multiselect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [
        FavoriteDocument(
            doc_id=101,
            title="Doc A",
            book_id=8,
            book_namespace="owner/book-b",
            favorite_time="今天 21:05",
        ),
        FavoriteDocument(
            doc_id=102,
            title="Doc B",
            book_id=8,
            book_namespace="owner/book-b",
            favorite_time="08-19 18:27",
        ),
    ]
    application = application_with(FakeFavoriteClient(documents))
    monkeypatch.setattr(UI, "create_progress", staticmethod(lambda: FakeProgress()))
    monkeypatch.setattr(UI, "show_favorite_docs", staticmethod(lambda _docs: None))
    monkeypatch.setattr(
        UI,
        "ask_checkbox",
        staticmethod(lambda _message, choices: [choice["value"] for choice in choices]),
    )

    selected = application._select_from_favorite_documents()

    assert [document.doc_id for document in selected] == [101, 102]


def test_select_repositories_routes_favorite_docs_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    favorite = FavoriteDocument(doc_id=101, title="Doc A", book_id=8)
    application = application_with(FakeFavoriteClient())
    monkeypatch.setattr(
        UI,
        "ask_choice",
        staticmethod(lambda *_args, **_kwargs: "从收藏文档列表选择"),
    )

    def _fake_select() -> list[FavoriteDocument]:
        return [favorite]

    monkeypatch.setattr(
        application, "_select_from_favorite_documents", _fake_select
    )

    selection = application._select_repositories()

    assert selection == ("favorite_docs", [favorite])


def test_export_favorite_documents_groups_by_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = application_with(FakeFavoriteClient())
    captured: list[tuple[int, list[FavoriteDocument]]] = []

    def _fake_group(
        favorites: list[FavoriteDocument],
        export_type: object,
        download_images: bool = False,
        incremental: bool = False,
    ) -> None:
        captured.append((favorites[0].book_id, favorites))

    monkeypatch.setattr(application, "_export_favorite_group", _fake_group)

    application.export_favorite_documents(
        [
            FavoriteDocument(doc_id=1, title="A", book_id=8),
            FavoriteDocument(doc_id=2, title="B", book_id=9),
            FavoriteDocument(doc_id=3, title="C", book_id=8),
            FavoriteDocument(doc_id=4, title="NoBook"),
        ],
        object(),  # type: ignore[arg-type]
    )

    assert [book_id for book_id, _ in captured] == [8, 9]
    assert [doc.doc_id for _, docs in captured for doc in docs] == [1, 3, 2]


def test_export_favorite_group_prefers_catalog_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.models import Document

    repo = Repository(id=8, name="Book B", slug="book-b", user_login="owner")
    catalog_doc = Document(
        id=101, doc_id=101, title="Doc A", slug="doc-a", uuid="u-1", book_id=8
    )

    class GroupClient(FakeFavoriteClient):
        def get_repository(self, reference: object) -> Repository:
            assert reference == 8
            return repo

        def get_catalog_nodes(self, _repo: Repository) -> list[Document]:
            return [catalog_doc]

    application = application_with(GroupClient())
    captured: dict[str, object] = {}

    def _fake_export_target(
        _repo: Repository,
        target_docs: list[Document],
        path_map: dict[str, str],
        export_type: object,
        download_images: bool = False,
        incremental: bool = False,
        full_catalog_nodes: list[Document] | None = None,
    ) -> None:
        captured["target_docs"] = target_docs
        captured["repo"] = _repo

    monkeypatch.setattr(application, "_export_target_docs", _fake_export_target)

    application._export_favorite_group(
        [FavoriteDocument(doc_id=101, title="Doc A", slug="doc-a", book_id=8)],
        object(),  # type: ignore[arg-type]
    )

    assert captured["repo"] == repo
    assert captured["target_docs"] == [catalog_doc]


def test_export_favorite_group_falls_back_when_catalog_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.models import Document
    from core.repository_resolver import RepositoryTransportError

    repo = Repository(id=8, name="Book B", slug="book-b", user_login="owner")

    class GroupClient(FakeFavoriteClient):
        def get_repository(self, reference: object) -> Repository:
            return repo

        def get_catalog_nodes(self, _repo: Repository) -> list[Document]:
            raise RepositoryTransportError("catalog unavailable")

    application = application_with(GroupClient())
    warnings: list[str] = []
    monkeypatch.setattr(UI, "warning", staticmethod(warnings.append))
    captured: dict[str, object] = {}

    def _fake_export_target(
        _repo: Repository,
        target_docs: list[Document],
        path_map: dict[str, str],
        export_type: object,
        download_images: bool = False,
        incremental: bool = False,
        full_catalog_nodes: list[Document] | None = None,
    ) -> None:
        captured["target_docs"] = target_docs

    monkeypatch.setattr(application, "_export_target_docs", _fake_export_target)

    application._export_favorite_group(
        [FavoriteDocument(doc_id=101, title="Doc A", slug="doc-a", book_id=8)],
        object(),  # type: ignore[arg-type]
    )

    target_docs = captured["target_docs"]
    assert isinstance(target_docs, list) and len(target_docs) == 1
    assert target_docs[0].id == 101
    assert warnings
