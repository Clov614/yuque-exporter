from __future__ import annotations

import json
from typing import Any

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

from core.client import YuqueClient  # type: ignore  # noqa: E402
from core.favorite_repository_provider import FavoriteRepositoryUnavailableError  # type: ignore  # noqa: E402


class Tab:
    user_agent = "test-agent"

    def cookies(self) -> list[dict[str, str]]:
        return []


class Response:
    status_code = 200
    encoding = "utf-8"

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        text = json.dumps(payload) if not isinstance(payload, str) else payload
        self.body = text.encode("utf-8")
        self.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(self.body)),
        }

    def json(self) -> Any:
        return self.payload

    def iter_content(self, chunk_size: int):
        yield self.body


class Session:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        return Response(self.payload)


def test_client_lists_favorite_repositories_without_common_used(monkeypatch: pytest.MonkeyPatch) -> None:
    client = YuqueClient(Tab())
    session = Session({"data": {"books": [{
        "id": 7,
        "type": "Book",
        "name": "Book",
        "slug": "book",
        "namespace": "owner/book",
        "user": {"login": "owner"},
    }]}})
    client.session = session  # type: ignore[assignment]
    monkeypatch.setattr(client, "get_repositories", lambda: pytest.fail("must not use common_used"))

    repositories = client.get_favorite_repositories()

    assert [repo.id for repo in repositories] == [7]
    assert session.calls[0]["url"] == "https://www.yuque.com/dashboard/collections"


def test_client_maps_favorite_source_failure() -> None:
    client = YuqueClient(Tab())
    session = Session({"data": {"unknown": []}})
    client.session = session  # type: ignore[assignment]

    with pytest.raises(FavoriteRepositoryUnavailableError):
        client.get_favorite_repositories()
