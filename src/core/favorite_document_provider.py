"""Discover single documents from the Yuque favorites marks API."""

from __future__ import annotations

from typing import Any

from .favorite_document import FavoriteDocument
from .favorite_repository_provider import (
    FavoriteRepositoryUnavailableError,
    FavoriteRequester,
)
from .repository_resolver import RepositoryResolver


class FavoriteDocumentProvider:
    """Enumerate single ``Doc`` favorites via ``/api/mine/marks``.

    Reuses the marks pagination contract of
    :class:`FavoriteRepositoryProvider` (``type=all`` pages of 20,
    ``totalItems`` early stop, ``MAX_MARK_ACTIONS`` circuit breaker)
    but keeps only ``Doc`` targets. The favorites HTML page is not
    parsed: document card markup is not confirmed, while the marks API
    already carries everything export needs.
    """

    MARKS_ENDPOINT_TEMPLATE = (
        "https://www.yuque.com/api/mine/marks?limit=20&offset={offset}&type=all&q="
    )
    MAX_MARK_ACTIONS = 5000

    def __init__(self, requester: FavoriteRequester) -> None:
        self._requester = requester

    def list_documents(self) -> list[FavoriteDocument]:
        documents: list[FavoriteDocument] = []
        offset = 0
        page_size = 20
        while offset < self.MAX_MARK_ACTIONS:
            endpoint = self.MARKS_ENDPOINT_TEMPLATE.format(offset=offset)
            result = self._requester(endpoint)
            RepositoryResolver.raise_for_status(result.status_code)
            page_documents, action_count, total_items = self._parse_marks_page(
                result.payload
            )
            documents.extend(page_documents)
            offset += action_count
            if action_count == 0 or action_count < page_size:
                break
            if total_items is not None and offset >= total_items:
                break
        else:
            raise FavoriteRepositoryUnavailableError("too many favorites marks actions")
        return self._deduplicate(documents)

    @classmethod
    def _parse_marks_page(
        cls,
        payload: Any,
    ) -> tuple[list[FavoriteDocument], int, int | None]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise FavoriteRepositoryUnavailableError("favorites marks response is invalid")
        actions = payload["data"].get("actions")
        if not isinstance(actions, list):
            raise FavoriteRepositoryUnavailableError("favorites marks actions are invalid")
        if len(actions) > cls.MAX_MARK_ACTIONS:
            raise FavoriteRepositoryUnavailableError("too many favorites marks actions")
        total_items = payload["data"].get("totalItems")
        if total_items is not None and (
            not isinstance(total_items, int)
            or isinstance(total_items, bool)
            or total_items < 0
            or total_items > cls.MAX_MARK_ACTIONS
        ):
            raise FavoriteRepositoryUnavailableError("favorites marks total is invalid")
        documents = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            document = cls._document_from_action(action)
            if document is not None:
                documents.append(document)
        return documents, len(actions), total_items

    @staticmethod
    def _document_from_action(action: dict[str, Any]) -> FavoriteDocument | None:
        target = action.get("target")
        if not isinstance(target, dict) or target.get("type") != "Doc":
            return None
        doc_id = FavoriteDocumentProvider._positive_int(
            target.get("id"), target.get("doc_id"), action.get("target_id")
        )
        if doc_id is None:
            return None
        book = target.get("book")
        book_dict = book if isinstance(book, dict) else {}
        book_id = FavoriteDocumentProvider._positive_int(
            target.get("book_id"), book_dict.get("id")
        ) or 0
        title = FavoriteDocumentProvider._non_empty_str(
            target.get("title"), target.get("name")
        ) or f"doc-{doc_id}"
        slug = FavoriteDocumentProvider._non_empty_str(
            target.get("slug"), target.get("url")
        )
        if not slug:
            slug = FavoriteDocumentProvider._slug_from_url(
                FavoriteDocumentProvider._non_empty_str(action.get("_url"))
            )
        book_name = FavoriteDocumentProvider._non_empty_str(book_dict.get("name"))
        book_namespace = FavoriteDocumentProvider._non_empty_str(
            book_dict.get("namespace")
        )
        if not book_namespace:
            slug_value = FavoriteDocumentProvider._non_empty_str(book_dict.get("slug"))
            login = ""
            user = book_dict.get("user")
            if isinstance(user, dict):
                login = FavoriteDocumentProvider._non_empty_str(user.get("login"))
            if login and slug_value:
                book_namespace = f"{login}/{slug_value}"
        favorite_time = FavoriteDocumentProvider._non_empty_str(
            action.get("created_at"),
            action.get("favorited_at"),
            action.get("updated_at"),
            action.get("time"),
        )
        url = FavoriteDocumentProvider._non_empty_str(action.get("_url"))
        if url.startswith("/"):
            url = f"https://www.yuque.com{url}"
        return FavoriteDocument(
            doc_id=doc_id,
            title=title,
            slug=slug,
            book_id=book_id,
            book_name=book_name,
            book_namespace=book_namespace,
            favorite_time=favorite_time,
            url=url,
        )

    @staticmethod
    def _positive_int(*candidates: Any) -> int | None:
        for candidate in candidates:
            if (
                isinstance(candidate, int)
                and not isinstance(candidate, bool)
                and candidate > 0
            ):
                return candidate
        return None

    @staticmethod
    def _non_empty_str(*candidates: Any) -> str:
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    @staticmethod
    def _slug_from_url(url: str) -> str:
        if not url:
            return ""
        segment = url.rstrip("/").rsplit("/", 1)[-1]
        return segment.split("?", 1)[0].split("#", 1)[0]

    @staticmethod
    def _deduplicate(documents: list[FavoriteDocument]) -> list[FavoriteDocument]:
        result = []
        seen: set[tuple[int, int]] = set()
        for document in documents:
            key = (document.book_id, document.doc_id)
            if key in seen:
                continue
            seen.add(key)
            result.append(document)
        return result
