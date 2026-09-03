from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List

import click

from ..core.project import ensure_src_on_path


ensure_src_on_path()

from core.repository_reference import (  # type: ignore  # noqa: E402
    RepositoryReference,
    RepositoryReferenceError,
    RepositoryReferenceKind,
)
from core.markdown_input import normalize_markdown_path_input  # type: ignore  # noqa: E402


FORMAT_CHOICES = ("markdown", "pdf", "word", "lake")
PROFILE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
REPOSITORY_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")


def validate_profile(profile: str) -> str:
    if not PROFILE_RE.match(profile):
        raise click.BadParameter("profile must match ^[a-zA-Z0-9_-]{1,64}$")
    return profile


def validate_format(fmt: str) -> str:
    if fmt not in FORMAT_CHOICES:
        raise click.BadParameter(f"format must be one of: {', '.join(FORMAT_CHOICES)}")
    return fmt


def validate_repo_id(repo_id: int) -> int:
    if repo_id <= 0:
        raise click.BadParameter("repo-id must be positive")
    return repo_id


def validate_repository_selector(
    repo_id: int | None,
    repo: str | None,
) -> RepositoryReference:
    """Validate mutually exclusive CLI repository selectors."""
    try:
        reference = RepositoryReference.from_selector(
            repository_id=repo_id,
            reference=repo,
        )
    except RepositoryReferenceError as exc:
        raise click.BadParameter(
            str(exc),
            param_hint="--repo-id/--repo",
        ) from exc

    if repo is not None and reference.kind is RepositoryReferenceKind.ID:
        raise click.BadParameter(
            "numeric repository targets must use --repo-id",
            param_hint="--repo",
        )
    return reference


def validate_repository_references(values: Iterable[str]) -> List[str]:
    """Normalize repeated namespace or Yuque URL options."""
    result = []
    for value in values:
        reference = validate_repository_selector(None, value)
        if reference.namespace is None:
            raise click.BadParameter(
                "--repo requires owner/slug or a Yuque repository URL",
                param_hint="--repo",
            )
        result.append(reference.namespace)
    return result


def validate_node_values(values: Iterable[str]) -> List[str]:
    result = [v.strip() for v in values if v and v.strip()]
    bad = [v for v in result if len(v) < 4]
    if bad:
        raise click.BadParameter("node values look invalid")
    return result


def validate_repository_name(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > 200:
        raise click.BadParameter("repository name must be 1-200 characters", param_hint="--name")
    return normalized


def validate_repository_slug(slug: str | None) -> str | None:
    if slug is None:
        return None
    normalized = slug.strip()
    if not REPOSITORY_SLUG_RE.fullmatch(normalized):
        raise click.BadParameter(
            "slug must contain lowercase letters, digits, and hyphens",
            param_hint="--slug",
        )
    return normalized


def validate_visibility(visibility: str) -> str:
    if visibility not in {"private", "public", "team"}:
        raise click.BadParameter("visibility must be private, public, or team")
    return visibility


def validate_markdown_file(value: str) -> str:
    path = Path(normalize_markdown_path_input(value)).expanduser()
    if path.is_symlink() or not path.exists() or not path.is_file():
        raise click.BadParameter("Markdown file must be an existing regular file", param_hint="--file")
    if path.suffix.lower() != ".md":
        raise click.BadParameter("Markdown file must use the .md extension", param_hint="--file")
    return str(path.resolve())


def normalize_output_dir(output_dir: str | None) -> str | None:
    if not output_dir:
        return None
    return str(Path(output_dir).expanduser().resolve())
