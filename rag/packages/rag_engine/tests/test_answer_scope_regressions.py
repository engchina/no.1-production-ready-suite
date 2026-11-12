"""同じ原文の別主張の保持と、質問ごとの根拠計画の適用を合成資料で再現する。"""

import json
from pathlib import Path

from rag_engine.generation.grounded import CheckedItem, verify
from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem

EXAMPLES = Path(__file__).with_name("fixtures") / "answer_accuracy"
CASES = json.loads((EXAMPLES / "cases.json").read_text())


def checked(text, quote, *, request="Q1", evidence="E1", kind="operation", condition=""):
    return CheckedItem(
        GroundedItem(
            kind=kind,
            text=text,
            citations=[GroundedCitation(quote=quote, evidence_id=evidence)],
            request_id=request,
            condition=condition,
        ),
        {"evidence_id": evidence, "text": quote},
    )


def test_model_plan_does_not_exclude_real_citations():
    correct = CASES["summary"]["evidence"]
    other = CASES["coverage"]["additional_evidence"]
    spans = [
        dict(
            evidence_id=eid,
            label=eid,
            text=text,
            source="operations.txt",
            tags=[],
            document_scope=["operations.txt"],
            function=["operations.txt", ""],
            section_path=[],
            lines=[],
        )
        for eid, text in [("E1", correct), ("E2", other)]
    ]
    draft = GroundedDraft.model_validate(
        {
            "request_coverage": [{"request_id": "Q1", "evidence_ids": ["E1"]}],
            "items": [
                {"kind": "operation", "request_id": "Q1", "text": correct, "citations": [{"quote": correct, "evidence_id": "E1"}]},
                {"kind": "operation", "request_id": "Q1", "text": other, "citations": [{"quote": other, "evidence_id": "E2"}]},
            ],
            "summary": "CSV を更新します。",
            "confidence": "high",
        }
    )
    result, dropped = verify(CASES["summary"]["question"], draft, spans)
    assert any(x.item.evidence_id == "E2" for x in result)
    assert not any(x.get("scope_excluded") for x in dropped)


def test_request_plan_keeps_related_steps_without_question_word_overlap():
    source = "確定ボタンを押して完了します。"
    spans = [{"evidence_id": "actual-id", "label": "E2", "text": source, "source": "operations.txt"}]
    draft = GroundedDraft.model_validate(
        {
            "request_coverage": [{"request_id": "Q1", "evidence_ids": ["E2"]}],
            "items": [{"kind": "operation", "request_id": "Q1.R1", "text": source, "citations": [{"quote": source, "evidence_id": "E2"}]}],
            "summary": "確定します。",
            "confidence": "high",
        }
    )
    result, dropped = verify("設定を登録する方法を教えてください。", draft, spans)
    assert any(entry.item.text == source for entry in result)
    assert not any(row.get("scope_excluded") for row in dropped)


def test_retrieval_rank_does_not_reorder_a_documents_heading_and_body():
    from dataclasses import replace

    from test_grounded_answer import context, record

    from rag_engine.generation.answering import _grounded_spans
    from rag_engine.generation.grounded import format_functions

    sections = (EXAMPLES / "operations.txt").read_text().strip().split("\n\n")
    parts = [line for section in sections for line in section.splitlines()]
    records = [replace(record(i, text, "", source="operations.txt"), chunk_seq=i) for i, text in enumerate(parts)]
    ranked = [records[i] for i in [8, 9, 5, 4, 6, 7, 0, 1, 2, 3]]
    spans = _grounded_spans(CASES["summary"]["question"], context(*ranked), (), None)
    packet = format_functions(spans)
    assert all(packet.index(parts[i]) < packet.index(parts[i + 1]) for i in range(len(parts) - 1))
    # 根拠の選択と表示 ID は関連度順のまま、モデルへ読ませる本文だけ原文順にする。
    assert spans[0]["text"] == parts[8]


def test_unsectioned_contiguous_chunks_keep_separate_numbered_headings():
    from dataclasses import replace

    from test_grounded_answer import context, record

    from rag_engine.generation.answering import _grounded_spans
    from rag_engine.generation.grounded import _retrieved_heading_context, format_functions

    sections = (EXAMPLES / "operations.txt").read_text().strip().split("\n\n")
    parts = [line for section in sections for line in section.splitlines()]
    records = [replace(record(i, text, "", source="operations.txt"), chunk_seq=i, metadata={}) for i, text in enumerate(parts)]
    ranked = [records[i] for i in [8, 9, 5, 4, 6, 7, 0, 1, 2, 3]]
    spans = _grounded_spans(CASES["summary"]["question"], context(*ranked), (), None)
    presented = _retrieved_heading_context(spans)
    by_text = {span["text"]: span for span in presented}
    assert by_text[parts[9]]["function"][-1] == parts[8]
    assert by_text[parts[5]]["function"][-1] == parts[4]
    assert by_text[parts[7]]["function"][-1] == parts[6]
    assert all(not span["section_path"] and not span["function"][-1] for span in spans)
    assert parts[8] in format_functions(spans)
    # 未取得の章境界を飛び越えた本文へ、前章の名前を与えない。
    gap = _retrieved_heading_context([span for span in spans if span["text"] in [parts[0], parts[5]]])
    assert next(span for span in gap if span["text"] == parts[5])["function"][-1] == ""


def test_unrelated_prediction_does_not_override_a_real_citation():
    quote = "対象を選択します。"
    span = {"evidence_id": "E1", "label": "E1", "text": quote, "source": "source.txt"}
    draft = GroundedDraft.model_validate(
        {
            "request_coverage": [
                {
                    "request_id": "Q1",
                    "evidence_ids": ["E1"],
                    "evidence_scope": [{"evidence_id": "E1", "target": "別の対象", "relation": "unrelated", "link_quote": ""}],
                }
            ],
            "items": [{"kind": "operation", "request_id": "Q1", "text": quote, "citations": [{"quote": quote, "evidence_id": "E1"}]}],
            "summary": quote,
            "confidence": "high",
        }
    )
    kept, dropped = verify("対象を選ぶ方法", draft, [span])
    assert len(kept) == 1
    assert kept[0].item.quote == quote
    assert not any(row.get("scope_excluded") for row in dropped)
