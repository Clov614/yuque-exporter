"""Repair previously exported Markdown image references for preview renderers.

Older exports wrote sibling asset references verbatim, e.g.::

    ![](./my doc.assets/image-ab12cd34ef.png)

Per CommonMark, a link destination inside ``![](...)`` must not contain a
raw space, so any asset directory derived from a title with spaces renders
as a broken image even though the file exists on disk.

This script rewrites those references to the same encoding used by new
exports (percent-encoded path wrapped in angle brackets)::

    ![](<./my%20doc.assets/image-ab12cd34ef.png>)

Only Markdown text is changed; no files are renamed, moved, or deleted.
Safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.exporter import DocumentExporter

_IMAGE_REF_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]\n]*)\]\(\s*(?P<dest>\./[^\s\)<>][^\)\n]*\.assets/[^\s\)\n]+)\s*\)"
)


def repair_content(content: str) -> tuple[str, int]:
    fixed = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal fixed
        formatted = DocumentExporter.format_asset_destination(match.group("dest"))
        fixed += 1
        return f"![{match.group('alt')}]({formatted})"

    return _IMAGE_REF_PATTERN.sub(_replace, content), fixed


def repair_file(path: Path) -> int:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"skip {path}: {exc}")
        return 0
    updated, fixed = repair_content(content)
    if fixed and updated != content:
        try:
            DocumentExporter._atomic_write_text(path, updated)
        except OSError as exc:
            print(f"skip {path}: {exc}")
            return 0
    return fixed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        default="yuque_export",
        help="export directory to scan (default: yuque_export)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"directory not found: {root}")
        return 2

    files = sorted(root.rglob("*.md"))
    total_refs = 0
    touched = 0
    for path in files:
        fixed = repair_file(path)
        if fixed:
            touched += 1
            total_refs += fixed

    print(f"scanned {len(files)} markdown files, repaired {total_refs} references in {touched} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
