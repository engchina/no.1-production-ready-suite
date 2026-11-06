"""推導の公開門は原文照合と別に検査し、未完了を成功へ変えない。"""

import json
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest
from application_verdict_stub import APPLICATION_SCHEMAS, SYNTHETIC_REASON, audit_reply
from audit_fixtures import synthetic_explanation
from citation_unit_support import selected_answer_payload

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.application_explanation import is_explanation_schema
from rag_engine.generation.application_reason import AuditReason
from rag_engine.generation.application_review import review_applications
from rag_engine.generation.applications import CheckedApplication
from rag_engine.models.llm import ApplicationVerdictAudit, GroundedApplication, GroundedAudit

SOURCE = "操作Aは2営業日前の17時までなら0%、その期限後は50%です。"
QUESTION = "操作Aを前営業日に行った場合の割合を教えてください。"


def candidate():
    item = SimpleNamespace(kind="rule", applies="matched", text=SOURCE, quote=SOURCE)
    entry = SimpleNamespace(item=item, span={"text": SOURCE, "source": "synthetic-a.txt"}, quote_only=False, removed=False, state="")
    return CheckedApplication(
        GroundedApplication(request_id="Q1", item_indexes=[0], reasoning="前営業日は期限後に当たる。", result="50%"), [entry]
    )


def review(checked, parse, *, existing=None, requested=False, settings=None):
    return review_applications(
        QUESTION,
        checked,
        evidence_spans=[entry.span for application in checked for entry in application.entries],
        existing_audit=existing,
        audit_requested=requested,
        parse_text=parse,
        settings=settings or get_settings(environ={}, dotenv_path=None),
        provider_id=None,
    )


def test_many_applications_are_judged_in_one_call_and_explained_per_application():
    calls = []

    def parse(system, prompt, settings, schema, **options):
        if schema is AuditReason:
            return audit_reply(schema)
        data = json.loads(prompt)
        calls.append(data)
        assert data["items"][0]["quote"] == SOURCE
        return audit_reply(schema, [{"index": i, "valid": True} for i in range(2)], prompt=prompt)

    checked = [candidate(), candidate()]
    trace = review(checked, parse)
    # 判定は 1 回にまとめ、理由は判定が確定した推導ごとに 1 回。
    assert len(calls) == 1 and trace["calls"] == 3
    assert all(entry.published for entry in checked)
    assert all(entry.assessment == {"status": "valid", "reason": SYNTHETIC_REASON} for entry in checked)


@pytest.mark.parametrize("outcome", ["wrong", "missing", "duplicate", "foreign", "timeout"])
def test_bad_or_incomplete_judgements_cannot_publish(outcome):
    def parse(*args, **kwargs):
        if outcome == "timeout":
            raise TimeoutError("synthetic")
        if args[3] is AuditReason:
            return audit_reply(AuditReason)
        row = {"index": 0, "valid": outcome != "wrong"}
        rows = (
            []
            if outcome == "missing"
            else [row, row]
            if outcome == "duplicate"
            else [{**row, "index": 99}]
            if outcome == "foreign"
            else [row]
        )
        return audit_reply(ApplicationVerdictAudit, rows, prompt=args[1])

    entry = candidate()
    trace = review([entry], parse)
    # 判定が確定したときだけ理由の段を送る（欠落・重複・範囲外・失敗では送らない）。
    assert trace["calls"] == (2 if outcome == "wrong" else 1)
    assert not entry.published
    assert entry.assessment["status"] == ("invalid" if outcome == "wrong" else "unknown")


def test_budget_excess_is_unknown_without_sending_a_partial_input():
    entry = candidate()
    config = replace(get_settings(environ={}, dotenv_path=None), answer_max_input_tokens=1)
    trace = review([entry], lambda *a, **kw: pytest.fail("予算超過では送信しない"), settings=config)
    assert trace["calls"] == 0
    assert trace["error_type"] == "GenerationInputBudgetExceeded"
    assert entry.assessment["status"] == "unknown" and not entry.published


@pytest.mark.parametrize(
    "existing",
    [
        None,
        GroundedAudit(
            reviews=[],
            summary_supported=True,
            goal_alignment="aligned",
            application_reviews=[{"index": 0, "valid": True, "reason": "対応した期間"}],
        ),
    ],
)
def test_existing_audit_is_reused_without_another_call(existing):
    entry = candidate()
    trace = review([entry], lambda *a, **kw: pytest.fail("既存監査を再利用"), existing=existing, requested=True)
    assert trace["calls"] == 0
    assert entry.published == (existing is not None)


def test_no_derived_conclusion_adds_no_model_call():
    trace = review([], lambda *a, **kw: pytest.fail("推導なし"))
    assert trace == {"status": "not_applicable", "calls": 0}


def test_gate_receives_only_confirmed_context_and_preserves_its_origin():
    def parse(system, prompt, settings, schema, **options):
        if schema is AuditReason:
            return audit_reply(schema)
        data = json.loads(prompt)
        assert data["confirmed_premises"] == [{"question": "区分", "answer": "A", "source": "user"}]
        return audit_reply(schema, [{"index": 0, "valid": True}], prompt=prompt)

    config = replace(
        get_settings(environ={}, dotenv_path=None),
        confirmed_premises=(("区分", "A"), ("未確認", "B")),
        confirmed_premise_sources=(("区分", "A", "user"), ("未確認", "B", "assumption")),
    )
    entry = candidate()
    assert review([entry], parse, settings=config)["status"] == "completed"
    assert entry.published


def test_missing_existing_review_is_unknown_in_diagnostic_and_withheld():
    entry = candidate()
    existing = GroundedAudit(reviews=[], summary_supported=True, goal_alignment="aligned", application_reviews=[])
    trace = review([entry], lambda *a, **kw: pytest.fail("既存監査は再試行しない"), existing=existing, requested=True)
    assert trace["status"] == "incomplete"
    assert trace["calls"] == 0
    assert trace["results"][0]["status"] == "unknown"
    assert not entry.published


def test_existing_audit_with_foreign_application_is_not_adopted():
    entry = candidate()
    existing = GroundedAudit(
        reviews=[],
        summary_supported=True,
        goal_alignment="aligned",
        application_reviews=[{"index": index, "valid": True, "reason": "合成判定"} for index in (0, 99)],
    )
    trace = review([entry], lambda *a, **kw: pytest.fail("不正な既存監査で追加呼出しない"), existing=existing, requested=True)
    assert trace["status"] == "invalid_contract"
    assert trace["calls"] == 0
    assert entry.assessment["status"] == "unknown"
    assert not entry.published


def test_authorization_failure_is_not_converted_to_an_answer():
    def denied(*a, **kw):
        raise PermissionError("synthetic")

    with pytest.raises(PermissionError):
        review([candidate()], denied)


@pytest.mark.parametrize("valid", [True, False, None], ids=["correct", "wrong-period", "missing-review"])
def test_actual_generation_boundary_keeps_source_and_synchronizes_body_summary(valid):
    calls = []

    def parse(system, prompt, settings, schema, **options):
        calls.append(schema)
        if is_explanation_schema(schema):
            return synthetic_explanation(schema, prompt)
        if schema in APPLICATION_SCHEMAS:
            return audit_reply(schema, [] if valid is None else [{"index": 0, "valid": valid}], prompt=prompt)
        unit = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)[0]
        value = selected_answer_payload([unit], text="割合は50%です。" if valid else "割合は0%です。")
        value["answer_parts"][0]["requirement"] = "操作Aの割合"
        value["answer_parts"][0]["answer"]["reasoning"] = "前営業日は期限後です。" if valid else "前営業日の17時までなら0%です。"
        value["summary_part_indexes"] = [0]
        return schema.model_validate(value)

    def unused(*a, **kw):
        pytest.fail("外部I/O不要")

    row = AnswerRecord(
        id="a",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=SOURCE,
        source="synthetic-a.txt",
        chunk_level="parent",
        chunk_id="a",
        source_run_id="synthetic",
    )
    config = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0)
    with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
        result = answering.synthesize_grounded_answer(
            QUESTION, AnswerContext(records=[row], text=SOURCE), config, image_prompt_mode="text_only"
        ).response
    assert calls.count(ApplicationVerdictAudit) == 1
    # 判定が確定したときだけ理由の段を 1 回足す。説明の段（#4393）は論理が valid な推導だけに 1 回。
    assert calls.count(AuditReason) == (0 if valid is None else 1)
    assert sum(is_explanation_schema(schema) for schema in calls) == (1 if valid else 0)
    assert result.generation_trace["llm_calls"] == (2 if valid is None else 3)
    assert SOURCE in result.answer_text
    assert result.generation_trace["applications"][0]["published"] == bool(valid)
    if valid:
        assert result.answer_text.startswith("割合は50%です。")
    else:
        assert "割合は0%です。" not in result.answer_text
        assert "前営業日の17時までなら0%です。" not in result.answer_text
        assert "操作Aの割合" in result.answer_text
