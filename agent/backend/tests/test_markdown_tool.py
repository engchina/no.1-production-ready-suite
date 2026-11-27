"""Markdown のファイルを作るツール `markdown_create` と、組み込みのスキル「Markdown 作成」
（#5867）。

- 本文はそのまま UTF-8 で保存し、拡張子は .md、MIME は `text/markdown; charset=utf-8`。
- 確かめるのは UTF-8 で表せるか（対になっていないサロゲートは引数の誤り）と、大きさの上限（ほかの
  ファイルと同じ `AGENT_ARTIFACT_FILE_MAX_BYTES`）だけ。1 つの Run のファイルの数はほかの形式と
  合わせて数える。
- 結果・step・MCP の応答に、ファイルの中身が載らない。既定では承認が要らない。

保存先は memory。値は合成。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime, file_tool_support
from app.features.agent.artifact_file_store import (
    FILE_RUN_CLOSED,
    FILE_TOO_LARGE,
    MARKDOWN_MIME_TYPE,
)
from app.features.agent.docx_tool import DOCX_CREATE_TOOL
from app.features.agent.file_tool_support import FILE_LIMIT_CODE
from app.features.agent.markdown_tool import MARKDOWN_CREATE_INPUT_SCHEMA, MARKDOWN_CREATE_TOOL
from app.features.agent.mcp_server import FILES_DOWNLOAD_MESSAGE, run_output
from app.features.agent.runtime import RunState, RunStatus, runtime_repository
from app.features.agent.skills import MARKDOWN_CREATE_SKILL_ID, skill_registry
from app.features.agent.tool_arguments import INVALID_ARGUMENTS_CODE
from app.features.agent.tools import (
    ToolCall,
    ToolInvocationContext,
    ToolPolicy,
    ToolPolicyDecision,
    ToolResult,
    tool_registry,
)
from app.settings import get_settings

CONTENT = (
    "# 合成の題\n\n"
    "合成の段落。\n\n"
    "| 名前 | 数量 |\n|---|---:|\n| a | 1 |\n\n"
    '```mermaid\nflowchart LR\n  A["受付"] --> B["確認"]\n```\n'
)


def _invoke(
    run_id: str | None, content: str = CONTENT, file_name: str = "合成の手順"
) -> ToolResult:
    return tool_registry.invoke(
        ToolCall(name=MARKDOWN_CREATE_TOOL, arguments={"file_name": file_name, "content": content}),
        context=ToolInvocationContext(run_id=run_id),
    )


def _files(run_id: str) -> list[Any]:
    return [item for item in runtime_repository.get_run(run_id).artifacts if item.kind == "file"]


@pytest.fixture
def run_id() -> Iterator[str]:
    """実行中の Run（テストの後に、Run とファイルを消す）。"""
    run = RunState(
        id=f"run5867m-{uuid4().hex[:8]}",
        goal="合成の質問",
        agent_id="default",
        runtime_id="builtin",
        status=RunStatus.RUNNING,
    )
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - 実行中の Run を直接置く
        repository._runs[run.id] = run  # noqa: SLF001
    try:
        yield run.id
    finally:
        with repository._lock:  # noqa: SLF001 - テストの後始末
            repository._runs.pop(run.id, None)  # noqa: SLF001
            repository._artifact_files = {  # noqa: SLF001
                key: data
                for key, data in repository._artifact_files.items()  # noqa: SLF001
                if key[0] != run.id
            }


def test_content_is_saved_as_is_as_a_utf8_markdown_file(run_id: str) -> None:
    result = _invoke(run_id)

    assert result.success, result.error
    data = CONTENT.encode("utf-8")
    [artifact] = _files(run_id)
    assert artifact.content["file_name"] == "合成の手順.md"
    assert artifact.content["mime_type"] == MARKDOWN_MIME_TYPE == "text/markdown; charset=utf-8"
    assert artifact.content["size_bytes"] == len(data)
    stored = runtime_repository.read_artifact_file(run_id, artifact.id)
    assert stored.data == data and stored.file_name == "合成の手順.md"
    # 結果は参照だけ（中身は載らない）。
    output = result.output or {}
    assert set(output) == {"file"}
    assert output["file"]["artifact_id"] == artifact.id
    assert "合成の段落" not in str(result.output)


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [("notes.md", "notes.md"), ("report.docx", "report.md"), ("../a/b", "_a_b.md")],
    ids=["md", "office-extension", "path"],
)
def test_file_name_gets_the_markdown_extension(run_id: str, file_name: str, expected: str) -> None:
    assert _invoke(run_id, file_name=file_name).success
    assert [item.name for item in _files(run_id)] == [expected]


def test_text_that_utf8_cannot_encode_is_an_argument_error(run_id: str) -> None:
    """対になっていないサロゲート（JSON の \\ud800 から来る）は UTF-8 で表せない。"""
    result = _invoke(run_id, content="合成の本文\ud800")

    assert (result.success, result.error_code) == (False, INVALID_ARGUMENTS_CODE), result
    errors = result.error_details["argument_errors"]
    assert [(item["argument"], item["code"]) for item in errors] == [("content", "encoding")]
    assert _files(run_id) == []


def test_content_over_the_artifact_size_limit_is_not_saved(
    run_id: str, monkeypatch: MonkeyPatch
) -> None:
    """大きさの上限は、ほかのファイルと同じ設定（`AGENT_ARTIFACT_FILE_MAX_BYTES`）。"""
    size = len(CONTENT.encode("utf-8"))
    monkeypatch.setattr(get_settings(), "agent_artifact_file_max_bytes", size - 1)

    result = _invoke(run_id)

    assert (result.success, result.error_code) == (False, FILE_TOO_LARGE)
    assert _files(run_id) == []
    monkeypatch.setattr(get_settings(), "agent_artifact_file_max_bytes", size)
    assert _invoke(run_id).success


def test_schema_rejects_empty_content_and_missing_arguments(run_id: str) -> None:
    for arguments in ({"file_name": "a", "content": ""}, {"content": CONTENT}, {"file_name": "a"}):
        result = tool_registry.invoke(
            ToolCall(name=MARKDOWN_CREATE_TOOL, arguments=arguments),
            context=ToolInvocationContext(run_id=run_id),
        )
        assert (result.success, result.error_code) == (False, INVALID_ARGUMENTS_CODE), arguments
    assert _files(run_id) == []
    # モデルには strict の schema で渡す（項目はすべて必須。追加の項目は不可）。
    assert builtin_runtime._strict_schema(MARKDOWN_CREATE_INPUT_SCHEMA)  # noqa: SLF001
    assert MARKDOWN_CREATE_INPUT_SCHEMA["additionalProperties"] is False
    assert sorted(MARKDOWN_CREATE_INPUT_SCHEMA["required"]) == ["content", "file_name"]


def test_files_per_run_are_counted_with_the_other_formats(
    run_id: str, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(file_tool_support, "MAX_FILES_PER_RUN", 2)
    docx = tool_registry.invoke(
        ToolCall(
            name=DOCX_CREATE_TOOL,
            arguments={
                "title": "file-0",
                "subtitle": None,
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "合成の段落。",
                        "level": None,
                        "items": [],
                        "table": None,
                        "chart": None,
                    }
                ],
            },
        ),
        context=ToolInvocationContext(run_id=run_id),
    )
    assert docx.success, docx.error

    results = [_invoke(run_id, file_name=f"file-{index}") for index in (1, 2)]

    assert [result.success for result in results] == [True, False]
    assert results[1].error_code == FILE_LIMIT_CODE
    assert [item.name for item in _files(run_id)] == ["file-0.docx", "file-1.md"]


def test_tool_fails_outside_a_run_and_after_the_run_ended(run_id: str) -> None:
    assert _invoke(None).error_code == FILE_RUN_CLOSED
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - Run を終わった状態にする
        repository._runs[run_id].status = RunStatus.COMPLETED  # noqa: SLF001

    assert _invoke(run_id).error_code == FILE_RUN_CLOSED
    assert _files(run_id) == []


def test_mcp_run_output_lists_the_file_without_its_content(run_id: str) -> None:
    assert _invoke(run_id).success

    output = run_output(runtime_repository.get_run(run_id))

    assert [item.model_dump() for item in output.files] == [
        {
            "file_name": "合成の手順.md",
            "mime_type": MARKDOWN_MIME_TYPE,
            "size_bytes": len(CONTENT.encode("utf-8")),
        }
    ]
    assert output.message.endswith(FILES_DOWNLOAD_MESSAGE)
    assert "合成の段落" not in output.model_dump_json()


def test_default_policy_allows_and_the_skill_declares_only_the_tool() -> None:
    definition = tool_registry.get(MARKDOWN_CREATE_TOOL)
    assert definition is not None and not definition.side_effects
    for mode in ("approval", "deny"):
        assert ToolPolicy(default_mode=mode).decide(definition) == ToolPolicyDecision.ALLOW
    skill = skill_registry.get(MARKDOWN_CREATE_SKILL_ID)
    assert skill is not None and skill.source == "builtin" and skill.name == "Markdown 作成"
    assert skill.final_answer_tools == []
    assert [(item.server_id, item.tool_names) for item in skill.mcp_requirements] == [
        ("control-plane", [MARKDOWN_CREATE_TOOL])
    ]
    assert builtin_runtime.agent_tool_names([MARKDOWN_CREATE_SKILL_ID]) == [MARKDOWN_CREATE_TOOL]
