from __future__ import annotations

import builtins
from typing import Any

import pytest

from cli_anything.yuque.core.project import ensure_src_on_path


ensure_src_on_path()

from core.models import Repository  # type: ignore  # noqa: E402
from core.repository_resolver import RepositoryTransportError  # type: ignore  # noqa: E402
from main import Application  # type: ignore  # noqa: E402
from ui import console as console_mod  # type: ignore  # noqa: E402
from ui.console import UI  # type: ignore  # noqa: E402


class FakeClient:
    def __init__(self, repositories: list[Repository] | None = None) -> None:
        self.repositories = repositories or []
        self.direct_calls: list[str] = []
        self.list_calls = 0
        self.favorite_calls = 0

    def get_repositories(self) -> list[Repository]:
        self.list_calls += 1
        return list(self.repositories)

    def get_favorite_repositories(self) -> list[Repository]:
        self.favorite_calls += 1
        return list(self.repositories)

    def get_repository(self, reference: str) -> Repository:
        self.direct_calls.append(reference)
        return Repository(
            id=42,
            name="Favorite Repo",
            slug="favorite-repo",
            user_login="owner",
        )


class FakeProgress:
    def __enter__(self) -> "FakeProgress":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def add_task(self, *_args: Any, **_kwargs: Any) -> int:
        return 1

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def application_with(client: FakeClient) -> Application:
    application = Application()
    application.client = client
    return application


def test_direct_repository_selection_does_not_list_common_repositories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    application = application_with(client)
    monkeypatch.setattr(
        UI,
        "ask_choice",
        staticmethod(lambda *_args, **_kwargs: "通过 ID / namespace / URL 直接指定"),
    )
    monkeypatch.setattr(
        UI,
        "ask_text",
        staticmethod(lambda *_args, **_kwargs: "https://www.yuque.com/owner/favorite-repo"),
    )
    monkeypatch.setattr(UI, "ask_confirm", staticmethod(lambda *_args, **_kwargs: False))

    selected = application._select_repositories()

    assert client.list_calls == 0
    assert client.direct_calls == ["https://www.yuque.com/owner/favorite-repo"]
    assert [repo.id for repo in selected] == [42]


def test_favorite_repository_selection_uses_favorite_source_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(
        id=7,
        name="Favorite",
        slug="favorite",
        user_login="owner",
    )
    client = FakeClient([repository])
    application = application_with(client)
    monkeypatch.setattr(
        UI,
        "ask_choice",
        staticmethod(lambda *_args, **_kwargs: "从收藏知识库列表选择"),
    )
    monkeypatch.setattr(UI, "create_progress", staticmethod(lambda: FakeProgress()))
    monkeypatch.setattr(UI, "show_repos", staticmethod(lambda _repos: None))
    monkeypatch.setattr(
        UI,
        "ask_checkbox",
        staticmethod(lambda _message, choices: [choices[0]["value"]]),
    )

    selected = application._select_repositories()

    assert [repo.id for repo in selected] == [7]
    assert client.favorite_calls == 1
    assert client.list_calls == 0


def test_empty_common_list_can_fall_back_to_direct_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    application = application_with(client)
    monkeypatch.setattr(
        UI,
        "ask_choice",
        staticmethod(lambda *_args, **_kwargs: "从常用知识库列表选择"),
    )
    monkeypatch.setattr(UI, "create_progress", staticmethod(lambda: FakeProgress()))
    monkeypatch.setattr(UI, "warning", staticmethod(lambda *_args, **_kwargs: None))
    answers = iter([True, False])
    monkeypatch.setattr(
        UI,
        "ask_confirm",
        staticmethod(lambda *_args, **_kwargs: next(answers)),
    )
    monkeypatch.setattr(
        UI,
        "ask_text",
        staticmethod(lambda *_args, **_kwargs: "owner/favorite-repo"),
    )

    selected = application._select_repositories()

    assert client.list_calls == 1
    assert client.direct_calls == ["owner/favorite-repo"]
    assert [repo.id for repo in selected] == [42]


def test_ui_ask_text_falls_back_to_input(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenPrompt:
        @staticmethod
        def ask() -> str:
            raise EOFError("not interactive")

    monkeypatch.setattr(console_mod.questionary, "text", lambda *_a, **_k: BrokenPrompt())
    monkeypatch.setattr(builtins, "input", lambda _prompt: "  owner/repo  ")

    assert UI.ask_text("Repository") == "owner/repo"


def test_ui_ask_text_falls_back_on_generic_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate NoConsoleScreenBufferError from prompt_toolkit on Windows."""

    class BrokenPrompt:
        @staticmethod
        def ask() -> str:
            raise RuntimeError("No Windows console found. Are you running cmd.exe?")

    monkeypatch.setattr(console_mod.questionary, "text", lambda *_a, **_k: BrokenPrompt())
    monkeypatch.setattr(builtins, "input", lambda _prompt: "  note.md  ")

    assert UI.ask_text("Markdown path") == "note.md"


def test_ui_ask_text_returns_none_when_fallback_input_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenPrompt:
        @staticmethod
        def ask() -> str:
            raise RuntimeError("No Windows console found. Are you running cmd.exe?")

    monkeypatch.setattr(console_mod.questionary, "text", lambda *_a, **_k: BrokenPrompt())

    def _raise(_prompt: str) -> str:
        raise OSError("no input")

    monkeypatch.setattr(builtins, "input", _raise)

    assert UI.ask_text("Markdown path") is None


def test_single_common_selection_uses_import_wording_and_single_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(
        id=7,
        name="Import Target",
        slug="import-target",
        user_login="owner",
    )
    application = application_with(FakeClient([repository]))
    monkeypatch.setattr(
        UI,
        "ask_choice",
        staticmethod(
            lambda message, choices: (
                "从常用知识库列表选择"
                if "来源" in message
                else choices[0]
            )
        ),
    )
    monkeypatch.setattr(UI, "create_progress", staticmethod(lambda: FakeProgress()))
    monkeypatch.setattr(UI, "show_repos", staticmethod(lambda _repos: None))
    captured: list[str] = []
    original_ask_choice = UI.ask_choice

    def _capture(message: str, choices: list[str]) -> str | None:
        captured.append(message)
        return original_ask_choice(message, choices)

    monkeypatch.setattr(UI, "ask_choice", staticmethod(_capture))

    selected = application._select_single_repository()

    assert selected is not None and selected.id == 7
    assert any("导入" in message for message in captured)
    assert all("导出" not in message for message in captured)


def test_single_direct_selection_returns_one_repository_without_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    application = application_with(client)
    monkeypatch.setattr(
        UI,
        "ask_choice",
        staticmethod(lambda *_args, **_kwargs: "通过 ID / namespace / URL 直接指定"),
    )
    monkeypatch.setattr(
        UI,
        "ask_text",
        staticmethod(lambda *_args, **_kwargs: "owner/import-target"),
    )
    confirm_calls: list[str] = []
    monkeypatch.setattr(
        UI,
        "ask_confirm",
        staticmethod(lambda message, **_kwargs: confirm_calls.append(message) or False),
    )

    selected = application._select_single_repository()

    assert selected is not None and selected.id == 42
    assert client.direct_calls == ["owner/import-target"]
    assert confirm_calls == []


def test_common_list_failure_returns_to_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingListClient(FakeClient):
        def get_repositories(self) -> list[Repository]:
            raise RepositoryTransportError("list unavailable")

    application = application_with(FailingListClient())
    errors: list[str] = []
    monkeypatch.setattr(UI, "create_progress", staticmethod(lambda: FakeProgress()))
    monkeypatch.setattr(UI, "error", staticmethod(errors.append))

    assert application._select_from_common_repositories() == []
    assert errors == ["获取知识库列表失败: list unavailable"]


def test_catalog_failure_returns_from_repository_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingCatalogClient(FakeClient):
        def get_catalog_nodes(self, _repo: Repository) -> list[Any]:
            raise RepositoryTransportError("catalog unavailable")

    application = application_with(FailingCatalogClient())
    errors: list[str] = []
    monkeypatch.setattr(UI, "error", staticmethod(errors.append))
    repository = Repository(
        id=42,
        name="Repo",
        slug="repo",
        user_login="owner",
    )

    application.process_repo_export(repository, object())  # type: ignore[arg-type]

    assert errors == ["获取 [Repo] 的目录失败: catalog unavailable"]


def test_repository_table_labels_sequence_and_real_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(console_mod.console, "print", captured.append)
    repository = Repository(
        id=42,
        name="Favorite Repo",
        slug="favorite-repo",
        user_login="owner",
    )

    UI.show_repos([repository])

    table = captured[0]
    assert [column.header for column in table.columns[:3]] == [
        "序号",
        "知识库 ID",
        "Namespace",
    ]


def test_ask_required_text_retries_empty_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter([None, None, "note.md"])
    calls = []
    monkeypatch.setattr(
        UI, "ask_text", staticmethod(lambda message: (calls.append(message), next(answers))[1])
    )

    assert UI.ask_required_text("Path") == "note.md"
    assert len(calls) == 3


def test_ask_required_text_gives_up_after_three_empties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        UI, "ask_text", staticmethod(lambda message: calls.append(message) or None)
    )

    assert UI.ask_required_text("Path") is None
    assert len(calls) == 3


def test_ask_required_text_attempts_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        UI, "ask_text", staticmethod(lambda message: calls.append(message) or None)
    )

    assert UI.ask_required_text("Path", attempts=1) is None
    assert len(calls) == 1
