from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

import core.auth as auth_module  # type: ignore  # noqa: E402
from core.auth import (  # type: ignore  # noqa: E402
    LoginStatus,
    YuqueAuth,
    credentials_lock,
    is_authenticated_yuque_url,
)
from cli_anything.yuque.core import auth as profile_auth_module
from cli_anything.yuque.core.auth import ProfileAuth


class FakeTab:
    def __init__(self, *, cdp_error: bool = False) -> None:
        self.cdp_error = cdp_error
        self.cdp_calls: list[str] = []
        self.loaded_cookies: list[dict[str, str]] = []
        self.set = type(
            "Setter",
            (),
            {"cookies": lambda inner_self, values: self.loaded_cookies.extend(values)},
        )()

    def cookies(self) -> list[dict[str, str]]:
        return [{"name": "_yuque_session", "value": "secret"}]

    def run_cdp(self, method: str) -> None:
        self.cdp_calls.append(method)
        if self.cdp_error:
            raise RuntimeError("CDP unavailable")

    def get(self, _url: str) -> None:
        return None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.yuque.com/dashboard",
        "https://www.yuque.com/dashboard/collections",
    ],
)
def test_authenticated_yuque_url_accepts_trusted_paths(url: str) -> None:
    assert is_authenticated_yuque_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/dashboard",
        "https://attacker.example/yuque.com/u/example",
        "http://www.yuque.com/dashboard",
        "https://www.yuque.com.evil.example/dashboard",
        "https://www.yuque.com/login",
        "https://www.yuque.com/u/",
        "https://www.yuque.com/u//",
    ],
)
def test_authenticated_yuque_url_rejects_untrusted_destinations(url: str) -> None:
    assert is_authenticated_yuque_url(url) is False


def configure_credentials(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(YuqueAuth, "CREDENTIALS_DIR", root)
    monkeypatch.setattr(YuqueAuth, "COOKIES_FILE", root / "cookies.json")


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock regression")
def test_credentials_lock_is_reentrant_on_posix(tmp_path: Path) -> None:
    lock_target = tmp_path / "profile"
    started = time.monotonic()

    with credentials_lock(lock_target):
        with credentials_lock(lock_target):
            pass

    assert time.monotonic() - started < 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock regression")
def test_credentials_lock_blocks_other_process_on_posix(tmp_path: Path) -> None:
    lock_target = tmp_path / "profile"
    script = (
        "import sys,time; from pathlib import Path; "
        "from core.auth import credentials_lock; "
        "p=Path(sys.argv[1]); "
        "ctx=credentials_lock(p); ctx.__enter__(); print('locked', flush=True); "
        "time.sleep(1); ctx.__exit__(None,None,None)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_target)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[4] / "src")},
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        started = time.monotonic()
        with credentials_lock(lock_target):
            pass
        assert time.monotonic() - started >= 0.7
    finally:
        child.wait(timeout=5)


def test_windows_acl_verifier_allows_only_owner_and_system_principals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def record(command: list[str], **_kwargs: object) -> object:
        calls.append(command)
        return object()

    monkeypatch.setattr(auth_module.subprocess, "run", record)
    auth_module._verify_windows_acl(tmp_path, 0)

    script = calls[0][4]
    assert "S-1-5-18" in script
    assert "S-1-5-32-544" in script
    assert "$sid -notin $allowed" in script


def test_load_cookies_returns_false_for_missing_or_empty_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    configure_credentials(monkeypatch, credentials)
    tab = FakeTab()
    assert YuqueAuth().load_cookies(tab) is False
    credentials.mkdir(parents=True, exist_ok=True)
    (credentials / "cookies.json").write_text('{"cookies": []}', encoding="utf-8")
    assert YuqueAuth().load_cookies(tab) is False


def test_load_cookies_returns_false_for_malformed_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    configure_credentials(monkeypatch, credentials)
    credentials.mkdir(parents=True)
    (credentials / "cookies.json").write_text("not-json", encoding="utf-8")

    assert YuqueAuth().load_cookies(FakeTab()) is False


def test_clear_credentials_removes_profile_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    configure_credentials(monkeypatch, credentials)
    credentials.mkdir(parents=True)
    cookie_file = credentials / "cookies.json"
    cookie_file.write_text("{}", encoding="utf-8")

    assert YuqueAuth().clear_credentials() is True
    assert not cookie_file.exists()


def test_check_login_status_accepts_trusted_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    configure_credentials(monkeypatch, credentials)
    credentials.mkdir(parents=True)
    (credentials / "cookies.json").write_text(
        '{"cookies": [{"name": "session", "value": "profile"}]}',
        encoding="utf-8",
    )
    tab = FakeTab()
    tab.url = "https://www.yuque.com/dashboard"
    tab.wait = type("Wait", (), {"load_start": lambda _self: None})()

    assert YuqueAuth().check_login_status(tab) == LoginStatus.LOGGED_IN


def test_check_login_status_rejects_external_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    configure_credentials(monkeypatch, credentials)
    credentials.mkdir(parents=True)
    (credentials / "cookies.json").write_text(
        '{"cookies": [{"name": "session", "value": "profile"}]}',
        encoding="utf-8",
    )
    tab = FakeTab()
    tab.url = "https://attacker.example/dashboard"
    tab.wait = type("Wait", (), {"load_start": lambda _self: None})()

    assert YuqueAuth().check_login_status(tab) == LoginStatus.EXPIRED


def test_save_cookies_rejects_non_serializable_cookie_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    configure_credentials(monkeypatch, credentials)

    class BadTab:
        def cookies(self):
            return {"bad": object()}

    assert YuqueAuth().save_cookies(BadTab()) is False
    assert list(credentials.glob("*.tmp")) == []


def test_load_cookies_clears_browser_before_loading_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    configure_credentials(monkeypatch, credentials)
    credentials.mkdir(parents=True)
    (credentials / "cookies.json").write_text(
        '{"cookies": [{"name": "session", "value": "profile"}]}',
        encoding="utf-8",
    )
    tab = FakeTab()

    assert YuqueAuth().load_cookies(tab) is True
    assert tab.cdp_calls == ["Network.clearBrowserCookies"]
    assert tab.loaded_cookies == [{"name": "session", "value": "profile"}]


def test_load_cookies_fails_closed_when_browser_clear_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    configure_credentials(monkeypatch, credentials)
    credentials.mkdir(parents=True)
    (credentials / "cookies.json").write_text(
        '{"cookies": [{"name": "session", "value": "profile"}]}',
        encoding="utf-8",
    )
    tab = FakeTab(cdp_error=True)

    assert YuqueAuth().load_cookies(tab) is False
    assert tab.loaded_cookies == []


def test_save_cookies_is_atomic_and_owner_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    configure_credentials(monkeypatch, credentials)

    assert YuqueAuth().save_cookies(FakeTab()) is True

    payload = json.loads((credentials / "cookies.json").read_text(encoding="utf-8"))
    assert payload["cookies"] == [{"name": "_yuque_session", "value": "secret"}]
    assert list(credentials.glob("*.tmp")) == []
    if os.name != "nt":
        assert stat.S_IMODE(credentials.stat().st_mode) == 0o700
        assert stat.S_IMODE((credentials / "cookies.json").stat().st_mode) == 0o600


def test_empty_profile_cannot_inherit_another_profiles_cookies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    roots = tmp_path / "profiles"
    profile_a = roots / "a" / "cookies.json"
    profile_a.parent.mkdir(parents=True)
    profile_a.write_text('{"cookies": [{"name": "session", "value": "a"}]}')
    seen_paths: list[Path] = []

    class FakeStore:
        def __init__(self, path: Path) -> None:
            self.path = path
            seen_paths.append(path)

        def check_login_status(self, _page: object) -> LoginStatus:
            return LoginStatus.LOGGED_IN if self.path.exists() else LoginStatus.NONE

    class FakeManager:
        def __init__(
            self,
            user_data_dir: Path | None = None,
            lifecycle_lock: object = None,
        ) -> None:
            self.user_data_dir = user_data_dir

        def start(self, headless: bool = True) -> object:
            return object()

        def quit(self) -> None:
            return None

    monkeypatch.setattr(
        profile_auth_module,
        "profile_root",
        lambda profile: roots / profile,
    )
    monkeypatch.setattr(profile_auth_module, "YuqueAuth", FakeStore)
    monkeypatch.setattr(profile_auth_module, "BrowserManager", FakeManager)

    result = ProfileAuth("b").status()

    assert result["status"] == "none"
    assert seen_paths == [roots / "b" / "cookies.json"]
    assert not (roots / "b" / "cookies.json").exists()
    assert profile_a.exists()


def test_profile_login_and_logout_share_lifecycle_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import core.client as client_module  # type: ignore

    roots = tmp_path / "profiles"
    events: list[tuple[str, Path]] = []

    class RecordingLock:
        def __init__(self, path: Path) -> None:
            self.path = path

        def __enter__(self):
            events.append(("enter", self.path))
            return self

        def __exit__(self, *_args):
            events.append(("exit", self.path))

    class FakeStore:
        def __init__(self, path: Path) -> None:
            self.path = path

        def clear_credentials(self, *, _locked: bool = False) -> bool:
            return True

    class FakeManager:
        def __init__(self, user_data_dir=None, lifecycle_lock=None) -> None:
            self.lifecycle_lock = lifecycle_lock
            self.context = None

        def start(self, headless: bool = True) -> object:
            self.context = self.lifecycle_lock()
            self.context.__enter__()
            return object()

        def quit(self) -> None:
            if self.context:
                self.context.__exit__(None, None, None)
                self.context = None

    class FakeClient:
        def __init__(self, _page, auth=None) -> None:
            self.auth = auth

        def login(self) -> bool:
            return True

    monkeypatch.setattr(profile_auth_module, "profile_root", lambda name: roots / name)
    monkeypatch.setattr(profile_auth_module, "credentials_lock", RecordingLock)
    monkeypatch.setattr(profile_auth_module, "secure_credentials_path", lambda *_a, **_k: None)
    monkeypatch.setattr(profile_auth_module, "YuqueAuth", FakeStore)
    monkeypatch.setattr(profile_auth_module, "BrowserManager", FakeManager)
    monkeypatch.setattr(client_module, "YuqueClient", FakeClient)

    profile = ProfileAuth("a")
    assert profile.login()["status"] == "logged_in"
    assert profile.logout()["status"] == "logged_out"

    assert events == [
        ("enter", profile.state_dir),
        ("exit", profile.state_dir),
        ("enter", profile.state_dir),
        ("exit", profile.state_dir),
    ]


def test_profiles_use_distinct_browser_data_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(profile_auth_module, "profile_root", lambda name: tmp_path / name)

    manager_a = ProfileAuth("a").browser_manager()
    manager_b = ProfileAuth("b").browser_manager()

    assert manager_a.user_data_dir == (tmp_path / "a" / "browser").resolve()
    assert manager_b.user_data_dir == (tmp_path / "b" / "browser").resolve()
    assert manager_a.user_data_dir != manager_b.user_data_dir


def test_logout_clears_cookie_and_browser_stores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(profile_auth_module, "profile_root", lambda name: tmp_path / name)
    profile = ProfileAuth("a")
    profile.profile_cookies.parent.mkdir(parents=True)
    profile.profile_cookies.write_text("{}", encoding="utf-8")
    profile.browser_data_dir.mkdir(parents=True)
    (profile.browser_data_dir / "state.bin").write_bytes(b"authenticated")

    result = profile.logout()

    assert result["status"] == "logged_out"
    assert not profile.profile_cookies.exists()
    assert profile.browser_data_dir.exists()
    assert list(profile.browser_data_dir.iterdir()) == []


def test_save_cookies_removes_file_when_hardening_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    configure_credentials(monkeypatch, credentials)
    original_secure_path = auth_module.secure_credentials_path

    def fail_for_file(path: Path, *, directory: bool) -> None:
        if directory:
            original_secure_path(path, directory=True)
            return
        raise OSError("ACL failure")

    monkeypatch.setattr(auth_module, "secure_credentials_path", fail_for_file)

    assert YuqueAuth().save_cookies(FakeTab()) is False
    assert not (credentials / "cookies.json").exists()
    assert list(credentials.glob("*.tmp")) == []
