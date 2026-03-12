from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from .project import ensure_src_on_path, profile_root


ensure_src_on_path()

from core.auth import LoginStatus, YuqueAuth  # type: ignore  # noqa: E402
from utils.browser import BrowserManager  # type: ignore  # noqa: E402


@dataclass(frozen=True)
class ProfileAuth:
    profile: str

    @property
    def state_dir(self) -> Path:
        return profile_root(self.profile)

    @property
    def profile_cookies(self) -> Path:
        return self.state_dir / "cookies.json"

    def status(self) -> Dict[str, str]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        manager = BrowserManager()
        page = manager.start(headless=True)
        try:
            self._sync_profile_to_legacy()
            auth = YuqueAuth()
            status = auth.check_login_status(page)
            self._sync_legacy_to_profile()
            return {
                "profile": self.profile,
                "status": _status_name(status),
                "cookies_file": str(self.profile_cookies),
                "has_local_cookies": self.profile_cookies.exists(),
            }
        finally:
            manager.quit()

    def login(self) -> Dict[str, str]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        manager = BrowserManager()
        page = manager.start(headless=False)
        try:
            from core.client import YuqueClient  # type: ignore

            client = YuqueClient(page)
            ok = client.login()
            if ok:
                self._sync_legacy_to_profile()
            return {
                "profile": self.profile,
                "status": "logged_in" if ok else "failed",
                "cookies_file": str(self.profile_cookies),
            }
        finally:
            manager.quit()

    def logout(self) -> Dict[str, str]:
        auth = YuqueAuth()
        auth.clear_credentials()
        if self.profile_cookies.exists():
            self.profile_cookies.unlink()
        return {
            "profile": self.profile,
            "status": "logged_out",
            "cookies_file": str(self.profile_cookies),
        }

    def _sync_profile_to_legacy(self) -> None:
        auth = YuqueAuth()
        legacy = auth.COOKIES_FILE
        if not self.profile_cookies.exists():
            return
        legacy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.profile_cookies, legacy)
        _harden_permissions(legacy)

    def _sync_legacy_to_profile(self) -> None:
        auth = YuqueAuth()
        legacy = auth.COOKIES_FILE
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if legacy.exists():
            shutil.copyfile(legacy, self.profile_cookies)
            _harden_permissions(self.profile_cookies)


def _status_name(status: LoginStatus) -> str:
    if status == LoginStatus.LOGGED_IN:
        return "logged_in"
    if status == LoginStatus.EXPIRED:
        return "expired"
    return "none"


def _harden_permissions(target: Path) -> None:
    if not target.exists():
        return
    try:
        target.chmod(0o600)
    except OSError:
        return
