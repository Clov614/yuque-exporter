from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import click

from .core.auth import ProfileAuth
from .core.export import ExportService
from .core.project import ensure_src_on_path, project_info, project_paths
from .core.repo import RepoService
from .core.session import SessionStore
from .utils.output import emit, failure, success
from .utils.validators import (
    normalize_output_dir,
    validate_format,
    validate_node_values,
    validate_profile,
    validate_repo_id,
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
@common_cmd_options
@click.pass_context
def repo_list(ctx: click.Context, as_json: bool, profile: Optional[str], output_dir: Optional[str], verbose: bool) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)
    _run(ctx, lambda: RepoService(_profile(ctx)).list_repos())


@repo.command("tree")
@click.option("--repo-id", type=int, required=True)
@common_cmd_options
@click.pass_context
def repo_tree(
    ctx: click.Context,
    repo_id: int,
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)
    _run(
        ctx,
        lambda: RepoService(_profile(ctx)).tree(validate_repo_id(repo_id)),
    )


@cli.group()
def export() -> None:
    """Export commands."""


@export.command("run")
@click.option("--repo-id", type=int, required=True)
@click.option("--format", "fmt", default="markdown")
@click.option("--all", "all_docs", is_flag=True)
@click.option("--node", "nodes", multiple=True)
@common_cmd_options
@click.pass_context
def export_run(
    ctx: click.Context,
    repo_id: int,
    fmt: str,
    all_docs: bool,
    nodes: Iterable[str],
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)

    def execute() -> Dict[str, Any]:
        validated_nodes = validate_node_values(nodes)
        if not all_docs and not validated_nodes:
            raise click.BadParameter("use --all or at least one --node")
        return ExportService(_profile(ctx), _ctx_value(ctx, "output_dir")).run(
            repo_id=validate_repo_id(repo_id),
            fmt=validate_format(fmt),
            all_docs=all_docs,
            node_uuids=validated_nodes,
        )

    _run(ctx, execute)


@export.command("batch")
@click.option("--repo-id", "repo_ids", multiple=True, type=int, required=True)
@click.option("--format", "fmt", default="markdown")
@click.option("--all", "all_docs", is_flag=True)
@click.option("--node", "nodes", multiple=True)
@common_cmd_options
@click.pass_context
def export_batch(
    ctx: click.Context,
    repo_ids: Iterable[int],
    fmt: str,
    all_docs: bool,
    nodes: Iterable[str],
    as_json: bool,
    profile: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
) -> None:
    _apply_common_overrides(ctx, as_json, profile, output_dir, verbose)

    def execute() -> Dict[str, Any]:
        validated_nodes = validate_node_values(nodes)
        if not all_docs and not validated_nodes:
            raise click.BadParameter("use --all or at least one --node")
        return ExportService(_profile(ctx), _ctx_value(ctx, "output_dir")).batch(
            repo_ids=[validate_repo_id(v) for v in repo_ids],
            fmt=validate_format(fmt),
            all_docs=all_docs,
            node_uuids=validated_nodes,
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
