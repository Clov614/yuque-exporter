from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from .auth import ProfileAuth
from .audit import append_audit
from .project import ensure_src_on_path


ensure_src_on_path()

from core.browser_writer import YuqueBrowserWriter  # type: ignore  # noqa: E402
from core.client import YuqueClient  # type: ignore  # noqa: E402
from core.mutation_errors import MutationConfirmationRequired  # type: ignore  # noqa: E402
from core.repository_reference import RepositoryReference  # type: ignore  # noqa: E402
from core.repository_resolver import (  # type: ignore  # noqa: E402
    RepositoryAuthenticationError,
    RepositoryResolutionError,
)


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

    def create(
        self,
        *,
        name: str,
        slug: str | None = None,
        description: str = "",
        visibility: str = "private",
        confirmed: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        normalized_name = name.strip()
        normalized_description = description.strip()
        if not normalized_name:
            raise ValueError("repository name cannot be empty")
        if len(normalized_name) > 200:
            raise ValueError("repository name is too long")
        if visibility not in {"private", "public", "team"}:
            raise ValueError("unsupported repository visibility")
        if slug is not None:
            slug = slug.strip()
            if not slug or len(slug) > 100:
                raise ValueError("repository slug is invalid")
        if not confirmed and not dry_run:
            raise MutationConfirmationRequired("repository creation requires explicit confirmation")
        if dry_run:
            return {
                "status": "dry_run",
                "name": normalized_name,
                "slug": slug,
                "description": normalized_description,
                "visibility": visibility,
            }

        profile_auth = ProfileAuth(self.profile)
        manager = profile_auth.browser_manager()
        page = manager.start(headless=True)
        try:
            auth = profile_auth.auth()
            if not auth.load_cookies(page):
                raise RepositoryAuthenticationError("profile is not authenticated")
            client = YuqueClient(page, auth=auth)
            if visibility == "team":
                # The books protocol has no team flag (see YuqueClient
                # .create_repository): route team requests straight to the
                # visible flow and say so, instead of silently degrading
                # to private or hiding behind a protocol-error fallback.
                print(
                    "⚠️ team visibility has no protocol support; "
                    "creating through the visible browser flow"
                )
                writer = YuqueBrowserWriter(page)
                namespace = writer.create_repository(
                    name=normalized_name,
                    slug=slug,
                    description=normalized_description,
                    visibility=visibility,
                )
                repository = client.get_repository(RepositoryReference.parse(namespace))
            else:
                try:
                    repository = client.create_repository(
                        name=normalized_name,
                        slug=slug,
                        description=normalized_description,
                        visibility=visibility,
                    )
                except RepositoryResolutionError as exc:
                    if not _is_protocol_unsupported(exc):
                        raise
                    writer = YuqueBrowserWriter(page)
                    namespace = writer.create_repository(
                        name=normalized_name,
                        slug=slug,
                        description=normalized_description,
                        visibility=visibility,
                    )
                    repository = client.get_repository(RepositoryReference.parse(namespace))
            append_audit(
                self.profile,
                {
                    "event": "repo.create",
                    "repository_id": repository.id,
                    "namespace": f"{repository.user_login}/{repository.slug}",
                    "status": "created",
                    "via": "browser" if visibility == "team" else "protocol",
                    "visibility": visibility,
                },
            )
            return {"status": "created", "repo": asdict(repository)}
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


def _is_protocol_unsupported(exc: Exception) -> bool:
    """Only fall back to browser UI when the protocol itself is unavailable."""
    message = str(exc).lower()
    return "status 404" in message or "status 405" in message or "not found" in message
