"""推導の論理の審査と説明の段を分け、説明の失敗が結論・原文・論理の判定を変えないことを 3 つの監査の型で確かめる。"""

import json
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from application_verdict_stub import audit_reply
from audit_fixtures import legacy_audit_to_wire_fixture

from rag_engine.config import get_settings
from rag_engine.generation.application_explanation import KEY_SOURCES, is_explanation_schema
from rag_engine.generation.application_reason import AuditReason
from rag_engine.generation.application_review import review_applications
from rag_engine.generation.applications import APPLICATION_AUDIT_SYSTEM_PROMPT, CheckedApplication, render_lines
from rag_engine.generation.grounded import AUDIT_SYSTEM_PROMPT, FACT_AUDIT_SYSTEM_PROMPT
from rag_engine.models.audit_wire import audit_wire_schema, decode_audit_wire, is_audit_wire
from rag_engine.models.llm import ApplicationAudit, ApplicationVerdictAudit, GroundedApplication, GroundedAudit, GroundedFactAudit

RECORDED = {
    case["name"]: case
    for case in json.loads((Path(__file__).parent / "fixtures" / "application_explanation_recorded.json").read_text(encoding="utf-8"))[
        "cases"
    ]
}
CASE = RECORDED["multiple_rows-r1"]
QUESTION = CASE["question"]
SOURCE = CASE["sources"][0]
RESULT = CASE["conclusion"]
EXPLANATION = CASE["recorded_explanation"]
# 生成が書いた英語の推導。表示にも説明の段の入力にも使わない。
ORIGINAL = "The total is derived from the quantities and unit prices in the source."


def candidate():
    item = SimpleNamespace(kind="rule", applies="matched", text=SOURCE, quote=SOURCE)
    rule = SimpleNamespace(item=item, span={"text": SOURCE, "source": "synthetic.txt"}, quote_only=False, removed=False, state="")
    return CheckedApplication(GroundedApplication(item_indexes=[0], reasoning=ORIGINAL, result=RESULT), [rule])


@pytest.mark.parametrize("base", [ApplicationAudit, GroundedAudit, GroundedFactAudit])
def test_review_schema_and_prompts_hold_logic_only(base):
    node = audit_wire_schema(base, (), application_indexes=(0, 3)).model_json_schema()
    assert "application_presentations" not in node["properties"] and "output_language" not in node["properties"]
    keys = node["$defs"][node["properties"]["application_reviews"]["$ref"].split("/")[-1]]
    assert set(keys["required"]) == {"A0", "A3"}
    for prompt in (APPLICATION_AUDIT_SYSTEM_PROMPT, AUDIT_SYSTEM_PROMPT, FACT_AUDIT_SYSTEM_PROMPT):
        assert "推導説明の表示" not in prompt and "display_reasoning" not in prompt and "output_language" not in prompt


def test_existing_audit_is_reused_without_a_review_call_and_without_a_display():
    schema = audit_wire_schema(GroundedAudit, (), application_indexes=(0,))
    raw = legacy_audit_to_wire_fixture(
        GroundedAudit(
            reviews=[],
            application_reviews=[{"index": 0, "valid": True, "reason": "規則と一致"}],
            summary_supported=True,
            goal_alignment="aligned",
        ),
        schema,
    )
    entry = candidate()

    def unused(*a, **kw):
        pytest.fail("推導の審査を再利用するときは呼び出さない")

    trace = review_applications(
        QUESTION,
        [entry],
        evidence_spans=[{"evidence_id": "E1", "text": SOURCE}],
        existing_audit=decode_audit_wire(json.dumps(raw), schema).audit,
        audit_requested=True,
        parse_text=unused,
        settings=get_settings(environ={}, dotenv_path=None),
        provider_id=None,
    )
    assert trace["calls"] == 0 and entry.assessment["status"] == "valid" and entry.published
    # 説明の段の前は説明を出さない（生成の推導の文へ戻さない）。
    before = deepcopy(entry.application.model_dump())
    rendered = "\n".join(render_lines([entry], lambda _: "根拠：synthetic.txt", lambda _: None))
    assert RESULT in rendered and "当てはめ" not in rendered and ORIGINAL not in rendered
    assert entry.application.model_dump() == before


@pytest.mark.parametrize("audit_mode", ["none", "standard", "detailed"])
@pytest.mark.parametrize("state", ["confirmed", "hidden", "call_error", "logic_invalid", "invalid_language"])
def test_generation_calls_the_explanation_once_after_review_and_never_falls_back(state, audit_mode):
    from citation_unit_support import selected_answer_payload

    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
    from rag_engine.generation.answering import synthesize_grounded_answer

    calls: list[str] = []
    explanation_inputs: list[dict] = []
    valid = state != "logic_invalid"

    def parse(system, prompt, settings, schema, **options):
        if is_explanation_schema(schema):
            calls.append("explanation")
            explanation_inputs.append(json.loads(prompt))
            if state == "call_error":
                raise RuntimeError("synthetic transport failure")
            text = RECORDED["explicit_english-r1"]["recorded_explanation"] if state == "hidden" else EXPLANATION
            return schema.model_validate({"explanation": text})
        if schema in (ApplicationVerdictAudit, AuditReason):
            # 監査なしの設定の独立の監査（#4471）: 判定（A）と判定の理由（B）の 2 段。
            calls.append("audit" if schema is ApplicationVerdictAudit else "reason")
            return audit_reply(schema, [{"index": 0, "valid": valid}], prompt=prompt)
        if is_audit_wire(schema):
            calls.append("audit")
            if schema.canonical_schema is ApplicationAudit:
                raw = {"summary_supported": True, "application_reviews": {"A0": {"valid": valid, "reason": "推導対応"}}}
                return decode_audit_wire(json.dumps(raw), schema)
            source = GroundedAudit(
                reviews=[
                    {
                        "index": 0,
                        "support": "supported",
                        "applicability": "matched",
                        "source_applicability": "matched",
                        "reason": "原文対応",
                    }
                ],
                request_reviews=[{"request_id": key, "status": "addressed", "reason": "要求に回答"} for key in schema.expected_request_ids],
                application_reviews=[{"index": 0, "valid": valid, "reason": "推導対応"}],
                summary_supported=True,
                goal_alignment="aligned",
            )
            return decode_audit_wire(json.dumps(legacy_audit_to_wire_fixture(source, schema)), schema)
        calls.append("generate")
        units = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)
        value = selected_answer_payload(units, text=RESULT)
        value["answer_parts"][0]["answer"]["reasoning"] = ORIGINAL
        value["summary_part_indexes"] = [0]
        if state == "invalid_language":
            value["response_language"] = {"selection": {"kind": "explicit", "quote": "invented language"}}
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
        source="synthetic.txt",
        chunk_level="parent",
        chunk_id="a",
        source_run_id="synthetic",
    )
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode=audit_mode, answer_max_correction_rounds=0)
    with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
        response = synthesize_grounded_answer(
            QUESTION, AnswerContext(records=[row], text=SOURCE), settings, image_prompt_mode="text_only"
        ).response
    called = state in {"confirmed", "hidden", "call_error"}
    # 生成 1 回・推導の審査 1 回（標準・詳細は通常の監査と同じ呼び出し。監査なしは独立の監査の判定と、確定した判定の
    # 理由の段 1 回）の後に、valid な推導 1 件ごとの説明の段 1 回。
    reason = ["reason"] if audit_mode == "none" else []
    assert calls == ["generate", "audit", *reason, *(["explanation"] if called else [])]
    # 回答の記録の呼び出し回数にも、説明の段を含める。
    assert response.generation_trace["llm_calls"] == len(calls)
    trace = response.generation_trace["applications"][0]
    assert trace["reasoning"] == ORIGINAL and trace["result"] == RESULT
    assert ORIGINAL not in response.answer_text
    if called:
        (user,) = explanation_inputs
        assert set(user) == {"質問", KEY_SOURCES} and user["質問"] == QUESTION
        assert ORIGINAL not in json.dumps(user, ensure_ascii=False) and RESULT not in json.dumps(user, ensure_ascii=False)
    if not valid:
        assert not trace["published"] and trace["presentation"] == {"status": "not_requested", "reason": "logic_not_valid"}
        return
    assert trace["published"]
    assert response.answer_text.startswith(RESULT) and SOURCE in response.answer_text
    assert (EXPLANATION in response.answer_text) == (state == "confirmed")
    expected = {"confirmed": "confirmed", "hidden": "hidden", "call_error": "unrecorded", "invalid_language": "hidden"}[state]
    assert trace["presentation"]["status"] == expected
