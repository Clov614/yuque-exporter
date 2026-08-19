from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

from core.repository_reference import RepositoryReference  # type: ignore  # noqa: E402
from core.repository_resolver import (  # type: ignore  # noqa: E402
    RepositoryAccessDeniedError,
    RepositoryAuthenticationError,
    RepositoryHttpResult,
    RepositoryNotFoundError,
    RepositoryResolver,
    RepositoryResponseError,
    RepositoryTransportError,
)


class FakeRequester:
    def __init__(
        self,
        result: RepositoryHttpResult | Exception,
        fallbacks: dict[str, RepositoryHttpResult] | None = None,
    ) -> None:
        self.result = result
        self.fallbacks = fallbacks or {}
        self.urls: list[str] = []

    def __call__(self, url: str) -> RepositoryHttpResult:
        self.urls.append(url)
        if url in self.fallbacks:
            return self.fallbacks[url]
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def repository_data() -> dict[str, object]:
    return {
        "id": 42,
        "name": "Favorite Repo",
        "slug": "repo-slug",
        "namespace": "owner-login/repo-slug",
        "items_count": 3,
        "public": 1,
    }


def collection_payload(*items: object) -> dict[str, object]:
    return {"data": list(items)}


def resolve_with(
    result: RepositoryHttpResult | Exception,
    reference: int | str = 42,
    fallbacks: dict[str, RepositoryHttpResult] | None = None,
) -> tuple[object, FakeRequester]:
    requester = FakeRequester(result, fallbacks)
    resolver = RepositoryResolver(requester)
    return resolver.resolve(RepositoryReference.parse(reference)), requester


def encoded_app_data(book: dict[str, object]) -> str:
    import json

    app_data = quote(json.dumps({"book": book}, ensure_ascii=False), safe="")
    return f'<script>window.appData = JSON.parse(decodeURIComponent("{app_data}"))</script>'


def test_resolver_fetches_repository_by_numeric_id() -> None:
    repo, requester = resolve_with(
        RepositoryHttpResult(200, collection_payload(repository_data()))
    )

    assert requester.urls == ["https://www.yuque.com/api/books?id=42"]
    assert repo.id == 42
    assert repo.name == "Favorite Repo"
    assert repo.user_login == "owner-login"


def test_resolver_percent_encodes_namespace_query() -> None:
    repo_data = {
        **repository_data(),
        "id": 43,
        "slug": "知识库",
        "namespace": "所有者/知识库",
    }
    repo, requester = resolve_with(
        RepositoryHttpResult(200, collection_payload(repo_data)),
        "所有者/知识库",
    )

    assert requester.urls == [
        "https://www.yuque.com/api/books?namespace=%E6%89%80%E6%9C%89%E8%80%85%2F%E7%9F%A5%E8%AF%86%E5%BA%93"
    ]
    assert repo.id == 43


def test_resolver_accepts_target_wrapped_collection_item() -> None:
    repo, _ = resolve_with(
        RepositoryHttpResult(
            200,
            collection_payload({"target": repository_data()}),
        )
    )

    assert repo.id == 42
    assert repo.user_login == "owner-login"


def test_namespace_falls_back_to_verified_page_app_data() -> None:
    page_url = "https://www.yuque.com/owner-login/repo-slug"
    repo, requester = resolve_with(
        RepositoryHttpResult(200, collection_payload()),
        "owner-login/repo-slug",
        {
            page_url: RepositoryHttpResult(
                200,
                encoded_app_data(repository_data()),
                "text/html; charset=utf-8",
            )
        },
    )

    assert requester.urls == [
        "https://www.yuque.com/api/books?namespace=owner-login%2Frepo-slug",
        page_url,
    ]
    assert repo.id == 42


def test_numeric_id_without_authoritative_metadata_fails_explicitly() -> None:
    with pytest.raises(RepositoryNotFoundError, match="use owner/slug"):
        resolve_with(
            RepositoryHttpResult(200, collection_payload()),
            99,
        )


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [
        (401, RepositoryAuthenticationError),
        (403, RepositoryAccessDeniedError),
        (404, RepositoryNotFoundError),
        (429, RepositoryTransportError),
        (500, RepositoryTransportError),
    ],
)
def test_resolver_maps_remote_statuses(
    status: int,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        resolve_with(RepositoryHttpResult(status, {"secret": "cookie-value"}))


def test_resolver_rejects_invalid_response_without_leaking_payload() -> None:
    with pytest.raises(RepositoryResponseError) as captured:
        resolve_with(RepositoryHttpResult(200, {"data": {"token": "secret-token"}}))

    assert "secret-token" not in str(captured.value)
    assert "token" not in str(captured.value).lower()


@pytest.mark.parametrize(
    "payload",
    [
        collection_payload(
            {"id": "42", "name": "Repo", "slug": "repo", "user": {"login": "owner"}}
        ),
        collection_payload({"id": 42, "name": "Repo", "slug": "repo", "user": None}),
        collection_payload(
            {"id": 42, "name": 3, "slug": "repo", "user": {"login": "owner"}}
        ),
        collection_payload(
            {
                "id": 42,
                "name": "Repo",
                "slug": "repo",
                "user": {"login": "owner"},
                "items_count": {},
            }
        ),
        collection_payload(
            {
                "id": 42,
                "name": "Repo",
                "slug": "repo",
                "user": {"login": "owner"},
                "public": "yes",
            }
        ),
        collection_payload(
            {
                "id": 42,
                "name": "Repo",
                "slug": "repo",
                "user": {"login": "owner"},
                "description": 7,
            }
        ),
    ],
)
def test_resolver_rejects_malformed_repository_fields(payload: dict[str, object]) -> None:
    with pytest.raises(RepositoryResponseError):
        resolve_with(RepositoryHttpResult(200, payload))


def test_resolver_maps_request_exception_to_transport_error() -> None:
    requester: Callable[[str], RepositoryHttpResult] = FakeRequester(
        RuntimeError("cookie=secret-cookie")
    )

    with pytest.raises(RepositoryTransportError) as captured:
        RepositoryResolver(requester).resolve(RepositoryReference.parse(42))

    assert "secret-cookie" not in str(captured.value)
