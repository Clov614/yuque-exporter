from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from .auth import ProfileAuth
from .project import ensure_src_on_path


ensure_src_on_path()

from core.auth import YuqueAuth  # type: ignore  # noqa: E402
from core.client import YuqueClient  # type: ignore  # noqa: E402
from utils.browser import BrowserManager  # type: ignore  # noqa: E402


class RepoService:
    def __init__(self, profile: str):
        self.profile = profile

    def list_repos(self) -> List[Dict[str, Any]]:
        auth = ProfileAuth(self.profile)
        auth._sync_profile_to_legacy()
        manager = BrowserManager()
        page = manager.start(headless=True)
        try:
            auth = YuqueAuth()
            auth.load_cookies(page)
            client = YuqueClient(page)
            repos = client.get_repositories()
            return [asdict(repo) for repo in repos]
        finally:
            manager.quit()

    def tree(self, repo_id: int) -> Dict[str, Any]:
        auth = ProfileAuth(self.profile)
        auth._sync_profile_to_legacy()
        manager = BrowserManager()
        page = manager.start(headless=True)
        try:
            auth = YuqueAuth()
            auth.load_cookies(page)
            client = YuqueClient(page)
            repos = client.get_repositories()
            target = next((r for r in repos if int(r.id) == int(repo_id)), None)
            if not target:
                raise ValueError(f"repository not found: {repo_id}")
            nodes = client.get_catalog_nodes(target)
            return {
                "repo": asdict(target),
                "nodes": [asdict(n) for n in nodes],
            }
        finally:
            manager.quit()
