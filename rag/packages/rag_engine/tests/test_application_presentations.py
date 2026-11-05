"""推導の論理と表示を別々に束縛し、元の説明・結果・原文を改変しない。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from audit_fixtures import legacy_audit_to_wire_fixture

from rag_engine.config import get_settings
from rag_engine.generation.application_review import review_applications
from rag_engine.generation.applications import CheckedApplication, apply_application_audit, render_lines
from rag_engine.models.audit_wire import audit_wire_schema, decode_audit_wire
from rag_engine.models.llm import ApplicationAudit, GroundedApplication, GroundedAudit, GroundedFactAudit

ORIGINAL = "The total is 3 * 800 + 2 * 500 = 3400."
DISPLAY = "単価800円を3個分、単価500円を2個分計算し、合計は3400円です。"
SOURCE = "項目Aは800円、項目Bは500円です。SQL: SELECT COUNT(*) FROM SYNTHETIC_ITEMS;"


def candidate():
    item = SimpleNamespace(kind="rule", applies="matched", text=SOURCE, quote=SOURCE)
    rule = SimpleNamespace(item=item, span={"text": SOURCE, "source": "synthetic.txt"}, quote_only=False, removed=False, state="")
    return CheckedApplication(GroundedApplication(item_indexes=[0], reasoning=ORIGINAL, result="3400円"), [rule])


def payload(base=ApplicationAudit, *, indexes=(0,)):
    schema = audit_wire_schema(base, (), application_indexes=indexes)
    value = (
        {"summary_supported": True}
        if base is ApplicationAudit
        else legacy_audit_to_wire_fixture(GroundedAudit(reviews=[], summary_supported=True, goal_alignment="aligned"), schema)
    )
    value["output_language"] = "日本語"
    value["application_reviews"] = {f"A{i}": {"valid": True, "reason": "規則と一致"} for i in indexes}
    value["application_presentations"] = {f"A{i}": {"display_reasoning": DISPLAY, "equivalent": True} for i in indexes}
    return schema, value


@pytest.mark.parametrize("base", [ApplicationAudit, GroundedAudit, GroundedFactAudit])
def test_all_modes_bind_logic_and_display_to_same_fixed_application_ids(base):
    schema, raw = payload(base, indexes=(0, 3))
    result = decode_audit_wire(json.dumps(raw), schema)
    assert [r.index for r in result.audit.application_reviews] == [0, 3]
    assert [r.index for r in result.audit.application_presentations] == [0, 3]
    assert result.validation["strict_wire_validation"] == "passed"
    node = schema.model_json_schema()
    for field in ["application_reviews", "application_presentations"]:
        keys = node["$defs"][node["properties"][field]["$ref"].split("/")[-1]]
        assert set(keys["required"]) == {"A0", "A3"}
        child = node["$defs"][keys["properties"]["A0"]["$ref"].split("/")[-1]]
        assert "index" not in child["properties"]
        assert not {"result", "quote", "premises"} & set(child["properties"])


@pytest.mark.parametrize("failure", ["missing", "foreign", "null", "empty", "invalid_type", "whitespace", "extra_result", "not_equivalent"])
def test_bad_display_keeps_valid_result_and_quote_without_original_fallback(failure):
    schema, raw = payload()
    if failure == "missing":
        raw["application_presentations"].pop("A0")
    elif failure == "foreign":
        raw["application_presentations"]["A9"] = raw["application_presentations"].pop("A0")
    elif failure == "null":
        raw["application_presentations"]["A0"] = None
    elif failure in {"empty", "invalid_type"}:
        raw["application_presentations"]["A0"]["display_reasoning"] = "" if failure == "empty" else 1
    elif failure == "whitespace":
        raw["application_presentations"]["A0"]["display_reasoning"] = "   "
    elif failure == "extra_result":
        raw["application_presentations"]["A0"]["result"] = "9999円"
    else:
        raw["application_presentations"]["A0"]["equivalent"] = False
    checked = candidate()
    before = deepcopy(checked.application.model_dump()), deepcopy(checked.entries[0].span)
    apply_application_audit([checked], decode_audit_wire(json.dumps(raw), schema).audit, require_complete=True)
    assert checked.published and checked.assessment["status"] == "valid"
    assert checked.application.model_dump() == before[0] and checked.entries[0].span == before[1]
    rendered = "\n".join(render_lines([checked], lambda _: "原文", lambda _: None))
    assert "3400円" in rendered and "当てはめ" not in rendered and ORIGINAL not in rendered
    assert checked.presentation["status"] in {"unrecorded", "unconfirmed"}


def test_duplicate_display_key_does_not_erase_valid_logic():
    schema, raw = payload(indexes=(0, 1))
    text = json.dumps(raw).replace('"A0": {"display_reasoning"', '"A0": {}, "A0": {"display_reasoning"')
    audit = decode_audit_wire(text, schema).audit
    assert [r.index for r in audit.application_reviews] == [0, 1]
    assert [r.index for r in audit.application_presentations] == [1]


def test_bad_logic_cannot_be_made_valid_by_a_good_display():
    schema, raw = payload()
    raw["application_reviews"]["A0"]["valid"] = False
    checked = candidate()
    apply_application_audit([checked], decode_audit_wire(json.dumps(raw), schema).audit, require_complete=True)
    assert not checked.published and checked.assessment["status"] == "invalid"
    assert render_lines([checked], lambda _: "原文", lambda _: None) == []


def test_display_does_not_mutate_original_reasoning_result_or_sql_quote():
    schema, raw = payload()
    checked = candidate()
    before = deepcopy(checked.application.model_dump()), deepcopy(checked.entries[0].span)
    apply_application_audit([checked], decode_audit_wire(json.dumps(raw), schema).audit, require_complete=True)
    rendered = "\n".join(render_lines([checked], lambda _: SOURCE, lambda _: None))
    assert DISPLAY in rendered and ORIGINAL not in rendered and SOURCE in rendered
    assert checked.application.model_dump() == before[0] and checked.entries[0].span == before[1]
    assert checked.to_payload()["reasoning"] == ORIGINAL
    assert checked.to_payload()["presentation"]["reasoning"] == DISPLAY


def test_other_language_and_identifiers_are_not_rewritten_by_code():
    schema, raw = payload()
    text = "SyntheticFeature uses SELECT 'a  b' FROM SYNTHETIC_ITEMS; to count items."
    raw["output_language"] = "English"
    raw["application_presentations"]["A0"].update(display_reasoning=text)
    checked = candidate()
    apply_application_audit([checked], decode_audit_wire(json.dumps(raw), schema).audit, require_complete=True)
    assert text in "\n".join(render_lines([checked], lambda _: "原文", lambda _: None))
    assert checked.presentation["language"] == "English"


def test_existing_audit_reuses_display_without_model_call():
    schema, raw = payload(GroundedAudit)
    checked = candidate()

    def unused(*a, **kw):
        pytest.fail("言語表示のために呼び出さない")

    trace = review_applications(
        "合計を日本語で説明してください。",
        [checked],
        existing_audit=decode_audit_wire(json.dumps(raw), schema).audit,
        audit_requested=True,
        parse_text=unused,
        settings=get_settings(environ={}, dotenv_path=None),
        provider_id=None,
    )
    assert trace["calls"] == 0 and checked.presentation["reasoning"] == DISPLAY


@pytest.mark.parametrize("display_state", ["confirmed", "missing", "not_equivalent"])
def test_generation_keeps_valid_result_and_source_while_english_never_falls_back(display_state):
    import re
    from dataclasses import replace

    from citation_unit_support import selected_answer_payload

    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
    from rag_engine.generation.answering import synthesize_grounded_answer
    from rag_engine.models.audit_wire import is_audit_wire

    calls = []

    def parse(system, prompt, settings, schema, **options):
        calls.append(schema)
        if is_audit_wire(schema):
            _, raw = payload()
            if display_state == "missing":
                raw["application_presentations"] = {}
            elif display_state == "not_equivalent":
                raw["application_presentations"]["A0"]["equivalent"] = False
            return decode_audit_wire(json.dumps(raw), schema)
        units = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)
        value = selected_answer_payload(units, text="3400円")
        value["answer_parts"][0]["answer"]["reasoning"] = ORIGINAL
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
        source="synthetic.txt",
        chunk_level="parent",
        chunk_id="a",
        source_run_id="synthetic",
    )
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0)
    with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
        response = synthesize_grounded_answer(
            "項目Aを3個、項目Bを2個利用する合計額を教えてください。",
            AnswerContext(records=[row], text=SOURCE),
            settings,
            image_prompt_mode="text_only",
        ).response
    assert len(calls) == 2
    assert response.answer_text.startswith("3400円") and SOURCE in response.answer_text
    assert ORIGINAL not in response.answer_text
    assert (DISPLAY in response.answer_text) == (display_state == "confirmed")
    trace = response.generation_trace["applications"][0]
    assert trace["published"] and trace["reasoning"] == ORIGINAL and trace["result"] == "3400円"


@pytest.mark.parametrize("language", [None, "", "   ", 1])
def test_missing_or_invalid_display_language_does_not_revoke_valid_logic(language):
    schema, raw = payload()
    raw["output_language"] = language
    checked = candidate()
    apply_application_audit([checked], decode_audit_wire(json.dumps(raw), schema).audit, require_complete=True)
    assert checked.published and checked.assessment["status"] == "valid"
    rendered = "\n".join(render_lines([checked], lambda _: SOURCE, lambda _: None))
    assert "3400円" in rendered and ORIGINAL not in rendered and DISPLAY not in rendered
