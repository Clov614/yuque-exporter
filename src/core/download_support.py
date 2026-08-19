"""Bounded export downloads with safe redirect and cookie handling."""

from __future__ import annotations

import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

import requests


class ExportDownloadMixin:
    """Reusable export download behavior for YuqueClient."""

    def download_file(
        self,
        url: str,
        save_path: str,
        progress_callback: Optional[Any] = None,
        *,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> bool:
        """Download an export with bounded, credential-safe redirects."""
        if max_bytes <= 0 or not self._is_yuque_https_url(url):
            return False

        response = None
        deadline = time.monotonic() + self.MAX_EXPORT_DOWNLOAD_SECONDS
        try:
            current_url = url
            headers = {
                "User-Agent": self.tab.user_agent,
                "Referer": "https://www.yuque.com/",
            }
            for _ in range(self.MAX_IMAGE_REDIRECTS + 1):
                response = self._request_download(current_url, headers, deadline)
                if not 300 <= response.status_code < 400:
                    break
                location = response.headers.get("Location") or response.headers.get("location")
                if not location:
                    return False
                current_url = urljoin(current_url, location)
                response.close()
                response = None
            else:
                return False

            if response.status_code != 200:
                print(f"❌ 下载请求失败: {response.status_code}")
                return False
            return self._save_download_response(
                response,
                Path(save_path),
                progress_callback,
                max_bytes,
                deadline,
            )
        except (OSError, requests.RequestException, socket.error, ValueError):
            print("❌ 下载请求失败")
            return False
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()

    def _request_download(
        self,
        url: str,
        headers: Dict[str, str],
        deadline: float,
    ) -> Any:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise ValueError("download deadline exceeded")
        timeout_seconds = max(1.0, min(60.0, remaining_seconds))
        if urlparse(url).scheme.lower() != "https":
            raise ValueError("download redirects must use HTTPS")
        if self._is_yuque_https_url(url):
            cookies = self._yuque_cookies(self.tab.cookies(), url)
            return self.session.get(
                url,
                cookies=cookies,
                headers=headers,
                stream=True,
                timeout=(timeout_seconds, timeout_seconds),
                allow_redirects=False,
            )

        pinned_ip = self._public_ip_for_url(url)
        if pinned_ip is None:
            raise ValueError("unsafe external download URL")
        return self._external_get(url, pinned_ip, headers, timeout_seconds)

    @staticmethod
    def _is_yuque_https_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            return (
                parsed.scheme.lower() == "https"
                and parsed.hostname in {"yuque.com", "www.yuque.com"}
                and parsed.username is None
                and parsed.password is None
                and parsed.port is None
            )
        except (ValueError, UnicodeError):
            return False

    @staticmethod
    def _save_download_response(
        response: Any,
        save_path: Path,
        progress_callback: Optional[Any],
        max_bytes: int,
        deadline: float,
    ) -> bool:
        content_length = response.headers.get("content-length")
        if content_length:
            declared_size = int(content_length)
            if declared_size > max_bytes:
                return False
            if progress_callback and declared_size > 0:
                progress_callback(0, declared_size)

        temporary_path: Optional[Path] = None
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=save_path.parent,
                prefix=f".{save_path.name}.",
                suffix=".part",
                delete=False,
            ) as temp_file:
                temporary_path = Path(temp_file.name)
                total_size = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if time.monotonic() >= deadline:
                        return False
                    if not chunk:
                        continue
                    total_size += len(chunk)
                    if total_size > max_bytes:
                        return False
                    temp_file.write(chunk)
                    if progress_callback:
                        progress_callback(len(chunk), None)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            if total_size == 0:
                return False
            os.replace(temporary_path, save_path)
            temporary_path = None
            return True
        finally:
            if temporary_path and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
