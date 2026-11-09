"""選択後の全文背景、原文関連、版、重複、文書順位を合成入力で確認する。"""

import json
from dataclasses import replace

from test_batched_evidence import body, config, finding

from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.batched_evidence import prepare_batched_evidence
from rag_engine.generation.document_background import BACKGROUND_ONLY, DocumentBackgroundReader, without_embedded_background
from rag_engine.generation.document_inputs import document_key, document_priority, order_document_spans
from rag_engine.generation.grounded import format_functions, generation_unit_catalog, tag_spans
from rag_engine.knowledge.document_metadata import extract_first_page_context, normalize_first_page_context
from rag_engine.models.storage import StoredChunk
from rag_engine.retrieval.article_catalog import ARTICLE_CATALOG_KEY
from rag_engine.retrieval.evidence_selection import evidence_spans
from rag_engine.retrieval.metadata_context import document_context_key


def record():
    return AnswerRecord(
        id="selected",
        engine="native",
        engine_label="Native",
        category="Chunk",
        source="a.pdf",
        source_run_id="run",
        page=3,
        seq_no=3,
        text="選択した本文。",
        chunk_uid="selected",
        chunk_id="selected",
        metadata={"document_id": "document-a", "chunk_set_id": "cs-a"},
    )


def page(text, record_id="r1"):
    return extract_first_page_context(
        [dict(id=record_id, page=1, seq_no=1, text=text)], engine="native", analyzed_pages=[1], paginated=True
    )


def deps(reader):
    return AnswerDependencies(
        search=lambda **kw: None,
        check_ready=lambda **kw: None,
        parse_text=lambda *args, **kw: None,
        parse_images=lambda *args, **kw: None,
        rerank=lambda *args, **kw: None,
        document_background=reader,
    )


def selected(original):
    return tag_spans(evidence_spans("条件は？", [original], preserve_records=True))


def test_full_capture_keeps_all_records_repeated_text_and_tail():
    rows = [dict(page=1, seq_no=i, id=f"r{i}", text=("長文。" * 3000 if i == 1 else "同じ原文。")) for i in range(150)]
    saved = extract_first_page_context(rows, engine="native", analyzed_pages=[1], paginated=True)
    assert len(saved["text"]) > 8000 and len(saved["record_ids"]) == 150 and not saved["truncated"]
    assert saved["text"].count("同じ原文。") == 149
    assert saved["source_units"][-1]["record_id"] == "r149"
    assert normalize_first_page_context(saved) == saved


def test_chunk_set_is_part_of_document_identity():
    original = record()
    assert document_context_key(original) != document_context_key(replace(original, metadata={**original.metadata, "chunk_set_id": "cs-b"}))


def test_first_chunk_uses_raw_body_and_preserves_original_citation_tree():
    original = record()
    first = StoredChunk(
        chunk_uid="first",
        chunk_id="first",
        chunk_level="child",
        chunk_seq=0,
        parent_chunk_uid="parent",
        parent_chunk_id="parent",
        child_chunk_ids=(),
        source_seq_ranges=(),
        source_record_refs=(),
        text="開始条件。",
        retrieval_text="検索専用の説明",
        source_run_id="run",
        source_file_name="a.pdf",
        source_engine_id="native",
        source_engine_label="Native",
        page_start=1,
        page_end=1,
        metadata=original.metadata,
    )
    context = AnswerContext([original], "本文。")
    with bind_dependencies(deps(lambda **kw: [{"document_scope": list(document_context_key(original)), "first_chunk": first}])):
        result, spans, _ = DocumentBackgroundReader().append("条件は？", context, selected(original))
    assert [value.chunk_uid for value in result.records] == ["selected", "first"]
    assert {parent.record.chunk_uid for parent in result.evidence_tree} == {"selected", "first"}
    assert "開始条件。" in format_functions(spans) and "検索専用の説明" not in format_functions(spans)
    assert context.records == [original]


def test_full_page_is_independent_of_metadata_budget_and_not_a_chunk_citation():
    original = record()
    context = AnswerContext(
        [replace(original, metadata={**original.metadata, "document": {"first_page_context": page("全体の前提。")}})], "本文。"
    )
    reader = DocumentBackgroundReader()
    reader.remember(context)
    stripped = without_embedded_background(context)
    assert "first_page_context" not in stripped.records[0].metadata["document"]
    assert "first_page_context" in context.records[0].metadata["document"]
    _, spans, _ = reader.append("条件は？", stripped, selected(stripped.records[0]))
    normal = [value for value in spans if not value.get(BACKGROUND_ONLY)]
    assert set(generation_unit_catalog(spans).unit_ids) == set(generation_unit_catalog(normal).unit_ids)
    assert "全体の前提。" in format_functions(spans) and "Chunkの引用対象外" in format_functions(spans)


def test_oversized_page_is_read_to_end_with_source_binding():
    original = record()
    saved = page("前提。" + "補足。" * 6000 + "末尾条件。", "page-record")
    with bind_dependencies(deps(lambda **kw: [{"document_scope": list(document_context_key(original)), "first_page": saved}])):
        _, spans, _ = DocumentBackgroundReader().append("条件は？", AnswerContext([original], "本文。"), selected(original))
    seen = []

    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        seen.extend(body(value) for value in values if value.get(BACKGROUND_ONLY))
        return schema.model_validate({"findings": [finding(value) for value in values]})

    final, _, diagnostic = prepare_batched_evidence(
        spans,
        config(),
        question="条件は？",
        render=lambda values: format_functions(values, model_view=True),
        evidence_text=format_functions,
        system="指示",
        schema={},
        read=read,
        unusable_output=lambda error: False,
        tag=lambda values: tag_spans(values),
    )
    assert "".join(seen) == saved["text"]
    assert diagnostic["batch_count"] > 1
    refs = [value["background_ref"] for value in final if value.get(BACKGROUND_ONLY)]
    assert refs and all(ref["record_ids"] == ["page-record"] for ref in refs)


def test_dedup_requires_matching_original_unit_in_same_version():
    original = replace(record(), text="全体の前提。", metadata={**record().metadata, "element_ids": ["r1"]})
    with bind_dependencies(
        deps(lambda **kw: [{"document_scope": list(document_context_key(original)), "first_page": page(original.text)}])
    ):
        _, spans, diagnostic = DocumentBackgroundReader().append("条件は？", AnswerContext([original], "本文。"), selected(original))
    assert not any(value.get(BACKGROUND_ONLY) for value in spans)
    assert diagnostic["documents"][0]["first_page_deduplicated_units"] == 1


def test_document_rank_uses_best_valid_score_and_preserves_catalog_slot():
    values = [
        {"source_id": "a1", "document_scope": ["a"]},
        {"source_id": "catalog", "document_scope": ["c"], ARTICLE_CATALOG_KEY: True},
        {"source_id": "b1", "document_scope": ["b"]},
        {"source_id": "b2", "document_scope": ["b"]},
        {"source_id": "d1", "document_scope": ["d"]},
    ]
    priority = document_priority(values, {"a1": 0.3, "b1": 0.1, "b2": 0.9, "d1": float("nan")})
    ordered = order_document_spans(values, priority=priority)
    assert list(dict.fromkeys(document_key(value) for value in ordered)) == [("b",), ("c",), ("a",), ("d",)]
