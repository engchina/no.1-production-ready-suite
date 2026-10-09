"""多段の質問の案内（#1345・#1351・#1365）の決定論テスト。

RAG は部品（根拠・構成・順に読む）を出し、多段の組み立ては Agent が行う。Control Plane は、根拠を
集める・読むツールの本文に同じ文書の別の箇所への参照（「第 4 章を参照」）があれば読む先を、同じ
（ほぼ同じ）query の検索を繰り返したら繰り返しを止める案内を、RAG の予算があればこの Run で残る
検索の回数を、モデルへの結果に足す（記録する step の結果は RAG のまま）。モデルが既定より小さくした
`evidence_limit` は既定に引き上げる（#1351）。query の実体に当たる台帳・一覧の行に略号・区分の
短い値があれば、その意味を引く次の段を案内する（#1365）。
SDK の `ScriptedModel` と契約どおりの fake の RAG の MCP（`mcp_support`）で確かめる。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from mcp_support import (
    DEFAULT_OUTPUTS,
    FakeProductMcp,
    RagRetrieveEvidenceIn,
    RagSearchIn,
    fake_product_mcp,
)
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunState,
    RunStep,
    runtime_repository,
)
from app.features.agent.skills import (
    AgentSkillDefinition,
    SkillMcpRequirement,
    skill_registry,
)
from app.features.agent.support_task import (
    MAX_RECORD_CODES,
    MAX_REFERENCES,
    RECORD_CODE_HINT,
    REFERENCE_READ_HINT,
    REPEATED_QUERY_HINT,
    normalized_query,
    raised_evidence_limit,
    record_codes,
    repeated_query_note,
    similar_queries,
    text_references,
)
from app.features.agent.tools import ToolCall, ToolResult
from app.settings import Settings, get_settings

SKILL_ID = "test1345-skill"
AGENT_ID = "test1345-agent"
USER_UUID = "11111111-2222-3333-4444-555555555555"
GOAL = "HRM の変更の承認者の代理は誰ですか？"


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    fake = fake_product_mcp(monkeypatch)
    # 最終の検証（#1246）はこのテストの対象外。
    monkeypatch.setattr(get_settings(), "agent_final_validation_enabled", False)
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name=SKILL_ID,
            instructions="段ごとに rag_retrieve_evidence で調べる。",
            mcp_requirements=[
                SkillMcpRequirement(
                    server_id="rag",
                    tool_names=[
                        "rag_search",
                        "rag_retrieve_evidence",
                        "rag_outline",
                        "rag_read_document",
                    ],
                )
            ],
        )
    )
    runtime_repository.create_agent(AgentProfile(id=AGENT_ID, name=AGENT_ID, skill_ids=[SKILL_ID]))
    try:
        yield fake
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == AGENT_ID
            ]:
                repository._runs.pop(run_id)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(AGENT_ID)
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(SKILL_ID)


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


def _run() -> RunState:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=GOAL, agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id)


def _tool_output(model: ScriptedModel, call_index: int, call_id: str) -> dict[str, Any]:
    """モデルの `call_index` 回目の入力にある、ツールの結果（モデルが見たもの）。"""
    for item in model.calls[call_index].input:
        if isinstance(item, dict) and item.get("call_id") == call_id and "output" in item:
            loaded: dict[str, Any] = json.loads(str(item["output"]))
            return loaded
    raise AssertionError(f"{call_id} の結果がモデルの入力にありません")


def _evidence_with(excerpt: str) -> dict[str, Any]:
    output: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["rag_retrieve_evidence"])
    output["evidence"][0].update(document_id="doc-org", file_name="組織規程.pdf", excerpt=excerpt)
    return output


# ---- 参照の検出（決定的） ---------------------------------------------------------------------


def test_references_are_found_in_evidence_and_read_text() -> None:
    evidence = {
        "evidence": [
            {
                "document_id": "doc-org",
                "file_name": "組織規程.pdf",
                # 全角の数字は NFKC で同じに扱い、空白だけが違う同じ参照（第 4 章も）は 1 つにする。
                "excerpt": "代理は第４章を参照してください。期限は第 2 章 を参照。第 4 章も参照。",
            },
            {"document_id": "doc-ledger", "excerpt": "詳しくは別表 2 参照のこと。"},
            {"document_id": "", "excerpt": "第 9 章を参照"},
        ]
    }
    assert text_references("rag_retrieve_evidence", evidence) == [
        {
            "document_id": "doc-org",
            "file_name": "組織規程.pdf",
            "reference": "第4章",
            "how_to_read": REFERENCE_READ_HINT,
        },
        {
            "document_id": "doc-org",
            "file_name": "組織規程.pdf",
            "reference": "第 2 章",
            "how_to_read": REFERENCE_READ_HINT,
        },
        {
            "document_id": "doc-ledger",
            "file_name": None,
            "reference": "別表 2",
            "how_to_read": REFERENCE_READ_HINT,
        },
    ]

    # 読み取りのツールは本文（rag_read_source は親の本文も）から探す。
    read_source = {"document_id": "doc-1", "text": "本文", "parent_text": "付録 A を参照"}
    assert [item["reference"] for item in text_references("rag_read_source", read_source)] == [
        "付録 A"
    ]
    read_document = {"document_id": "doc-1", "text": "--- p.2 ---\n第3条を参照する。"}
    assert [item["reference"] for item in text_references("rag_read_document", read_document)] == [
        "第3条"
    ]


def test_references_ignore_mentions_without_a_reference_and_other_tools() -> None:
    # 「参照」を伴わない章の言及・ほかのツールの出力は案内しない。
    assert (
        text_references(
            "rag_retrieve_evidence",
            {"evidence": [{"document_id": "d", "excerpt": "第 4 章では代理を定める。"}]},
        )
        == []
    )
    assert (
        text_references(
            "rag_search", {"evidence": [{"document_id": "d", "excerpt": "第 4 章を参照"}]}
        )
        == []
    )
    assert text_references("rag_read_document", {"text": "第 4 章を参照"}) == []

    many = "".join(f"第 {index} 章を参照。" for index in range(1, 10))
    found = text_references("rag_read_document", {"document_id": "d", "text": many})
    assert len(found) == MAX_REFERENCES


# ---- Run ----------------------------------------------------------------------------------------


def test_evidence_with_a_reference_gets_where_to_read_and_the_remaining_budget(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    mcp.outputs["rag_retrieve_evidence"] = _evidence_with("代理は第 4 章を参照してください。")
    model = _script(
        monkeypatch,
        [
            function_call(
                "rag__rag_retrieve_evidence", {"query": "情報システム部 承認者"}, call_id="call-1"
            )
        ],
        [function_call("rag__rag_read_document", {"document_id": "doc-org"}, call_id="call-2")],
        [assistant_message("代理は管理本部長です。")],
    )
    run = _run()

    limit = int(get_settings().agent_max_rag_calls_per_run)
    seen = _tool_output(model, 1, "call-1")
    assert seen["references"] == [
        {
            "document_id": "doc-org",
            "file_name": "組織規程.pdf",
            "reference": "第 4 章",
            "how_to_read": REFERENCE_READ_HINT,
        }
    ]
    assert seen["rag_calls_remaining"] == limit - 1
    # 読み取りは RAG の上限に数えないので、残りは変わらない。
    # 参照の無い本文には references を足さない。
    read = _tool_output(model, 2, "call-2")
    assert read["rag_calls_remaining"] == limit - 1
    assert "references" not in read

    # 記録する step の結果は RAG の結果のまま。
    recorded = [step.tool_result.output for step in run.steps if step.tool_result is not None]
    assert all("references" not in (output or {}) for output in recorded)
    assert all("rag_calls_remaining" not in (output or {}) for output in recorded)


def test_no_budget_means_no_remaining_count(monkeypatch: MonkeyPatch, mcp: FakeProductMcp) -> None:
    monkeypatch.setattr(get_settings(), "agent_max_rag_calls_per_run", 0)
    model = _script(
        monkeypatch,
        [function_call("rag__rag_retrieve_evidence", {"query": "承認者"}, call_id="call-1")],
        [assistant_message("承認者は部長です。")],
    )
    _run()
    seen = _tool_output(model, 1, "call-1")
    assert "rag_calls_remaining" not in seen
    assert "references" not in seen


def test_default_rag_budget_covers_five_hops_and_a_rephrase() -> None:
    # 5 段の橋渡し（段ごとに 1 回の rag_retrieve_evidence）と 1 回の言い換え（#1345）。
    assert Settings.model_fields["agent_max_rag_calls_per_run"].default == 6


# ---- 段の検索（#1351） --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "similar"),
    [
        # 空白・助詞・疑問の記号・全角の違いは同じ query。
        ("勤怠管理システムの担当部署は？", "勤怠管理システム 担当 部署", True),
        ("ＨＲＭ　担当部署", "HRM 担当部署", True),
        ("勤怠管理システム 締め 期限", "勤怠管理システムの締めの期限", True),
        # 属性が違う・属性を足した検索は次の段（繰り返しではない）。
        ("経費精算ポータル 担当部署", "経費精算ポータル 承認者", False),
        ("経費精算ポータル 担当部署", "経費精算ポータル 担当部署 承認者", False),
        ("経費精算ポータル 担当部署", "総務部 承認者", False),
        ("", "担当部署", False),
    ],
    ids=["particles", "full-width", "paraphrase", "attribute", "added", "entity", "empty"],
)
def test_similar_queries_are_deterministic(left: str, right: str, similar: bool) -> None:
    assert similar_queries(normalized_query(left), normalized_query(right)) is similar


def test_evidence_limit_below_the_default_is_raised_to_the_schema_default() -> None:
    schema = {"properties": {"evidence_limit": {"type": "integer", "default": 12}}}
    assert raised_evidence_limit({"evidence_limit": 5}, schema) == 12
    # 既定以上・指定なし・schema に既定が無いときは変えない。
    assert raised_evidence_limit({"evidence_limit": 12}, schema) is None
    assert raised_evidence_limit({"evidence_limit": 20}, schema) is None
    assert raised_evidence_limit({}, schema) is None
    assert raised_evidence_limit({"evidence_limit": 5}, {"properties": {}}) is None
    assert raised_evidence_limit({"evidence_limit": True}, schema) is None


def _step(name: str, arguments: dict[str, Any], *, success: bool = True) -> RunStep:
    return RunStep(
        run_id="run-1",
        tool_call=ToolCall(name=name, arguments=arguments),
        tool_result=ToolResult(name=name, success=success, output={} if success else None),
    )


def test_repeated_query_compares_the_same_tool_and_scope_only() -> None:
    tool = "rag__rag_retrieve_evidence"
    arguments: dict[str, Any] = {"query": "勤怠管理システムの担当部署"}
    current = _step(tool, arguments)
    steps = [
        _step(tool, {"query": "勤怠管理システム 担当部署"}),
        # 失敗した呼び出し・別のツール・別のプロファイル・条件を足した呼び直しは数えない。
        _step(tool, {"query": "勤怠管理システム 担当部署"}, success=False),
        _step("rag__rag_search", {"query": "勤怠管理システム 担当部署"}),
        _step(tool, {"query": "勤怠管理システム 担当部署", "search_answer_profile_id": "p2"}),
        _step(tool, {"query": "勤怠管理システム 担当部署", "conditions": {"kind": "年間"}}),
        _step(tool, {"query": "総務部 承認者"}),
        current,
    ]
    note = repeated_query_note(tool, arguments, steps, current_step_id=current.id)
    assert note == {
        "count": 1,
        "similar_queries": ["勤怠管理システム 担当部署"],
        "next_step": REPEATED_QUERY_HINT,
    }
    # 件数（evidence_limit・top_k）だけが違う呼び出しも同じ範囲。空のリストは指定なしと同じ。
    assert repeated_query_note(
        tool,
        {"query": "総務部 承認者", "evidence_limit": 20, "knowledge_base_ids": []},
        steps,
        current_step_id=current.id,
    ) == {"count": 1, "similar_queries": ["総務部 承認者"], "next_step": REPEATED_QUERY_HINT}
    # 初めての query・読み取りのツールには付けない。
    assert (
        repeated_query_note(tool, {"query": "管理本部長 期限"}, steps, current_step_id=current.id)
        is None
    )
    assert (
        repeated_query_note(
            "rag__rag_read_document", {"query": "総務部 承認者"}, steps, current_step_id="x"
        )
        is None
    )


def test_repeated_hop_query_gets_a_stop_hint_and_small_evidence_limit_is_raised(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    model = _script(
        monkeypatch,
        [
            function_call(
                "rag__rag_retrieve_evidence",
                {"query": "勤怠管理システム 担当部署", "evidence_limit": 5},
                call_id="call-1",
            )
        ],
        [
            function_call(
                "rag__rag_retrieve_evidence",
                {"query": "勤怠管理システムの担当部署は？", "evidence_limit": 20},
                call_id="call-2",
            )
        ],
        [
            function_call(
                "rag__rag_retrieve_evidence",
                {"query": "システム台帳 勤怠管理システム 担当部署"},
                call_id="call-3",
            )
        ],
        [assistant_message("担当部署は総務部です。")],
    )
    run = _run()

    # 既定より小さい evidence_limit は既定（RAG の契約の 20。#1365）に引き上げ、既定以上は変えない。
    sent = [
        call["arguments"].get("evidence_limit") for call in mcp.calls_of("rag_retrieve_evidence")
    ]
    assert sent == [20, 20, None]
    # step には送った値を残す。
    recorded = [step.tool_call.arguments for step in run.steps if step.tool_call is not None]
    assert recorded[0]["evidence_limit"] == 20

    assert "repeated_query" not in _tool_output(model, 1, "call-1")
    assert _tool_output(model, 2, "call-2")["repeated_query"] == {
        "count": 1,
        "similar_queries": ["勤怠管理システム 担当部署"],
        "next_step": REPEATED_QUERY_HINT,
    }
    # 台帳を引く次の段は繰り返しではない。
    assert "repeated_query" not in _tool_output(model, 3, "call-3")
    # 記録する step の結果は RAG の結果のまま。
    outputs = [step.tool_result.output for step in run.steps if step.tool_result is not None]
    assert all("repeated_query" not in (output or {}) for output in outputs)


def test_business_rag_research_instructions_cover_hop_queries() -> None:
    skill = skill_registry.get("business_rag_research")
    assert skill is not None
    instructions = skill.instructions
    # 段の query は実体と属性だけ・evidence_limit は既定より小さくしない・言い換えは 2 回まで・
    # 台帳を示されたらその実体で台帳を引く・繰り返しの案内に従う（#1351）。
    for phrase in (
        "実体（前の段で分かった正式名・略号・ID・役職名を根拠の表記のまま）と引く属性だけ",
        "evidence_limit は既定より小さくしない",
        "record_codes",
        "1 回目の結果だけで答えず、その意味を次の段で引く",
        "言い換えて 2 回引いても根拠が出なければ",
        "その台帳・一覧の名前と実体と属性で次の段を引く",
        "repeated_query",
        "rag_calls_remaining",
        "references",
    ):
        assert phrase in instructions, phrase


# ---- 台帳の行の略号・区分（#1365） --------------------------------------------------------------

CONTRACTS = Path(__file__).resolve().parents[3] / "platform/contracts/mcp"


def _record(excerpt: str, chunk_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "document_id": "doc-ledger",
        "file_name": "system-ledger.xlsx",
        "chunk_id": chunk_id,
        "content_kind": "record",
        "excerpt": excerpt,
        **extra,
    }


# #1335 の再評価の D の形: 「経費精算ポータル 担当部署」の 1 回目の結果に、台帳の行（担当部署が
# 略号の「経」）と別のシステムの行、略号の表を指さない規程の本文が並ぶ。
_LEDGER_EVIDENCE: dict[str, Any] = {
    "evidence": [
        {
            "document_id": "doc-guide",
            "chunk_id": "guide-1",
            "content_kind": "text",
            "excerpt": "変更の承認は担当部署: 経 の承認者が行う。",
        },
        _record(
            "システムID: SYS-102 / 正式名: 勤怠管理システム / 担当部署: 人 / 重要度: A",
            "ledger-102",
        ),
        _record(
            "システムID: SYS-101 / 正式名: 経費精算ポータル / 略称・別表記: 経費 Portal"
            " / 担当部署: 経 / 重要度: B / 機密区分: 社外秘 / 保管年数: 7",
            "ledger-101",
        ),
    ]
}


def test_record_codes_point_to_short_values_of_the_matching_row() -> None:
    note = record_codes(_LEDGER_EVIDENCE, "経費精算ポータル 担当部署", [])
    assert note == {
        "values": [
            {
                "field": "担当部署",
                "value": "経",
                "record_of": "経費精算ポータル",
                "document_id": "doc-ledger",
                "file_name": "system-ledger.xlsx",
                "chunk_id": "ledger-101",
            },
            {
                "field": "重要度",
                "value": "B",
                "record_of": "経費精算ポータル",
                "document_id": "doc-ledger",
                "file_name": "system-ledger.xlsx",
                "chunk_id": "ledger-101",
            },
        ],
        "next_step": RECORD_CODE_HINT,
    }
    # 略称・全角の表記でも同じ行に当たる。数字だけの値・長い値は略号ではない。
    by_alias = record_codes(_LEDGER_EVIDENCE, "経費 Ｐｏｒｔａｌ 担当部署", [])
    assert by_alias is not None
    assert [(item["field"], item["value"]) for item in by_alias["values"]] == [
        ("担当部署", "経"),
        ("重要度", "B"),
    ]


def test_record_codes_skip_values_already_searched_and_unrelated_rows() -> None:
    # この Run で「担当部署 経」を引いた後は、その値を案内しない。
    note = record_codes(_LEDGER_EVIDENCE, "経費精算ポータル 担当部署", ["担当部署 経 正式名"])
    assert note is not None
    assert [item["value"] for item in note["values"]] == ["B"]
    # query の実体に当たる行が無い・記録の無い結果・query の無い呼び出しは案内しない。
    assert record_codes(_LEDGER_EVIDENCE, "予算管理システム 担当部署", []) is None
    assert (
        record_codes({"evidence": _LEDGER_EVIDENCE["evidence"][:1]}, "経費精算ポータル", []) is None
    )
    assert record_codes(_LEDGER_EVIDENCE, "", []) is None
    assert record_codes({}, "経費精算ポータル", []) is None


def test_record_codes_read_multi_row_headers_and_sheet_rows_without_kind() -> None:
    # 複数行の表頭（「 / 」でつないだ列名）と、種類の無い表計算の 1 行の根拠。
    row = {
        "document_id": "doc-ledger",
        "chunk_id": "row-5",
        "excerpt": "システム名: 予算管理システム / 区分 / 重要度: S / 担当部署: 企",
        "locator": {"sheet_name": "台帳", "row_start": 5, "row_end": 5},
    }
    note = record_codes({"evidence": [row]}, "予算管理システム 重要度", [])
    assert note is not None
    assert [(item["field"], item["value"]) for item in note["values"]] == [
        ("区分 / 重要度", "S"),
        ("担当部署", "企"),
    ]
    # 手順書の手順（1 行に 1 つの「列名: 値」）も同じに読む。
    step = _record("手順名: 予算管理システムの再起動\n担当: 企\n所要時間: 30", "step-1")
    steps_note = record_codes({"evidence": [step]}, "予算管理システムの再起動 担当", [])
    assert steps_note is not None
    assert [(item["field"], item["value"]) for item in steps_note["values"]] == [("担当", "企")]
    # 複数の行の範囲（表のかたまり）は 1 行の記録ではない。
    block = {**row, "locator": {"sheet_name": "台帳", "row_start": 5, "row_end": 9}}
    assert record_codes({"evidence": [block]}, "予算管理システム 重要度", []) is None


def test_record_codes_are_bounded() -> None:
    excerpt = "正式名: 経費精算ポータル / " + " / ".join(f"項目{n}: {n}a" for n in range(10))
    note = record_codes({"evidence": [_record(excerpt, "wide")]}, "経費精算ポータル", [])
    assert note is not None
    assert len(note["values"]) == MAX_RECORD_CODES


def test_ledger_row_with_codes_gets_a_next_hop_hint(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    output: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["rag_retrieve_evidence"])
    output["evidence"] = deepcopy(_LEDGER_EVIDENCE["evidence"])
    mcp.outputs["rag_retrieve_evidence"] = output
    model = _script(
        monkeypatch,
        [
            function_call(
                "rag__rag_retrieve_evidence", {"query": "経費精算ポータル 担当部署"}, call_id="c1"
            )
        ],
        [
            function_call(
                "rag__rag_retrieve_evidence", {"query": "担当部署 経 正式名"}, call_id="c2"
            )
        ],
        [assistant_message("担当部署は経理部です。")],
    )
    run = _run()

    first = _tool_output(model, 1, "c1")
    assert [item["value"] for item in first["record_codes"]["values"]] == ["経", "B"]
    assert first["record_codes"]["next_step"] == RECORD_CODE_HINT
    # 略号を引く次の段（「担当部署 経 正式名」）は台帳の行の実体に当たらないので案内しない。
    second = _tool_output(model, 2, "c2")
    assert "record_codes" not in second
    # 記録する step の結果は RAG の結果のまま。
    recorded = [step.tool_result.output for step in run.steps if step.tool_result is not None]
    assert all("record_codes" not in (output or {}) for output in recorded)


def test_fake_rag_evidence_limit_defaults_match_the_contract() -> None:
    """fake の既定の件数は RAG の契約と同じ（rag_retrieve_evidence 20・rag_search 12。#1365）。"""
    contract = json.loads((CONTRACTS / "rag-tools.json").read_text(encoding="utf-8"))
    defaults = {
        tool["name"]: tool["inputSchema"]["properties"]["evidence_limit"]["default"]
        for tool in contract["tools"]
        if tool["name"] in {"rag_search", "rag_retrieve_evidence"}
    }
    assert defaults == {
        "rag_search": RagSearchIn.model_fields["evidence_limit"].default,
        "rag_retrieve_evidence": RagRetrieveEvidenceIn.model_fields["evidence_limit"].default,
    }
    assert defaults == {"rag_search": 12, "rag_retrieve_evidence": 20}
