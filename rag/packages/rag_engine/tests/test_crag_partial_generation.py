"""CRAGの部分支持が生成入力と最終回答まで維持されることを検証する。"""

from dataclasses import replace
from unittest.mock import patch

import pytest
from citation_unit_support import canonical_fixture
from test_answer_generation import _chunk_run_stub
from test_crag_context_recovery import record
from test_grounded_answer import draft, ids

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.models.llm import CragRetrievalGradeOutput
from rag_engine.retrieval.task_contract import task_contract

QUESTION = "設定画面を開いてから、設定を保存する手順は？"
OPEN = "設定画面を開きます。"
OVERVIEW = "このシステムは設定の管理に対応しています。"


def grade(text, *, partial):
    checks = []
    for aspect in task_contract(QUESTION)["required_aspects"]:
        status = "missing" if aspect == "requested_result" else "supported" if aspect == "business_object" else "unknown"
        checks.append(
            dict(
                aspect=aspect,
                status=status,
                source_ids=["run:c0"] if status == "supported" else [],
                evidence=[dict(source_id="run:c0", quote=text)] if status == "supported" else [],
                reason="入口は記載、保存の直接説明は不足" if partial else "同じ管理機能の概述だけ",
            )
        )
    return CragRetrievalGradeOutput(
        candidate_verdicts=[dict(chunk_uid="run:c0", relevant=True, reason="同じ対象")],
        aspect_checks=checks,
        answerable_parts=[dict(aspect="requested_result", kind="operation", claim=OPEN, evidence=[dict(source_id="run:c0", quote=OPEN)])]
        if partial
        else [],
        sufficient=True,
        confidence=0.8,
        reason="観点の充足と部分回答を区別",
        rewritten_query="",
    )


def run_answer(text, grade_output, generation):
    context = a.AnswerContext(records=[record("c0", 0, text)], text=text)
    settings = replace(
        get_settings(environ={}, dotenv_path=None),
        section_expansion_enabled=False,
    )
    calls = []

    def model(system, prompt, settings, schema, **kwargs):
        calls.append((schema.__name__, prompt))
        if schema is CragRetrievalGradeOutput:
            return grade_output
        assert issubclass(schema, generation_draft_wire_model())
        # 既存のquote/lines応答はcanonical再生であり、wire検証ではない。
        return canonical_fixture(generation(context))

    with (
        patch.object(a, "load_latest_or_source_chunk_run", return_value=_chunk_run_stub()),
        patch.object(a, "check_adb_hybrid_search_ready"),
        patch.object(a, "build_adb_hybrid_answer_context", return_value=context),
        patch.object(a, "parse_text_response", side_effect=model),
    ):
        result = a.answer_question_result(
            QUESTION, "abcdef", ["docling"], settings, query_strategy=a.SIMPLE_RETRIEVAL_LABEL, answer_flow=a.CRAG_ANSWER_FLOW_LABEL
        )
    return result, calls


def test_same_business_overview_cannot_reach_operation_generation():
    result, calls = run_answer(OVERVIEW, grade(OVERVIEW, partial=False), lambda context: pytest.fail("概述だけで操作を生成してはいけない"))
    assert all(schema not in {"GroundedDraft", "GroundedDraftWire"} for schema, _ in calls)
    assert result.crag_attempts[-1]["generation_usable"] is False
    assert "直接裏付ける根拠を確認できていません" in result.answer_text
    assert not result.generation_trace["crag_generation_gate"]["generation_calls"]
    # 生成を呼ばずに拒否しても、通常の回答と同じ形で件数を残す（#4735）。
    counts = result.generation_trace["retrieval_counts"]
    assert counts["passed_to_model"] == 0 and counts["found"] >= 1 and counts["candidate_count"] >= counts["found"]


def test_overview_gate_precedes_legacy_generation_dispatch():
    from unittest import TestCase

    from grounded_stub import stub_generation

    owner = TestCase()
    legacy = stub_generation(owner)
    try:
        result, calls = run_answer(OVERVIEW, grade(OVERVIEW, partial=False), lambda context: pytest.fail("概述だけでは生成しない"))
    finally:
        owner.doCleanups()
    legacy.assert_not_called()
    assert [schema for schema, _ in calls] == ["CragRetrievalGradeOutput"]
    assert result.generation_trace["crag_generation_gate"]["generation_calls"] == 0
    assert "直接裏付ける根拠を確認できていません" in result.answer_text


def test_direct_part_removed_from_generator_input_cannot_enable_generation():
    from rag_engine.generation import crag_generation

    context = a.AnswerContext(records=[record("c0", 0, OPEN)], text=OPEN)
    assessment = {
        "aspect_checks": [{"aspect": "procedure", "status": "supported", "evidence": [{"source_id": "run:c0", "quote": OPEN}]}],
        "answerable_parts": [{"aspect": "procedure", "status": "supported", "evidence": [{"source_id": "run:c0", "quote": OPEN}]}],
    }
    view = crag_generation.generation_view(assessment, [], context)
    assert view["generation_usable"] is False
    assert view["aspect_checks"][0]["status"] == "unknown"
    assert view["aspect_checks"][0]["retained_reason"] == "direct_evidence_not_in_generation_context"


def test_new_material_without_reassessment_preserves_gap_as_unknown():
    from rag_engine.generation import crag_generation
    from rag_engine.generation.crag_support import _crag_context_fingerprint

    old = a.AnswerContext(records=[record("c0", 0, OPEN)], text=OPEN)
    context = a.AnswerContext(records=[record("c0", 0, OPEN + "設定を保存します。")], text=OPEN + "設定を保存します。")
    assessment = {
        "context_fingerprint": _crag_context_fingerprint(old),
        "aspect_checks": [{"aspect": "procedure", "status": "missing", "evidence": []}],
        "answerable_parts": [],
    }
    view = crag_generation.generation_view(assessment, [], context)
    assert view["required_aspect_coverage"] == "unknown"
    assert view["aspect_checks"][0]["status"] == "unknown"
    assert view["aspect_checks"][0]["retained_reason"] == "new_context_without_aspect_reassessment"
    assert "未確認" in crag_generation.gap_lines(QUESTION, view)[0]


def grade_with_part_quote(text, quote):
    """評価が回答できる部分を返すが、その写し（quote）が送った本文と一致しない応答（#3837）。"""
    output = grade(text, partial=True)
    part = output.answerable_parts[0].model_copy(
        update={"evidence": [output.answerable_parts[0].evidence[0].model_copy(update={"quote": quote})]}
    )
    return output.model_copy(update={"answerable_parts": [part]})


def test_unverifiable_part_quote_proceeds_to_one_generation_and_keeps_the_answer():
    # 写しの照合の失敗は判定不能。不足と扱って生成を止めず、取得済みの根拠で 1 回だけ生成する（#3837）。
    def generation(context):
        eid = ids(context, QUESTION)[OPEN]
        return draft(
            {"kind": "operation", "request_id": "Q1", "text": "設定画面を開きます。", "citations": [{"evidence_id": eid, "quote": OPEN}]},
            summary="設定画面を開いて操作します。",
        )

    result, calls = run_answer(OPEN, grade_with_part_quote(OPEN, "設定の画面を表示する。"), generation)
    assert [schema for schema, _ in calls] == ["CragRetrievalGradeOutput", "GroundedDraftWire"]
    assert "設定画面を開きます。" in result.answer_text
    assert "直接裏付ける根拠を確認できていません" not in result.answer_text
    trace = result.generation_trace
    assert trace["crag_generation_gate"] == {
        "generation_usable": False,
        "reason": "answerable_part_unverified",
        "unverified_binding_reasons": {"quote_not_in_generation_source": 1},
    }
    assert trace["crag_assessment"]["answerable_parts"][0]["status"] == "unknown"
    # 確かめた部分が無いので部分回答の範囲へ切り詰めない。未確認・不足の観点は印として残す。
    assert trace["crag_partial_projection"] == {"applied": False, "reason": "answerable_part_unverified"}
    assert "求める結果については、検索評価で直接の根拠を確認できていません。" in result.answer_text
    assert result.needs_human_review


def test_part_quote_outside_generation_source_is_unknown_not_refusal():
    from rag_engine.generation import crag_generation

    context = a.AnswerContext(records=[record("c0", 0, OPEN)], text=OPEN)
    assessment = {
        "grade_execution": "completed",
        "aspect_checks": [{"aspect": "procedure", "status": "supported", "evidence": [{"source_id": "run:c0", "quote": "別の原文"}]}],
        "answerable_parts": [
            {"aspect": "procedure", "status": "supported", "evidence": [{"source_id": "run:c0", "quote": OPEN}]},
            {"aspect": "procedure", "status": "supported", "evidence": [{"source_id": "run:c0", "quote": "別の原文"}]},
            {"aspect": "procedure", "status": "supported", "evidence": [{"source_id": "run:other", "quote": OPEN}]},
        ],
    }
    spans = [{"evidence_id": "E1", "label": "E1", "source_id": "run:c0", "text": "前の原文"}]
    view = crag_generation.generation_view(assessment, spans, context)
    assert [part["status"] for part in view["answerable_parts"]] == ["unknown", "unknown", "unknown"]
    assert view["generation_decision"] == crag_generation.GATE_UNVERIFIED_PART
    assert view["unverified_binding_reasons"] == {"quote_not_in_generation_source": 2, "source_not_in_generation_context": 1}
    assert crag_generation.refusal_response(QUESTION, view) is None
    # 診断だけの欄は生成の指示へ渡さない。
    prompt = crag_generation.prompt_context(view)
    assert "generation_decision" not in prompt and "unverified_binding_reasons" not in prompt


@pytest.mark.parametrize(
    "grade_execution,changed,decision",
    [
        ("completed", False, "no_answerable_part"),
        ("error", False, "grading_incomplete"),
        ("partial", False, "grading_incomplete"),
        ("not_run", False, "grading_incomplete"),
        ("completed", True, "context_changed_without_reassessment"),
    ],
    ids=["completed", "error", "partial", "skipped", "context_changed"],
)
def test_only_completed_grading_of_the_same_context_without_parts_refuses(grade_execution, changed, decision):
    from rag_engine.generation import crag_generation
    from rag_engine.generation.crag_support import _crag_context_fingerprint

    graded = a.AnswerContext(records=[record("c0", 0, OVERVIEW)], text=OVERVIEW)
    context = a.AnswerContext(records=[record("c0", 0, OVERVIEW + OPEN)], text=OVERVIEW + OPEN) if changed else graded
    assessment = {
        "grade_execution": grade_execution,
        "context_fingerprint": _crag_context_fingerprint(graded),
        "aspect_checks": [{"aspect": "procedure", "status": "missing", "evidence": []}],
        "answerable_parts": [],
    }
    view = crag_generation.generation_view(assessment, [], context)
    assert view["generation_decision"] == decision
    refusal = crag_generation.refusal_response(QUESTION, view)
    if decision == "no_answerable_part":
        assert refusal.generation_trace["crag_generation_gate"] == {
            "generation_usable": False,
            "reason": "no_answerable_part",
            "unverified_binding_reasons": {},
            "generation_calls": 0,
        }
    else:
        assert refusal is None
