from __future__ import annotations

import json
import pytest

from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

from core.favorite_repository_provider import (  # type: ignore  # noqa: E402
    FavoriteRepositoryProvider,
    FavoriteRepositoryUnavailableError,
)
from core.models import Repository  # type: ignore  # noqa: E402
from core.repository_resolver import (  # type: ignore  # noqa: E402
    RepositoryAuthenticationError,
    RepositoryHttpResult,
)


def book_payload(repo_id: int, slug: str = "book-a") -> dict[str, object]:
    return {
        "id": repo_id,
        "type": "Book",
        "name": "Book A",
        "slug": slug,
        "namespace": f"owner/{slug}",
        "items_count": 2,
        "user": {"login": "owner"},
    }


def provider(payload: object, *, status: int = 200) -> FavoriteRepositoryProvider:
    def requester(_url: str) -> RepositoryHttpResult:
        return RepositoryHttpResult(status, payload, "text/html")

    return FavoriteRepositoryProvider(requester, "https://www.yuque.com/dashboard/collections")


def test_provider_returns_explicit_book_cards_and_deduplicates() -> None:
    cards = {
        "data": {
            "books": [book_payload(7), {"target": book_payload(7)}],
        }
    }

    repositories = provider(cards).list_repositories()

    assert [(repo.id, repo.user_login, repo.slug) for repo in repositories] == [
        (7, "owner", "book-a")
    ]


def test_provider_rejects_repo_target_type() -> None:
    repo_target = book_payload(10, "repo-target")
    repo_target["type"] = "Repo"
    assert provider({"data": {"actions": [{"target": repo_target}]}}).list_repositories() == []


def test_provider_ignores_document_actions_and_nested_books() -> None:
    payload = {
        "data": {
            "actions": [
                {
                    "target_id": 99,
                    "_url": "/owner/book-b/doc-a",
                    "target": {
                        "type": "Doc",
                        "book_id": 8,
                        "book": book_payload(8, "book-b"),
                    },
                }
            ]
        }
    }

    assert provider(payload).list_repositories() == []


def test_provider_ignores_document_links() -> None:
    payload = {
        "data": {
            "cards": [
                {"href": "https://www.yuque.com/owner/book-a/doc-a"},
                {"href": "https://example.com/owner/book-b"},
            ]
        }
    }

    assert provider(payload).list_repositories() == []


def test_provider_keeps_scope_with_self_closing_void_element() -> None:
    page_url = "https://www.yuque.com/dashboard/collections"
    html = (
        '<article class="index-module_bookItem_eqobr">'
        '<img src="cover"/><a href="/owner/book-a">Book</a>'
        '</article>'
    )

    def requester(url: str) -> RepositoryHttpResult:
        if url == page_url:
            return RepositoryHttpResult(200, html, "text/html")
        if "namespace=owner%2Fbook-a" in url:
            return RepositoryHttpResult(200, {"data": [book_payload(7)]})
        return RepositoryHttpResult(200, {"data": {"actions": []}})

    repositories = FavoriteRepositoryProvider(requester, page_url).list_repositories()
    assert [repo.id for repo in repositories] == [7]


def test_provider_does_not_leak_scope_past_void_elements() -> None:
    page_url = "https://www.yuque.com/dashboard/collections"
    html = (
        '<article class="index-module_bookItem_eqobr">'
        '<img src="cover"><a href="/owner/book-a">Book</a>'
        '</article><a href="/owner/outside">Outside</a>'
    )
    calls: list[str] = []

    def requester(url: str) -> RepositoryHttpResult:
        calls.append(url)
        if url == page_url:
            return RepositoryHttpResult(200, html, "text/html")
        if "namespace=owner%2Fbook-a" in url:
            return RepositoryHttpResult(200, {"data": [book_payload(7)]})
        return RepositoryHttpResult(200, {"data": {"actions": []}})

    repositories = FavoriteRepositoryProvider(requester, page_url).list_repositories()

    assert [repo.id for repo in repositories] == [7]
    assert not any("outside" in call for call in calls)


def test_provider_ignores_lookalike_book_class() -> None:
    page_url = "https://www.yuque.com/dashboard/collections"
    html = '<article class="not_bookItem_fake"><a href="/owner/not-favorite">X</a></article>'

    def requester(url: str) -> RepositoryHttpResult:
        if url == page_url:
            return RepositoryHttpResult(200, html, "text/html")
        return RepositoryHttpResult(200, {"data": {"actions": []}})

    assert FavoriteRepositoryProvider(requester, page_url).list_repositories() == []


def test_provider_rejects_excessive_json_books() -> None:
    with pytest.raises(FavoriteRepositoryUnavailableError, match="too many"):
        provider({"data": {"books": [book_payload(i) for i in range(1001)]}}).list_repositories()


def test_provider_rejects_excessive_html_or_actions() -> None:
    too_large = "x" * (FavoriteRepositoryProvider.MAX_HTML_BYTES + 1)
    with pytest.raises(FavoriteRepositoryUnavailableError, match="too large"):
        provider(too_large).list_repositories()

    actions = [{}] * (FavoriteRepositoryProvider.MAX_MARK_ACTIONS + 1)
    with pytest.raises(FavoriteRepositoryUnavailableError, match="too many"):
        provider({"data": {"actions": actions}}).list_repositories()


def test_provider_accepts_real_book_item_card_and_resolves_namespace() -> None:
    page_url = "https://www.yuque.com/dashboard/collections"
    html = (
        '<article class="index-module_bookItem_eqobr">'
        '<a href="/owner/book-a">Book A</a>'
        '</article>'
    )
    calls: list[str] = []

    def requester(url: str) -> RepositoryHttpResult:
        calls.append(url)
        if url == page_url:
            return RepositoryHttpResult(200, html, "text/html")
        if url == "https://www.yuque.com/api/books?namespace=owner%2Fbook-a":
            return RepositoryHttpResult(
                200,
                {"data": [book_payload(7)]},
                "application/json",
            )
        return RepositoryHttpResult(
            200,
            {"data": {"actions": []}},
            "application/json",
        )

    repositories = FavoriteRepositoryProvider(requester, page_url).list_repositories()

    assert [repo.id for repo in repositories] == [7]
    assert calls == [
        page_url,
        "https://www.yuque.com/api/books?namespace=owner%2Fbook-a",
        FavoriteRepositoryProvider.MARKS_ENDPOINT_TEMPLATE.format(offset=0),
    ]


def test_provider_paginates_marks_until_total_items() -> None:
    page_url = "https://www.yuque.com/dashboard/collections"
    html = '<article class="index-module_bookItem_eqobr"></article>'
    actions_page_one = [{"target": {"type": "Doc"}}] * 20
    actions_page_two = [{"target": book_payload(9, "book-c")}]
    calls: list[str] = []

    def requester(url: str) -> RepositoryHttpResult:
        calls.append(url)
        if url == page_url:
            return RepositoryHttpResult(200, html, "text/html")
        if "offset=0" in url:
            return RepositoryHttpResult(
                200,
                {"data": {"actions": actions_page_one, "totalItems": 21}},
            )
        return RepositoryHttpResult(
            200,
            {"data": {"actions": actions_page_two, "totalItems": 21}},
        )

    repositories = FavoriteRepositoryProvider(requester, page_url).list_repositories()

    assert [repository.id for repository in repositories] == [9]
    assert any("offset=20" in call for call in calls)


def test_provider_accepts_book_actions_but_ignores_doc_actions() -> None:
    payload = {
        "data": {
            "actions": [
                {"target": {"type": "Doc", "book_id": 8}},
                {"target": book_payload(9, "book-c")},
            ]
        }
    }

    repositories = provider(payload).list_repositories()

    assert [repository.id for repository in repositories] == [9]


def test_provider_docs_only_page_is_empty() -> None:
    page_url = "https://www.yuque.com/dashboard/collections"
    html = '<a href="https://www.yuque.com/owner/book-a/doc-a">Doc</a>'

    def requester(url: str) -> RepositoryHttpResult:
        if url == page_url:
            return RepositoryHttpResult(200, html, "text/html")
        return RepositoryHttpResult(200, {"data": {"actions": []}}, "application/json")

    assert FavoriteRepositoryProvider(requester, page_url).list_repositories() == []


def test_provider_rejects_unknown_response_shape() -> None:
    with pytest.raises(FavoriteRepositoryUnavailableError):
        provider({"data": {"unknown": []}}).list_repositories()


def test_provider_maps_authentication_error() -> None:
    with pytest.raises(RepositoryAuthenticationError):
        provider({}, status=401).list_repositories()


def test_provider_accepts_json_book_container_only() -> None:
    repositories = provider({"data": {"repositories": [book_payload(9)]}}).list_repositories()

    assert [repo.id for repo in repositories] == [9]
