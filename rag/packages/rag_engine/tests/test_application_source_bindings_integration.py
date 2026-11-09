"""実生成/監査経路と、原文view・版の正負対照。モデル/DBなし。"""

import copy
import json
import re
from dataclasses import replace

import pytest
from audit_fixtures import legacy_audit_to_wire_fixture
from citation_unit_support import selected_answer_payload
from test_application_source_bindings import QUESTION, TEXT, sources, verified

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering, grounded
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.application_sources import application_item_citations
from rag_engine.models.audit_wire import is_audit_wire
from rag_engine.models.llm import GroundedDraft


@pytest.mark.parametrize("field", ["content_sha256", "view_sha256", "char_end"])
def test_changed_private_proof_never_becomes_new_binding(field):
    entry, _, _ = verified()
    entry.item.citations[0]._extraction[field] = "0" * 64
    assert application_item_citations(entry) == []


def test_changed_quote_never_gets_the_original_proof():
    entry, _, _ = verified()
    entry.item.citations[0].quote = "別の引用"
    assert application_item_citations(entry) == []


def test_unit_snapshot_cannot_mix_new_scope_with_old_unit_ids():
    entry, _, _ = verified()
    citation = entry.item.citations[0]
    binding = citation._unit_binding
    changed = binding.snapshot.span()
    changed["document_scope"] = ["synthetic.pdf", "new-version", "new-document"]
    citation._unit_binding = replace(binding, snapshot=replace(binding.snapshot, span_json=json.dumps(changed)))
    assert application_item_citations(entry) == []


def test_legacy_scope_change_after_verification_is_not_relabelled_as_verified():
    entry, _, _ = verified(stable_units=False)
    entry.span["document_scope"] = ["synthetic.pdf", "new-version", "new-document"]
    assert application_item_citations(entry) == []


def test_same_name_different_sections_keep_their_own_snapshots(monkeypatch):
    import test_application_source_bindings as support

    spans = sources()
    for index, span in enumerate(spans):
        span.update(section_path=[f"節{index + 1}"], function=("synthetic.pdf", f"節{index + 1}"))
    monkeypatch.setattr(support, "sources", lambda: copy.deepcopy(spans))
    first, _, _ = verified((0,))
    second, _, _ = verified((1,))
    left, right = application_item_citations(first)[0], application_item_citations(second)[0]
    assert first.item.citations[0]._extraction["snapshot_id"] != second.item.citations[0]._extraction["snapshot_id"]
    assert first.item.citations[0]._extraction["view_sha256"] != second.item.citations[0]._extraction["view_sha256"]
    assert left["document_scope"] != right["document_scope"]


@pytest.mark.parametrize("mode", ["standard", "detailed"])
@pytest.mark.parametrize("legacy", [False, True], ids=["sent-units", "legacy-lines"])
def test_actual_generation_audit_reuse_preserves_fullwidth_raw_and_sent_view(mode, legacy):
    raw = "機器Ａの測定値は２８です。ｶﾞｲﾄﾞの表示です。"
    observations = []

    def parse(system, prompt, settings, schema, **options):
        if is_audit_wire(schema):
            data = json.loads(prompt)
            observations.append(data)
            for row in data["items"]:
                assert row["citation_bindings"], row
                for binding in row["citation_bindings"]:
                    assert binding["quote"] in raw
                    assert not ({"snapshot_id", "content_sha256", "view_sha256"} & binding.keys())
                    assert bool(binding["unit_ids"]) != legacy
                if "citations" in row:
                    assert row["citations"] == [{"evidence_id": row["citation_bindings"][0]["evidence_id"], "quote": row["quote"]}]
            review = {"application_reviews": [{"index": 0, "valid": True, "reason": "形式fixture"}], "summary_supported": True}
            return schema.model_validate(
                legacy_audit_to_wire_fixture(
                    {
                        **review,
                        "reviews": [
                            {"index": row["index"], "support": "supported", "applicability": "matched", "reason": "形式fixture"}
                            for row in data["items"]
                        ],
                        "goal_alignment": "aligned",
                        "request_reviews": [
                            {"request_id": q, "status": "addressed", "reason": "形式fixture"} for q in schema.expected_request_ids
                        ],
                    },
                    schema,
                )
            )
        if legacy:
            label = re.findall(r"\[(E\d+)\]", prompt)[0]
            return GroundedDraft.model_validate(
                {
                    "items": [{"request_id": "Q1", "kind": "rule", "text": raw, "citations": [{"evidence_id": label, "lines": "1"}]}],
                    "applications": [
                        {"request_id": "Q1", "item_indexes": [0], "reasoning": "選択した原文の値を示す。", "result": "測定値は28です。"}
                    ],
                    "request_coverage": [{"request_id": "Q1", "evidence_ids": [label]}],
                    "summary": "",
                    "confidence": "low",
                }
            )
        uid = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)[0]
        return schema.model_validate(selected_answer_payload([uid], text="測定値は28です。"))

    def unused(*args, **kwargs):
        pytest.fail("外部I/O不要")

    row = AnswerRecord(
        id="synthetic",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=raw,
        source="synthetic.pdf",
        chunk_level="parent",
        chunk_id="synthetic",
        source_run_id="synthetic",
        metadata={"document_id": "doc-a", "chunk_set_id": "cs-a", "section_path": ["節A"]},
    )
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode=mode, answer_max_correction_rounds=0)
    with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
        result = answering.synthesize_grounded_answer(
            QUESTION, AnswerContext(records=[row], text=raw), settings, image_prompt_mode="text_only"
        ).response
    assert len(observations) == 1
    assert result.generation_trace["llm_calls"] == 2
    assert raw in result.answer_text


def test_actual_verify_system_added_quote_only_keeps_old_audit_selection():
    spans = sources()
    spans[1].update(text="機器Bには別の値が記載されています。", pinned=True)
    observations = []

    def parse(system, prompt, settings, schema, **options):
        if is_audit_wire(schema):
            data = json.loads(prompt)
            observations.append(data)
            assert len(data["items"]) == 1
            row = data["items"][0]
            assert row["citations"] == [{"evidence_id": spans[0]["evidence_id"], "quote": TEXT}]
            assert row["citation_bindings"][0]["source_id"] == spans[0]["source_id"]
            assert len(data["documents"]) == 2
            return schema.model_validate(
                legacy_audit_to_wire_fixture(
                    {
                        "reviews": [{"index": row["index"], "support": "supported", "applicability": "matched", "reason": "形式fixture"}],
                        "application_reviews": [{"index": 0, "valid": True, "reason": "形式fixture"}],
                        "summary_supported": True,
                        "goal_alignment": "aligned",
                        "request_reviews": [
                            {"request_id": q, "status": "addressed", "reason": "形式fixture"} for q in schema.expected_request_ids
                        ],
                    },
                    schema,
                )
            )
        uid = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)[0]
        return schema.model_validate(selected_answer_payload([uid], text="測定値は28です。"))

    config = replace(get_settings(environ={}, dotenv_path=None), answer_max_correction_rounds=0)
    result = grounded.run_round(
        QUESTION,
        spans,
        config,
        prompt=grounded.format_functions(spans),
        image_paths=(),
        provider_id=None,
        parse_text=parse,
        parse_images=lambda *_a, **_k: pytest.fail("画像不要"),
        audit_mode="standard",
    )
    assert any(entry.added and entry.quote_only and entry.span["source_id"] == spans[1]["source_id"] for entry in result.checked)
    assert len(observations) == 1


def test_actual_generation_with_also_keeps_existing_citation_group_and_all_bindings():
    spans = sources()
    observations = []

    def parse(system, prompt, settings, schema, **options):
        if is_audit_wire(schema):
            data = json.loads(prompt)
            observations.append(data)
            assert len(data["items"]) == 2
            expected = [{"evidence_id": span["evidence_id"], "quote": TEXT} for span in spans]
            for row in data["items"]:
                assert row["citations"] == expected
                assert [binding["source_id"] for binding in row["citation_bindings"]] == [span["source_id"] for span in spans]
                assert len({tuple(binding["unit_ids"]) for binding in row["citation_bindings"]}) == 2
            return schema.model_validate(
                legacy_audit_to_wire_fixture(
                    {
                        "reviews": [
                            {"index": row["index"], "support": "supported", "applicability": "matched", "reason": "形式fixture"}
                            for row in data["items"]
                        ],
                        "application_reviews": [{"index": 0, "valid": True, "reason": "形式fixture"}],
                        "summary_supported": True,
                        "goal_alignment": "aligned",
                        "request_reviews": [
                            {"request_id": q, "status": "addressed", "reason": "形式fixture"} for q in schema.expected_request_ids
                        ],
                    },
                    schema,
                )
            )
        units = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)
        return schema.model_validate(selected_answer_payload(units, text="測定値は28です。"))

    config = replace(get_settings(environ={}, dotenv_path=None), answer_max_correction_rounds=0)
    result = grounded.run_round(
        QUESTION,
        spans,
        config,
        prompt=grounded.format_functions(spans),
        image_paths=(),
        provider_id=None,
        parse_text=parse,
        parse_images=lambda *_a, **_k: pytest.fail("画像不要"),
        audit_mode="standard",
    )
    assert len(observations) == 1
    assert any(entry.also for entry in result.checked)
