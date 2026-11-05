"""片段IDを、それを生成した行と範囲にだけ結び付ける。"""

import pytest

from rag_engine.evaluation.answer_eval import _evidence_fragments
from rag_engine.evaluation.guide_step_validation import _source_catalog


def spans(rows):
    return [{"source_id": row["id"], "evidence_id": row["id"], "text": row["text"]} for row in rows]


@pytest.mark.parametrize("same_text", [False, True], ids=["different-body", "same-body-other-source-run"])
def test_shared_chunk_uid_does_not_replace_the_row_of_a_generated_id(same_text):
    rows = [
        {"id": "row-a", "chunk_uid": "shared-uid", "source_run_id": "run-a", "text": "合成原文A。"},
        {"id": "row-b", "chunk_uid": "shared-uid", "source_run_id": "run-b", "text": "合成原文A。" if same_text else "合成原文B。"},
    ]
    source_spans = spans(rows)
    catalog = _source_catalog(rows, source_spans)
    for row, span in zip(rows, source_spans, strict=True):
        fragment = next(_evidence_fragments([row]))
        assert catalog[fragment["evidence_id"]] == (span, row["text"])
    assert "shared-uid" not in catalog


def test_same_fragment_id_with_conflicting_row_id_is_not_guessed():
    rows = [{"id": key, "chunk_uid": "shared-uid", "text": "同じ合成原文。"} for key in ["row-a", "row-b"]]
    eid = next(_evidence_fragments(rows))["evidence_id"]
    catalog = _source_catalog(rows, spans(rows))
    assert eid not in catalog
    assert "shared-uid" not in catalog


def test_one_row_multiple_fragments_keeps_each_range():
    rows = [{"id": "row-a", "chunk_uid": "canonical-uid", "text": "合成原文。" * 450}]
    source_spans = spans(rows)
    catalog = _source_catalog(rows, source_spans)
    fragments = list(_evidence_fragments(rows))
    assert len(fragments) > 1
    for fragment in fragments:
        assert catalog[fragment["evidence_id"]] == (source_spans[0], fragment["text"])


@pytest.mark.parametrize("same_uid", [False, True], ids=["child-own-identity", "conflicting-parent-child-uid"])
def test_nested_child_is_not_silently_bound_to_a_parent_span(same_uid):
    child = {"id": "child-a", "text": "子の合成原文。"}
    if same_uid:
        child["chunk_uid"] = "canonical-uid"
    rows = [{"id": "parent-a", "chunk_uid": "canonical-uid", "text": "親の合成原文。", "children": [child]}]
    catalog = _source_catalog(rows, spans(rows))
    child_fragment = list(_evidence_fragments(rows))[-1]
    assert child_fragment["text"] == child["text"]
    assert child_fragment["evidence_id"] not in catalog
    # 呼び出し側が子を独立した正式根拠として渡す場合だけ、子自身へ束縛できる。
    child_catalog = _source_catalog([child], spans([child]))
    assert child_catalog[next(_evidence_fragments([child]))["evidence_id"]][0]["source_id"] == child["id"]
