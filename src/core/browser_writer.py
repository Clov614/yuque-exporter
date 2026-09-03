from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .markdown_input import MarkdownDocument
from .models import Repository
from .mutation_errors import (
    MutationAccessError,
    MutationAuthenticationError,
    MutationConflictError,
    MutationProtocolError,
)


YUQUE_HOSTS = frozenset({"yuque.com", "www.yuque.com"})


class YuqueBrowserWriter:
    """Drive Yuque's visible write flows through an authenticated page.

    This adapter deliberately avoids undocumented HTTP mutations. Selectors are
    kept in this module so a UI change has one fail-closed repair point.
    """

    DASHBOARD_URL = "https://www.yuque.com/dashboard"
    _CREATE_REPOSITORY_BUTTONS = ("text:新建知识库", "text:新建空间")
    _CREATE_REPOSITORY_SUBMIT = ("text:创建知识库", "text:创建")
    _CREATE_DOCUMENT_BUTTONS = ("text:新建文档", "text:新建")
    _IMPORT_BUTTONS = ("text:导入", "text:导入文档")
    _MARKDOWN_BUTTONS = ("text:Markdown", "text:Markdown 文件")
    _IMPORT_SUBMIT = ("text:开始导入", "text:确认导入", "text:导入")

    def __init__(self, page: Any) -> None:
        self.page = page

    def create_repository(
        self,
        *,
        name: str,
        slug: str | None,
        description: str,
        visibility: str,
    ) -> str:
        self._open_authenticated(self.DASHBOARD_URL)
        self._click(self._CREATE_REPOSITORY_BUTTONS, "new repository")
        self._input(("css:input[name='name']", "css:input[placeholder*='名称']"), name, "name")
        if slug:
            self._input(("css:input[name='slug']", "css:input[placeholder*='slug']"), slug, "slug")
        if description:
            self._input(
                ("css:textarea[name='description']", "css:textarea"),
                description,
                "description",
            )
        if visibility == "public":
            self._click(("text:公开", "css:input[value='public']"), "public visibility")
        elif visibility == "team":
            self._click(("text:组织内公开", "text:团队可见"), "team visibility")
        self._click(self._CREATE_REPOSITORY_SUBMIT, "create repository")
        self._wait_for_page()
        return self._namespace_from_url("created repository")

    def import_markdown(self, repository: Repository, document: MarkdownDocument) -> str:
        self._open_authenticated(repository.url)
        self._click(self._CREATE_DOCUMENT_BUTTONS, "new document")
        self._click(self._IMPORT_BUTTONS, "Markdown import")
        self._click(self._MARKDOWN_BUTTONS, "Markdown format")
        self._upload(document.path)
        self._click(self._IMPORT_SUBMIT, "Markdown import submit")
        self._wait_for_page()
        return self._document_url_from_page("imported document")

    def _open_authenticated(self, url: str) -> None:
        self._assert_authenticated()
        try:
            self.page.get(url)
            self._wait_for_page()
        except MutationProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MutationProtocolError("unable to open Yuque write page") from exc
        self._assert_authenticated()

    def _assert_authenticated(self) -> None:
        current = str(getattr(self.page, "url", ""))
        parsed = urlparse(current)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in YUQUE_HOSTS:
            raise MutationProtocolError("Yuque page is not authenticated")
        if parsed.path.rstrip("/") in {"", "/login"} or parsed.path.startswith("/login/"):
            raise MutationAuthenticationError("Yuque browser session is not authenticated")

    def _wait_for_page(self) -> None:
        wait = getattr(self.page, "wait", None)
        load_start = getattr(wait, "load_start", None)
        if callable(load_start):
            try:
                load_start()
            except Exception as exc:  # noqa: BLE001
                raise MutationProtocolError("Yuque page did not finish loading") from exc

    def _find(self, selectors: Iterable[str], label: str) -> Any:
        for selector in selectors:
            try:
                element = self.page.ele(selector, timeout=1)
            except Exception:  # noqa: BLE001
                continue
            if element is not None:
                return element
        raise MutationProtocolError(f"Yuque page does not expose {label} control")

    def _click(self, selectors: Iterable[str], label: str) -> None:
        element = self._find(selectors, label)
        try:
            element.click()
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if "conflict" in message or "duplicate" in message:
                raise MutationConflictError(f"Yuque rejected duplicate {label}") from exc
            if "permission" in message or "forbidden" in message:
                raise MutationAccessError(f"Yuque denied {label}") from exc
            raise MutationProtocolError(f"unable to activate {label} control") from exc

    def _input(self, selectors: Iterable[str], value: str, label: str) -> None:
        element = self._find(selectors, label)
        try:
            element.input(value)
        except Exception as exc:  # noqa: BLE001
            raise MutationProtocolError(f"unable to fill Yuque {label}") from exc

    def _upload(self, path: Path) -> None:
        element = self._find(("css:input[type='file']", "tag:input@type=file"), "file upload")
        try:
            element.input(str(path))
        except Exception as exc:  # noqa: BLE001
            raise MutationProtocolError("unable to select Markdown file") from exc

    def _namespace_from_url(self, label: str) -> str:
        parsed = self._validated_url(label)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            raise MutationProtocolError(f"Yuque did not confirm {label}")
        return "/".join(parts)

    def _document_url_from_page(self, label: str) -> str:
        parsed = self._validated_url(label)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 3:
            raise MutationProtocolError(f"Yuque did not confirm {label}")
        return f"https://www.yuque.com/{'/'.join(parts)}"

    def _validated_url(self, label: str):
        current = str(getattr(self.page, "url", ""))
        parsed = urlparse(current)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in YUQUE_HOSTS:
            raise MutationProtocolError(f"Yuque did not confirm {label}")
        if parsed.query or parsed.fragment:
            raise MutationProtocolError(f"Yuque returned an ambiguous {label} URL")
        return parsed
