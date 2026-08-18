from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

from core.exporter import DocumentExporter  # type: ignore  # noqa: E402
from core.models import Document  # type: ignore  # noqa: E402


class RecordingImageClient:
    def __init__(self, failing_urls: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.failing_urls = failing_urls or set()

    def download_external_image(self, url: str, save_path: str | Path, **_kwargs: Any) -> bool:
        self.calls.append(url)
        if url in self.failing_urls:
            return False
        Path(save_path).write_bytes(f"image:{url}".encode("utf-8"))
        return True


def _image_targets(markdown: str) -> list[str]:
    return re.findall(r"!\[[^]]*\]\((\./[^)]+)\)", markdown)


def test_add_metadata_writes_parseable_unindented_front_matter(tmp_path: Path) -> None:
    markdown_path = tmp_path / "metadata.md"
    markdown_path.write_text("正文\n", encoding="utf-8")
    doc = Document(id=1, title='A: "quoted"', slug="docs/1", doc_id=1, book_id=2)

    DocumentExporter().add_metadata(markdown_path, doc)

    metadata = markdown_path.read_text(encoding="utf-8").split("\n\n", 1)[0]
    assert "\t" not in metadata
    assert metadata.startswith("---\ntitle: ")
    assert 'title: "A: \\"quoted\\""' in metadata
    assert metadata.endswith("\n---")


def test_localize_images_rewrites_successful_http_image_to_document_assets(tmp_path: Path) -> None:
    markdown_path = tmp_path / "guide.md"
    source_url = "https://images.example.test/diagrams/overview.png?version=2"
    markdown_path.write_text(f"Before ![overview]({source_url}) after\n", encoding="utf-8")
    client = RecordingImageClient()

    result = DocumentExporter().localize_images(markdown_path, client.download_external_image)

    localized = markdown_path.read_text(encoding="utf-8")
    targets = _image_targets(localized)
    assert result.__class__.__name__ == "ImageLocalizationResult"
    assert result.downloaded_count == 1
    assert result.failed_urls == ()
    assert client.calls == [source_url]
    assert len(targets) == 1
    assert targets == [f"./guide.assets/{Path(targets[0]).name}"]
    assert (tmp_path / targets[0][2:]).read_bytes() == f"image:{source_url}".encode("utf-8")


def test_localize_images_keeps_failed_urls_and_does_not_create_empty_assets_dir(tmp_path: Path) -> None:
    markdown_path = tmp_path / "failed.md"
    source_url = "https://images.example.test/unavailable.png"
    markdown_path.write_text(f"![unavailable]({source_url})\n", encoding="utf-8")
    client = RecordingImageClient(failing_urls={source_url})

    result = DocumentExporter().localize_images(markdown_path, client.download_external_image)

    assert markdown_path.read_text(encoding="utf-8") == f"![unavailable]({source_url})\n"
    assert result.downloaded_count == 0
    assert result.failed_urls == (source_url,)
    assert client.calls == [source_url]
    assert not (tmp_path / "failed.assets").exists()


def test_localize_images_downloads_duplicate_url_once_and_reuses_its_reference(tmp_path: Path) -> None:
    markdown_path = tmp_path / "duplicates.md"
    source_url = "http://images.example.test/shared.png"
    markdown_path.write_text(
        f"![first]({source_url}) and ![second]({source_url})\n",
        encoding="utf-8",
    )
    client = RecordingImageClient()

    result = DocumentExporter().localize_images(markdown_path, client.download_external_image)

    targets = _image_targets(markdown_path.read_text(encoding="utf-8"))
    assert result.downloaded_count == 1
    assert client.calls == [source_url]
    assert len(targets) == 2
    assert targets[0] == targets[1]
    assert (tmp_path / targets[0][2:]).is_file()


def test_localize_images_gives_same_basename_urls_distinct_stable_names(tmp_path: Path) -> None:
    source = (
        "![one](https://one.example.test/path/image.png)\n"
        "![two](https://two.example.test/other/image.png)\n"
    )

    first_path = tmp_path / "first.md"
    first_path.write_text(source, encoding="utf-8")
    first_client = RecordingImageClient()
    DocumentExporter().localize_images(first_path, first_client.download_external_image)
    first_names = [Path(target).name for target in _image_targets(first_path.read_text(encoding="utf-8"))]

    second_path = tmp_path / "second.md"
    second_path.write_text(source, encoding="utf-8")
    second_client = RecordingImageClient()
    DocumentExporter().localize_images(second_path, second_client.download_external_image)
    second_names = [Path(target).name for target in _image_targets(second_path.read_text(encoding="utf-8"))]

    assert len(first_names) == 2
    assert len(set(first_names)) == 2
    assert first_names == second_names
    assert all((tmp_path / "first.assets" / name).is_file() for name in first_names)
    assert all((tmp_path / "second.assets" / name).is_file() for name in second_names)


def test_localize_images_ignores_non_network_images_and_fenced_code_blocks(tmp_path: Path) -> None:
    markdown_path = tmp_path / "literal.md"
    source = (
        "![relative](./image.png)\n"
        "![ftp](ftp://images.example.test/image.png)\n"
        "![data](data:image/png;base64,AAAA)\n"
        "```markdown\n"
        "![example](https://images.example.test/example.png)\n"
        "```\n"
    )
    markdown_path.write_text(source, encoding="utf-8")
    client = RecordingImageClient()

    result = DocumentExporter().localize_images(markdown_path, client.download_external_image)

    assert markdown_path.read_text(encoding="utf-8") == source
    assert result.downloaded_count == 0
    assert result.failed_urls == ()
    assert client.calls == []
    assert not (tmp_path / "literal.assets").exists()


def test_localize_images_ignores_inline_code_and_escaped_images(tmp_path: Path) -> None:
    markdown_path = tmp_path / "examples.md"
    source = (
        "`![inline](https://images.example.test/inline.png)`\n"
        "\\![escaped](https://images.example.test/escaped.png)\n"
    )
    markdown_path.write_text(source, encoding="utf-8")
    client = RecordingImageClient()

    result = DocumentExporter().localize_images(markdown_path, client.download_external_image)

    assert result.found_count == 0
    assert client.calls == []
    assert markdown_path.read_text(encoding="utf-8") == source


def test_localize_images_stops_after_document_byte_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    markdown_path = tmp_path / "budget.md"
    urls = [f"https://images.example.test/{index}.png" for index in range(3)]
    markdown_path.write_text(
        "\n".join(f"![{index}]({url})" for index, url in enumerate(urls)),
        encoding="utf-8",
    )
    calls: list[str] = []

    def download(url: str, save_path: Path) -> bool:
        calls.append(url)
        save_path.write_bytes(b"1234")
        return True

    exporter = DocumentExporter()
    monkeypatch.setattr(exporter, "MAX_IMAGE_BYTES_PER_DOCUMENT", 5)

    result = exporter.localize_images(markdown_path, download)

    assert calls == urls[:2]
    assert result.downloaded_count == 1
    assert result.skipped_count == 2
    assert len(list((tmp_path / "budget.assets").iterdir())) == 1
