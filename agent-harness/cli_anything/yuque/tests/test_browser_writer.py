from __future__ import annotations

from pathlib import Path

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path

ensure_src_on_path()

from core.browser_writer import YuqueBrowserWriter  # type: ignore  # noqa: E402
from core.markdown_input import read_markdown  # type: ignore  # noqa: E402
from core.models import Repository  # type: ignore  # noqa: E402
from core.mutation_errors import (  # type: ignore  # noqa: E402
    MutationAuthenticationError,
    MutationProtocolError,
)


class FakeStates:
    def __init__(
        self,
        displayed: bool = True,
        enabled: bool = True,
        in_viewport: bool = True,
    ) -> None:
        self.is_displayed = displayed
        self.is_enabled = enabled
        self.is_in_viewport = in_viewport
        self.has_rect = displayed


class FakeWait:
    def clickable(self, timeout: float | None = None) -> bool:
        return True

    def displayed(self, timeout: float | None = None) -> bool:
        return True


class FakeScroll:
    def to_see(self, center: bool | None = None) -> None:
        return None


class FakeElement:
    def __init__(
        self,
        page: "FakePage",
        action=None,
        *,
        states: FakeStates | None = None,
        click_error: Exception | None = None,
    ) -> None:
        self.page = page
        self.action = action
        self.states = states or FakeStates()
        self.wait = FakeWait()
        self.scroll = FakeScroll()
        self._click_error = click_error

    def click(self) -> None:
        if self._click_error is not None:
            raise self._click_error
        self.page.actions.append("click")
        if self.action:
            self.action()

    def input(self, value: str) -> None:
        self.page.actions.append(("input", value))


class FakePage:
    def __init__(
        self,
        elements: set[str],
        *,
        hidden: set[str] | None = None,
        click_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.elements = elements
        self.hidden = hidden or set()
        self.click_errors = click_errors or {}
        self.repo_mode = "text:新建知识库" in elements
        self.actions: list[object] = []
        self.url = "https://www.yuque.com/dashboard"
        self.wait = type("Wait", (), {"load_start": lambda _self: None})()

    def get(self, url: str) -> None:
        self.actions.append(("get", url))

    def _make_element(self, selector: str) -> FakeElement:
        action = None
        if "submit" in selector or "导入" in selector or "创建" in selector:
            action = self._complete
        states = FakeStates(displayed=selector not in self.hidden)
        return FakeElement(
            self,
            action,
            states=states,
            click_error=self.click_errors.get(selector),
        )

    def ele(self, selector: str, timeout: float = 0):
        if selector in self.elements:
            return self._make_element(selector)
        raise LookupError(selector)

    def eles(self, selector: str, timeout: float = 0):
        if selector in self.elements:
            return [self._make_element(selector)]
        return []

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


def test_click_skips_hidden_candidates_for_broad_text_selector() -> None:
    page = FakePage(
        {
            "text:新建文档",
            "text:导入",
            "css:input[type='file']",
            "text:Markdown",
            "text:开始导入",
        }
    )
    real_eles = page.eles

    def _eles_with_hidden_first(selector: str, timeout: float = 0):
        if selector == "text:新建文档":
            return [
                FakeElement(page, states=FakeStates(displayed=False)),
                FakeElement(page, states=FakeStates(displayed=True)),
            ]
        return real_eles(selector, timeout=timeout)

    page.eles = _eles_with_hidden_first  # type: ignore[method-assign]
    repo = Repository(id=42, name="Existing", slug="existing-book", user_login="tester")

    writer = YuqueBrowserWriter(page)
    element = writer._find(writer._CREATE_DOCUMENT_BUTTONS, "new document")

    assert element.states.is_displayed is True


def test_click_failure_keeps_cause_and_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("# Note\n\nbody", encoding="utf-8")
    page = FakePage(
        {
            "text:新建文档",
            "text:导入",
            "css:input[type='file']",
            "text:Markdown",
            "text:开始导入",
        },
        click_errors={"text:新建文档": RuntimeError("CanNotClickError: covered")},
    )
    repo = Repository(id=42, name="Existing", slug="existing-book", user_login="tester")

    with pytest.raises(MutationProtocolError, match="unable to activate new document") as exc_info:
        YuqueBrowserWriter(page).import_markdown(repo, read_markdown(path))

    assert exc_info.value.__cause__ is not None
    assert "state=" in str(exc_info.value)
    assert "url=" in str(exc_info.value)


class NoneElementStub:
    """Mimic DrissionPage NoneElement: falsy, but .states raises."""

    def __bool__(self) -> bool:
        return False

    def __getattr__(self, name: str):
        raise LookupError(f"no {name}")


def test_find_skips_none_element_without_leaking() -> None:
    page = FakePage({"text:新建文档"})
    page.ele = lambda selector, timeout=0: NoneElementStub()  # type: ignore[method-assign]
    page.eles = lambda selector, timeout=0: []  # type: ignore[method-assign]

    with pytest.raises(MutationProtocolError, match="does not expose"):
        YuqueBrowserWriter(page)._find(("css:input[name='name']",), "name")

    assert YuqueBrowserWriter._is_missing(NoneElementStub()) is True
    assert YuqueBrowserWriter(page)._describe(NoneElementStub()) == "missing"
