from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .mutation_errors import MarkdownInputError


DEFAULT_MAX_MARKDOWN_BYTES = 10 * 1024 * 1024
_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
_TITLE_RE = re.compile(r"^\s*#\s+(.+?)\s*#*\s*$", re.MULTILINE)
_FRONT_MATTER_TITLE_RE = re.compile(r"^title\s*:\s*(?P<value>.*)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class MarkdownDocument:
    path: Path
    title: str
    body: str
    byte_length: int


def read_markdown(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_MARKDOWN_BYTES,
) -> MarkdownDocument:
    """Read and validate one UTF-8 Markdown document without mutating the file."""
    if max_bytes <= 0:
        raise MarkdownInputError("Markdown file size limit must be positive")

    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise MarkdownInputError("Markdown symlinks are not supported")
    if not candidate.exists():
        raise MarkdownInputError("Markdown file does not exist")
    if not candidate.is_file():
        raise MarkdownInputError("Markdown path must be a regular file")
    if candidate.suffix.lower() != ".md":
        raise MarkdownInputError("Markdown file must use the .md extension")

    resolved = candidate.resolve()
    try:
        byte_length = resolved.stat().st_size
    except OSError as exc:
        raise MarkdownInputError("Unable to inspect Markdown file") from exc
    if byte_length > max_bytes:
        raise MarkdownInputError("Markdown file exceeds the configured size limit")

    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise MarkdownInputError("Unable to read Markdown file") from exc
    if len(raw) > max_bytes:
        raise MarkdownInputError("Markdown file exceeds the configured size limit")
    try:
        body = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MarkdownInputError("Markdown file encoding must be valid UTF-8") from exc

    title = _title_from_body(body) or resolved.stem
    title = " ".join(title.split())
    if not title:
        raise MarkdownInputError("Markdown title cannot be empty")
    return MarkdownDocument(
        path=resolved,
        title=title,
        body=body,
        byte_length=len(raw),
    )


def _title_from_body(body: str) -> str | None:
    front_matter = _FRONT_MATTER_RE.match(body)
    if front_matter:
        title_match = _FRONT_MATTER_TITLE_RE.search(front_matter.group("body"))
        if title_match:
            value = title_match.group("value").strip().strip("'\"")
            if value:
                return value
    heading = _TITLE_RE.search(body)
    return heading.group(1) if heading else None
