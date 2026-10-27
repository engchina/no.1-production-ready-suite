"""完全な出典を実際の最終プロンプトで照合する。"""

import json
from dataclasses import replace

import pytest

from rag_engine.generation import grounded
from rag_engine.generation.answering import AnswerContext, AnswerRecord, _grounded_prompt, _grounded_spans
from rag_engine.retrieval.evidence_selection import evidence_spans


def make_record(index, path, *, enabled=None, source="document-a.txt"):
    metadata = {"section_path": path}
    if enabled is not None:
        metadata["context_header_enabled"] = enabled
        if enabled:
            metadata["context_header"] = " > ".join([source, *path])
    return AnswerRecord(
        id=f"r{index}",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=index,
        category="Text",
        text=f"項目 {index} の説明本文です。",
        source=source,
        metadata=metadata,
    )


@pytest.mark.parametrize("enabled", [True, False, None])
def test_final_prompt_keeps_each_complete_path_separate_from_quote(enabled):
    records = [
        make_record(1, ["分類 A", "機能 A", "説明"], enabled=enabled),
        make_record(2, ["分類 B", "機能 A", "説明"], enabled=enabled),
        make_record(3, ["分類 C", "機能 A", "説明"], source="document-b.txt", enabled=enabled),
    ]
    ctx = AnswerContext(records=records, text="\n".join(r.text for r in records))
    spans = _grounded_spans("各項目の説明", ctx, (), None)
    # 最終回答の組立関数を通し、グループ名だけでなく各 Evidence 内を検査する。
    prompt = _grounded_prompt("各項目の説明", spans, ctx, (), None, None, None, None, None)
    for record in records:
        path_json = json.dumps(record.metadata["section_path"], ensure_ascii=False)
        assert path_json in prompt
        assert record.source in prompt
        span = next(s for s in spans if s["text"] == record.text)
        assert "出典情報" not in span["text"]
        note = grounded._source_note(span)
        assert path_json in note and grounded.CONTEXT_NOTE_BEGIN in note
        assert record.text not in note
    assert (
        prompt.index("\nBEGIN_UNTRUSTED_RETRIEVED_CONTEXT\n")
        < prompt.index("出典情報:")
        < prompt.rindex("\nEND_UNTRUSTED_RETRIEVED_CONTEXT")
    )


def test_unknown_path_is_not_invented_and_long_path_is_not_truncated():
    missing = replace(make_record(1, []), metadata={})
    known = make_record(2, ["階層" + str(i) for i in range(80)])
    spans = evidence_spans("説明", [known, missing], budget=3000)
    assert spans[0]["source_context"]["section_path"] == known.metadata["section_path"]
    assert "section_path" not in spans[1]["source_context"]
    # 完全なヘッダを収められない予算では、パスの途中を削って渡さない。
    assert evidence_spans("説明", [known], budget=100) == []


def test_parent_excerpt_keeps_only_corresponding_child_source_context():
    child_a = replace(make_record(1, ["分類 A", "説明"], enabled=True), parent_chunk_id="parent")
    child_b = replace(make_record(2, ["分類 B", "説明"], enabled=False), parent_chunk_id="parent")
    parent = replace(
        make_record(0, ["共通分類"]), id="parent", chunk_id="parent", chunk_level="parent", text=child_a.text + "\n" + child_b.text
    )
    spans = evidence_spans("説明", [parent], scope_records=[child_a, child_b], budget=3000)
    for child in (child_a, child_b):
        span = next(s for s in spans if child.text in s["text"])
        children = span["source_context"]["children"]
        info = next(c for c in children if c["source_id"] == child.id)
        assert info["section_path"] == child.metadata["section_path"]
        assert info.get("search_context_header") == child.metadata.get("context_header")
        other = child_b if child is child_a else child_a
        assert all(c["source_id"] != other.id for c in children)
