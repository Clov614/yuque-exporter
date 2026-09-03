from __future__ import annotations

from pathlib import Path

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

import utils.browser as browser_module  # type: ignore  # noqa: E402
from utils.browser import BrowserManager  # type: ignore  # noqa: E402


class FakeOptions:
    instances: list["FakeOptions"] = []

    def __init__(self) -> None:
        self.arguments: list[str] = []
        self.user_data_path: str | None = None
        self.auto_port_value: bool | None = None
        self.headless_value: bool | None = None
        self.muted = False
        self.instances.append(self)

    def set_user_data_path(self, value: str) -> None:
        self.user_data_path = value

    def auto_port(self, value: bool) -> None:
        self.auto_port_value = value

    def set_argument(self, value: str) -> None:
        self.arguments.append(value)

    def mute(self, value: bool) -> None:
        self.muted = value

    def headless(self, value: bool) -> None:
        self.headless_value = value


class FakeWindow:
    def __init__(self) -> None:
        self.maximized = False

    def max(self) -> None:
        self.maximized = True


class FakeSetter:
    def __init__(self) -> None:
        self.window = FakeWindow()


class FakePage:
    instances: list["FakePage"] = []

    def __init__(self, options: FakeOptions) -> None:
        self.options = options
        self.url = "about:blank"
        self.set = FakeSetter()
        self.quit_called = False
        self.instances.append(self)

    def quit(self) -> None:
        self.quit_called = True


def test_browser_manager_uses_isolated_profile_and_keeps_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    FakeOptions.instances = []
    FakePage.instances = []
    monkeypatch.setattr(browser_module, "ChromiumOptions", FakeOptions)
    monkeypatch.setattr(browser_module, "ChromiumPage", FakePage)
    profile_dir = tmp_path / "profile-browser"

    manager = BrowserManager(user_data_dir=profile_dir)
    page = manager.start(headless=True)

    options = FakeOptions.instances[0]
    assert options.user_data_path == str(profile_dir.resolve())
    assert options.auto_port_value is True
    assert options.headless_value is True
    assert "--disable-gpu" in options.arguments
    assert "--no-sandbox" not in options.arguments
    assert page is FakePage.instances[0]

    manager.quit()
    assert page.quit_called is True


def test_browser_manager_reuses_live_page_and_switches_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOptions.instances = []
    FakePage.instances = []
    monkeypatch.setattr(browser_module, "ChromiumOptions", FakeOptions)
    monkeypatch.setattr(browser_module, "ChromiumPage", FakePage)
    manager = BrowserManager()

    first = manager.start(headless=True)
    assert manager.start(headless=True) is first
    second = manager.restart_headed()

    assert first.quit_called is True
    assert second is not first
    assert FakeOptions.instances[-1].headless_value is False
    assert second.set.window.maximized is True


def test_browser_manager_uses_free_port_without_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOptions.instances = []
    FakePage.instances = []
    monkeypatch.setattr(browser_module, "ChromiumOptions", FakeOptions)
    monkeypatch.setattr(browser_module, "ChromiumPage", FakePage)

    manager = BrowserManager()
    page = manager.start(headless=True)

    options = FakeOptions.instances[0]
    assert options.user_data_path is None
    assert options.auto_port_value is True
    assert page is FakePage.instances[0]

    manager.quit()
