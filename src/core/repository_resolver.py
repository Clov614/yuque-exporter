"""Resolve Yuque repository references without common-used discovery."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlencode

from .models import Repository
from .repository_reference import RepositoryReference, RepositoryReferenceKind


class RepositoryResolutionError(RuntimeError):
    """Base class for repository resolution failures."""


class RepositoryAuthenticationError(RepositoryResolutionError):
    """The current Yuque session is not authenticated."""


class RepositoryAccessDeniedError(RepositoryResolutionError):
    """The current Yuque session cannot access the repository."""


class RepositoryNotFoundError(RepositoryResolutionError):
    """The referenced repository does not exist."""


class RepositoryTransportError(RepositoryResolutionError):
    """Yuque could not be reached or temporarily rejected the request."""


class RepositoryResponseError(RepositoryResolutionError):
    """Yuque returned an unexpected repository response."""


@dataclass(frozen=True)
class RepositoryHttpResult:
    """Sanitized HTTP result retained for explicit status handling."""

    status_code: int
    payload: Any
    content_type: str = "application/json"


RepositoryRequester = Callable[[str], RepositoryHttpResult]


class RepositoryResolver:
    """Resolve an ID, namespace, or Yuque URL using authenticated web APIs."""

    BOOKS_API = "https://www.yuque.com/api/books"
    APP_DATA_PATTERN = re.compile(
        r'window\.appData\s*=\s*JSON\.parse\(decodeURIComponent\("([^"]+)"\)\)'
    )

    def __init__(self, requester: RepositoryRequester) -> None:
        self._requester = requester

    def resolve(self, reference: RepositoryReference) -> Repository:
        """Resolve ``reference`` without consulting ``common_used``."""
        primary = self._request(self._books_query_url(reference), reference)
        try:
            return self.repository_from_collection(primary.payload, reference)
        except RepositoryNotFoundError:
            if reference.kind is RepositoryReferenceKind.NAMESPACE:
                return self._resolve_namespace_page(reference)
            raise RepositoryNotFoundError(
                f"repository metadata unavailable for ID: {reference.canonical}; "
                "use owner/slug or a Yuque repository URL"
            )

    def _resolve_namespace_page(self, reference: RepositoryReference) -> Repository:
        namespace = reference.namespace
        if namespace is None:
            raise RepositoryResponseError("repository namespace is missing")
        owner, slug = namespace.split("/", 1)
        url = (
            f"https://www.yuque.com/{quote(owner, safe='')}/"
            f"{quote(slug, safe='')}"
        )
        result = self._request(url, reference)
        return self.repository_from_page(result.payload, reference)

    def _request(
        self,
        url: str,
        reference: RepositoryReference,
    ) -> RepositoryHttpResult:
        try:
            result = self._requester(url)
        except RepositoryResolutionError:
            raise
        except Exception as exc:
            raise RepositoryTransportError(
                "failed to request repository details"
            ) from exc
        self.raise_for_status(result.status_code, reference)
        return result

    @classmethod
    def _books_query_url(cls, reference: RepositoryReference) -> str:
        if reference.kind is RepositoryReferenceKind.ID:
            query = urlencode({"id": reference.repository_id})
        else:
            query = urlencode({"namespace": reference.namespace})
        return f"{cls.BOOKS_API}?{query}"

    @staticmethod
    def raise_for_status(
        status_code: int,
        reference: RepositoryReference | None = None,
    ) -> None:
        if 200 <= status_code < 300:
            return
        if status_code == 401:
            raise RepositoryAuthenticationError("Yuque login has expired")
        reference_suffix = f": {reference.canonical}" if reference else ""
        if status_code == 403:
            raise RepositoryAccessDeniedError(
                f"repository access denied{reference_suffix}"
            )
        if status_code == 404:
            raise RepositoryNotFoundError(
                f"repository not found{reference_suffix}"
            )
        if status_code == 429:
            raise RepositoryTransportError("Yuque repository API rate limited the request")
        if status_code >= 500:
            raise RepositoryTransportError(
                f"Yuque repository API failed with status {status_code}"
            )
        raise RepositoryResponseError(
            f"Yuque repository API returned status {status_code}"
        )

    @classmethod
    def repository_from_collection(
        cls,
        payload: Any,
        reference: RepositoryReference,
    ) -> Repository:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RepositoryResponseError("Yuque returned invalid repository list JSON")
        for item in payload["data"]:
            repository = cls.repository_from_payload(item)
            if cls._matches(repository, reference):
                return repository
        raise RepositoryNotFoundError(
            f"repository not found: {reference.canonical}"
        )

    @staticmethod
    def _matches(
        repository: Repository,
        reference: RepositoryReference,
    ) -> bool:
        if reference.repository_id is not None:
            return repository.id == reference.repository_id
        namespace = f"{repository.user_login}/{repository.slug}"
        return namespace == reference.namespace

    @classmethod
    def repository_from_page(
        cls,
        payload: Any,
        reference: RepositoryReference,
    ) -> Repository:
        if not isinstance(payload, str):
            raise RepositoryResponseError("Yuque returned invalid repository page")
        match = cls.APP_DATA_PATTERN.search(payload)
        if not match:
            raise RepositoryResponseError("Yuque repository page data is missing")
        try:
            app_data = json.loads(unquote(match.group(1)))
        except (UnicodeError, ValueError, TypeError) as exc:
            raise RepositoryResponseError("Yuque repository page data is invalid") from exc
        book = app_data.get("book") if isinstance(app_data, dict) else None
        repository = cls.repository_from_payload(book, reference)
        if not cls._matches(repository, reference):
            raise RepositoryNotFoundError(
                f"repository not found: {reference.canonical}"
            )
        return repository

    @staticmethod
    def repository_from_payload(
        payload: Any,
        reference: RepositoryReference | None = None,
    ) -> Repository:
        if not isinstance(payload, dict):
            raise RepositoryResponseError("Yuque returned invalid repository JSON")
        candidate = payload.get("data", payload)
        if not isinstance(candidate, dict):
            raise RepositoryResponseError("Yuque repository data is missing")
        target = candidate.get("target", candidate)
        if not isinstance(target, dict):
            raise RepositoryResponseError("Yuque repository target is invalid")

        raw_id = target.get("id")
        raw_name = target.get("name")
        raw_slug = target.get("slug")
        raw_user = target.get("user")
        raw_namespace = target.get("namespace")
        raw_login = raw_user.get("login") if isinstance(raw_user, dict) else None
        reference_label = reference.canonical if reference else "repository list entry"
        if not raw_login and isinstance(raw_namespace, str) and "/" in raw_namespace:
            raw_login = raw_namespace.split("/", 1)[0]
        raw_items_count = target.get("items_count", 0)
        raw_public = target.get("public", False)
        raw_description = target.get("description", "")
        raw_cover = target.get("cover")
        optional_fields_valid = (
            isinstance(raw_items_count, int)
            and not isinstance(raw_items_count, bool)
            and raw_items_count >= 0
            and (
                isinstance(raw_public, bool)
                or (
                    isinstance(raw_public, int)
                    and not isinstance(raw_public, bool)
                    and raw_public in {0, 1}
                )
            )
            and isinstance(raw_description, str)
            and (raw_cover is None or isinstance(raw_cover, str))
        )
        if (
            isinstance(raw_id, bool)
            or not isinstance(raw_id, int)
            or raw_id <= 0
            or not isinstance(raw_name, str)
            or not raw_name
            or not isinstance(raw_slug, str)
            or not raw_slug
            or not isinstance(raw_login, str)
            or not raw_login
            or not optional_fields_valid
        ):
            raise RepositoryResponseError(
                f"Yuque returned incomplete repository data for {reference_label}"
            )

        normalized = RepositoryResolver._with_namespace_owner(candidate)
        try:
            return Repository.from_api_response(normalized)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RepositoryResponseError(
                f"Yuque returned incomplete repository data for {reference_label}"
            ) from exc

    @staticmethod
    def _with_namespace_owner(candidate: dict[str, Any]) -> dict[str, Any]:
        target = candidate.get("target", candidate)
        if not isinstance(target, dict):
            raise RepositoryResponseError("Yuque repository target is invalid")
        user = target.get("user")
        if isinstance(user, dict) and user.get("login"):
            return dict(candidate)
        namespace = target.get("namespace")
        if not isinstance(namespace, str) or "/" not in namespace:
            return dict(candidate)
        owner, _ = namespace.split("/", 1)
        enriched_target = {**target, "user": {"login": owner}}
        if "target" in candidate:
            return {**candidate, "target": enriched_target}
        return enriched_target
