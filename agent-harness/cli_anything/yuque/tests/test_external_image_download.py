from __future__ import annotations

import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
import requests

from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

from core.client import _PinnedHTTPAdapter, YuqueClient  # type: ignore  # noqa: E402


class FakeTab:
    user_agent = "test-agent"

    def cookies(self) -> list[dict[str, str]]:
        return [{"name": "_yuque_session", "value": "must-not-leak"}]


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content_type: str = "image/png",
        content_length: str | None = None,
        chunks: tuple[bytes, ...] = (b"image",),
        iteration_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self._chunks = chunks
        self._iteration_error = iteration_error

    def iter_content(self, chunk_size: int) -> Any:
        assert chunk_size > 0
        for chunk in self._chunks:
            yield chunk
        if self._iteration_error:
            raise self._iteration_error


class RecordingSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        return self.response


def _public_dns(_host: str, *_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def _tun_fake_ip_dns(_host: str, *_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 443))]


def _client_with(session: RecordingSession) -> YuqueClient:
    client = YuqueClient(FakeTab())
    client.external_session = session
    return client


def test_download_external_image_uses_no_yuque_cookie(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    session = RecordingSession(FakeResponse(content_length="5"))
    destination = tmp_path / "image.png"

    ok = _client_with(session).download_external_image(
        "https://images.example.test/image.png", destination, max_bytes=1024
    )

    assert ok is True
    assert destination.read_bytes() == b"image"
    assert len(session.calls) == 1
    _url, request = session.calls[0]
    assert request["cookies"] == {}
    assert "Cookie" not in request["headers"]


@pytest.mark.parametrize(
    ("url", "resolved_address"),
    [
        ("ftp://images.example.test/image.png", "93.184.216.34"),
        ("https://user:password@images.example.test/image.png", "93.184.216.34"),
        ("http://127.0.0.1/image.png", "127.0.0.1"),
        ("http://[::1]/image.png", "::1"),
        ("https://internal.example.test/image.png", "10.0.0.8"),
    ],
)
def test_download_external_image_rejects_unsafe_urls_before_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    url: str,
    resolved_address: str,
) -> None:
    def resolve_to_test_address(_host: str, *_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
        family = socket.AF_INET6 if ":" in resolved_address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (resolved_address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve_to_test_address)
    session = RecordingSession(FakeResponse())
    destination = tmp_path / "image.png"

    ok = _client_with(session).download_external_image(url, destination, max_bytes=1024)

    assert ok is False
    assert session.calls == []
    assert not destination.exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.nlark.com/yuque/0/2026/png.png",
        "https://intranetproxy.alipay.com/image.png",
        "https://cdn.alipayobjects.com/image.png",
        "https://img.yuque.com/image.png",
        "https://yuque.antfin.com/image.png",
        "https://lark-assets-prod-aliyun.oss-cn-hangzhou.aliyuncs.com/image.png",
    ],
    ids=["nlark", "alipay", "alipayobjects", "yuque", "yuque-antfin", "yuque-oss"],
)
def test_public_ip_for_url_allows_trusted_yuque_image_domains_with_tun_fake_ip(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _tun_fake_ip_dns)

    assert YuqueClient._public_ip_for_url(url) == "198.18.0.1"


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/image.png",
        "https://cdn.nlark.com.attacker.example/image.png",
        "https://attacker.yuque.antfin.com/image.png",
        "https://attacker.lark-assets-prod-aliyun.oss-cn-hangzhou.aliyuncs.com/image.png",
    ],
    ids=["unrelated-domain", "trusted-domain-suffix", "antfin-subdomain", "oss-subdomain"],
)
def test_public_ip_for_url_rejects_tun_fake_ip_for_untrusted_domain(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _tun_fake_ip_dns)

    assert YuqueClient._public_ip_for_url(url) is None


def test_public_ip_for_url_rejects_tun_fake_ip_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _tun_fake_ip_dns)

    assert YuqueClient._public_ip_for_url("https://198.18.12.34/image.png") is None


@pytest.mark.parametrize(
    "resolved_address",
    ["127.0.0.1", "10.0.0.8", "192.168.0.1", "::1"],
    ids=["loopback-ipv4", "private-10", "private-192", "loopback-ipv6"],
)
def test_public_ip_for_url_rejects_loopback_and_private_addresses(
    monkeypatch: pytest.MonkeyPatch, resolved_address: str
) -> None:
    def resolve_to_test_address(_host: str, *_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
        family = socket.AF_INET6 if ":" in resolved_address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (resolved_address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve_to_test_address)

    assert YuqueClient._public_ip_for_url("https://images.example.test/image.png") is None


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(content_type="text/html", content_length="5"),
        FakeResponse(content_length="4096"),
        FakeResponse(content_length=None, chunks=(b"123", b"456")),
        FakeResponse(content_length="5", iteration_error=OSError("connection dropped")),
    ],
    ids=["non-image-content-type", "declared-too-large", "stream-too-large", "stream-failure"],
)
def test_download_external_image_removes_temporary_files_after_validation_or_transfer_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    response: FakeResponse,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    session = RecordingSession(response)
    destination = tmp_path / "image.png"

    ok = _client_with(session).download_external_image(
        "https://images.example.test/image.png", destination, max_bytes=5
    )

    assert ok is False
    assert len(session.calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_pinned_http_adapter_connects_to_validated_ip() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args: Any) -> None:
            return None

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        session = requests.Session()
        session.trust_env = False
        session.mount("http://", _PinnedHTTPAdapter("127.0.0.1"))
        response = session.get(
            f"http://example.test:{server.server_port}/", timeout=5
        )
        assert response.status_code == 200
        assert response.content == b"ok"
    finally:
        server.shutdown()
        thread.join(timeout=5)
