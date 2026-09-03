from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from .audit import append_audit
from .auth import ProfileAuth
from .project import ensure_src_on_path


ensure_src_on_path()

from core.client import ExportType, YuqueClient  # type: ignore  # noqa: E402
from core.exporter import DocumentExporter  # type: ignore  # noqa: E402
from core.incremental import (  # type: ignore  # noqa: E402
    finalize as finalize_incremental,
)
from core.incremental import (  # type: ignore  # noqa: E402
    plan_incremental,
    record_exported,
    stamp_metadata,
)
from core.repository_reference import RepositoryReference  # type: ignore  # noqa: E402
from core.repository_resolver import RepositoryAuthenticationError  # type: ignore  # noqa: E402


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
        repo_id: int | None = None,
        fmt: str = "markdown",
        all_docs: bool = False,
        node_uuids: Iterable[str] = (),
        download_images: bool = False,
        repo: str | None = None,
        incremental: bool = False,
    ) -> Dict[str, Any]:
        if fmt not in FORMAT_TO_EXPORT_TYPE:
            raise ValueError(f"unsupported export format: {fmt}")
        if download_images and fmt != "markdown":
            raise ValueError("download_images requires markdown format")
        if incremental and fmt != "markdown":
            raise ValueError("incremental export requires markdown format")
        node_uuids = tuple(node_uuids)
        if all_docs == bool(node_uuids):
            raise ValueError("provide exactly one of all_docs or node_uuids")

        reference = RepositoryReference.from_selector(
            repository_id=repo_id,
            reference=repo,
        )
        profile_auth = ProfileAuth(self.profile)
        manager = profile_auth.browser_manager()
        page = manager.start(headless=True)
        try:
            auth = profile_auth.auth()
            if not auth.load_cookies(page):
                raise RepositoryAuthenticationError("profile is not authenticated")
            client = YuqueClient(page, auth=auth)
            repository = client.get_repository(reference)

            nodes = client.get_catalog_nodes(repository)
            selected = _select_nodes(nodes, all_docs=all_docs, node_uuids=set(node_uuids))

            exporter = DocumentExporter(output_dir=self.output_dir)
            export_type = FORMAT_TO_EXPORT_TYPE[fmt]

            exported = []
            image_summary = {
                "enabled": download_images,
                "found": 0,
                "downloaded": 0,
                "failed": 0,
                "skipped": 0,
            }
            path_map = _build_path_map(nodes)
            extension = ".md" if fmt == "markdown" else f".{fmt}"
            incremental_plan = plan_incremental(
                client=client,
                exporter=exporter,
                repository=repository,
                selected=selected,
                path_map=path_map,
                output_dir=self.output_dir,
                incremental=incremental,
                extension=extension,
            )
            skipped_uuids = incremental_plan.skipped_uuids
            for doc in selected:
                if doc.uuid in skipped_uuids:
                    save_path = incremental_plan.save_paths.get(doc.uuid)
                    exported.append(
                        {
                            "doc": asdict(doc),
                            "status": "skipped",
                            "path": str(save_path) if save_path else "",
                        }
                    )
                    continue
                full_path = path_map.get(doc.uuid, "")
                path_parts = full_path.split("/") if full_path else []
                rel_dir = "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
                save_path = exporter.get_save_path(
                    doc,
                    repository.name,
                    extension=extension,
                    relative_path=rel_dir,
                )

                if doc.type == "TITLE":
                    exported.append({"doc": asdict(doc), "status": "directory", "path": str(save_path.parent)})
                    continue

                url = client.export_document(doc, export_type)
                if url == "EMPTY_DOC":
                    save_path.touch(exist_ok=True)
                    if fmt == "markdown":
                        stamp_metadata(incremental_plan, exporter, save_path, doc)
                    exported.append({"doc": asdict(doc), "status": "empty", "path": str(save_path)})
                    record_exported(incremental_plan, doc)
                    continue

                if not url:
                    exported.append(
                        {
                            "doc": asdict(doc),
                            "status": "failed",
                            "path": str(save_path),
                            "reason": "export task returned no download url",
                        }
                    )
                    continue

                ok = client.download_file(url, str(save_path))
                item = {
                    "doc": asdict(doc),
                    "status": "ok" if ok else "failed",
                    "path": str(save_path),
                }
                if not ok:
                    item["reason"] = "download failed"
                if ok and fmt == "markdown":
                    if download_images:
                        image_result = exporter.localize_images(
                            save_path, client.download_external_image
                        )
                        item["image_localization"] = {
                            "found": image_result.found_count,
                            "downloaded": image_result.downloaded_count,
                            "failed": len(image_result.failed_urls),
                            "skipped": image_result.skipped_count,
                        }
                        image_summary = {
                            **image_summary,
                            "found": image_summary["found"] + image_result.found_count,
                            "downloaded": image_summary["downloaded"] + image_result.downloaded_count,
                            "failed": image_summary["failed"] + len(image_result.failed_urls),
                            "skipped": image_summary["skipped"] + image_result.skipped_count,
                        }
                    stamp_metadata(incremental_plan, exporter, save_path, doc)
                if ok:
                    record_exported(incremental_plan, doc)
                exported.append(item)

            finalized = finalize_incremental(incremental_plan, nodes)
            state_file = finalized["state_file"]
            stale_files = finalized["stale"]
            summary = {
                "repo": asdict(repository),
                "format": fmt,
                "incremental": incremental,
                "requested": len(selected),
                "success": len([x for x in exported if x["status"] in {"ok", "empty", "directory"}]),
                "skipped": len([x for x in exported if x["status"] == "skipped"]),
                "failed": len([x for x in exported if x["status"] == "failed"]),
                "failed_items": [x for x in exported if x["status"] == "failed"],
                "image_localization": image_summary,
                "items": exported,
                "stale_files": stale_files,
                **({"state_file": state_file} if state_file else {}),
            }
            append_audit(
                self.profile,
                {
                    "event": "export.run",
                    "repo_id": repository.id,
                    "format": fmt,
                    "incremental": incremental,
                    "requested": summary["requested"],
                    "success": summary["success"],
                    "skipped": summary["skipped"],
                },
            )
            return summary
        finally:
            manager.quit()

    def batch(
        self,
        repo_ids: Iterable[int] = (),
        fmt: str = "markdown",
        all_docs: bool = False,
        node_uuids: Iterable[str] = (),
        download_images: bool = False,
        repos: Iterable[str] = (),
        incremental: bool = False,
    ) -> Dict[str, Any]:
        repo_id_values = tuple(repo_ids)
        repository_values = tuple(repos)
        node_values = tuple(node_uuids)
        if not repo_id_values and not repository_values:
            raise ValueError("at least one repository selector is required")
        if fmt not in FORMAT_TO_EXPORT_TYPE:
            raise ValueError(f"unsupported export format: {fmt}")
        if all_docs == bool(node_values):
            raise ValueError("provide exactly one of all_docs or node_uuids")

        id_results = []
        for repository_id in repo_id_values:
            try:
                id_results.append(
                    self.run(
                        repo_id=repository_id,
                        fmt=fmt,
                        all_docs=all_docs,
                        node_uuids=node_values,
                        download_images=download_images,
                        incremental=incremental,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - batch keeps partial results
                id_results.append(
                    {"status": "failed", "repo_id": repository_id, "error": str(exc)}
                )
        reference_results = []
        for repository_reference in repository_values:
            try:
                reference_results.append(
                    self.run(
                        repo=repository_reference,
                        fmt=fmt,
                        all_docs=all_docs,
                        node_uuids=node_values,
                        download_images=download_images,
                        incremental=incremental,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - batch keeps partial results
                reference_results.append(
                    {"status": "failed", "repo": repository_reference, "error": str(exc)}
                )
        results = [*id_results, *reference_results]
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
    unknown_uuids = node_uuids - node_map.keys()
    if unknown_uuids:
        unknown = ", ".join(sorted(unknown_uuids))
        raise ValueError(f"unknown node UUID(s): {unknown}")
    children_map: Dict[str, List[Any]] = {}
    for node in nodes:
        children_map.setdefault(node.parent_uuid, []).append(node)

    final_uuids: Set[str] = set()
    for uuid in node_uuids:
        node = node_map.get(uuid)
        if node:
            _collect_descendants(node, children_map, final_uuids)

    return [node for node in nodes if node.uuid in final_uuids]
