from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

from core.repository_reference import (  # type: ignore  # noqa: E402
    RepositoryReference,
    RepositoryReferenceError,
    RepositoryReferenceKind,
)


def test_repository_reference_parses_positive_id() -> None:
    reference = RepositoryReference.parse(42)

    assert reference.kind is RepositoryReferenceKind.ID
    assert reference.canonical == "42"
    assert reference.repository_id == 42
    assert reference.namespace is None


@pytest.mark.parametrize("value", [0, -1, "", "0", "-1", "  "])
def test_repository_reference_rejects_invalid_id_like_values(value: int | str) -> None:
    with pytest.raises(RepositoryReferenceError):
        RepositoryReference.parse(value)


def test_repository_reference_parses_namespace() -> None:
    reference = RepositoryReference.parse("  owner-login/repo-slug  ")

    assert reference.kind is RepositoryReferenceKind.NAMESPACE
    assert reference.canonical == "owner-login/repo-slug"
    assert reference.repository_id is None
    assert reference.namespace == "owner-login/repo-slug"


@pytest.mark.parametrize(
    "value",
    [
        "owner",
        "/repo",
        "owner/",
        "owner//repo",
        "owner/repo/doc",
        "owner/../repo",
        "owner/repo?tab=docs",
        "owner\\repo",
        "owner name/repo",
        "owner%2Fextra/repo",
        "owner%5Cextra/repo",
        "owner%3Ftab/repo",
        "owner%252Fextra/repo",
    ],
)
def test_repository_reference_rejects_invalid_namespace(value: str) -> None:
    with pytest.raises(RepositoryReferenceError):
        RepositoryReference.parse(value)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.yuque.com/owner/repo",
        "https://yuque.com/owner/repo/",
        "https://www.yuque.com/owner/repo/?tab=docs#catalog",
    ],
)
def test_repository_reference_normalizes_yuque_url(url: str) -> None:
    reference = RepositoryReference.parse(url)

    assert reference.kind is RepositoryReferenceKind.NAMESPACE
    assert reference.canonical == "owner/repo"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.yuque.com/owner/repo",
        "https://example.com/owner/repo",
        "https://yuque.com.evil.example/owner/repo",
        "https://yuque.com@evil.example/owner/repo",
        "https://www.yuque.com:444/owner/repo",
        "https://www.yuque.com/owner/repo/doc",
        "https://www.yuque.com/owner%2Frepo",
        "https://www.yuque.com/owner/repo%5Cextra",
        "https://www.yuque.com/owner/repo%3Ftab",
        "https://www.yuque.com/owner/repo%23fragment",
    ],
)
def test_repository_reference_rejects_unsafe_url(url: str) -> None:
    with pytest.raises(RepositoryReferenceError):
        RepositoryReference.parse(url)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (RepositoryReferenceKind.ID, "42"),
        (RepositoryReferenceKind.NAMESPACE, 42),
    ],
)
def test_repository_reference_rejects_mismatched_direct_construction(
    kind: RepositoryReferenceKind,
    value: int | str,
) -> None:
    with pytest.raises(RepositoryReferenceError):
        RepositoryReference(kind=kind, value=value)


def test_repository_reference_is_immutable() -> None:
    reference = RepositoryReference.parse("owner/repo")

    with pytest.raises(FrozenInstanceError):
        reference.value = "other/repo"  # type: ignore[misc]
