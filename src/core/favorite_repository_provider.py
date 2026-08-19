"""Discover explicit Yuque knowledge-base cards from the favorites page."""

from __future__ import annotations

from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
import re
from urllib.parse import urlparse

from .models import Repository
from .repository_reference import RepositoryReference, RepositoryReferenceError
from .repository_resolver import (
    RepositoryHttpResult,
    RepositoryResolver,
    RepositoryResolutionError,
    RepositoryResponseError,
)


class FavoriteRepositoryUnavailableError(RepositoryResponseError):
    """The favorites source is unavailable or its shape is unconfirmed."""


FavoriteRequester = Callable[[str], RepositoryHttpResult]


class _FavoriteBookCardParser(HTMLParser):
    """Collect links only while inside verified CSS-module Book cards."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._book_depth = 0
        self._stack: list[tuple[str, bool]] = []
        self.hrefs: list[str] = []
        self._void_tags = {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        starts_book_card = any(
            re.fullmatch(r"(?:index-module_)?bookItem_[A-Za-z0-9]+", class_name)
            for class_name in classes
        )
        if tag.lower() not in self._void_tags:
            self._stack.append((tag.lower(), starts_book_card))
            if starts_book_card:
                self._book_depth += 1
        if tag == "a" and self._book_depth > 0:
            href = attributes.get("href")
            if href:
                self.hrefs.append(href)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._void_tags:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        while self._stack:
            open_tag, is_book_card = self._stack.pop()
            if is_book_card:
                self._book_depth -= 1
            if open_tag == tag:
                break


class FavoriteRepositoryProvider:
    """Enumerate only repositories represented by favorites-page Book cards."""

    MARKS_ENDPOINT_TEMPLATE = (
        "https://www.yuque.com/api/mine/marks?limit=20&offset={offset}&type=all&q="
    )
    MAX_HTML_BYTES = 5 * 1024 * 1024
    MAX_BOOK_CARDS = 1000
    MAX_MARK_ACTIONS = 5000

    def __init__(self, requester: FavoriteRequester, page_url: str) -> None:
        self._requester = requester
        self._page_url = page_url

    def list_repositories(self) -> list[Repository]:
        result = self._requester(self._page_url)
        RepositoryResolver.raise_for_status(result.status_code)
        if isinstance(result.payload, str):
            repositories = self._repositories_from_html(result.payload)
            marks_repositories = self._list_marked_books()
            return self._deduplicate([*repositories, *marks_repositories])
        if isinstance(result.payload, dict):
            return self._repositories_from_explicit_json(result.payload)
        raise FavoriteRepositoryUnavailableError("favorites response is not JSON or HTML")

    def _repositories_from_html(self, page_html: str) -> list[Repository]:
        if len(page_html.encode("utf-8")) > self.MAX_HTML_BYTES:
            raise FavoriteRepositoryUnavailableError("favorites page is too large")
        parser = _FavoriteBookCardParser()
        try:
            parser.feed(page_html)
            parser.close()
        except (ValueError, TypeError) as exc:
            raise FavoriteRepositoryUnavailableError(
                "favorites page HTML is invalid"
            ) from exc

        if len(parser.hrefs) > self.MAX_BOOK_CARDS:
            raise FavoriteRepositoryUnavailableError("too many favorites Book cards")
        references = self._references_from_hrefs(parser.hrefs)
        repositories = [
            RepositoryResolver(self._requester).resolve(reference)
            for reference in references
        ]
        return self._deduplicate(repositories)

    def _list_marked_books(self) -> list[Repository]:
        repositories: list[Repository] = []
        offset = 0
        page_size = 20
        while offset < self.MAX_MARK_ACTIONS:
            endpoint = self.MARKS_ENDPOINT_TEMPLATE.format(offset=offset)
            result = self._requester(endpoint)
            RepositoryResolver.raise_for_status(result.status_code)
            page_repositories, action_count, total_items = self._parse_marks_page(
                result.payload
            )
            repositories.extend(page_repositories)
            offset += action_count
            if action_count == 0 or action_count < page_size:
                break
            if total_items is not None and offset >= total_items:
                break
        else:
            raise FavoriteRepositoryUnavailableError("too many favorites marks actions")
        return repositories

    @classmethod
    def _parse_marks_page(
        cls,
        payload: Any,
    ) -> tuple[list[Repository], int, int | None]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise FavoriteRepositoryUnavailableError("favorites marks response is invalid")
        actions = payload["data"].get("actions")
        if not isinstance(actions, list):
            raise FavoriteRepositoryUnavailableError("favorites marks actions are invalid")
        if len(actions) > FavoriteRepositoryProvider.MAX_MARK_ACTIONS:
            raise FavoriteRepositoryUnavailableError("too many favorites marks actions")
        total_items = payload["data"].get("totalItems")
        if total_items is not None and (
            not isinstance(total_items, int)
            or isinstance(total_items, bool)
            or total_items < 0
            or total_items > cls.MAX_MARK_ACTIONS
        ):
            raise FavoriteRepositoryUnavailableError("favorites marks total is invalid")
        repositories = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            target = action.get("target")
            if not isinstance(target, dict) or target.get("type") != "Book":
                continue
            repositories.append(RepositoryResolver.repository_from_payload(target))
        return repositories, len(actions), total_items

    def _repositories_from_explicit_json(self, payload: dict[str, Any]) -> list[Repository]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FavoriteRepositoryUnavailableError("favorites response data is invalid")
        if isinstance(data.get("actions"), list):
            repositories, _, _ = self._parse_marks_page(payload)
            return repositories
        for key in ("books", "repositories"):
            items = data.get(key)
            if isinstance(items, list):
                if len(items) > self.MAX_BOOK_CARDS:
                    raise FavoriteRepositoryUnavailableError(
                        "too many favorites Book items"
                    )
                repositories = []
                for item in items:
                    if not isinstance(item, dict):
                        raise FavoriteRepositoryUnavailableError(
                            "favorites Book item is invalid"
                        )
                    target = item.get("target", item)
                    if not isinstance(target, dict) or target.get("type") != "Book":
                        continue
                    repositories.append(
                        RepositoryResolver.repository_from_payload(target)
                    )
                return self._deduplicate(repositories)
        if isinstance(data.get("cards"), list):
            return []
        raise FavoriteRepositoryUnavailableError("favorites response shape is unconfirmed")

    @staticmethod
    def _references_from_hrefs(hrefs: list[str]) -> list[RepositoryReference]:
        references: list[RepositoryReference] = []
        seen_namespaces: set[str] = set()
        for href in hrefs:
            try:
                candidate = (
                    f"https://www.yuque.com{href}"
                    if href.startswith("/")
                    else href
                )
                parsed = urlparse(candidate)
                host = (parsed.hostname or "").lower()
                if parsed.scheme and (
                    parsed.scheme.lower() != "https"
                    or host not in {"yuque.com", "www.yuque.com"}
                ):
                    continue
                reference = RepositoryReference.parse(candidate)
            except RepositoryReferenceError:
                continue
            namespace = reference.namespace
            if namespace is None or namespace in seen_namespaces:
                continue
            seen_namespaces.add(namespace)
            references.append(reference)
        return references

    @staticmethod
    def _deduplicate(repositories: list[Repository]) -> list[Repository]:
        result = []
        seen_ids: set[int] = set()
        for repository in repositories:
            if repository.id in seen_ids:
                continue
            seen_ids.add(repository.id)
            result.append(repository)
        return result
