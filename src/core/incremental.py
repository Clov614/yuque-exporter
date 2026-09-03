"""
增量导出判定逻辑（GUI / CLI 共享）
==================================
把“哪些文档可以跳过”的判定收敛到一处，调用方只需：

1. plan_incremental(...) 拿到跳过集、目标路径与本次时间戳；
2. 逐文档导出，每成功一篇调用 record_exported(...)；
3. 结束后调用 finalize(...) 落盘并汇总孤儿记录。

client / exporter 以鸭子类型传入（需 get_document_updated_at /
get_save_path / add_metadata），harness 与桌面 GUI 共用同一实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from .incremental_state import IncrementalStateStore

OutputDirLike = Union[str, Path, None]


class IncrementalPlan:
    """一次增量运行的判定结果。"""

    def __init__(
        self,
        state_store: Optional[IncrementalStateStore],
        skipped_uuids: Optional[Set[str]] = None,
        save_paths: Optional[Dict[str, Path]] = None,
        current_timestamps: Optional[Dict[str, str]] = None,
    ) -> None:
        self.state_store = state_store
        self.skipped_uuids: Set[str] = set(skipped_uuids or ())
        self.save_paths: Dict[str, Path] = dict(save_paths or {})
        self.current_timestamps: Dict[str, str] = dict(current_timestamps or {})

    @property
    def enabled(self) -> bool:
        return self.state_store is not None


def plan_incremental(
    client: Any,
    exporter: Any,
    repository: Any,
    selected: List[Any],
    path_map: Dict[str, str],
    output_dir: OutputDirLike,
    incremental: bool,
    extension: str,
) -> IncrementalPlan:
    """划分本次需要导出与可以跳过的文档。

    非增量模式返回未启用的空计划；增量模式对每篇候选 DOC 用
    get_document_updated_at 拿到的服务端时间戳与本地记录比对，
    相同且目标文件存在时跳过。detail 失败视为已修改（重导）。
    """
    if not incremental:
        return IncrementalPlan(state_store=None)
    state_store = IncrementalStateStore.for_repo(
        output_dir, repository.id, repository.name
    )
    skipped_uuids: Set[str] = set()
    save_paths: Dict[str, Path] = {}
    current_timestamps: Dict[str, str] = {}
    for doc in selected:
        if getattr(doc, "type", "DOC") == "TITLE":
            continue
        save_path = _planned_save_path(
            exporter, repository, doc, path_map, extension
        )
        save_paths[doc.uuid] = save_path
        # 每次都取服务端时间戳：无记录时用于首次建记录，
        # 有记录时用于比对；detail 失败则视为已修改（重导且不记录）。
        current = client.get_document_updated_at(doc)
        if current:
            current_timestamps[doc.uuid] = current
        known = state_store.get(doc.id)
        if known is None:
            continue
        if current == known and save_path.exists() and save_path.is_file():
            skipped_uuids.add(doc.uuid)
    return IncrementalPlan(
        state_store=state_store,
        skipped_uuids=skipped_uuids,
        save_paths=save_paths,
        current_timestamps=current_timestamps,
    )


def record_exported(plan: IncrementalPlan, doc: Any) -> None:
    """一篇文档成功导出后记录服务端时间戳；TITLE/缺时间戳时不记录。"""
    if not plan.enabled or plan.state_store is None:
        return
    if getattr(doc, "type", "DOC") == "TITLE":
        return
    timestamp = plan.current_timestamps.get(getattr(doc, "uuid", ""))
    if timestamp:
        plan.state_store.record(getattr(doc, "id", 0), timestamp)


def stamp_metadata(plan: IncrementalPlan, exporter: Any, save_path: Path, doc: Any) -> None:
    """用 detail 拿到的新鲜 updated_at 回填 front matter（仅增量模式有效）。

    catalog 场景下 doc.updated_at 常为空串，回填让增量导出的
    文件元数据比全量更准；非增量模式直接写元数据，行为不变。
    """
    timestamp = plan.current_timestamps.get(getattr(doc, "uuid", ""))
    if timestamp:
        try:
            doc.updated_at = timestamp
        except (AttributeError, TypeError):
            pass
    exporter.add_metadata(save_path, doc)


def finalize(plan: IncrementalPlan, catalog_docs: List[Any]) -> Dict[str, Any]:
    """落盘状态并汇总：返回 {"state_file": str|None, "stale": [doc_id...]}。

    stale 仅汇总报告（目录中已不存在、但状态里有记录的文档），不删除文件。
    """
    if not plan.enabled or plan.state_store is None:
        return {"state_file": None, "stale": []}
    plan.state_store.save()
    known_doc_ids = [
        getattr(doc, "id", 0)
        for doc in catalog_docs
        if getattr(doc, "type", "DOC") != "TITLE"
    ]
    return {
        "state_file": str(plan.state_store.state_file),
        "stale": [
            f"doc_id={doc_id}"
            for doc_id in plan.state_store.stale_doc_ids(known_doc_ids)
        ],
    }


def _planned_save_path(
    exporter: Any,
    repository: Any,
    doc: Any,
    path_map: Dict[str, str],
    extension: str,
) -> Path:
    full_path = path_map.get(doc.uuid, "")
    path_parts = full_path.split("/") if full_path else []
    rel_dir = "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
    return exporter.get_save_path(
        doc,
        repository.name,
        extension=extension,
        relative_path=rel_dir,
    )
