"""要点と結果のinline契約に、モデルによる重複分類・内部番号を要求しない。"""

import pytest
from pydantic import ValidationError
from test_citation_unit_ids import snapshot

from rag_engine.generation.answer_parts_wire import answer_parts_model
from rag_engine.generation.citation_unit_ids import SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.models.llm import ApplicationAudit


def case():
    original = snapshot()
    catalog = SentUnitCatalog([original])
    schema = answer_parts_model(generation_draft_wire_model(), catalog)
    raw = {
        "answer_parts": [
            {
                "request_id": "Q1",
                "requirement": "元の値",
                "answer": {
                    "kind": "answer",
                    "basis": {"unit_ids": [original.unit_ids[0]]},
                    "reasoning": "原文の記載を示す。",
                    "result": "単位A",
                },
            },
            {
                "request_id": "Q1",
                "requirement": "導出結果",
                "answer": {
                    "kind": "answer",
                    "basis": {"unit_ids": [original.unit_ids[0]]},
                    "result": "導いた結果",
                    "reasoning": "資料の値から導く。",
                },
            },
        ],
        "summary_part_indexes": [1],
        "confidence": "high",
    }
    return raw, schema, catalog


def test_inline_results_generate_all_internal_references():
    raw, schema, catalog = case()
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert [item.text for item in draft.items] == ["単位A"]
    assert draft.applications[1].result == "導いた結果"
    assert draft.applications[0].item_indexes == [0]
    assert [part.id for part in draft.answer_parts] == ["P1", "P2"]
    assert draft.answer_parts[0].application_indexes == [0]
    assert draft.answer_parts[1].application_indexes == [1]
    assert draft.summary == "導いた結果"
    assert draft.summary_application_indexes == [1]
    assert draft.items[0].citations[0]._unit_binding.text == "単位A"


def test_same_source_for_distinct_requests_keeps_each_request_reference():
    raw, schema, catalog = case()
    raw["answer_parts"][1]["request_id"] = "Q2"
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert [item.request_id for item in draft.items] == ["Q1", "Q2"]
    assert draft.applications[1].item_indexes == [1]


@pytest.mark.parametrize("change", ["missing_result", "missing_basis", "invented_source", "direct_text"])
def test_invalid_declared_derived_or_free_direct_fact_cannot_parse(change):
    raw, schema, _ = case()
    answer = raw["answer_parts"][1]["answer"]
    if change == "missing_result":
        answer.pop("result")
    elif change == "missing_basis":
        answer.pop("basis")
    elif change == "invented_source":
        answer["basis"]["unit_ids"] = ["not-sent"]
    else:
        raw["answer_parts"][0]["answer"]["text"] = "新しい結論"
    with pytest.raises(ValidationError):
        schema.model_validate(raw)


def test_gap_and_summary_have_no_fabricated_source():
    raw, schema, catalog = case()
    raw["answer_parts"] = [{"request_id": "Q1", "requirement": "不足の値", "answer": {"kind": "gap", "text": "値が必要です。"}}]
    raw["summary_part_indexes"] = [0]
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.items[0].kind == "gap"
    assert not draft.items[0].citations
    assert not draft.applications
    assert not draft.summary


def test_no_sent_source_allows_only_a_gap_at_the_generation_boundary():
    catalog = SentUnitCatalog(())
    schema = answer_parts_model(generation_draft_wire_model(), catalog)
    raw = {
        "answer_parts": [{"request_id": "Q1", "requirement": "不足の値", "answer": {"kind": "gap", "text": "資料が必要です。"}}],
        "confidence": "low",
    }
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.items[0].kind == "gap"
    assert not draft.applications
    assert "SupportedAnswerPart" not in schema.model_json_schema().get("$defs", {})
    raw["answer_parts"][0]["answer"] = {"kind": "answer", "basis": {"unit_ids": []}, "reasoning": "根拠がない。", "result": "推測値"}
    with pytest.raises(ValidationError):
        schema.model_validate(raw)


def test_question_copy_is_not_generated_again_as_a_premise():
    raw, schema, catalog = case()
    raw["answer_parts"][1]["answer"]["question_premises"] = ["合成の質問"]
    with pytest.raises(ValidationError):
        schema.model_validate(raw)


def test_source_method_and_result_precede_global_coverage_without_dropping_guards():
    raw, schema, catalog = case()
    document = schema.model_json_schema()
    fields = list(document["properties"])
    assert fields[0] == "answer_parts"
    assert fields[-1] == "request_coverage"
    supported = document["$defs"]["SupportedAnswerPart"]
    assert list(supported["properties"]) == ["kind", "basis", "reasoning", "result"]
    assert set(supported["required"]) == {"kind", "basis", "reasoning", "result"}
    assert document["$defs"]["CitationUnitSelection"]["properties"]["unit_ids"]["minItems"] == 1
    raw["request_coverage"] = [
        {
            "request_id": "Q1",
            "evidence_scope": [
                {
                    "evidence_id": "E1",
                    "target": "項目A",
                    "relation": "unrelated",
                    "link": None,
                }
            ],
        }
    ]
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.request_coverage[0].evidence_scope[0].relation == "unrelated"


def test_forty_answer_parts_preserve_every_result_and_last_summary_dependency():
    from copy import deepcopy

    raw, schema, catalog = case()
    template = raw["answer_parts"][0]
    raw["answer_parts"] = []
    for index in range(40):
        part = deepcopy(template)
        part["requirement"] = f"要点{index + 1}"
        part["answer"]["result"] = f"結論{index + 1}"
        raw["answer_parts"].append(part)
    raw["summary_part_indexes"] = [39]
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert len(draft.applications) == 40
    assert len(draft.items) == 1
    assert draft.answer_parts[-1].application_indexes == [39]
    assert draft.summary_application_indexes == [39]
    assert draft.summary == "結論40"


def test_bound_source_locators_use_structured_citation_without_discarding_conclusion():
    raw, schema, catalog = case()
    evidence_id = catalog.snapshots[0].span()["evidence_id"]
    raw["answer_parts"][1]["answer"]["reasoning"] = f"根拠 [{evidence_id}] に記載された値から導く。"
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.applications[1].reasoning == "根拠 に記載された値から導く。"
    assert draft.applications[1].item_indexes == [0]
    assert draft.applications[1].result == "導いた結果"


@pytest.mark.parametrize("reasoning", ["根拠 [E999] から導く。", "E1の規則から導く。", "根拠 [E1は条件の対象] から導く。"])
def test_unbound_or_sentence_locators_remain_for_existing_rejection(reasoning):
    raw, schema, catalog = case()
    raw["answer_parts"][1]["answer"]["reasoning"] = reasoning
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.applications[1].reasoning == reasoning


def test_selected_long_original_is_preserved_instead_of_rejected_or_truncated():
    from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot

    original = snapshot().span()
    text = "対象の条件と例外を省略しない原文です。" * 80
    original["text"] = text
    source = EvidenceUnitSnapshot.create(original, [(0, len(text))], "synthetic-long-source")
    catalog = SentUnitCatalog([source])
    schema = answer_parts_model(generation_draft_wire_model(), catalog)
    raw, _, _ = case()
    raw["answer_parts"] = raw["answer_parts"][:1]
    raw["answer_parts"][0]["answer"]["basis"]["unit_ids"] = list(source.unit_ids)
    raw["summary_part_indexes"] = [0]
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.items[0].text == text
    assert draft.items[0].citations[0]._unit_binding.text == text
    assert draft.applications[0].item_indexes == [0]
    assert len(draft.items[0].text) > 600


def test_generated_answer_still_has_a_length_limit_separate_from_source_length():
    raw, schema, _ = case()
    raw["answer_parts"][0]["answer"]["result"] = "あ" * 601
    with pytest.raises(ValidationError):
        schema.model_validate(raw)


def test_formal_generation_entry_uses_answer_parts_and_keeps_preplanned_scope_hidden():
    from rag_engine.generation import grounded
    from rag_engine.generation.citation_wire import planned_generation_wire_model

    _, _, catalog = case()
    schema = grounded.generation_output_model("対象の値は何ですか。", unit_catalog=catalog)
    assert schema._uses_typed_answer_parts
    assert "items" not in schema.model_json_schema()["properties"]
    assert "request_coverage" in schema.model_json_schema()["properties"]
    planned = answer_parts_model(planned_generation_wire_model(), catalog)
    assert "request_coverage" not in planned.model_json_schema()["properties"]


@pytest.mark.parametrize("source_text", ["項目Aは12です。", "条件と例外を含む原文です。" * 80], ids=["short-source", "long-source"])
def test_formal_pipeline_builds_sources_and_conclusion_without_extra_generation(source_text):
    import re
    from dataclasses import replace

    from rag_engine.config import get_settings
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answering import AnswerContext, AnswerRecord, synthesize_grounded_answer

    calls = []

    def generate(system, prompt, config, schema, **options):
        calls.append(schema)
        if schema is ApplicationAudit:
            return schema(application_reviews=[{"index": 0, "valid": True, "reason": "合成の対応"}], summary_supported=True)
        assert schema._uses_typed_answer_parts
        assert "summary_part_indexes" in schema.model_json_schema()["properties"]
        unit_ids = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)
        assert unit_ids
        return schema.model_validate(
            {
                "request_coverage": [
                    {
                        "request_id": "Q1",
                        "evidence_scope": [
                            {
                                "evidence_id": "E1",
                                "target": "項目A",
                                "relation": "direct",
                                "link": None,
                            }
                        ],
                    }
                ],
                "answer_parts": [
                    {
                        "request_id": "Q1",
                        "requirement": "値を答える",
                        "answer": {
                            "kind": "answer",
                            "basis": {"unit_ids": unit_ids},
                            "reasoning": "選択した原文の記載を示す。",
                            "result": "合成の回答です。",
                        },
                    }
                ],
                "summary_part_indexes": [0],
                "confidence": "medium",
            }
        )

    def unused(*args, **kwargs):
        raise AssertionError("追加の外部呼出は不可")

    record = AnswerRecord(
        id="source-1",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=source_text,
        source="synthetic.txt",
        chunk_level="parent",
        chunk_id="source-1",
        source_run_id="synthetic",
        metadata={"section_path": ["合成資料"]},
    )
    config = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0)
    with bind_dependencies(AnswerDependencies(unused, unused, generate, unused, unused)):
        response = synthesize_grounded_answer(
            "項目Aの値を教えてください。", AnswerContext(records=[record], text=source_text), config, image_prompt_mode="text_only"
        ).response
    assert len(calls) == 2 and calls[-1] is ApplicationAudit
    assert "合成の回答です。" in response.answer_text
    assert source_text in response.answer_text
    trace = response.generation_trace
    assert trace["applications"][0]["published"]
    assert trace["rounds"][0]["timing"]["answer_parts"]["status"] == "linked"


def test_locator_uses_display_label_while_source_keeps_immutable_evidence_identity():
    raw, _, _ = case()
    source = snapshot(evidence_id="immutable-evidence-identity", label="E1")
    catalog = SentUnitCatalog([source])
    schema = answer_parts_model(generation_draft_wire_model(), catalog)
    for part in raw["answer_parts"]:
        part["answer"]["basis"]["unit_ids"] = [source.unit_ids[0]]
    raw["answer_parts"][1]["answer"]["reasoning"] = "根拠[E1]に記載された値から導く。"
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.applications[1].reasoning == "根拠に記載された値から導く。"
    assert draft.items[0].citations[0].evidence_id == "immutable-evidence-identity"


@pytest.mark.parametrize("request_count,source_count", [(1, 7), (40, 2)])
def test_selected_facts_are_not_limited_by_the_number_of_generated_parts(request_count, source_count):
    from copy import deepcopy

    sources = [snapshot(evidence_id=f"source-{index}", label=f"E{index + 1}", source_id=f"source-{index}") for index in range(source_count)]
    catalog = SentUnitCatalog(sources)
    schema = answer_parts_model(generation_draft_wire_model(), catalog)
    raw, _, _ = case()
    part = raw["answer_parts"][0]
    part["answer"]["basis"]["unit_ids"] = [source.unit_ids[0] for source in sources]
    raw["answer_parts"] = []
    for index in range(request_count):
        current = deepcopy(part)
        current["request_id"] = f"Q{index + 1}"
        current["requirement"] = f"合成の要点{index + 1}"
        raw["answer_parts"].append(current)
    raw["summary_part_indexes"] = [request_count - 1]
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert len(draft.items) == request_count * source_count
    assert len(draft.applications) == request_count
    assert len(draft.applications[-1].item_indexes) == source_count
    assert draft.applications[-1].item_indexes[-1] == len(draft.items) - 1
    assert draft.summary_application_indexes == [request_count - 1]


def test_review_contract_accepts_all_code_selected_sources():
    from rag_engine.models.llm import GroundedFactAudit, GroundedItemReview

    reviews = [GroundedItemReview(index=index, support="supported", applicability="matched", reason="原文") for index in range(80)]
    audit = GroundedFactAudit(reviews=reviews, goal_alignment="aligned", summary_supported=True)
    assert len(audit.reviews) == 80


def test_maximum_length_result_survives_canonicalization_without_truncation():
    raw, schema, catalog = case()
    raw["answer_parts"][0]["answer"]["result"] = "あ" * 600
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.applications[0].result == "あ" * 600


def test_one_generation_call_publishes_derived_result_and_keeps_source_facts():
    import re
    from dataclasses import replace

    from test_grounded_answer import context, record

    from rag_engine.config import get_settings
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answering import synthesize_grounded_answer

    calls = []

    def generated(system, prompt, settings, schema, **options):
        calls.append(schema.__name__)
        if schema is ApplicationAudit:
            return schema(application_reviews=[{"index": 0, "valid": True, "reason": "計算の合成判定"}], summary_supported=True)
        assert getattr(schema, "_uses_unit_ids", False)
        units = {text: uid for uid, text in re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\] (.*)$", prompt)}
        return schema.model_validate(
            {
                "request_coverage": [
                    {
                        "request_id": "Q1",
                        "evidence_scope": [
                            {
                                "evidence_id": "E1",
                                "target": "項目Aと項目B",
                                "relation": "direct",
                                "link": None,
                            }
                        ],
                    }
                ],
                "answer_parts": [
                    {
                        "request_id": "Q1",
                        "requirement": "合計",
                        "answer": {
                            "kind": "answer",
                            "basis": {"unit_ids": [units[text] for text in ("項目Aは12です。", "項目Bは8です。")]},
                            "reasoning": "12＋8＝20",
                            "result": "合計は20です。",
                        },
                    }
                ],
                "summary_part_indexes": [0],
                "confidence": "high",
            }
        )

    def unused(*args, **kwargs):
        raise AssertionError("不要な外部呼出")

    ctx = context(record(0, "項目Aは12です。\n項目Bは8です。", "合成の値", source="synthetic.txt"))
    settings = replace(get_settings(), answer_audit_mode="none", answer_max_correction_rounds=0)
    with bind_dependencies(AnswerDependencies(unused, unused, generated, unused, unused)):
        result = synthesize_grounded_answer("項目Aと項目Bの合計を教えてください。", ctx, settings, image_prompt_mode="text_only").response
    assert calls == ["GroundedDraftWire", "ApplicationAudit"]
    assert result.answer_text.startswith("合計は20です。")
    assert result.generation_trace["applications"][0]["published"]
    assert result.generation_trace["applications"][0]["result"] == "合計は20です。"
    assert result.generation_trace["finalization"]["summary"]["citations"]["links"][0]["application_index"] == 0
    assert result.generation_trace["rounds"][0]["timing"]["answer_parts"]["status"] == "linked"
