from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List

import click


FORMAT_CHOICES = ("markdown", "pdf", "word", "lake")
PROFILE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


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


def validate_node_values(values: Iterable[str]) -> List[str]:
    result = [v.strip() for v in values if v and v.strip()]
    bad = [v for v in result if len(v) < 4]
    if bad:
        raise click.BadParameter("node values look invalid")
    return result


def normalize_output_dir(output_dir: str | None) -> str | None:
    if not output_dir:
        return None
    return str(Path(output_dir).expanduser().resolve())
