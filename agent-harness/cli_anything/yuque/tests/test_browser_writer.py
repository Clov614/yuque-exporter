from __future__ import annotations

from pathlib import Path

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path

ensure_src_on_path()

from core.browser_writer import YuqueBrowserWriter  # type: ignore  # noqa: E402
from core.markdown_input import read_markdown  # type: ignore  # noqa: E402
from core.models import Repository  # type: ignore  # noqa: E402
from core.mutation_errors import MutationAuthenticationError  # type: ignore  # noqa: E402


class FakeElement:
    def __init__(self, page: "FakePage", action=None) -> None:
        self.page = page
        self.action = action

    def click(self) -> None:
        self.page.actions.append("click")
        if self.action:
            self.action()

    def input(self, value: str) -> None:
        self.page.actions.append(("input", value))


class FakePage:
    def __init__(self, elements: set[str]) -> None:
        self.elements = elements
        self.repo_mode = "text:新建知识库" in elements
        self.actions: list[object] = []
        self.url = "https://www.yuque.com/dashboard"
        self.wait = type("Wait", (), {"load_start": lambda _self: None})()

    def get(self, url: str) -> None:
        self.actions.append(("get", url))

    def ele(self, selector: str, timeout: float = 0):
        if selector in self.elements:
            if "submit" in selector or "导入" in selector or "创建" in selector:
                return FakeElement(self, self._complete)
            return FakeElement(self)
        raise LookupError(selector)

    def _complete(self) -> None:
        if self.repo_mode:
            self.url = "https://www.yuque.com/tester/new-book"
        else:
            self.url = "https://www.yuque.com/tester/existing-book/imported-doc"


def test_create_repository_uses_semantic_page_controls() -> None:
    page = FakePage(
        {
            "text:新建知识库",
            "css:input[name='name']",
            "css:input[name='slug']",
            "css:textarea[name='description']",
            "text:创建知识库",
        }
    )

    namespace = YuqueBrowserWriter(page).create_repository(
        name="New Book", slug="new-book", description="Description", visibility="private"
    )

    assert namespace == "tester/new-book"
    assert ("get", "https://www.yuque.com/dashboard") in page.actions


def test_import_markdown_returns_confirmed_document_url(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("# Note\n\nbody", encoding="utf-8")
    page = FakePage(
        {
            "text:新建文档",
            "text:导入",
            "css:input[type='file']",
            "text:Markdown",
            "text:开始导入",
        }
    )
    repo = Repository(id=42, name="Existing", slug="existing-book", user_login="tester")

    url = YuqueBrowserWriter(page).import_markdown(repo, read_markdown(path))

    assert url == "https://www.yuque.com/tester/existing-book/imported-doc"
    assert ("input", str(path.resolve())) in page.actions


def test_writer_fails_closed_when_page_is_not_authenticated() -> None:
    page = FakePage({"text:新建知识库"})
    page.url = "https://www.yuque.com/login"

    with pytest.raises(MutationAuthenticationError, match="authenticated"):
        YuqueBrowserWriter(page).create_repository(
            name="New Book", slug="new-book", description="", visibility="private"
        )
