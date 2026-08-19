from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from .project import ensure_src_on_path, profile_root


ensure_src_on_path()

from core.auth import (  # type: ignore  # noqa: E402
    LoginStatus,
    YuqueAuth,
    credentials_lock,
    secure_credentials_path,
)
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

    @property
    def browser_data_dir(self) -> Path:
        return self.state_dir / "browser"

    def auth(self) -> YuqueAuth:
        """Return an auth store bound only to this profile."""
        return YuqueAuth(self.profile_cookies)

    def browser_manager(self) -> BrowserManager:
        self.browser_data_dir.mkdir(parents=True, exist_ok=True)
        from core.auth import secure_credentials_path  # type: ignore

        secure_credentials_path(self.browser_data_dir, directory=True)
        return BrowserManager(
            user_data_dir=self.browser_data_dir,
            lifecycle_lock=lambda: credentials_lock(self.state_dir),
        )

    def status(self) -> Dict[str, str | bool]:
        manager = self.browser_manager()
        page = manager.start(headless=True)
        try:
            status = self.auth().check_login_status(page)
            return {
                "profile": self.profile,
                "status": _status_name(status),
                "cookies_file": str(self.profile_cookies),
                "has_local_cookies": self.profile_cookies.exists(),
            }
        finally:
            manager.quit()

    def login(self) -> Dict[str, str]:
        manager = self.browser_manager()
        page = manager.start(headless=False)
        try:
            from core.client import YuqueClient  # type: ignore

            auth = self.auth()
            client = YuqueClient(page, auth=auth)
            ok = client.login()
            return {
                "profile": self.profile,
                "status": "logged_in" if ok else "failed",
                "cookies_file": str(self.profile_cookies),
            }
        finally:
            manager.quit()

    def logout(self) -> Dict[str, str]:
        try:
            with credentials_lock(self.state_dir):
                if not self.auth().clear_credentials(_locked=True):
                    raise OSError("failed to clear profile credentials")
                if self.browser_data_dir.exists():
                    shutil.rmtree(self.browser_data_dir)
                self.browser_data_dir.mkdir(parents=True, exist_ok=True)
                secure_credentials_path(self.browser_data_dir, directory=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise OSError("failed to clear profile browser session") from exc
        return {
            "profile": self.profile,
            "status": "logged_out",
            "cookies_file": str(self.profile_cookies),
        }


def _status_name(status: LoginStatus) -> str:
    if status == LoginStatus.LOGGED_IN:
        return "logged_in"
    if status == LoginStatus.EXPIRED:
        return "expired"
    return "none"
