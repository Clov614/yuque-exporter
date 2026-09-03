from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import click

from .core.auth import ProfileAuth
from .core.export import ExportService
from .core.importer import ImportService
from .core.project import ensure_src_on_path, project_info, project_paths
from .core.repo import RepoService
from .core.session import SessionStore
from core.mutation_errors import (  # type: ignore  # noqa: E402
    MarkdownInputError,
    MutationAccessError,
    MutationAuthenticationError,
    MutationConfirmationRequired,
    MutationConflictError,
    MutationProtocolError,
    MutationTimeoutError,
)
from core.repository_resolver import (  # type: ignore  # noqa: E402
    RepositoryAuthenticationError,
    RepositoryResolutionError,
)
from .utils.output import emit, failure, success
from .utils.validators import (
    normalize_output_dir,
    validate_format,
    validate_markdown_file,
    validate_node_values,
    validate_profile,
    validate_repo_id,
    validate_repository_name,
    validate_repository_references,
    validate_repository_selector,
    validate_repository_slug,
    validate_visibility,
)


EXIT_OK = 0
EXIT_PARAM = 2
EXIT_AUTH = 3
EXIT_REMOTE = 4
EXIT_IO = 5
EXIT_UNKNOWN = 6


class HarnessError(Exception):
    def __init__(self, code: str, message: str, exit_code: int, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details


def map_exception(exc: Exception) -> HarnessError:
    if isinstance(exc, click.BadParameter):
        return HarnessError("bad_parameter", str(exc), EXIT_PARAM)
    if isinstance(exc, click.UsageError):
        return HarnessError("usage_error", str(exc), EXIT_PARAM)
    if isinstance(exc, (MarkdownInputError,)):
        return HarnessError("input_error", str(exc), EXIT_PARAM)
    if isinstance(exc, MutationConfirmationRequired):
        return HarnessError("confirmation_required", str(exc), EXIT_PARAM)
    if isinstance(exc, (MutationAuthenticationError, RepositoryAuthenticationError)):
        return HarnessError("auth_error", str(exc), EXIT_AUTH)
    if isinstance(exc, MutationAccessError):
        return HarnessError("access_error", str(exc), EXIT_REMOTE)
    if isinstance(exc, MutationConflictError):
        return HarnessError("conflict_error", str(exc), EXIT_REMOTE)
    if isinstance(exc, MutationTimeoutError):
        return HarnessError("ambiguous_write", str(exc), EXIT_REMOTE)
    if isinstance(exc, MutationProtocolError):
        return HarnessError("write_protocol_error", str(exc), EXIT_REMOTE)
    if isinstance(exc, RepositoryResolutionError):
        return HarnessError("remote_error", str(exc), EXIT_REMOTE)

    msg = str(exc).lower()
    if "login" in msg or "cookie" in msg or "auth" in msg:
        return HarnessError("auth_error", str(exc), EXIT_AUTH)
    if "api" in msg or "repository not found" in msg:
        return HarnessError("remote_error", str(exc), EXIT_REMOTE)
    if "file" in msg or "path" in msg or "permission" in msg:
        return HarnessError("io_error", str(exc), EXIT_IO)
    return HarnessError("unknown_error", str(exc), EXIT_UNKNOWN)


def _ctx_value(ctx: click.Context, key: str) -> Any:
    return ctx.obj.get(key) if ctx.obj else None


def _apply_common_overrides(
    ctx: click.Context,
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> Dict[str, Any]:
    base = dict(ctx.obj or {})
    selected_profile = profile if profile is not None else base.get("profile", "default")
    normalized_output = normalize_output_dir(output_dir) if output_dir is not None else base.get("output_dir")
    merged = {
        **base,
        "json": bool(as_json) or bool(base.get("json")),
        "profile": validate_profile(str(selected_profile)),
        "output_dir": normalized_output,
        "verbose": bool(verbose) or bool(base.get("verbose")),
    }
    ctx.obj = merged
    return merged


def _profile(ctx: click.Context) -> str:
    return validate_profile(str(_ctx_value(ctx, "profile")))


def _repository_selector_kwargs(
    repo_id: int | None,
    repo: str | None,
) -> Dict[str, Any]:
    reference = validate_repository_selector(repo_id, repo)
    if reference.repository_id is not None:
        return {"repo_id": reference.repository_id}
    return {"repo": reference.namespace}


@contextlib.contextmanager
def _safe_streams():
    with contextlib.ExitStack() as stack:
        stdout = sys.stdout
        stderr = sys.stderr
        if hasattr(stdout, "reconfigure"):
            stack.enter_context(_reconfigure_stream(stdout, "utf-8"))
        if hasattr(stderr, "reconfigure"):
            stack.enter_context(_reconfigure_stream(stderr, "utf-8"))
        yield


def _reconfigure_stream(stream, encoding: str):
    class _StreamGuard:
        def __enter__(self):
            self._old = getattr(stream, "encoding", None)
            stream.reconfigure(encoding=encoding)
            return stream

        def __exit__(self, exc_type, exc, tb):
            if self._old:
                stream.reconfigure(encoding=self._old)
            return False

    return _StreamGuard()


def _run(ctx: click.Context, fn, *args, **kwargs) -> None:
    as_json = bool(_ctx_value(ctx, "json"))
    try:
        with _safe_streams():
            if as_json:
                with contextlib.redirect_stdout(sys.stderr):
                    data = fn(*args, **kwargs)
            else:
                data = fn(*args, **kwargs)
            emit(success(data), as_json=as_json)
        raise SystemExit(EXIT_OK)
    except HarnessError as he:
        with _safe_streams():
            emit(failure(he.code, he.message, details=he.details), as_json=as_json)
        raise SystemExit(he.exit_code)
    except Exception as exc:  # noqa: BLE001
        mapped = map_exception(exc)
        with _safe_streams():
            emit(failure(mapped.code, mapped.message, details=mapped.details), as_json=as_json)
        raise SystemExit(mapped.exit_code)


def common_cmd_options(func):
    func = click.option("--verbose", is_flag=True, default=False)(func)
    func = click.option("--output-dir", default=None)(func)
    func = click.option("--profile", default=None)(func)
    func = click.option("--json", "as_json", is_flag=True, default=False)(func)
    return func


@click.group()
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON envelope")
@click.option("--profile", default="default", help="Profile name")
@click.option("--output-dir", default=None, help="Override export output directory")
@click.option("--verbose", is_flag=True, help="Enable verbose logs")
@click.pass_context
def cli(ctx: click.Context, as_json: bool, profile: str, output_dir: Optional[str], verbose: bool) -> None:
    ensure_src_on_path()
    ctx.obj = {
        "json": as_json,
        "profile": validate_profile(profile),
        "output_dir": normalize_output_dir(output_dir),
        "verbose": verbose,
    }


@cli.group()
def auth() -> None:
    """Authentication commands."""


@auth.command("login")
@common_cmd_options
@click.pass_context
def auth_login(ctx: click.Context, as_json: bool, profile: Optional[str], output_dir: Optional[str], verbose: bool) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)
    _run(ctx, lambda: ProfileAuth(_profile(ctx)).login())


@auth.command("status")
@common_cmd_options
@click.pass_context
def auth_status(ctx: click.Context, as_json: bool, profile: Optional[str], output_dir: Optional[str], verbose: bool) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)
    _run(ctx, lambda: ProfileAuth(_profile(ctx)).status())


@auth.command("logout")
@common_cmd_options
@click.pass_context
def auth_logout(ctx: click.Context, as_json: bool, profile: Optional[str], output_dir: Optional[str], verbose: bool) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)
    _run(ctx, lambda: ProfileAuth(_profile(ctx)).logout())


@cli.group()
def repo() -> None:
    """Repository queries."""


@repo.command("list")
@click.option(
    "--source",
    type=click.Choice(["common", "favorites"], case_sensitive=False),
    default="common",
    show_default=True,
)
@common_cmd_options
@click.pass_context
def repo_list(
    ctx: click.Context,
    source: str,
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)
    _run(ctx, lambda: RepoService(_profile(ctx)).list_repos(source=source.lower()))


@repo.command("tree")
@click.option("--repo-id", type=int, default=None, help="Numeric Yuque repository ID")
@click.option("--repo", default=None, help="Repository owner/slug or Yuque URL")
@common_cmd_options
@click.pass_context
def repo_tree(
    ctx: click.Context,
    repo_id: int | None,
    repo: str | None,
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)

    def execute() -> Dict[str, Any]:
        selector = _repository_selector_kwargs(repo_id, repo)
        return RepoService(_profile(ctx)).tree(**selector)

    _run(ctx, execute)


@repo.command("create")
@click.option("--name", required=True)
@click.option("--slug", default=None)
@click.option("--description", default="")
@click.option(
    "--visibility",
    type=click.Choice(["private", "public", "team"], case_sensitive=False),
    default="private",
    show_default=True,
)
@click.option("--yes", "confirmed", is_flag=True, help="Confirm the remote write")
@click.option("--dry-run", is_flag=True, help="Validate without changing Yuque")
@common_cmd_options
@click.pass_context
def repo_create(
    ctx: click.Context,
    name: str,
    slug: str | None,
    description: str,
    visibility: str,
    confirmed: bool,
    dry_run: bool,
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)

    def execute() -> Dict[str, Any]:
        validated_name = validate_repository_name(name)
        validated_slug = validate_repository_slug(slug)
        validated_visibility = validate_visibility(visibility.lower())
        if not confirmed and not dry_run:
            raise click.BadParameter("repo create requires --yes or --dry-run", param_hint="--yes")
        return RepoService(_profile(ctx)).create(
            name=validated_name,
            slug=validated_slug,
            description=description,
            visibility=validated_visibility,
            confirmed=confirmed,
            dry_run=dry_run,
        )

    _run(ctx, execute)


@cli.group("import")
def import_group() -> None:
    """Import Markdown through the authenticated Yuque browser."""


@import_group.command("run")
@click.option("--repo-id", type=int, default=None, help="Numeric Yuque repository ID")
@click.option("--repo", default=None, help="Repository owner/slug or Yuque URL")
@click.option("--file", "file_path", required=True, type=click.Path())
@click.option("--title", default=None)
@click.option("--yes", "confirmed", is_flag=True, help="Confirm the remote write")
@click.option("--dry-run", is_flag=True, help="Validate without changing Yuque")
@common_cmd_options
@click.pass_context
def import_run(
    ctx: click.Context,
    repo_id: int | None,
    repo: str | None,
    file_path: str,
    title: str | None,
    confirmed: bool,
    dry_run: bool,
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)

    def execute() -> Dict[str, Any]:
        selector = _repository_selector_kwargs(repo_id, repo)
        validated_file = validate_markdown_file(file_path)
        if not confirmed and not dry_run:
            raise click.BadParameter("import run requires --yes or --dry-run", param_hint="--yes")
        return ImportService(_profile(ctx)).run(
            **selector,
            file=validated_file,
            title=title,
            confirmed=confirmed,
            dry_run=dry_run,
        )

    _run(ctx, execute)


@import_group.command("batch")
@click.option("--repo-id", type=int, default=None, help="Numeric Yuque repository ID")
@click.option("--repo", default=None, help="Repository owner/slug or Yuque URL")
@click.option("--file", "files", multiple=True, required=True, type=click.Path())
@click.option("--yes", "confirmed", is_flag=True, help="Confirm the remote write")
@click.option("--dry-run", is_flag=True, help="Validate without changing Yuque")
@common_cmd_options
@click.pass_context
def import_batch(
    ctx: click.Context,
    repo_id: int | None,
    repo: str | None,
    files: Iterable[str],
    confirmed: bool,
    dry_run: bool,
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)

    def execute() -> Dict[str, Any]:
        selector = _repository_selector_kwargs(repo_id, repo)
        validated_files = tuple(validate_markdown_file(value) for value in files)
        if not confirmed and not dry_run:
            raise click.BadParameter("import batch requires --yes or --dry-run", param_hint="--yes")
        return ImportService(_profile(ctx)).batch(
            **selector,
            files=validated_files,
            confirmed=confirmed,
            dry_run=dry_run,
        )

    _run(ctx, execute)


@cli.group()
def export() -> None:
    """Export commands."""


@export.command("run")
@click.option("--repo-id", type=int, default=None, help="Numeric Yuque repository ID")
@click.option("--repo", default=None, help="Repository owner/slug or Yuque URL")
@click.option("--format", "fmt", default="markdown")
@click.option("--all", "all_docs", is_flag=True)
@click.option("--node", "nodes", multiple=True)
@click.option(
    "--download-images",
    is_flag=True,
    default=False,
    help="Download HTTP(S) Markdown images into local .assets directories",
)
@click.option(
    "--incremental",
    is_flag=True,
    default=False,
    help="Only export documents changed since the last run (markdown only)",
)
@common_cmd_options
@click.pass_context
def export_run(
    ctx: click.Context,
    repo_id: int | None,
    repo: str | None,
    fmt: str,
    all_docs: bool,
    nodes: Iterable[str],
    download_images: bool,
    incremental: bool,
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)

    def execute() -> Dict[str, Any]:
        selector = _repository_selector_kwargs(repo_id, repo)
        validated_nodes = validate_node_values(nodes)
        validated_format = validate_format(fmt)
        if all_docs == bool(validated_nodes):
            raise click.BadParameter("use exactly one of --all or --node")
        if download_images and validated_format != "markdown":
            raise click.BadParameter(
                "--download-images requires --format markdown",
                param_hint="--download-images",
            )
        if incremental and validated_format != "markdown":
            raise click.BadParameter(
                "--incremental requires --format markdown",
                param_hint="--incremental",
            )
        return ExportService(_profile(ctx), _ctx_value(ctx, "output_dir")).run(
            **selector,
            fmt=validated_format,
            all_docs=all_docs,
            node_uuids=validated_nodes,
            download_images=download_images,
            incremental=incremental,
        )

    _run(ctx, execute)


@export.command("batch")
@click.option("--repo-id", "repo_ids", multiple=True, type=int)
@click.option(
    "--repo",
    "repos",
    multiple=True,
    help="Repeatable repository owner/slug or Yuque URL",
)
@click.option("--format", "fmt", default="markdown")
@click.option("--all", "all_docs", is_flag=True)
@click.option("--node", "nodes", multiple=True)
@click.option(
    "--download-images",
    is_flag=True,
    default=False,
    help="Download HTTP(S) Markdown images into local .assets directories",
)
@click.option(
    "--incremental",
    is_flag=True,
    default=False,
    help="Only export documents changed since the last run (markdown only)",
)
@common_cmd_options
@click.pass_context
def export_batch(
    ctx: click.Context,
    repo_ids: Iterable[int],
    repos: Iterable[str],
    fmt: str,
    all_docs: bool,
    nodes: Iterable[str],
    download_images: bool,
    incremental: bool,
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)

    def execute() -> Dict[str, Any]:
        validated_repo_ids = [validate_repo_id(value) for value in repo_ids]
        validated_repos = validate_repository_references(repos)
        if not validated_repo_ids and not validated_repos:
            raise click.BadParameter(
                "use at least one --repo-id or --repo",
                param_hint="--repo-id/--repo",
            )
        validated_nodes = validate_node_values(nodes)
        validated_format = validate_format(fmt)
        if all_docs == bool(validated_nodes):
            raise click.BadParameter("use exactly one of --all or --node")
        if download_images and validated_format != "markdown":
            raise click.BadParameter(
                "--download-images requires --format markdown",
                param_hint="--download-images",
            )
        if incremental and validated_format != "markdown":
            raise click.BadParameter(
                "--incremental requires --format markdown",
                param_hint="--incremental",
            )
        selectors = {
            **({"repo_ids": validated_repo_ids} if validated_repo_ids else {}),
            **({"repos": validated_repos} if validated_repos else {}),
        }
        return ExportService(_profile(ctx), _ctx_value(ctx, "output_dir")).batch(
            **selectors,
            fmt=validated_format,
            all_docs=all_docs,
            node_uuids=validated_nodes,
            download_images=download_images,
            incremental=incremental,
        )

    _run(ctx, execute)


@cli.group()
def session() -> None:
    """Session store operations."""


@session.command("init")
@common_cmd_options
@click.pass_context
def session_init(ctx: click.Context, as_json: bool, profile: Optional[str], output_dir: Optional[str], verbose: bool) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)
    _run(ctx, lambda: SessionStore(_profile(ctx)).init())


@session.command("show")
@common_cmd_options
@click.pass_context
def session_show(ctx: click.Context, as_json: bool, profile: Optional[str], output_dir: Optional[str], verbose: bool) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)
    _run(ctx, lambda: SessionStore(_profile(ctx)).read())


@session.command("doctor")
@common_cmd_options
@click.pass_context
def session_doctor(ctx: click.Context, as_json: bool, profile: Optional[str], output_dir: Optional[str], verbose: bool) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)

    def execute() -> Dict[str, Any]:
        info = project_paths(_profile(ctx))
        checks = {
            "src_exists": Path(project_info()["src"]).exists(),
            "state_dir_exists": Path(info["state_dir"]).exists(),
            "cookies_exists": Path(info["cookies_file"]).exists(),
        }
        return {
            "paths": info,
            "checks": checks,
        }

    _run(ctx, execute)


@cli.group()
def project() -> None:
    """Project-level information."""


@project.command("info")
@common_cmd_options
@click.pass_context
def project_info_cmd(ctx: click.Context, as_json: bool, profile: Optional[str], output_dir: Optional[str], verbose: bool) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)
    _run(ctx, project_info)


@project.command("paths")
@common_cmd_options
@click.pass_context
def project_paths_cmd(ctx: click.Context, as_json: bool, profile: Optional[str], output_dir: Optional[str], verbose: bool) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)
    _run(ctx, lambda: project_paths(_profile(ctx)))


def main() -> None:
    as_json = "--json" in sys.argv
    try:
        cli.main(standalone_mode=False)
    except click.ClickException as exc:
        if as_json:
            with _safe_streams():
                emit(failure("usage_error", exc.format_message()), as_json=True)
        else:
            exc.show()
        raise SystemExit(EXIT_PARAM)
    except Exception as exc:  # noqa: BLE001
        mapped = map_exception(exc)
        if as_json:
            with _safe_streams():
                emit(failure(mapped.code, mapped.message, details=mapped.details), as_json=True)
        else:
            with _safe_streams():
                emit(failure(mapped.code, mapped.message, details=mapped.details), as_json=False)
        raise SystemExit(mapped.exit_code)


if __name__ == "__main__":
    main()
