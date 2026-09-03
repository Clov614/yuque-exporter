from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path

ensure_src_on_path()

import core.client as client_module  # type: ignore  # noqa: E402
from core.client import ExportType, YuqueClient  # type: ignore  # noqa: E402
from core.models import Repository  # type: ignore  # noqa: E402
from core.repository_resolver import (  # type: ignore  # noqa: E402
    RepositoryAccessDeniedError,
    RepositoryAuthenticationError,
    RepositoryResponseError,
)


class FakeTab:
    user_agent = "test-agent"

    def cookies(self) -> list[dict[str, str]]:
        return [
            {"name": "_yuque_session", "value": "session-secret"},
            {"name": "yuque_ctoken", "value": "csrf-secret"},
            {"name": "api_only", "value": "wrong-host", "domain": "api.yuque.com"},
            {"name": "third_party", "value": "third-party-secret"},
        ]


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}
        self.text = ""

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


class SequenceSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


class DownloadResponse:
    def __init__(
        self,
        status_code: int,
        *,
        location: str | None = None,
        content_length: int = 7,
        chunks: tuple[bytes, ...] = (b"content",),
    ) -> None:
        self.status_code = status_code
        self.headers = {"content-length": str(content_length)}
        if location:
            self.headers["Location"] = location
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size: int):
        return iter(self.chunks)

    def close(self) -> None:
        self.closed = True


class DownloadSession:
    def __init__(self, responses: list[DownloadResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.cookies = type("CookieJar", (), {"clear": lambda _self: None})()

    def get(self, url: str, **kwargs: Any) -> DownloadResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)

    def close(self) -> None:
        return None


def repository_payload() -> dict[str, object]:
    return {
        "data": [
            {
                "id": 42,
                "name": "Favorite Repo",
                "slug": "favorite-repo",
                "namespace": "owner/favorite-repo",
                "items_count": 2,
            }
        ]
    }


def client_with(response: FakeResponse) -> tuple[YuqueClient, FakeSession]:
    client = YuqueClient(FakeTab())
    session = FakeSession(response)
    client.session = session  # type: ignore[assignment]
    return client, session


def test_client_get_repository_uses_browser_session_without_listing() -> None:
    client, session = client_with(FakeResponse(200, repository_payload()))
    client.get_repositories = lambda: pytest.fail("direct resolution must not list repos")  # type: ignore[method-assign]

    repo = client.get_repository(42)

    assert repo.id == 42
    assert repo.user_login == "owner"
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://www.yuque.com/api/books?id=42"
    assert call["cookies"]["_yuque_session"] == "session-secret"
    assert "api_only" not in call["cookies"]
    assert "third_party" not in call["cookies"]
    assert call["headers"]["X-CSRF-Token"] == "csrf-secret"
    assert call["allow_redirects"] is False


def test_cookie_path_matching_requires_a_segment_boundary() -> None:
    cookies = [
        {
            "name": "api_cookie",
            "value": "scoped",
            "domain": ".yuque.com",
            "path": "/api",
        }
    ]

    assert YuqueClient._yuque_cookies(
        cookies,
        "https://www.yuque.com/api/v2/repos/42",
    ) == {"api_cookie": "scoped"}
    assert YuqueClient._yuque_cookies(
        cookies,
        "https://www.yuque.com/apiary",
    ) == {}


def test_client_parses_successful_common_repository_list() -> None:
    payload = {"data": {"books": repository_payload()["data"]}}
    client, session = client_with(FakeResponse(200, payload))

    repositories = client.get_repositories()

    assert [repository.id for repository in repositories] == [42]
    assert session.calls[0]["url"] == YuqueClient.API_COMMON_USED


def test_client_parses_successful_catalog_nodes() -> None:
    client, session = client_with(
        FakeResponse(
            200,
            {
                "data": [
                    {
                        "doc_id": 7,
                        "title": "Document",
                        "url": "document",
                        "uuid": "doc-uuid",
                        "parent_uuid": None,
                        "type": "DOC",
                    }
                ]
            },
        )
    )
    repository = Repository(
        id=42,
        name="Repo",
        slug="repo",
        user_login="owner",
    )

    nodes = client.get_catalog_nodes(repository)

    assert [(node.id, node.book_id, node.parent_uuid) for node in nodes] == [
        (7, 42, "")
    ]
    assert session.calls[0]["params"] == {"book_id": 42, "format": "list"}


def test_client_get_repository_preserves_access_denied_status() -> None:
    client, _ = client_with(FakeResponse(403, {"message": "private"}))

    with pytest.raises(RepositoryAccessDeniedError):
        client.get_repository("owner/private-repo")


def test_client_get_repository_rejects_invalid_json() -> None:
    client, _ = client_with(FakeResponse(200, ValueError("not json")))

    with pytest.raises(RepositoryResponseError):
        client.get_repository("owner/repo")


def test_client_catalog_preserves_authentication_failures() -> None:
    client, _ = client_with(FakeResponse(401, {"message": "expired"}))
    repository = Repository(
        id=42,
        name="Repo",
        slug="repo",
        user_login="owner",
    )

    with pytest.raises(RepositoryAuthenticationError):
        client.get_catalog_nodes(repository)


def test_download_file_drops_cookies_before_external_redirect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    download_session = DownloadSession(
        [
            DownloadResponse(302, location="https://cdn.example/download"),
            DownloadResponse(200),
        ]
    )
    client = YuqueClient(FakeTab())
    client.session = download_session  # type: ignore[assignment]
    monkeypatch.setattr(client, "_public_ip_for_url", lambda _url: "203.0.113.10")
    monkeypatch.setattr(
        client,
        "_external_get",
        lambda url, _ip, headers, timeout: download_session.get(
            url,
            cookies={},
            headers=headers,
            stream=True,
            timeout=(timeout, timeout),
            allow_redirects=False,
        ),
    )

    assert client.download_file(
        "https://www.yuque.com/export/file",
        str(tmp_path / "file.bin"),
    ) is True
    assert download_session.calls[0]["cookies"]["_yuque_session"] == "session-secret"
    assert download_session.calls[1]["cookies"] == {}


def test_download_file_rejects_https_to_http_redirect(tmp_path: Path) -> None:
    download_session = DownloadSession(
        [DownloadResponse(302, location="http://cdn.example/download")]
    )
    client = YuqueClient(FakeTab())
    client.session = download_session  # type: ignore[assignment]

    assert client.download_file(
        "https://www.yuque.com/export/file",
        str(tmp_path / "downgraded.bin"),
    ) is False
    assert len(download_session.calls) == 1
    assert not (tmp_path / "downgraded.bin").exists()


def test_download_file_rejects_non_yuque_initial_url(tmp_path: Path) -> None:
    client = YuqueClient(FakeTab())

    assert client.download_file(
        "https://127.0.0.1/internal",
        str(tmp_path / "blocked.bin"),
    ) is False
    assert not (tmp_path / "blocked.bin").exists()


def test_download_file_rejects_oversized_body_without_partial_file(
    tmp_path: Path,
) -> None:
    response = DownloadResponse(200, content_length=8, chunks=(b"12345678",))
    download_session = DownloadSession([response])
    client = YuqueClient(FakeTab())
    client.session = download_session  # type: ignore[assignment]

    assert client.download_file(
        "https://www.yuque.com/export/file",
        str(tmp_path / "oversized.bin"),
        max_bytes=7,
    ) is False
    assert not (tmp_path / "oversized.bin").exists()


@pytest.mark.parametrize(
    "nodes",
    [
        [
            {"doc_id": 1, "title": "A", "url": "a", "uuid": "dup", "type": "DOC"},
            {"doc_id": 2, "title": "B", "url": "b", "uuid": "dup", "type": "DOC"},
        ],
        [
            {
                "doc_id": 1,
                "title": "A",
                "url": "a",
                "uuid": "a",
                "parent_uuid": "a",
                "type": "DOC",
            }
        ],
    ],
)
def test_client_rejects_duplicate_or_cyclic_catalog(nodes: list[dict[str, object]]) -> None:
    client, _ = client_with(FakeResponse(200, {"data": nodes}))
    repository = Repository(id=42, name="Repo", slug="repo", user_login="owner")

    with pytest.raises(RepositoryResponseError):
        client.get_catalog_nodes(repository)


def test_catalog_rejects_dangling_parent() -> None:
    client, _ = client_with(
        FakeResponse(
            200,
            {
                "data": [
                    {
                        "doc_id": 1,
                        "title": "Orphan",
                        "url": "orphan",
                        "uuid": "orphan",
                        "parent_uuid": "missing",
                        "type": "DOC",
                    }
                ]
            },
        )
    )
    repository = Repository(id=42, name="Repo", slug="repo", user_login="owner")

    with pytest.raises(RepositoryResponseError, match="dangling"):
        client.get_catalog_nodes(repository)


def test_catalog_rejects_excessive_depth() -> None:
    nodes = []
    parent = ""
    for index in range(202):
        uuid = f"node-{index}"
        nodes.append(
            {
                "doc_id": index + 1,
                "title": uuid,
                "url": uuid,
                "uuid": uuid,
                "parent_uuid": parent,
                "type": "DOC",
            }
        )
        parent = uuid
    client, _ = client_with(FakeResponse(200, {"data": nodes}))
    repository = Repository(id=42, name="Repo", slug="repo", user_login="owner")

    with pytest.raises(RepositoryResponseError, match="too deep"):
        client.get_catalog_nodes(repository)


def test_catalog_rejects_excessive_node_count() -> None:
    client, _ = client_with(FakeResponse(200, {"data": [{}] * 10001}))
    repository = Repository(id=42, name="Repo", slug="repo", user_login="owner")

    with pytest.raises(RepositoryResponseError, match="too many"):
        client.get_catalog_nodes(repository)


@pytest.mark.parametrize(
    ("method_name", "payload"),
    [
        ("get_repositories", {"data": {"books": [{}]}}),
        ("get_catalog_nodes", {"data": [{"type": "TITLE"}]}),
    ],
)
def test_client_rejects_malformed_list_entries(
    method_name: str,
    payload: dict[str, object],
) -> None:
    client, _ = client_with(FakeResponse(200, payload))
    repository = Repository(
        id=42,
        name="Repo",
        slug="repo",
        user_login="owner",
    )

    with pytest.raises(RepositoryResponseError):
        if method_name == "get_repositories":
            client.get_repositories()
        else:
            client.get_catalog_nodes(repository)


def _detail_doc(doc_id: int = 7, slug: str = "document", book_id: int = 42):
    from core.models import Document

    return Document(
        id=doc_id,
        title="Document",
        slug=slug,
        uuid="doc-uuid",
        parent_uuid="",
        type="DOC",
        doc_id=doc_id,
        book_id=book_id,
    )


def test_get_document_updated_at_reads_doc_id_path() -> None:
    client, session = client_with(
        FakeResponse(200, {"data": {"id": 7, "updated_at": "2026-09-01T00:00:00Z"}})
    )

    assert client.get_document_updated_at(_detail_doc()) == "2026-09-01T00:00:00Z"
    assert session.calls[0]["url"] == "https://www.yuque.com/api/docs/7"
    assert session.calls[0]["params"] == {"book_id": 42}


def test_get_document_updated_at_falls_back_to_content_updated_at() -> None:
    client, _ = client_with(
        FakeResponse(200, {"data": {"id": 7, "content_updated_at": "2026-09-02T00:00:00Z"}})
    )

    assert client.get_document_updated_at(_detail_doc()) == "2026-09-02T00:00:00Z"


def test_get_document_updated_at_returns_none_without_timestamps() -> None:
    client, _ = client_with(FakeResponse(200, {"data": {"id": 7}}))

    assert client.get_document_updated_at(_detail_doc()) is None


def test_get_document_updated_at_returns_none_on_http_error() -> None:
    client, _ = client_with(FakeResponse(404, {"message": "not found"}))

    assert client.get_document_updated_at(_detail_doc()) is None


def test_get_document_updated_at_returns_none_on_invalid_payload() -> None:
    client, _ = client_with(FakeResponse(200, {"data": [1, 2, 3]}))

    assert client.get_document_updated_at(_detail_doc()) is None


def test_get_document_updated_at_retries_with_slug() -> None:
    client = YuqueClient(FakeTab())
    session = SequenceSession(
        [
            FakeResponse(404, {"message": "not found"}),
            FakeResponse(200, {"data": {"id": 7, "updated_at": "2026-09-03T00:00:00Z"}}),
        ]
    )
    client.session = session  # type: ignore[assignment]

    assert client.get_document_updated_at(_detail_doc()) == "2026-09-03T00:00:00Z"
    assert session.calls[0]["url"] == "https://www.yuque.com/api/docs/7"
    assert session.calls[1]["url"] == "https://www.yuque.com/api/docs/document"


def _create_repo() -> Repository:
    return Repository(id=42, name="Existing", slug="existing-book", user_login="tester")


def test_create_markdown_document_uses_docs_protocol() -> None:
    catalog = FakeResponse(200, {"data": []})
    created = FakeResponse(200, {"data": {"id": 777, "slug": "note", "title": "Note"}})
    client = YuqueClient(FakeTab())
    session = SequenceSession([catalog, created])
    client.session = session  # type: ignore[assignment]
    repo = _create_repo()

    doc = client.create_markdown_document(repo, "Note", "# Note\n")

    assert doc.id == 777
    assert doc.slug == "note"
    assert client.document_url(repo, doc) == "https://www.yuque.com/tester/existing-book/note"
    call = session.calls[1]
    assert call["method"] == "POST"
    assert call["url"] == "https://www.yuque.com/api/docs"
    assert call["json"]["book_id"] == 42
    assert call["json"]["format"] == "markdown"
    assert call["json"]["type"] == "Doc"
    assert "insert_to_catalog" not in call["json"]


def test_create_markdown_document_maps_auth_and_rejects_bad_payload() -> None:
    repo = _create_repo()
    catalog = FakeResponse(200, {"data": []})
    client = YuqueClient(FakeTab())
    client.session = SequenceSession(  # type: ignore[assignment]
        [catalog, FakeResponse(401, {"message": "Unauthorized"})]
    )
    with pytest.raises(RepositoryAuthenticationError):
        client.create_markdown_document(repo, "Note", "body")

    client = YuqueClient(FakeTab())
    client.session = SequenceSession(  # type: ignore[assignment]
        [catalog, FakeResponse(200, {"data": {"id": "bad"}})]
    )
    with pytest.raises(RepositoryResponseError):
        client.create_markdown_document(repo, "Note", "body")


def test_create_markdown_document_mounts_to_catalog() -> None:
    repo = _create_repo()
    catalog = FakeResponse(
        200,
        {"data": [{"uuid": "root-uuid", "parent_uuid": "", "type": "DOC",
                   "title": "Old", "doc_id": 1, "id": 1, "url": "old"}]},
    )
    created = FakeResponse(200, {"data": {"id": 778, "slug": "new", "title": "New"}})
    client = YuqueClient(FakeTab())
    session = SequenceSession([catalog, created])
    client.session = session  # type: ignore[assignment]

    doc = client.create_markdown_document(repo, "New", "# New\n")

    assert doc.id == 778
    create_call = session.calls[1]
    assert create_call["json"]["insert_to_catalog"] is True
    assert create_call["json"]["target_uuid"] == "root-uuid"
    assert create_call["json"]["action"] == "insert"


def test_create_repository_uses_books_protocol() -> None:
    client, session = client_with(
        FakeResponse(200, {"data": {"id": 99, "name": "New", "slug": "new",
                                    "user": {"login": "tester"}}})
    )

    repo = client.create_repository(name="New", slug="new", description="d",
                                    visibility="private")

    assert repo.id == 99
    assert repo.user_login == "tester"
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://www.yuque.com/api/books"
    assert call["json"]["name"] == "New"
    assert call["json"]["public"] == 0


def test_create_repository_rejects_team_without_protocol_call() -> None:
    client, session = client_with(
        FakeResponse(200, {"data": {"id": 99, "name": "New", "slug": "new",
                                    "user": {"login": "tester"}}})
    )

    with pytest.raises(RepositoryResponseError, match="team visibility"):
        client.create_repository(name="New", slug="new", description="d",
                                 visibility="team")

    assert session.calls == []
