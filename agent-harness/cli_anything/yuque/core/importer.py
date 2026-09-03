from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Iterable

from .auth import ProfileAuth
from .audit import append_audit
from .project import ensure_src_on_path

ensure_src_on_path()

from core.browser_writer import YuqueBrowserWriter  # type: ignore  # noqa: E402
from core.client import YuqueClient  # type: ignore  # noqa: E402
from core.markdown_input import MarkdownDocument, read_markdown  # type: ignore  # noqa: E402
from core.mutation_errors import (  # type: ignore  # noqa: E402
    MutationAuthenticationError,
    MutationConfirmationRequired,
    MutationError,
    MutationTimeoutError,
)
from core.repository_reference import RepositoryReference  # type: ignore  # noqa: E402
from core.repository_resolver import RepositoryAuthenticationError  # type: ignore  # noqa: E402


class ImportService:
    def __init__(self, profile: str) -> None:
        self.profile = profile

    def run(
        self,
        *,
        repo_id: int | None = None,
        repo: str | None = None,
        file: str | Path,
        title: str | None = None,
        confirmed: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        reference = RepositoryReference.from_selector(
            repository_id=repo_id,
            reference=repo,
        )
        document = self._read_document(file, title)
        self._require_confirmation(confirmed, dry_run)
        if dry_run:
            return self._dry_run(reference, document)

        profile_auth = ProfileAuth(self.profile)
        manager = profile_auth.browser_manager()
        page = manager.start(headless=True)
        try:
            auth = profile_auth.auth()
            if not auth.load_cookies(page):
                raise RepositoryAuthenticationError("profile is not authenticated")
            client = YuqueClient(page, auth=auth)
            repository = client.get_repository(reference)
            url = YuqueBrowserWriter(page).import_markdown(repository, document)
            result = {
                "status": "created",
                "repo": asdict(repository),
                "file": str(document.path),
                "title": document.title,
                "bytes": document.byte_length,
                "url": url,
            }
            append_audit(
                self.profile,
                {
                    "event": "import.run",
                    "repository_id": repository.id,
                    "file_name": document.path.name,
                    "bytes": document.byte_length,
                    "status": "created",
                },
            )
            return result
        finally:
            manager.quit()

    def batch(
        self,
        *,
        repo_id: int | None = None,
        repo: str | None = None,
        files: Iterable[str | Path],
        confirmed: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        reference = RepositoryReference.from_selector(
            repository_id=repo_id,
            reference=repo,
        )
        documents = tuple(self._read_document(path, None) for path in files)
        if not documents:
            raise ValueError("at least one Markdown file is required")
        self._require_confirmation(confirmed, dry_run)
        if dry_run:
            return {
                "status": "dry_run",
                "repo": reference.canonical,
                "requested": len(documents),
                "success": 0,
                "failed": 0,
                "items": [self._dry_run_item(document) for document in documents],
            }

        profile_auth = ProfileAuth(self.profile)
        manager = profile_auth.browser_manager()
        page = manager.start(headless=True)
        try:
            auth = profile_auth.auth()
            if not auth.load_cookies(page):
                raise RepositoryAuthenticationError("profile is not authenticated")
            client = YuqueClient(page, auth=auth)
            repository = client.get_repository(reference)
            writer = YuqueBrowserWriter(page)
            items: list[dict[str, Any]] = []
            for document in documents:
                try:
                    url = writer.import_markdown(repository, document)
                except MutationTimeoutError as exc:
                    items.append(self._failed_item(document, "ambiguous", str(exc)))
                    items.extend(
                        self._failed_item(rest, "not_attempted", "previous write was ambiguous")
                        for rest in documents[len(items) :]
                    )
                    break
                except MutationError as exc:
                    items.append(self._failed_item(document, "failed", str(exc)))
                else:
                    items.append(
                        {
                            "status": "created",
                            "file": str(document.path),
                            "title": document.title,
                            "bytes": document.byte_length,
                            "url": url,
                        }
                    )
            success_count = sum(item["status"] == "created" for item in items)
            result = {
                "status": "completed" if success_count == len(documents) else "partial",
                "repo": asdict(repository),
                "requested": len(documents),
                "success": success_count,
                "failed": len(items) - success_count,
                "items": items,
            }
            append_audit(
                self.profile,
                {
                    "event": "import.batch",
                    "repository_id": repository.id,
                    "requested": len(documents),
                    "success": success_count,
                    "failed": len(items) - success_count,
                },
            )
            return result
        finally:
            manager.quit()

    @staticmethod
    def _read_document(file: str | Path, title: str | None) -> MarkdownDocument:
        document = read_markdown(file)
        if title is None:
            return document
        normalized = title.strip()
        if not normalized:
            raise ValueError("document title cannot be empty")
        return replace(document, title=normalized)

    @staticmethod
    def _require_confirmation(confirmed: bool, dry_run: bool) -> None:
        if not confirmed and not dry_run:
            raise MutationConfirmationRequired("Markdown import requires explicit confirmation")

    @staticmethod
    def _dry_run(reference: RepositoryReference, document: MarkdownDocument) -> dict[str, Any]:
        return {
            "status": "dry_run",
            "repo": reference.canonical,
            "requested": 1,
            "success": 0,
            "failed": 0,
            "items": [ImportService._dry_run_item(document)],
        }

    @staticmethod
    def _dry_run_item(document: MarkdownDocument) -> dict[str, Any]:
        return {
            "status": "not_attempted",
            "file": str(document.path),
            "title": document.title,
            "bytes": document.byte_length,
        }

    @staticmethod
    def _failed_item(
        document: MarkdownDocument,
        status: str,
        error: str,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "file": str(document.path),
            "title": document.title,
            "bytes": document.byte_length,
            "error": error,
        }
