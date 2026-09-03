"""Tests for the incremental export state store (src/core/incremental_state.py)."""

from __future__ import annotations

import json
from pathlib import Path

from core.exporter import DocumentExporter
from core.incremental_state import (
    STATE_DIR_NAME,
    IncrementalStateStore,
    resolve_state_file,
)


def _store(tmp_path: Path, **kwargs) -> IncrementalStateStore:
    output_dir = kwargs.pop("output_dir", tmp_path)
    return IncrementalStateStore.for_repo(output_dir, 42, "Repo", **kwargs)


def test_resolve_state_file_uses_default_output_dir_when_none() -> None:
    expected = DocumentExporter.DEFAULT_OUTPUT_DIR / STATE_DIR_NAME / "7.json"
    assert resolve_state_file(None, 7) == expected


def test_resolve_state_file_scopes_by_output_dir_and_repo(tmp_path: Path) -> None:
    first = resolve_state_file(tmp_path, 1)
    second = resolve_state_file(tmp_path, 2)
    assert first.parent.name == STATE_DIR_NAME
    assert first != second
    assert first.name == "1.json"


def test_fresh_store_loads_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get(10) is None
    assert store.stale_doc_ids([10]) == []


def test_record_and_read_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(10, "2026-09-01T00:00:00+00:00")
    store.save()

    reloaded = _store(tmp_path)
    assert reloaded.get(10) == "2026-09-01T00:00:00+00:00"


def test_record_ignores_title_nodes_and_empty_timestamps(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(0, "2026-09-01T00:00:00+00:00")
    store.record(-3, "2026-09-01T00:00:00+00:00")
    store.record(10, "")
    store.record(11, None)
    store.save()

    reloaded = _store(tmp_path)
    assert reloaded.get(0) is None
    assert reloaded.get(10) is None
    assert reloaded.stale_doc_ids([]) == []


def test_stale_doc_ids_only_reports_missing_catalog_entries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(10, "t1")
    store.record(11, "t2")
    store.save()

    assert store.stale_doc_ids([10, 11]) == []
    assert store.stale_doc_ids([10]) == ["11"]


def test_state_files_are_isolated_per_repo(tmp_path: Path) -> None:
    first = _store(tmp_path)
    first.repo_id = 1
    first.record(10, "t1")
    first.save()

    second = IncrementalStateStore.for_repo(tmp_path, 2, "Other")
    assert second.get(10) is None


def test_corrupt_state_is_backed_up_and_rebuilt(tmp_path: Path) -> None:
    state_file = resolve_state_file(tmp_path, 42)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{not valid json", encoding="utf-8")

    store = _store(tmp_path)
    assert store.get(10) is None
    assert state_file.with_suffix(".json.corrupt").exists()


def test_state_payload_shape(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(10, "t1")
    saved = store.save()

    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["repo_id"] == 42
    assert payload["repo_name"] == "Repo"
    assert payload["docs"] == {"10": "t1"}
