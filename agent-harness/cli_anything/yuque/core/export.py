from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from .audit import append_audit
from .auth import ProfileAuth
from .project import ensure_src_on_path


ensure_src_on_path()

from core.auth import YuqueAuth  # type: ignore  # noqa: E402
from core.client import ExportType, YuqueClient  # type: ignore  # noqa: E402
from core.exporter import DocumentExporter  # type: ignore  # noqa: E402
from utils.browser import BrowserManager  # type: ignore  # noqa: E402


FORMAT_TO_EXPORT_TYPE = {
    "markdown": ExportType.MARKDOWN,
    "pdf": ExportType.PDF,
    "word": ExportType.WORD,
    "lake": ExportType.LAKEBOOK,
}


class ExportService:
    def __init__(self, profile: str, output_dir: Optional[str] = None):
        self.profile = profile
        self.output_dir = Path(output_dir).expanduser() if output_dir else None

    def run(
        self,
        repo_id: int,
        fmt: str,
        all_docs: bool,
        node_uuids: Iterable[str],
    ) -> Dict[str, Any]:
        auth = ProfileAuth(self.profile)
        auth._sync_profile_to_legacy()
        manager = BrowserManager()
        page = manager.start(headless=True)
        try:
            auth = YuqueAuth()
            auth.load_cookies(page)
            client = YuqueClient(page)
            repos = client.get_repositories()
            repo = next((r for r in repos if int(r.id) == int(repo_id)), None)
            if not repo:
                raise ValueError(f"repository not found: {repo_id}")

            nodes = client.get_catalog_nodes(repo)
            selected = _select_nodes(nodes, all_docs=all_docs, node_uuids=set(node_uuids))

            exporter = DocumentExporter(output_dir=self.output_dir)
            export_type = FORMAT_TO_EXPORT_TYPE[fmt]

            exported = []
            path_map = _build_path_map(nodes)
            for doc in selected:
                full_path = path_map.get(doc.uuid, "")
                path_parts = full_path.split("/") if full_path else []
                rel_dir = "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
                extension = ".md" if fmt == "markdown" else f".{fmt}"
                save_path = exporter.get_save_path(doc, repo.name, extension=extension, relative_path=rel_dir)

                if doc.type == "TITLE":
                    exported.append({"doc": asdict(doc), "status": "directory", "path": str(save_path.parent)})
                    continue

                url = client.export_document(doc, export_type)
                if url == "EMPTY_DOC":
                    save_path.touch(exist_ok=True)
                    if fmt == "markdown":
                        exporter.add_metadata(save_path, doc)
                    exported.append({"doc": asdict(doc), "status": "empty", "path": str(save_path)})
                    continue

                if not url:
                    exported.append({"doc": asdict(doc), "status": "failed", "path": str(save_path)})
                    continue

                ok = client.download_file(url, str(save_path))
                if ok and fmt == "markdown":
                    exporter.add_metadata(save_path, doc)
                exported.append({"doc": asdict(doc), "status": "ok" if ok else "failed", "path": str(save_path)})

            summary = {
                "repo": asdict(repo),
                "format": fmt,
                "requested": len(selected),
                "success": len([x for x in exported if x["status"] in {"ok", "empty", "directory"}]),
                "items": exported,
            }
            append_audit(
                self.profile,
                {
                    "event": "export.run",
                    "repo_id": repo_id,
                    "format": fmt,
                    "requested": summary["requested"],
                    "success": summary["success"],
                },
            )
            return summary
        finally:
            manager.quit()

    def batch(self, repo_ids: Iterable[int], fmt: str, all_docs: bool, node_uuids: Iterable[str]) -> Dict[str, Any]:
        results = [
            self.run(repo_id=r, fmt=fmt, all_docs=all_docs, node_uuids=node_uuids)
            for r in repo_ids
        ]
        return {
            "count": len(results),
            "results": results,
        }


def _build_path_map(nodes: List[Any]) -> Dict[str, str]:
    node_map = {node.uuid: node for node in nodes}
    result: Dict[str, str] = {}
    for node in nodes:
        parts = []
        current = node
        visited: Set[str] = set()
        while current:
            if current.uuid in visited:
                raise ValueError(f"cycle detected in catalog nodes at uuid={current.uuid}")
            visited.add(current.uuid)
            parts.insert(0, current.title)
            current = node_map.get(current.parent_uuid)
        result[node.uuid] = "/".join(parts)
    return result


def _collect_descendants(start: Any, children_map: Dict[str, List[Any]], acc: Set[str]) -> None:
    stack: List[Any] = [start]
    while stack:
        node = stack.pop()
        if node.uuid in acc:
            continue
        acc.add(node.uuid)
        stack.extend(children_map.get(node.uuid, []))


def _select_nodes(nodes: List[Any], all_docs: bool, node_uuids: Set[str]) -> List[Any]:
    if all_docs:
        return list(nodes)
    if not node_uuids:
        return []

    node_map = {node.uuid: node for node in nodes}
    children_map: Dict[str, List[Any]] = {}
    for node in nodes:
        children_map.setdefault(node.parent_uuid, []).append(node)

    final_uuids: Set[str] = set()
    for uuid in node_uuids:
        node = node_map.get(uuid)
        if node:
            _collect_descendants(node, children_map, final_uuids)

    return [node for node in nodes if node.uuid in final_uuids]
