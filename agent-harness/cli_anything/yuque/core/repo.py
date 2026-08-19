from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from .auth import ProfileAuth
from .project import ensure_src_on_path


ensure_src_on_path()

from core.client import YuqueClient  # type: ignore  # noqa: E402
from core.repository_resolver import RepositoryAuthenticationError  # type: ignore  # noqa: E402
from core.repository_reference import RepositoryReference  # type: ignore  # noqa: E402


class RepoService:
    def __init__(self, profile: str):
        self.profile = profile

    def list_repos(self, source: str = "common") -> List[Dict[str, Any]]:
        if source not in {"common", "favorites"}:
            raise ValueError(f"unsupported repository source: {source}")
        profile_auth = ProfileAuth(self.profile)
        manager = profile_auth.browser_manager()
        page = manager.start(headless=True)
        try:
            auth = profile_auth.auth()
            if not auth.load_cookies(page):
                raise RepositoryAuthenticationError("profile is not authenticated")
            client = YuqueClient(page, auth=auth)
            repos = (
                client.get_repositories()
                if source == "common"
                else client.get_favorite_repositories()
            )
            return [asdict(repo) for repo in repos]
        finally:
            manager.quit()

    def tree(
        self,
        repo_id: int | None = None,
        repo: str | None = None,
    ) -> Dict[str, Any]:
        reference = RepositoryReference.from_selector(
            repository_id=repo_id,
            reference=repo,
        )
        profile_auth = ProfileAuth(self.profile)
        manager = profile_auth.browser_manager()
        page = manager.start(headless=True)
        try:
            auth = profile_auth.auth()
            if not auth.load_cookies(page):
                raise RepositoryAuthenticationError("profile is not authenticated")
            client = YuqueClient(page, auth=auth)
            target = client.get_repository(reference)
            nodes = client.get_catalog_nodes(target)
            return {
                "repo": asdict(target),
                "nodes": [asdict(n) for n in nodes],
            }
        finally:
            manager.quit()
