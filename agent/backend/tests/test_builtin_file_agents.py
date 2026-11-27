"""ファイルを作る業務 Agent の Run（#5837・#5867）。

- ファイル作成アシスタント（組み込み）: MCP 接続（RAG・NL2SQL）のツールを持たず、会話の内容から
  ファイルを作る。
- 利用者が調べるスキル（RAG だけ・データつき）とファイルを作るスキルを選んだ業務 Agent: 資料・
  業務データのツールに、ファイルを作るツールが加わり、調べた内容をファイルにする。RAG だけの
  スキルと組み合わせても RAG の回答で Run を終えない（#5867）。

モデルは SDK の `ScriptedModel`、RAG・NL2SQL は契約どおりの fake の MCP（`mcp_support`）、保存先は
memory。値は合成。
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import suppress
from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from mcp_support import DEFAULT_OUTPUTS, fake_product_mcp
from openpyxl import load_workbook  # type: ignore[import-untyped]
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.artifact_file_store import FILE_ARTIFACT_KIND, MARKDOWN_MIME_TYPE
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.docx_tool import DOCX_CREATE_TOOL
from app.features.agent.markdown_tool import MARKDOWN_CREATE_TOOL
from app.features.agent.pptx_tool import PPTX_CREATE_TOOL
from app.features.agent.runtime import (
    FILE_CREATE_AGENT_ID,
    AgentProfile,
    RunCreateRequest,
    RunState,
    RunStatus,
    run_answer_notice,
    run_answer_text,
    runtime_repository,
)
from app.features.agent.skills import (
    DOCX_CREATE_SKILL_ID,
    MARKDOWN_CREATE_SKILL_ID,
    PPTX_CREATE_SKILL_ID,
    RAG_CHAT_AND_DATA_SKILL_ID,
    RAG_CHAT_SKILL_ID,
    RAG_SEARCH_AND_DATA_SKILL_ID,
    RAG_SEARCH_SKILL_ID,
    XLSX_CREATE_SKILL_ID,
)
from app.features.agent.tool_grounding import UNGROUNDED_NOTICE
from app.features.agent.tools import MCP_TOOL_SEPARATOR
from app.features.agent.xlsx_tool import XLSX_CREATE_TOOL

USER_UUID = "11111111-2222-3333-4444-555555558370"
FILE_TOOLS = {XLSX_CREATE_TOOL, DOCX_CREATE_TOOL, PPTX_CREATE_TOOL, MARKDOWN_CREATE_TOOL}
FILE_SKILL_IDS = (
    XLSX_CREATE_SKILL_ID,
    DOCX_CREATE_SKILL_ID,
    PPTX_CREATE_SKILL_ID,
    MARKDOWN_CREATE_SKILL_ID,
)
COMBO_AGENT_ID = "test5837-combo"
FILE_SKILL_HEADINGS = (
    "# Skill: Excel 作成",
    "# Skill: Word 作成",
    "# Skill: PowerPoint 作成",
    "# Skill: Markdown 作成",
)
# 合成の NL2SQL の結果。
COLUMNS = ["LABEL", "AMOUNT"]
ROWS: list[dict[str, Any]] = [
    {"LABEL": "record-1", "AMOUNT": 1200},
    {"LABEL": "record-2", "AMOUNT": 30},
]


def _column(column_type: str, *, header: str | None = None) -> dict[str, Any]:
    return {"header": header, "type": column_type, "format": None, "width": None}


def _sheet(**changes: Any) -> dict[str, Any]:
    """モデルに見せる schema（strict）どおり、すべての項目を持つシート。"""
    base: dict[str, Any] = {
        "name": "合成の表",
        "heading": None,
        "notes": [],
        "columns": [_column("text", header="名前")],
        "source": None,
        "rows": [],
        "summaries": [],
        "chart": None,
    }
    return {**base, **changes}


@pytest.fixture
def combo() -> Iterator[Any]:
    """利用者が調べるスキルとファイルを作るスキルを選んだ業務 Agent を作る関数（後で消す）。"""

    def create(search_skill_id: str) -> str:
        runtime_repository.create_agent(
            AgentProfile(
                id=COMBO_AGENT_ID,
                name="合成の組み合わせの業務 Agent",
                skill_ids=[search_skill_id, *FILE_SKILL_IDS],
            )
        )
        runtime_repository.publish_agent(COMBO_AGENT_ID)
        return COMBO_AGENT_ID

    try:
        yield create
    finally:
        with suppress(KeyError, ValueError):
            runtime_repository.delete_agent(COMBO_AGENT_ID)


@pytest.fixture
def script(monkeypatch: MonkeyPatch) -> Iterator[Any]:
    """台本のモデルで Run を実行する関数（テストの後に、作った Run とファイルを消す）。"""
    run_ids: list[str] = []

    def run(agent_id: str, goal: str, *steps: Any) -> tuple[RunState, ScriptedModel]:
        model = ScriptedModel(list(steps))
        monkeypatch.setattr(
            builtin_runtime,
            "resolve_model_target",
            lambda model_id="": ModelTarget(
                model_id=model_id or "test-model",
                endpoint="https://oci.example",
                project_ocid="",
                api_key="k",
            ),
        )
        monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
        created = runtime_repository.create_builtin_run(
            RunCreateRequest(goal=goal, agent_id=agent_id), created_by_user_uuid=USER_UUID
        )
        run_ids.append(created.id)
        anyio.run(builtin_runtime.execute_run, created.id)
        return runtime_repository.get_run(created.id), model

    try:
        yield run
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in run_ids:
                repository._runs.pop(run_id, None)  # noqa: SLF001
            repository._artifact_files = {  # noqa: SLF001
                key: data
                for key, data in repository._artifact_files.items()  # noqa: SLF001
                if key[0] not in run_ids
            }


def _tool_names(model: ScriptedModel) -> set[str]:
    return {tool.name for tool in model.calls[0].tools}


def _file_rows(run: RunState) -> list[list[Any]]:
    [artifact] = [item for item in run.artifacts if item.kind == FILE_ARTIFACT_KIND]
    data = runtime_repository.read_artifact_file(run.id, artifact.id).data
    sheet = load_workbook(io.BytesIO(data)).worksheets[0]
    return [[cell.value for cell in row] for row in sheet.iter_rows()]


def test_file_create_agent_makes_a_file_from_the_conversation_without_mcp(script: Any) -> None:
    answer = "ファイル「合成の一覧.xlsx」を作りました。シート「合成の表」に 2 行あります。"

    run, model = script(
        FILE_CREATE_AGENT_ID,
        "次の 2 つの名前を Excel のファイルにして: record-1、record-2",
        [
            function_call(
                XLSX_CREATE_TOOL,
                {"title": "合成の一覧", "sheets": [_sheet(rows=[["record-1"], ["record-2"]])]},
                call_id="call-xlsx",
            )
        ],
        [assistant_message(answer)],
    )

    assert run.status == RunStatus.COMPLETED, run.events[-1].model_dump_json()
    assert run.approvals == []
    # 渡るツールはファイルを作る 4 つで、MCP 接続（RAG・NL2SQL）のツールは渡らない。
    names = _tool_names(model)
    assert names >= FILE_TOOLS
    assert [name for name in names if MCP_TOOL_SEPARATOR in name] == []
    instructions = str(model.calls[0].system_instructions)
    assert "## Step 1: 会話と添付の内容をファイルにする" in instructions
    assert all(heading in instructions for heading in FILE_SKILL_HEADINGS)
    # 会話の内容で作ったファイルと回答が、そのまま利用者に返る（拒答に置き換えない）。
    assert _file_rows(run) == [["名前"], ["record-1"], ["record-2"]]
    assert run_answer_text(run) == answer
    model.assert_complete()


@pytest.mark.parametrize(
    ("skill_id", "rag_tool", "other_rag_tool"),
    [
        (RAG_SEARCH_AND_DATA_SKILL_ID, "rag__rag_search", "rag__rag_chat"),
        (RAG_CHAT_AND_DATA_SKILL_ID, "rag__rag_chat", "rag__rag_search"),
    ],
    ids=["rag_search", "rag_chat"],
)
def test_data_and_file_agents_make_a_file_from_the_looked_up_table(
    monkeypatch: MonkeyPatch,
    script: Any,
    combo: Any,
    skill_id: str,
    rag_tool: str,
    other_rag_tool: str,
) -> None:
    agent_id = combo(skill_id)
    fake_product_mcp(
        monkeypatch,
        outputs={
            "nl2sql_query": {
                **DEFAULT_OUTPUTS["nl2sql_query"],
                "columns": COLUMNS,
                "rows": ROWS,
                "returned_count": len(ROWS),
                "total": len(ROWS),
            }
        },
    )
    answer = "ファイル「合成の一覧.xlsx」を作りました。シート「一覧」に 2 行あります。"

    run, model = script(
        agent_id,
        "合成の値を調べて Excel のファイルにして",
        [function_call("nl2sql__nl2sql_query", {"question": "合成の値"}, call_id="call-data")],
        [
            function_call(
                XLSX_CREATE_TOOL,
                {
                    "title": "合成の一覧",
                    "sheets": [
                        _sheet(
                            name="一覧",
                            columns=[_column("text"), _column("number")],
                            source={"table": "T1", "rows": "*", "columns": "*"},
                        )
                    ],
                },
                call_id="call-xlsx",
            )
        ],
        [assistant_message(answer)],
    )

    assert run.status == RunStatus.COMPLETED, run.events[-1].model_dump_json()
    assert run.approvals == []
    names = _tool_names(model)
    # 資料・業務データのツールはスキルの宣言どおりで、ファイルを作る 4 つが加わる。
    assert {rag_tool, "nl2sql__nl2sql_query", *FILE_TOOLS} <= names
    assert other_rag_tool not in names
    instructions = str(model.calls[0].system_instructions)
    assert all(heading in instructions for heading in FILE_SKILL_HEADINGS)
    # 値は業務データの照会の結果と一致する（モデルの引数に値は無い）。
    assert _file_rows(run) == [COLUMNS, *[[row["LABEL"], row["AMOUNT"]] for row in ROWS]]
    assert run_answer_text(run) == answer
    model.assert_complete()


@pytest.mark.parametrize(
    ("skill_id", "rag_tool", "other_rag_tool"),
    [
        (RAG_SEARCH_SKILL_ID, "rag__rag_search", "rag__rag_chat"),
        (RAG_CHAT_SKILL_ID, "rag__rag_chat", "rag__rag_search"),
    ],
    ids=["rag_search", "rag_chat"],
)
def test_rag_and_file_agents_make_a_file_from_the_rag_answer(
    monkeypatch: MonkeyPatch,
    script: Any,
    combo: Any,
    skill_id: str,
    rag_tool: str,
    other_rag_tool: str,
) -> None:
    """RAG だけのスキルとファイルを作るスキルを選んだ業務 Agent は、RAG の回答で Run を終えずに、
    調べた内容をファイルにする（#5867）。"""
    agent_id = combo(skill_id)
    fake_product_mcp(monkeypatch)
    content = "# 合成の手順\n\n## 概要\n\n合成の内容。\n"
    answer = "ファイル「合成の手順.md」を作りました。章は「概要」の 1 つです。"

    run, model = script(
        agent_id,
        "合成の手順を資料で調べて Markdown のファイルにして",
        [function_call(rag_tool, {"query": "合成の手順"}, call_id="call-rag")],
        [
            function_call(
                MARKDOWN_CREATE_TOOL,
                {"file_name": "合成の手順", "content": content},
                call_id="call-md",
            )
        ],
        [assistant_message(answer)],
    )

    assert run.status == RunStatus.COMPLETED, run.events[-1].model_dump_json()
    assert run.approvals == []
    names = _tool_names(model)
    # 資料のツールは RAG だけのスキルの宣言どおりで、業務データのツールは渡らない。
    assert {rag_tool, *FILE_TOOLS} <= names
    assert other_rag_tool not in names
    assert [name for name in names if name.startswith("nl2sql")] == []
    instructions = str(model.calls[0].system_instructions)
    assert all(heading in instructions for heading in FILE_SKILL_HEADINGS)
    # RAG の回答の後にモデルがファイルを作り、モデルの回答で終わる（3 回とも呼ばれる）。
    assert len(model.calls) == 3
    [artifact] = [item for item in run.artifacts if item.kind == FILE_ARTIFACT_KIND]
    assert (artifact.content["file_name"], artifact.content["mime_type"]) == (
        "合成の手順.md",
        MARKDOWN_MIME_TYPE,
    )
    assert runtime_repository.read_artifact_file(run.id, artifact.id).data == content.encode()
    assert run_answer_text(run) == answer
    model.assert_complete()


def test_data_and_file_agent_treats_a_file_only_run_like_other_answers(
    monkeypatch: MonkeyPatch, script: Any, combo: Any
) -> None:
    """根拠のツールを呼ばずにファイルだけを作った Run も、ほかの回答と同じ判定・設定で扱う。

    ファイルを作るツールは根拠・データのツールに数えず、ファイルを作った Run を例外にしない。
    既定の設定では、モデルが返した回答とファイルをそのまま出し、確かめていない回答の注意を付ける
    （#5859）。
    """
    fake_product_mcp(monkeypatch)
    answer = "ファイル「合成の一覧.xlsx」を作りました。シート「合成の表」に 2 行あります。"

    run, model = script(
        combo(RAG_CHAT_AND_DATA_SKILL_ID),
        "次の 2 つの名前を Excel のファイルにして: record-1、record-2",
        [
            function_call(
                XLSX_CREATE_TOOL,
                {"title": "合成の一覧", "sheets": [_sheet(rows=[["record-1"], ["record-2"]])]},
                call_id="call-xlsx",
            )
        ],
        [assistant_message(answer)],
    )

    assert run.status == RunStatus.COMPLETED, run.events[-1].model_dump_json()
    # 資料・業務データのツールは渡っているが、呼んでいない。
    assert "nl2sql__nl2sql_query" in _tool_names(model)
    assert _file_rows(run) == [["名前"], ["record-1"], ["record-2"]]
    assert run_answer_text(run) == answer
    assert [event for event in run.events if "tool_grounding" in event.model_dump_json()] == []
    assert (run_answer_notice(run) or "").startswith(UNGROUNDED_NOTICE)
    model.assert_complete()
