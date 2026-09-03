"""
增量导出状态存储
================
记录每个知识库各文档上次成功导出时的服务端更新时间，
供下次运行时对比跳过未修改的文档。

状态文件位于导出目录内的隐藏目录，随输出目录天然隔离：
    <output_dir>/.yuque_export_state/<repo_id>.json
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

from .exporter import DocumentExporter


STATE_DIR_NAME = ".yuque_export_state"
STATE_VERSION = 1

OutputDirLike = Union[str, Path, None]


def resolve_state_file(output_dir: OutputDirLike, repo_id: int) -> Path:
    """解析指定知识库的状态文件路径。

    output_dir 为 None 时与 DocumentExporter 使用同一默认目录，
    保证 CLI 缺省 --output-dir 与 GUI 默认目录的状态落点一致。
    """
    base = Path(output_dir).expanduser() if output_dir else DocumentExporter.DEFAULT_OUTPUT_DIR
    return base / STATE_DIR_NAME / f"{repo_id}.json"


class IncrementalStateStore:
    """单个知识库的增量导出状态，key 为文档 ID（字符串），value 为服务端更新时间原文。"""

    def __init__(self, state_file: Path, repo_id: int, repo_name: str = "") -> None:
        self.state_file = state_file
        self.repo_id = repo_id
        self.repo_name = repo_name
        self._docs: Dict[str, str] = {}
        self._loaded = False

    @classmethod
    def for_repo(
        cls,
        output_dir: OutputDirLike,
        repo_id: int,
        repo_name: str = "",
    ) -> "IncrementalStateStore":
        store = cls(resolve_state_file(output_dir, repo_id), repo_id, repo_name)
        store.load()
        return store

    def load(self) -> Dict[str, str]:
        """从磁盘加载状态；文件缺失返回空状态，损坏时备份后重建。"""
        self._docs = {}
        if not self.state_file.exists():
            self._loaded = True
            return dict(self._docs)
        try:
            with self.state_file.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                raise ValueError("state payload must be object")
            docs = payload.get("docs", {})
            if not isinstance(docs, dict):
                raise ValueError("state docs must be object")
            self._docs = {
                str(key): str(value)
                for key, value in docs.items()
                if value is not None
            }
        except (json.JSONDecodeError, ValueError, TypeError, OSError):
            backup = self.state_file.with_suffix(".json.corrupt")
            try:
                if self.state_file.exists():
                    self.state_file.replace(backup)
            except OSError:
                pass
            self._docs = {}
        self._loaded = True
        return dict(self._docs)

    def get(self, doc_id: int) -> Optional[str]:
        if not self._loaded:
            self.load()
        return self._docs.get(str(doc_id))

    def record(self, doc_id: int, updated_at: Optional[str]) -> None:
        """记录一次成功导出的服务端更新时间。

        TITLE 目录节点（doc_id <= 0）与空时间戳不记录。
        """
        if not isinstance(doc_id, int) or doc_id <= 0:
            return
        if not updated_at:
            return
        if not self._loaded:
            self.load()
        self._docs[str(doc_id)] = str(updated_at)

    def stale_doc_ids(self, known_doc_ids: Iterable[int]) -> List[str]:
        """返回状态中有、但本次目录里已不存在的文档 ID（仅汇总报告，不删除）。"""
        if not self._loaded:
            self.load()
        known = {str(doc_id) for doc_id in known_doc_ids}
        return sorted(doc_id for doc_id in self._docs if doc_id not in known)

    def save(self) -> Path:
        payload = {
            "version": STATE_VERSION,
            "repo_id": self.repo_id,
            "repo_name": self.repo_name,
            "docs": dict(self._docs),
        }
        _atomic_write_json(self.state_file, payload)
        return self.state_file


def _atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        Path(tmp_name).replace(path)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
