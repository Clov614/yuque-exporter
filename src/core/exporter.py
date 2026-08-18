"""
文档导出器
==========
负责文件系统操作，保存文档内容
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional
from urllib.parse import unquote, urldefrag, urlparse

from .models import Document


@dataclass(frozen=True)
class ImageLocalizationResult:
    """Markdown 图片本地化结果。"""

    found_count: int = 0
    downloaded_count: int = 0
    failed_urls: tuple[str, ...] = ()
    skipped_count: int = 0

    @property
    def images_found(self) -> int:
        return self.found_count

    @property
    def images_downloaded(self) -> int:
        return self.downloaded_count

    @property
    def images_failed(self) -> int:
        return len(self.failed_urls)


@dataclass(frozen=True)
class _MarkdownImageMatch:
    url: str
    url_start: int
    url_end: int


_INLINE_IMAGE_PATTERN = re.compile(
    r"!\[[^\]\n]*\]\(\s*"
    r"(?P<url><https?://[^>\n]+>|https?://[^\s\)]+)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
_FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")


class DocumentExporter:
    """文档导出工具类"""

    DEFAULT_OUTPUT_DIR = Path("./yuque_export")
    MAX_IMAGES_PER_DOCUMENT = 100
    MAX_IMAGE_BYTES_PER_DOCUMENT = 100 * 1024 * 1024
    MAX_IMAGE_SECONDS_PER_DOCUMENT = 300

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or self.DEFAULT_OUTPUT_DIR

    def get_save_path(
        self,
        doc: Document,
        repo_name: str,
        extension: str = ".md",
        relative_path: str = "",
    ) -> Path:
        """
        生成并创建保存目录
        Args:
            doc: 文档对象
            repo_name: 知识库名称
            extension: 扩展名
            relative_path: 相对目录路径 (用于保持层级结构)
        """
        safe_repo = self._sanitize_filename(repo_name)
        save_dir = self.output_dir / safe_repo

        if relative_path:
            parts = [self._sanitize_filename(p) for p in relative_path.split("/") if p]
            save_dir = save_dir.joinpath(*parts)

        save_dir.mkdir(parents=True, exist_ok=True)
        filename = self._sanitize_filename(doc.title) + extension
        return save_dir / filename

    def localize_images(
        self,
        markdown_path: Path,
        download_image: Callable[[str, Path], bool],
    ) -> ImageLocalizationResult:
        """下载 Markdown 中的网络图片，并将成功项改写为本地引用。"""
        if not markdown_path.is_file():
            return ImageLocalizationResult()
        try:
            content = markdown_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"⚠️ 读取 Markdown 图片失败: {exc}")
            return ImageLocalizationResult()

        matches = list(self._iter_image_matches(content))
        if not matches:
            return ImageLocalizationResult()

        assets_dir = markdown_path.parent / f"{markdown_path.stem}.assets"
        (
            replacements,
            downloaded_count,
            skipped_count,
            failed_urls,
            downloaded_urls,
        ) = self._collect_localized_assets(
            matches, markdown_path, assets_dir, download_image
        )

        if replacements:
            updated_content = self._replace_image_references(content, matches, replacements)
            if updated_content != content:
                try:
                    self._atomic_write_text(markdown_path, updated_content)
                except OSError as exc:
                    print(f"⚠️ 写回 Markdown 图片引用失败: {exc}")
                    for url in downloaded_urls:
                        asset_path = assets_dir / self._asset_filename(url)
                        try:
                            asset_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    failed_urls.extend(downloaded_urls)
                    downloaded_count = 0

        if assets_dir.is_dir():
            try:
                if not any(assets_dir.iterdir()):
                    assets_dir.rmdir()
            except OSError:
                pass

        return ImageLocalizationResult(
            found_count=len(matches),
            downloaded_count=downloaded_count,
            failed_urls=tuple(failed_urls),
            skipped_count=skipped_count,
        )

    def _collect_localized_assets(
        self,
        matches: list[_MarkdownImageMatch],
        markdown_path: Path,
        assets_dir: Path,
        download_image: Callable[[str, Path], bool],
    ) -> tuple[dict[str, str], int, int, list[str], list[str]]:
        downloaded_count = 0
        skipped_count = 0
        failed_urls: list[str] = []
        downloaded_urls: list[str] = []
        replacements: dict[str, str] = {}
        processed_urls: set[str] = set()
        downloaded_bytes = 0
        deadline = time.monotonic() + self.MAX_IMAGE_SECONDS_PER_DOCUMENT
        for match in matches:
            normalized_url = urldefrag(match.url)[0]
            if normalized_url in replacements or normalized_url in failed_urls:
                continue
            if normalized_url in processed_urls:
                continue
            processed_urls.add(normalized_url)
            if len(processed_urls) > self.MAX_IMAGES_PER_DOCUMENT:
                skipped_count += 1
                continue
            if time.monotonic() >= deadline:
                skipped_count += 1
                continue
            if downloaded_bytes >= self.MAX_IMAGE_BYTES_PER_DOCUMENT:
                skipped_count += 1
                continue
            relative_path, status, asset_size = self._localize_asset(
                normalized_url, markdown_path, assets_dir, download_image
            )
            if status == "downloaded" and (
                downloaded_bytes + asset_size > self.MAX_IMAGE_BYTES_PER_DOCUMENT
            ):
                asset_path = assets_dir / self._asset_filename(normalized_url)
                try:
                    asset_path.unlink(missing_ok=True)
                except OSError:
                    pass
                relative_path, status = None, "budget"
                downloaded_bytes = self.MAX_IMAGE_BYTES_PER_DOCUMENT
            if status == "downloaded":
                downloaded_count += 1
                downloaded_bytes += asset_size
                downloaded_urls.append(normalized_url)
            elif status in {"skipped", "budget"}:
                skipped_count += 1
            else:
                failed_urls.append(normalized_url)
            if relative_path:
                replacements[normalized_url] = relative_path
        return (
            replacements,
            downloaded_count,
            skipped_count,
            failed_urls,
            downloaded_urls,
        )

    def _localize_asset(
        self,
        url: str,
        markdown_path: Path,
        assets_dir: Path,
        download_image: Callable[[str, Path], bool],
    ) -> tuple[Optional[str], str, int]:
        asset_name = self._asset_filename(url)
        asset_path = assets_dir / asset_name
        relative_path = f"./{markdown_path.stem}.assets/{asset_name}"
        if asset_path.is_file() and asset_path.stat().st_size > 0:
            return relative_path, "skipped", asset_path.stat().st_size

        try:
            assets_dir.mkdir(parents=True, exist_ok=True)
            ok = bool(download_image(url, asset_path))
        except (OSError, ValueError) as exc:
            print(f"⚠️ 下载图片失败 {url[:100]}: {exc}")
            ok = False
        if ok and asset_path.is_file() and asset_path.stat().st_size > 0:
            return relative_path, "downloaded", asset_path.stat().st_size
        if asset_path.exists():
            try:
                asset_path.unlink()
            except OSError:
                pass
        return None, "failed", 0

    @staticmethod
    def _replace_image_references(
        content: str,
        matches: list[_MarkdownImageMatch],
        replacements: dict[str, str],
    ) -> str:
        updated_content = content
        for match in reversed(matches):
            replacement = replacements.get(urldefrag(match.url)[0])
            if replacement is not None:
                updated_content = (
                    updated_content[: match.url_start]
                    + replacement
                    + updated_content[match.url_end :]
                )
        return updated_content

    def add_metadata(self, filepath: Path, doc: Document) -> None:
        """为 Markdown 文件添加 Front Matter"""
        if not filepath.exists():
            return

        try:
            content = filepath.read_text(encoding="utf-8")
            if content.startswith("---"):
                return

            metadata_values = {
                "title": doc.title,
                "url": doc.slug,
                "doc_id": doc.doc_id,
                "book_id": doc.book_id,
                "created_at": doc.created_at,
                "updated_at": doc.updated_at,
                "exported_at": datetime.now().isoformat(),
            }
            metadata = "---\n" + "\n".join(
                f"{key}: {self._yaml_scalar(value)}"
                for key, value in metadata_values.items()
            ) + "\n---\n\n"
            self._atomic_write_text(filepath, metadata + content)
        except Exception as e:
            print(f"⚠️ 添加元数据失败: {e}")

    @staticmethod
    def _yaml_scalar(value: object) -> str:
        if value is None:
            return "null"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return json.dumps(str(value), ensure_ascii=False)

    def _sanitize_filename(self, name: str) -> str:
        """文件名去除非法字符"""
        name = re.sub(r'[<>:"/\\|?*]', "_", name)
        name = re.sub(r"[\x00-\x1f\x7f]", "", name)
        name = name.strip().strip(".")

        if not name:
            name = "Untitled"

        return name[:100]

    def _asset_filename(self, url: str) -> str:
        parsed = urlparse(url)
        raw_name = unquote(PurePosixPath(parsed.path).name)
        safe_name = self._sanitize_filename(raw_name)
        stem, suffix = os.path.splitext(safe_name)
        if not stem or stem == "Untitled":
            stem = "image"
        if not suffix or len(suffix) > 12:
            suffix = ".img"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        return f"{stem[:80]}-{digest}{suffix.lower()}"

    @staticmethod
    def _iter_image_matches(content: str) -> Iterable[_MarkdownImageMatch]:
        in_fence = False
        fence_char = ""
        offset = 0
        for line in content.splitlines(keepends=True):
            fence = _FENCE_PATTERN.match(line)
            if fence:
                marker = fence.group(1)[0]
                if not in_fence:
                    in_fence = True
                    fence_char = marker
                elif marker == fence_char:
                    in_fence = False
                offset += len(line)
                continue

            if not in_fence:
                scan_line = DocumentExporter._mask_inline_code(line)
                for match in _INLINE_IMAGE_PATTERN.finditer(scan_line):
                    bang_position = match.start()
                    escaped = bang_position > 0 and line[bang_position - 1] == "\\"
                    if escaped:
                        continue
                    raw_url = match.group("url")
                    url = raw_url[1:-1] if raw_url.startswith("<") else raw_url
                    yield _MarkdownImageMatch(
                        url=url,
                        url_start=offset + match.start("url") + (1 if raw_url.startswith("<") else 0),
                        url_end=offset + match.end("url") - (1 if raw_url.endswith(">") else 0),
                    )
            offset += len(line)

    @staticmethod
    def _mask_inline_code(line: str) -> str:
        masked = list(line)
        position = 0
        while position < len(line):
            if line[position] != "`" or (position > 0 and line[position - 1] == "\\"):
                position += 1
                continue
            end = position
            while end < len(line) and line[end] == "`":
                end += 1
            marker = line[position:end]
            closing = line.find(marker, end)
            if closing < 0:
                position = end
                continue
            for index in range(position, closing + len(marker)):
                if masked[index] != "\n":
                    masked[index] = " "
            position = closing + len(marker)
        return "".join(masked)

    @staticmethod
    def _atomic_write_text(filepath: Path, content: str) -> None:
        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=filepath.parent,
                prefix=f".{filepath.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, filepath)
            temp_path = None
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()
