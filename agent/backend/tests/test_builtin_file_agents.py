"""ファイルを作る組み込みの業務 Agent の Run（#5837）。

- 文書作成アシスタント: MCP 接続（RAG・NL2SQL）のツールを持たず、会話の内容からファイルを作る。
- RAG 検索＋データ＋文書作成・RAG チャット＋データ＋文書作成: 対応する業務 Agent と同じ資料・
  業務データのツールに、ファイルを作るツールが加わり、調べた表をファイルにする。

モデルは SDK の `ScriptedModel`、RAG・NL2SQL は契約どおりの fake の MCP（`mcp_support`）、保存先は
memory。値は合成。
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from mcp_support import DEFAULT_OUTPUTS, fake_product_mcp
from openpyxl import load_workbook  # type: ignore[import-untyped]
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.artifact_file_store import FILE_ARTIFACT_KIND
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.docx_tool import DOCX_CREATE_TOOL
from app.features.agent.pptx_tool import PPTX_CREATE_TOOL
from app.features.agent.runtime import (
    DOC_CREATE_AGENT_ID,
    RAG_CHAT_DATA_DOC_CREATE_AGENT_ID,
    RAG_SEARCH_DATA_DOC_CREATE_AGENT_ID,
    RunCreateRequest,
    RunState,
    RunStatus,
    run_answer_text,
    runtime_repository,
)
from app.features.agent.tools import MCP_TOOL_SEPARATOR
from app.features.agent.xlsx_tool import XLSX_CREATE_TOOL

USER_UUID = "11111111-2222-3333-4444-555555558370"
FILE_TOOLS = {XLSX_CREATE_TOOL, DOCX_CREATE_TOOL, PPTX_CREATE_TOOL}
FILE_SKILL_HEADINGS = ("# Skill: Excel 作成", "# Skill: Word 作成", "# Skill: PowerPoint 作成")
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
    }
    return {**base, **changes}


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


def test_doc_create_agent_makes_a_file_from_the_conversation_without_mcp(script: Any) -> None:
    answer = "ファイル「合成の一覧.xlsx」を作りました。シート「合成の表」に 2 行あります。"

    run, model = script(
        DOC_CREATE_AGENT_ID,
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
    # 渡るツールはファイルを作る 3 つで、MCP 接続（RAG・NL2SQL）のツールは渡らない。
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
    ("agent_id", "rag_tool", "other_rag_tool"),
    [
        (RAG_SEARCH_DATA_DOC_CREATE_AGENT_ID, "rag__rag_search", "rag__rag_chat"),
        (RAG_CHAT_DATA_DOC_CREATE_AGENT_ID, "rag__rag_chat", "rag__rag_search"),
    ],
    ids=["rag_search", "rag_chat"],
)
def test_data_and_file_agents_make_a_file_from_the_looked_up_table(
    monkeypatch: MonkeyPatch, script: Any, agent_id: str, rag_tool: str, other_rag_tool: str
) -> None:
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
    # 資料・業務データのツールは対応する業務 Agent と同じで、ファイルを作る 3 つが加わる。
    assert {rag_tool, "nl2sql__nl2sql_query", *FILE_TOOLS} <= names
    assert other_rag_tool not in names
    instructions = str(model.calls[0].system_instructions)
    # 調べる Step の後に、ファイルにする Step が続く。
    assert instructions.index("## Step 1: 必要な情報源を選ぶ") < instructions.index(
        "## Step 2: 確かめた内容をファイルにする"
    )
    assert all(heading in instructions for heading in FILE_SKILL_HEADINGS)
    # 値は業務データの照会の結果と一致する（モデルの引数に値は無い）。
    assert _file_rows(run) == [COLUMNS, *[[row["LABEL"], row["AMOUNT"]] for row in ROWS]]
    assert run_answer_text(run) == answer
    model.assert_complete()
