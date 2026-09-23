from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlparse

from .mutation_errors import (
    MutationAccessError,
    MutationAuthenticationError,
    MutationConflictError,
    MutationProtocolError,
)


YUQUE_HOSTS = frozenset({"yuque.com", "www.yuque.com"})


class YuqueBrowserWriter:
    """Drive Yuque's visible write flows through an authenticated page.

    This adapter deliberately avoids undocumented HTTP mutations. Selectors are
    kept in this module so a UI change has one fail-closed repair point.
    """

    DASHBOARD_URL = "https://www.yuque.com/dashboard"
    FIND_TIMEOUT = 5
    CLICKABLE_TIMEOUT = 5
    _CREATE_REPOSITORY_BUTTONS = ("text:新建知识库", "text:新建空间")
    _CREATE_REPOSITORY_SUBMIT = ("text:创建知识库", "text:创建")

    def __init__(self, page: Any) -> None:
        self.page = page

    def create_repository(
        self,
        *,
        name: str,
        slug: str | None,
        description: str,
        visibility: str,
    ) -> str:
        self._open_authenticated(self.DASHBOARD_URL)
        self._click(self._CREATE_REPOSITORY_BUTTONS, "new repository")
        self._input(("css:input[name='name']", "css:input[placeholder*='名称']"), name, "name")
        if slug:
            self._input(("css:input[name='slug']", "css:input[placeholder*='slug']"), slug, "slug")
        if description:
            self._input(
                ("css:textarea[name='description']", "css:textarea"),
                description,
                "description",
            )
        if visibility == "public":
            self._click(("text:公开", "css:input[value='public']"), "public visibility")
        elif visibility == "team":
            self._click(("text:组织内公开", "text:团队可见"), "team visibility")
        self._click(self._CREATE_REPOSITORY_SUBMIT, "create repository")
        self._wait_for_page()
        return self._namespace_from_url("created repository")

    def _open_authenticated(self, url: str) -> None:
        self._assert_authenticated()
        try:
            self.page.get(url)
            self._wait_for_page()
        except MutationProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MutationProtocolError("unable to open Yuque write page") from exc
        self._assert_authenticated()

    def _assert_authenticated(self) -> None:
        current = str(getattr(self.page, "url", ""))
        parsed = urlparse(current)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in YUQUE_HOSTS:
            raise MutationProtocolError("Yuque page is not authenticated")
        if parsed.path.rstrip("/") in {"", "/login"} or parsed.path.startswith("/login/"):
            raise MutationAuthenticationError("Yuque browser session is not authenticated")

    def _wait_for_page(self) -> None:
        wait = getattr(self.page, "wait", None)
        load_start = getattr(wait, "load_start", None)
        if callable(load_start):
            try:
                load_start()
            except Exception as exc:  # noqa: BLE001
                raise MutationProtocolError("Yuque page did not finish loading") from exc

    def _find(self, selectors: Iterable[str], label: str) -> Any:
        tried: list[str] = []
        first_seen: Any | None = None
        first_selector: str | None = None
        for selector in selectors:
            tried.append(selector)
            candidates = self._candidates(selector)
            for element in candidates:
                if self._is_actionable(element):
                    return element
            if first_seen is None and candidates:
                first_seen = candidates[0]
                first_selector = selector
        if first_seen is not None:
            raise MutationProtocolError(
                f"Yuque {label} control is not clickable "
                f"(selector={first_selector!r}, state={self._describe(first_seen)}, "
                f"url={self._current_url()})"
            )
        raise MutationProtocolError(
            f"Yuque page does not expose {label} control "
            f"(tried={', '.join(tried)}, url={self._current_url()})"
        )

    def _candidates(self, selector: str) -> list[Any]:
        find_all = getattr(self.page, "eles", None)
        if callable(find_all):
            try:
                found = find_all(selector, timeout=self.FIND_TIMEOUT)
            except Exception:  # noqa: BLE001
                return []
            if found:
                return [element for element in list(found) if not self._is_missing(element)]
        try:
            element = self.page.ele(selector, timeout=self.FIND_TIMEOUT)
        except Exception:  # noqa: BLE001
            return []
        return [] if self._is_missing(element) else [element]

    @staticmethod
    def _is_missing(element: Any) -> bool:
        if element is None:
            return True
        if type(element).__name__ == "NoneElement":
            return True
        try:
            return not element
        except Exception:  # noqa: BLE001
            return True

    def _first_candidate(self, selector: str) -> Any | None:
        candidates = self._candidates(selector)
        return candidates[0] if candidates else None

    def _wait_displayed(self, element: Any) -> None:
        wait = getattr(element, "wait", None)
        displayed = getattr(wait, "displayed", None)
        if callable(displayed):
            try:
                displayed(timeout=self.CLICKABLE_TIMEOUT)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _is_actionable(element: Any) -> bool:
        try:
            states = getattr(element, "states", None)
        except Exception:  # noqa: BLE001
            # NoneElement.__getattr__ raises ElementNotFoundError, not AttributeError.
            return False
        if states is None:
            return True
        try:
            return bool(states.is_displayed) and bool(states.is_enabled)
        except Exception:  # noqa: BLE001
            return True

    def _describe(self, element: Any) -> str:
        try:
            states = getattr(element, "states", None)
        except Exception:  # noqa: BLE001
            return "missing"
        if states is None:
            return "unknown"
        parts = []
        for name in ("is_displayed", "is_enabled", "is_in_viewport", "has_rect"):
            try:
                parts.append(f"{name}={bool(getattr(states, name))}")
            except Exception:  # noqa: BLE001
                parts.append(f"{name}=unknown")
        return ",".join(parts)

    def _current_url(self) -> str:
        return str(getattr(self.page, "url", ""))

    def _click(self, selectors: Iterable[str], label: str) -> None:
        element = self._find(selectors, label)
        try:
            self._scroll_into_view(element)
            self._wait_clickable(element)
            element.click()
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if "conflict" in message or "duplicate" in message:
                raise MutationConflictError(f"Yuque rejected duplicate {label}") from exc
            if "permission" in message or "forbidden" in message:
                raise MutationAccessError(f"Yuque denied {label}") from exc
            raise MutationProtocolError(
                f"unable to activate {label} control "
                f"(state={self._describe(element)}, url={self._current_url()})"
            ) from exc

    @staticmethod
    def _scroll_into_view(element: Any) -> None:
        scroll = getattr(element, "scroll", None)
        to_see = getattr(scroll, "to_see", None)
        if callable(to_see):
            try:
                to_see()
            except Exception:  # noqa: BLE001
                pass

    def _wait_clickable(self, element: Any) -> None:
        wait = getattr(element, "wait", None)
        clickable = getattr(wait, "clickable", None)
        if callable(clickable):
            try:
                clickable(timeout=self.CLICKABLE_TIMEOUT)
            except Exception:  # noqa: BLE001
                pass

    def _input(self, selectors: Iterable[str], value: str, label: str) -> None:
        element = self._find(selectors, label)
        try:
            self._scroll_into_view(element)
            self._wait_displayed(element)
            element.input(value)
        except Exception as exc:  # noqa: BLE001
            raise MutationProtocolError(
                f"unable to fill Yuque {label} "
                f"(state={self._describe(element)}, url={self._current_url()})"
            ) from exc

    def _namespace_from_url(self, label: str) -> str:
        parsed = self._validated_url(label)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            raise MutationProtocolError(f"Yuque did not confirm {label}")
        return "/".join(parts)

    def _validated_url(self, label: str):
        current = str(getattr(self.page, "url", ""))
        parsed = urlparse(current)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in YUQUE_HOSTS:
            raise MutationProtocolError(f"Yuque did not confirm {label}")
        if parsed.query or parsed.fragment:
            raise MutationProtocolError(f"Yuque returned an ambiguous {label} URL")
        return parsed
