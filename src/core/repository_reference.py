"""Parse stable references to Yuque repositories."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import unquote, urlparse


class RepositoryReferenceError(ValueError):
    """Raised when a repository reference is invalid or unsafe."""


class RepositoryReferenceKind(str, Enum):
    """Supported repository reference kinds."""

    ID = "id"
    NAMESPACE = "namespace"


@dataclass(frozen=True)
class RepositoryReference:
    """An immutable, normalized repository identifier."""

    kind: RepositoryReferenceKind
    value: int | str

    def __post_init__(self) -> None:
        valid_id = (
            self.kind is RepositoryReferenceKind.ID
            and isinstance(self.value, int)
            and not isinstance(self.value, bool)
            and self.value > 0
        )
        valid_namespace = (
            self.kind is RepositoryReferenceKind.NAMESPACE
            and isinstance(self.value, str)
            and bool(self.value)
        )
        if not valid_id and not valid_namespace:
            raise RepositoryReferenceError("repository reference kind and value do not match")

    @classmethod
    def from_selector(
        cls,
        *,
        repository_id: int | None = None,
        reference: str | None = None,
    ) -> "RepositoryReference":
        """Build a reference from two mutually exclusive selector fields."""
        if (repository_id is None) == (reference is None):
            raise RepositoryReferenceError(
                "provide exactly one repository ID or repository reference"
            )
        if repository_id is not None:
            return cls.parse(repository_id)
        assert reference is not None
        return cls.parse(reference)

    @classmethod
    def parse(cls, value: int | str) -> "RepositoryReference":
        """Parse a positive ID, ``owner/slug`` namespace, or Yuque URL."""
        if isinstance(value, bool):
            raise RepositoryReferenceError("repository ID must be a positive integer")
        if isinstance(value, int):
            return cls._from_id(value)
        if not isinstance(value, str):
            raise RepositoryReferenceError("repository reference must be text or an integer")

        normalized = value.strip()
        if not normalized:
            raise RepositoryReferenceError("repository reference cannot be empty")
        if normalized.isdecimal():
            return cls._from_id(int(normalized))
        if "://" in normalized:
            return cls._from_url(normalized)
        return cls._from_namespace(normalized)

    @classmethod
    def _from_id(cls, value: int) -> "RepositoryReference":
        if value <= 0:
            raise RepositoryReferenceError("repository ID must be a positive integer")
        return cls(kind=RepositoryReferenceKind.ID, value=value)

    @classmethod
    def _from_url(cls, value: str) -> "RepositoryReference":
        try:
            parsed = urlparse(value)
            port = parsed.port
        except ValueError as exc:
            raise RepositoryReferenceError("invalid Yuque repository URL") from exc

        if parsed.scheme.lower() != "https":
            raise RepositoryReferenceError("Yuque repository URL must use HTTPS")
        if parsed.hostname not in {"yuque.com", "www.yuque.com"}:
            raise RepositoryReferenceError("repository URL must use yuque.com")
        if parsed.username is not None or parsed.password is not None or port is not None:
            raise RepositoryReferenceError("repository URL contains unsupported authority data")

        raw_path = parsed.path.rstrip("/")
        if not raw_path.startswith("/"):
            raise RepositoryReferenceError("invalid Yuque repository URL path")
        return cls._from_namespace(raw_path[1:], encoded=True)

    @classmethod
    def _from_namespace(
        cls,
        value: str,
        *,
        encoded: bool = False,
    ) -> "RepositoryReference":
        if any(character in value for character in ("?", "#", "\\")):
            raise RepositoryReferenceError("namespace must use the owner/slug form")

        raw_parts = value.split("/")
        if len(raw_parts) != 2 or any(not part for part in raw_parts):
            raise RepositoryReferenceError("namespace must contain exactly owner/slug")

        parts = tuple(cls._decode_segment(part) for part in raw_parts)
        if any(cls._unsafe_segment(part) for part in parts):
            raise RepositoryReferenceError("namespace contains unsafe path data")
        namespace = "/".join(parts)
        return cls(kind=RepositoryReferenceKind.NAMESPACE, value=namespace)

    @classmethod
    def _decode_segment(cls, value: str) -> str:
        current = value
        for _ in range(max(1, len(value)) + 1):
            decoded = unquote(current)
            if any(character in decoded for character in ("/", "\\", "?", "#")):
                raise RepositoryReferenceError("namespace contains encoded path data")
            if decoded == current:
                if "%" in decoded:
                    raise RepositoryReferenceError("namespace contains invalid encoding")
                return decoded
            current = decoded
        raise RepositoryReferenceError("namespace encoding is too deeply nested")

    @staticmethod
    def _unsafe_segment(value: str) -> bool:
        return (
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or "?" in value
            or "#" in value
            or any(character.isspace() or ord(character) < 32 for character in value)
        )

    @property
    def canonical(self) -> str:
        """Return the normalized reference suitable for logs and equality checks."""
        return str(self.value)

    @property
    def repository_id(self) -> int | None:
        """Return the numeric ID when this is an ID reference."""
        if self.kind is RepositoryReferenceKind.ID and isinstance(self.value, int):
            return self.value
        return None

    @property
    def namespace(self) -> str | None:
        """Return ``owner/slug`` when this is a namespace reference."""
        if self.kind is RepositoryReferenceKind.NAMESPACE and isinstance(self.value, str):
            return self.value
        return None
