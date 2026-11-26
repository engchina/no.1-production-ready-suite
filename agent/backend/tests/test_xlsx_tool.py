"""Excel のファイルを作るツール `xlsx_create` と、組み込みのスキル「Excel 作成」（#5837）。

- 参照した表（その Run の NL2SQL の結果）の値は、モデルの引数に値が無くても、保存した結果のまま
  cell に入る。無い参照・範囲外・型に合わない値は引数の誤りで、ファイルを作らない。
- 結果・step・イベント・モデルへの入力に、ファイルの中身（バイト列・base64）が載らない。
- 1 つの Run で作れるファイルの数に上限がある。
- スキルを選んだ業務 Agent の Run にだけツールが渡り、既定では承認待ちにならない。

モデルは SDK の `ScriptedModel`、NL2SQL は契約どおりの fake の MCP（`mcp_support`）、保存先は
memory。値は合成。
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from uuid import uuid4

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from mcp_support import DEFAULT_OUTPUTS, fake_product_mcp
from openpyxl import load_workbook  # type: ignore[import-untyped]
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime, xlsx_tool
from app.features.agent.artifact_file_store import FILE_ARTIFACT_KIND, FILE_RUN_CLOSED
from app.features.agent.builtin_runtime import UNDECLARED_CALL_NOT_DECLARED, ModelTarget
from app.features.agent.config import runtime_config_store
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunState,
    RunStatus,
    runtime_repository,
)
from app.features.agent.skills import (
    STRUCTURED_DATA_QUERY_SKILL_ID,
    XLSX_CREATE_SKILL_ID,
    skill_registry,
)
from app.features.agent.tool_arguments import INVALID_ARGUMENTS_CODE
from app.features.agent.tool_policy_catalog import CONTROL_PLANE_SOURCE, tool_policy_catalog
from app.features.agent.tools import (
    ToolCall,
    ToolInvocationContext,
    ToolPolicy,
    ToolPolicyDecision,
    ToolResult,
    tool_registry,
)
from app.features.agent.xlsx_tool import (
    FILE_LIMIT_CODE,
    MAX_FILES_PER_RUN,
    MAX_WRITTEN_ROWS,
    XLSX_CREATE_TOOL,
)
from app.features.agent.xlsx_workbook import XLSX_MIME_TYPE

USER_UUID = "11111111-2222-3333-4444-555555555837"
# 合成の NL2SQL の結果（数値・小数・日時・NULL・桁の多い番号・`=` で始まる文字）。
COLUMNS = ["LABEL", "AMOUNT", "RATE", "CREATED_AT", "CODE"]
ROWS: list[dict[str, Any]] = [
    {
        "LABEL": "record-1",
        "AMOUNT": 1200,
        "RATE": 0.25,
        "CREATED_AT": "2026-01-05T00:00:00",
        "CODE": "12345678901234567890",
    },
    {
        "LABEL": "=1+1",
        "AMOUNT": 30,
        "RATE": 0.5,
        "CREATED_AT": "2026-02-10T18:30:00",
        "CODE": "0000000000000000000001",
    },
    {"LABEL": "record-3", "AMOUNT": None, "RATE": None, "CREATED_AT": None, "CODE": None},
]
NL2SQL_OUTPUT: dict[str, Any] = {"status": "done", "columns": COLUMNS, "rows": ROWS}


def _column(
    column_type: str, *, header: str | None = None, format: str | None = None
) -> dict[str, Any]:
    return {"header": header, "type": column_type, "format": format, "width": None}


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


def _chart(chart_type: str, category_column: int, series_columns: list[int]) -> dict[str, Any]:
    return {
        "type": chart_type,
        "category_column": category_column,
        "series_columns": series_columns,
    }


def _source(table: str = "T1", rows: str = "*", columns: str = "*") -> dict[str, str]:
    return {"table": table, "rows": rows, "columns": columns}


def _invoke(run_id: str | None, *sheets: dict[str, Any], title: str = "合成の表") -> ToolResult:
    return tool_registry.invoke(
        ToolCall(name=XLSX_CREATE_TOOL, arguments={"title": title, "sheets": list(sheets)}),
        context=ToolInvocationContext(run_id=run_id),
    )


def _files(run_id: str) -> list[Any]:
    return [item for item in runtime_repository.get_run(run_id).artifacts if item.kind == "file"]


def _workbook(run_id: str, artifact_id: str) -> tuple[Any, bytes]:
    data = runtime_repository.read_artifact_file(run_id, artifact_id).data
    return load_workbook(io.BytesIO(data)), data


def _argument_errors(result: ToolResult) -> list[tuple[str, str]]:
    assert (result.success, result.error_code) == (False, INVALID_ARGUMENTS_CODE), result
    return [(item["argument"], item["code"]) for item in result.error_details["argument_errors"]]


def _forget_run(run_id: str) -> None:
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - テストの後始末
        repository._runs.pop(run_id, None)  # noqa: SLF001
        repository._artifact_files = {  # noqa: SLF001
            key: data
            for key, data in repository._artifact_files.items()  # noqa: SLF001
            if key[0] != run_id
        }


@pytest.fixture
def run_id() -> Iterator[str]:
    """NL2SQL の結果を 1 つ持つ、実行中の Run（結果は表の参照 `T1` として登録される）。"""
    run = RunState(
        id=f"run5837x-{uuid4().hex[:8]}",
        goal="合成の質問",
        agent_id="default",
        runtime_id="builtin",
        status=RunStatus.RUNNING,
    )
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - 実行中の Run を直接置く
        repository._runs[run.id] = run  # noqa: SLF001
    step_id, _ = runtime_repository.start_builtin_tool_step(
        run.id, ToolCall(name="nl2sql__nl2sql_query", arguments={})
    )
    runtime_repository.finish_builtin_tool_step(
        run.id,
        step_id,
        ToolResult(name="nl2sql__nl2sql_query", success=True, output=NL2SQL_OUTPUT),
    )
    try:
        yield run.id
    finally:
        _forget_run(run.id)


# ---- 表の参照（値はモデルから受け取らない） -------------------------------------------------


def test_referenced_values_are_copied_from_the_stored_result(run_id: str) -> None:
    arguments = _sheet(
        name="参照",
        # 見出しが null の列は、参照した表の列名になる。
        columns=[
            _column("number", header="金額（円）", format="integer"),
            _column("text"),
            _column("date"),
            _column("number", format="percent1"),
            _column("text", header="番号"),
        ],
        # 行・列は書いた順に並ぶ（回答の表の参照と同じ指定）。
        source=_source(rows="2,1,3", columns="2,1,4,3,5"),
        summaries=[{"function": "sum", "columns": [1], "label": "合計"}],
    )
    # モデルの引数に、表の値は 1 つも無い。
    assert all(str(value) not in json.dumps(arguments) for value in (1200, 0.25, "record-1"))

    result = _invoke(run_id, arguments, title="合成の集計")

    assert result.success, result.error
    assert result.policy_decision == ToolPolicyDecision.ALLOW and not result.approval_required
    [artifact] = _files(run_id)
    workbook, data = _workbook(run_id, artifact.id)
    sheet = workbook["参照"]
    assert [cell.value for cell in sheet[1]] == [
        "金額（円）",
        "LABEL",
        "CREATED_AT",
        "RATE",
        "番号",
    ]
    expected = [
        [30, "=1+1", datetime(2026, 2, 10, 18, 30), 0.5, "0000000000000000000001"],
        [1200, "record-1", datetime(2026, 1, 5), 0.25, "12345678901234567890"],
        [None, "record-3", None, None, None],
    ]
    assert [[cell.value for cell in sheet[row]] for row in (2, 3, 4)] == expected
    # 数値は数値、日付は日付、文字は文字（`=` で始まる値も数式にしない）。
    assert [cell.data_type for cell in sheet[2]] == ["n", "s", "d", "n", "s"]
    assert [cell.number_format for cell in sheet[2]] == [
        "#,##0",
        "@",
        "yyyy-mm-dd hh:mm",
        "0.0%",
        "@",
    ]
    assert [cell.value for cell in sheet[5]] == ["=SUM(A2:A4)", "合計", None, None, None]
    # 結果は成果物の参照と要約だけ（中身を載せない）。
    assert result.output == {
        "file": {
            "artifact_id": artifact.id,
            "file_name": "合成の集計.xlsx",
            "mime_type": XLSX_MIME_TYPE,
            "size_bytes": len(data),
            "sha256": artifact.content["sha256"],
        },
        "sheets": [{"name": "参照", "rows": 3, "columns": 5, "summary_rows": 1}],
        "charts": [],
    }
    encoded = base64.b64encode(data).decode("ascii")
    recorded = result.model_dump_json() + runtime_repository.get_run(run_id).model_dump_json()
    assert encoded[:64] not in recorded


def test_bracketed_table_reference_and_written_rows_work_together(run_id: str) -> None:
    result = _invoke(
        run_id,
        _sheet(
            name="参照",
            columns=[_column("text"), _column("number")],
            source=_source(table="[[table:T1]]", rows="1", columns="1-2"),
        ),
        _sheet(
            name="説明",
            heading="合成の説明",
            notes=["値の出所: 合成のデータ。"],
            columns=[_column("text", header="項目"), _column("number", header="値")],
            rows=[["a", 1], ["b", 2.5], ["c", None]],
            summaries=[{"function": "average", "columns": [2], "label": "平均"}],
        ),
    )

    assert result.success, result.error
    workbook, _ = _workbook(run_id, _files(run_id)[0].id)
    assert workbook.sheetnames == ["参照", "説明"]
    assert [cell.value for cell in workbook["参照"][2]] == ["record-1", 1200]
    written = workbook["説明"]
    assert [written["A1"].value, written["A2"].value] == ["合成の説明", "値の出所: 合成のデータ。"]
    assert [[cell.value for cell in written[row]] for row in (5, 6, 7)] == [
        ["a", 1],
        ["b", 2.5],
        ["c", None],
    ]
    assert written["B8"].value == '=IF(COUNT(B5:B7)=0,"",AVERAGE(B5:B7))'


def test_chart_refers_to_the_cells_copied_from_the_stored_result(run_id: str) -> None:
    arguments = _sheet(
        name="グラフ",
        heading="合成のグラフ",
        columns=[_column("text"), _column("number", format="integer"), _column("number")],
        source=_source(rows="1-2", columns="1-3"),
        summaries=[{"function": "sum", "columns": [2], "label": None}],
        chart=_chart("column", 1, [2, 3]),
    )
    # モデルの引数に、グラフの値は 1 つも無い（どの列を項目・系列にするかだけ）。
    assert all(str(value) not in json.dumps(arguments) for value in (1200, 0.25, "record-1"))

    result = _invoke(run_id, arguments, _sheet(name="表だけ", rows=[["x"]]))

    assert result.success, result.error
    assert result.output is not None
    assert result.output["charts"] == [
        {"sheet": "グラフ", "type": "column", "series": 2, "categories": 2}
    ]
    workbook, _ = _workbook(run_id, _files(run_id)[0].id)
    sheet = workbook["グラフ"]
    # 題（1 行目）・空行・見出し（3 行目）・データ（4〜5 行目）・集計（6 行目）。
    assert [[cell.value for cell in sheet[row]] for row in (3, 4, 5, 6)] == [
        ["LABEL", "AMOUNT", "RATE"],
        ["record-1", 1200, 0.25],
        ["=1+1", 30, 0.5],
        [None, "=SUM(B4:B5)", None],
    ]
    [chart] = sheet._charts  # noqa: SLF001 - openpyxl に、読んだグラフの公開の API が無い
    # グラフは、写した表の cell を参照する（集計の行は含めない）。
    assert [
        (series.tx.strRef.f, series.cat.strRef.f, series.val.numRef.f) for series in chart.series
    ] == [
        ("'グラフ'!$B$3", "'グラフ'!$A$4:$A$5", "'グラフ'!$B$4:$B$5"),
        ("'グラフ'!$C$3", "'グラフ'!$A$4:$A$5", "'グラフ'!$C$4:$C$5"),
    ]
    assert workbook["表だけ"]._charts == []  # noqa: SLF001


@pytest.mark.parametrize(
    ("sheet", "expected"),
    [
        (
            {"columns": [_column("text")], "source": _source(table="T9")},
            ("sheets.0.source.table", "unknown_table"),
        ),
        (
            # cell の参照は表の参照ではない。
            {"columns": [_column("text")], "source": _source(table="T1:R1:C1")},
            ("sheets.0.source.table", "unknown_table"),
        ),
        (
            {"columns": [_column("text")], "source": _source(rows="4", columns="1")},
            ("sheets.0.source.rows", "selection"),
        ),
        (
            {"columns": [_column("text")], "source": _source(rows="1", columns="6")},
            ("sheets.0.source.columns", "selection"),
        ),
        (
            {"columns": [_column("text")], "source": _source(rows="1,1", columns="1")},
            ("sheets.0.source.rows", "selection"),
        ),
        (
            {"columns": [_column("text")], "source": _source(rows="", columns="1")},
            ("sheets.0.source.rows", "selection"),
        ),
        (
            # 選んだ列の数と columns の数が違う。
            {"columns": [_column("text")], "source": _source(columns="1,2")},
            ("sheets.0.columns", "count"),
        ),
        (
            {"columns": [_column("text")], "source": _source(columns="1"), "rows": [["x"]]},
            ("sheets.0.rows", "conflict"),
        ),
        (
            # 参照しないシートは、列の見出しが要る。
            {"columns": [_column("text")], "rows": [["x"]]},
            ("sheets.0.columns.0.header", "required"),
        ),
        (
            # 参照した値が列の型に合わない（文字の列を数値にした）。
            {"columns": [_column("number")], "source": _source(columns="1")},
            ("sheets.0.columns.0.type", "not_number"),
        ),
        (
            # Excel の精度を超える番号を数値にした（丸めずに誤りにする）。
            {"columns": [_column("number")], "source": _source(columns="5")},
            ("sheets.0.columns.0.type", "precision"),
        ),
        (
            {"columns": [_column("date")], "source": _source(columns="2")},
            ("sheets.0.columns.0.type", "not_date"),
        ),
        (
            # 文字の列は、グラフの系列にできない。
            {
                "columns": [_column("text"), _column("text")],
                "source": _source(columns="1,5"),
                "chart": _chart("bar", 1, [2]),
            },
            ("sheets.0.chart.series_columns.0", "not_number"),
        ),
        (
            # 円のグラフは、空の値を含められない（3 行目の値が無い）。
            {
                "columns": [_column("text"), _column("number")],
                "source": _source(columns="1,2"),
                "chart": _chart("pie", 1, [2]),
            },
            ("sheets.0.columns.1", "pie_values"),
        ),
    ],
    ids=[
        "unknown-table",
        "cell-reference",
        "row-out-of-range",
        "column-out-of-range",
        "duplicate-row",
        "empty-selection",
        "column-count",
        "source-and-rows",
        "header-required",
        "text-as-number",
        "big-number",
        "number-as-date",
        "chart-text-series",
        "chart-pie-empty-value",
    ],
)
def test_bad_references_and_values_are_argument_errors(
    run_id: str, sheet: dict[str, Any], expected: tuple[str, str]
) -> None:
    result = _invoke(run_id, _sheet(**sheet))

    assert _argument_errors(result) == [expected]
    # 成功にせず、ファイルも作らない。誤りの文に結果の値を写さない。
    assert _files(run_id) == []
    text = json.dumps(result.error_details, ensure_ascii=False)
    assert all(str(row["LABEL"]) not in text for row in ROWS)
    assert "12345678901234567890" not in text


def test_schema_violations_are_rejected_before_the_handler(run_id: str) -> None:
    for arguments in (
        {"title": "", "sheets": [_sheet()]},
        {"title": "t", "sheets": []},
        {"title": "t", "sheets": [_sheet(name="x" * 32)]},
        {"title": "t", "sheets": [_sheet(columns=[_column("money", header="a")])]},
        {"title": "t", "sheets": [_sheet(columns=[_column("number", header="a", format="￥")])]},
        {"title": "t", "sheets": [_sheet(rows=[[True]])]},
        {"title": "t", "sheets": [_sheet(rows=[["x"]] * (MAX_WRITTEN_ROWS + 1))]},
        {"title": "t", "sheets": [_sheet(chart={"type": "bar"})]},
        {"title": "t", "sheets": [_sheet(chart=_chart("area", 1, [2]))]},
        {"title": "t", "sheets": [_sheet(chart={**_chart("bar", 1, [2]), "title": "x"})]},
        {"title": "t", "sheets": [_sheet(chart=_chart("bar", 0, [2]))]},
        {"title": "t", "sheets": [_sheet(image="x")]},
        {
            "title": "t",
            "sheets": [_sheet(summaries=[{"function": "median", "columns": [1], "label": None}])],
        },
        {"title": "t", "sheets": [_sheet(source={"table": "T1"})]},
    ):
        result = tool_registry.invoke(
            ToolCall(name=XLSX_CREATE_TOOL, arguments=arguments),
            context=ToolInvocationContext(run_id=run_id),
        )
        assert (result.success, result.error_code) == (False, INVALID_ARGUMENTS_CODE), arguments
        assert result.audit_metadata["argument_validation"] == "failed"
    assert _files(run_id) == []


def test_files_per_run_are_limited(run_id: str, monkeypatch: MonkeyPatch) -> None:
    assert MAX_FILES_PER_RUN == 10
    monkeypatch.setattr(xlsx_tool, "MAX_FILES_PER_RUN", 2)
    sheet = _sheet(rows=[["x"]])

    results = [_invoke(run_id, sheet, title=f"file-{index}") for index in range(3)]

    assert [result.success for result in results] == [True, True, False]
    assert results[2].error_code == FILE_LIMIT_CODE
    assert [item.name for item in _files(run_id)] == ["file-0.xlsx", "file-1.xlsx"]


def test_tool_fails_outside_a_run_and_after_the_run_ended(run_id: str) -> None:
    sheet = _sheet(rows=[["x"]])
    assert _invoke(None, sheet).error_code == FILE_RUN_CLOSED
    assert _invoke("run_missing", sheet).error_code == FILE_RUN_CLOSED
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - Run を終わった状態にする
        repository._runs[run_id].status = RunStatus.COMPLETED  # noqa: SLF001

    result = _invoke(run_id, sheet)

    assert (result.success, result.error_code) == (False, FILE_RUN_CLOSED)
    assert _files(run_id) == []


# ---- ツール権限（既定は承認なし。管理者が変えられる） ---------------------------------------


def test_default_policy_allows_and_the_admin_can_change_it(run_id: str) -> None:
    definition = tool_registry.get(XLSX_CREATE_TOOL)
    assert definition is not None and not definition.side_effects
    for mode in ("approval", "deny"):
        assert ToolPolicy(default_mode=mode).decide(definition) == ToolPolicyDecision.ALLOW
    # ツール権限の画面の一覧に、Control Plane のツールとして出る。
    [row] = [
        tool
        for tool in tool_policy_catalog(ToolInvocationContext()).tools
        if tool.name == XLSX_CREATE_TOOL
    ]
    assert (row.server_id, row.default_decision) == (CONTROL_PLANE_SOURCE, ToolPolicyDecision.ALLOW)
    call = ToolCall(name=XLSX_CREATE_TOOL, arguments={"title": "t", "sheets": [_sheet()]})
    context = ToolInvocationContext(run_id=run_id)

    asked = tool_registry.invoke(call, policy=ToolPolicy(ask={XLSX_CREATE_TOOL}), context=context)
    denied = tool_registry.invoke(
        call, policy=ToolPolicy(deny={XLSX_CREATE_TOOL}), context=context, force=True
    )

    assert (asked.success, asked.approval_required) == (False, True)
    assert (denied.success, denied.policy_decision) == (False, ToolPolicyDecision.DENY)
    assert _files(run_id) == []


# ---- スキルの宣言からだけ渡る（組み込み Runtime） -------------------------------------------


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
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
    return model


@pytest.fixture
def agent(monkeypatch: MonkeyPatch) -> Iterator[str]:
    """NL2SQL を fake にした、業務 Agent の ID（スキルは各テストが決める）。"""
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
    agent_id = f"agent5837x-{uuid4().hex[:8]}"
    original = runtime_config_store.get_tool_policy()
    try:
        yield agent_id
    finally:
        runtime_config_store.patch_tool_policy(
            default_mode=original.default_mode,
            allow=sorted(original.allow),
            ask=sorted(original.ask),
            deny=sorted(original.deny),
        )
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            run_ids = [
                run_id
                for run_id, run in repository._runs.items()  # noqa: SLF001
                if run.agent_id == agent_id
            ]
        for run_id in run_ids:
            _forget_run(run_id)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(agent_id)


def _run(agent_id: str) -> RunState:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="合成の値を Excel のファイルにして", agent_id=agent_id),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id)


XLSX_CALL = {
    "title": "合成の一覧",
    "sheets": [
        _sheet(
            name="一覧",
            columns=[_column("text"), _column("number", format="integer")],
            source=_source(columns="1,2"),
            summaries=[{"function": "sum", "columns": [2], "label": "合計"}],
            chart=_chart("bar", 1, [2]),
        )
    ],
}


def test_builtin_skill_declares_only_the_xlsx_tool() -> None:
    skill = skill_registry.get(XLSX_CREATE_SKILL_ID)
    assert skill is not None and skill.source == "builtin" and skill.name == "Excel 作成"
    assert [(item.server_id, item.tool_names) for item in skill.mcp_requirements] == [
        ("control-plane", [XLSX_CREATE_TOOL])
    ]
    assert builtin_runtime.agent_tool_names([XLSX_CREATE_SKILL_ID]) == [XLSX_CREATE_TOOL]
    assert builtin_runtime.agent_tool_names([STRUCTURED_DATA_QUERY_SKILL_ID]) == []


def test_agent_without_the_skill_does_not_get_the_tool(
    monkeypatch: MonkeyPatch, agent: str
) -> None:
    runtime_repository.create_agent(
        AgentProfile(
            id=agent, name="データ照会の業務 Agent", skill_ids=[STRUCTURED_DATA_QUERY_SKILL_ID]
        )
    )
    model = _script(
        monkeypatch,
        [function_call("nl2sql__nl2sql_query", {"question": "合成の値"}, call_id="call-data")],
        [function_call(XLSX_CREATE_TOOL, XLSX_CALL, call_id="call-xlsx")],
        [assistant_message("合成の値は表のとおりです。Excel のファイルは作れません。")],
    )

    run = _run(agent)

    assert run.status == RunStatus.COMPLETED, run.events[-1].model_dump_json()
    assert all(XLSX_CREATE_TOOL not in {tool.name for tool in call.tools} for call in model.calls)
    # 宣言の外の呼び出しは実行せず、理由を残す。
    undeclared = [
        event.payload["undeclared_tool_call"]
        for event in run.events
        if "undeclared_tool_call" in event.payload
    ]
    assert [(item["tool_name"], item["reason"]) for item in undeclared] == [
        (XLSX_CREATE_TOOL, UNDECLARED_CALL_NOT_DECLARED)
    ]
    assert [item for item in run.artifacts if item.kind == FILE_ARTIFACT_KIND] == []


def test_agent_with_the_skill_creates_a_file_from_the_run_data_without_approval(
    monkeypatch: MonkeyPatch, agent: str
) -> None:
    runtime_repository.create_agent(
        AgentProfile(
            id=agent,
            name="データと Excel の業務 Agent",
            skill_ids=[STRUCTURED_DATA_QUERY_SKILL_ID, XLSX_CREATE_SKILL_ID],
        )
    )
    model = _script(
        monkeypatch,
        [function_call("nl2sql__nl2sql_query", {"question": "合成の値"}, call_id="call-data")],
        [function_call(XLSX_CREATE_TOOL, XLSX_CALL, call_id="call-xlsx")],
        [
            assistant_message(
                "ファイル「合成の一覧.xlsx」を作りました。シート「一覧」に 3 行あります。"
            )
        ],
    )

    run = _run(agent)

    # 既定のツール権限で、承認待ちにならずに完了する。
    assert run.status == RunStatus.COMPLETED, run.events[-1].model_dump_json()
    assert run.approvals == []
    assert XLSX_CREATE_TOOL in {tool.name for tool in model.calls[0].tools}
    # スキルの指示がモデルに渡る。
    assert "## Step 2: 表の値の渡し方を選ぶ" in str(model.calls[0].system_instructions)
    [artifact] = [item for item in run.artifacts if item.kind == FILE_ARTIFACT_KIND]
    assert artifact.content["file_name"] == "合成の一覧.xlsx"
    workbook, data = _workbook(run.id, artifact.id)
    sheet = workbook["一覧"]
    # 値は NL2SQL の結果と一致する（モデルの引数に値は無い）。
    assert [[cell.value for cell in sheet[row]] for row in (1, 2, 3, 4)] == [
        ["LABEL", "AMOUNT"],
        *[[row["LABEL"], row["AMOUNT"]] for row in ROWS],
    ]
    assert sheet["B5"].value == "=SUM(B2:B4)" and sheet["A5"].value == "合計"
    # グラフは表の cell を参照する（集計の行は含めない）。
    [chart] = sheet._charts  # noqa: SLF001 - openpyxl に、読んだグラフの公開の API が無い
    assert [(item.cat.strRef.f, item.val.numRef.f) for item in chart.series] == [
        ("'一覧'!$A$2:$A$4", "'一覧'!$B$2:$B$4")
    ]
    # step・イベント・モデルへの入力に、ファイルの中身は載らない（参照だけ）。
    [step] = [
        item for item in run.steps if item.tool_call and item.tool_call.name == XLSX_CREATE_TOOL
    ]
    assert step.tool_result is not None and step.tool_result.success
    output = step.tool_result.output or {}
    assert set(output) == {"file", "sheets", "charts"}
    assert output["charts"] == [{"sheet": "一覧", "type": "bar", "series": 1, "categories": 3}]
    encoded = base64.b64encode(data).decode("ascii")
    assert encoded[:64] not in run.model_dump_json()
    model_input = str(model.calls[2].input)
    assert encoded[:64] not in model_input and artifact.id in model_input
    model.assert_complete()


def test_admin_deny_removes_the_tool_and_ask_waits_for_approval(
    monkeypatch: MonkeyPatch, agent: str
) -> None:
    """管理者がツール権限を変えれば、拒否のツールは渡らず、承認のツールは承認待ちになる。"""
    runtime_repository.create_agent(
        AgentProfile(id=agent, name="Excel の業務 Agent", skill_ids=[XLSX_CREATE_SKILL_ID])
    )
    written = {"title": "t", "sheets": [_sheet(rows=[["x"]])]}
    runtime_config_store.patch_tool_policy(deny=[XLSX_CREATE_TOOL])
    denied_model = _script(monkeypatch, [assistant_message("Excel のファイルは作れません。")])

    denied = _run(agent)

    assert denied.status == RunStatus.COMPLETED, denied.events[-1].model_dump_json()
    assert XLSX_CREATE_TOOL not in {tool.name for tool in denied_model.calls[0].tools}

    runtime_config_store.patch_tool_policy(ask=[XLSX_CREATE_TOOL], deny=[])
    _script(monkeypatch, [function_call(XLSX_CREATE_TOOL, written, call_id="call-ask")])

    asked = _run(agent)

    assert asked.status == RunStatus.WAITING_APPROVAL
    assert [item for item in asked.artifacts if item.kind == FILE_ARTIFACT_KIND] == []
