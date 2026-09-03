from __future__ import annotations

from pathlib import Path

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path

ensure_src_on_path()

from core.markdown_input import DEFAULT_MAX_MARKDOWN_BYTES, read_markdown  # type: ignore  # noqa: E402
from core.mutation_errors import MarkdownInputError  # type: ignore  # noqa: E402


def test_read_markdown_accepts_utf8_bom_and_front_matter_title(tmp_path: Path) -> None:
    source = "---\ntitle: 自定义标题\n---\n\n# 正文标题\n\n内容"
    path = tmp_path / "note.md"
    path.write_bytes(b"\xef\xbb\xbf" + source.encode("utf-8"))

    document = read_markdown(path)

    assert document.title == "自定义标题"
    assert document.body == source
    assert document.path == path.resolve()


def test_read_markdown_uses_first_h1_then_filename(tmp_path: Path) -> None:
    with_heading = tmp_path / "ignored-name.md"
    with_heading.write_text("intro\n# Heading\n", encoding="utf-8")
    without_heading = tmp_path / "file-name.md"
    without_heading.write_text("plain body", encoding="utf-8")

    assert read_markdown(with_heading).title == "Heading"
    assert read_markdown(without_heading).title == "file-name"


@pytest.mark.parametrize(
    "factory",
    [
        lambda path: path.with_suffix(".txt"),
        lambda path: path.parent,
    ],
)
def test_read_markdown_rejects_non_markdown_and_directories(
    tmp_path: Path, factory
) -> None:
    path = tmp_path / "note.md"
    path.write_text("content", encoding="utf-8")
    target = factory(path)
    if target.suffix == ".txt":
        target.write_text("content", encoding="utf-8")

    with pytest.raises(MarkdownInputError):
        read_markdown(target)


def test_read_markdown_rejects_invalid_encoding(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff\xfe\xfd")

    with pytest.raises(MarkdownInputError, match="encoding"):
        read_markdown(path)


def test_read_markdown_rejects_oversized_input(tmp_path: Path) -> None:
    path = tmp_path / "large.md"
    path.write_text("x", encoding="utf-8")

    with pytest.raises(MarkdownInputError, match="size"):
        read_markdown(path, max_bytes=0)


def test_default_markdown_limit_is_bounded() -> None:
    assert DEFAULT_MAX_MARKDOWN_BYTES > 0
