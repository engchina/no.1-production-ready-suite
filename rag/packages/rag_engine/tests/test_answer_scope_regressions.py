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


def test_unplanned_operations_are_excluded_even_when_their_quotes_are_real():
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
    assert all(x.item.evidence_id != "E2" for x in result)
    assert any(x.get("scope_excluded") for x in dropped)


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


def test_an_independent_operation_is_excluded_even_if_listed_in_evidence_ids():
    from rag_engine.generation.grounded import _outside_request_plan

    quote = CASES["coverage"]["additional_evidence"]
    span = {"evidence_id": "actual-id", "label": "E2", "text": quote}
    item = GroundedItem(kind="operation", request_id="Q1", text=quote, citations=[GroundedCitation(evidence_id="E2")])
    draft = GroundedDraft.model_validate(
        {
            "request_coverage": [
                {
                    "request_id": "Q1",
                    "evidence_ids": ["E2"],
                    "evidence_scope": [{"evidence_id": "E2", "target": "対象選択の設定", "relation": "unrelated", "link_quote": ""}],
                }
            ],
            "items": [item.model_dump()],
            "summary": quote,
            "confidence": "high",
        }
    )
    assert _outside_request_plan(item, span, draft, [span])


def test_a_dependency_needs_an_actual_source_quote_but_not_question_word_overlap():
    from rag_engine.generation.grounded import _outside_request_plan

    text = "確定ボタンを押して完了します。"
    span = {"evidence_id": "actual-id", "label": "E2", "text": text}
    link = {"evidence_id": "link-id", "text": "設定の登録後、確定ボタンを押して完了します。"}
    item = GroundedItem(kind="operation", request_id="Q1", text=text, citations=[GroundedCitation(evidence_id="E2")])

    def plan(quote):
        return GroundedDraft.model_validate(
            {
                "request_coverage": [
                    {
                        "request_id": "Q1",
                        "evidence_ids": ["E2"],
                        "evidence_scope": [{"evidence_id": "E2", "target": "確定", "relation": "linked", "link_quote": quote}],
                    }
                ],
                "items": [item.model_dump()],
                "summary": "確定します。",
                "confidence": "high",
            }
        )

    assert _outside_request_plan(item, span, plan(""), [span, link])
    assert not _outside_request_plan(item, span, plan(link["text"]), [span, link])


def test_an_explicitly_empty_scope_does_not_authorize_an_operation():
    from rag_engine.generation.grounded import _outside_request_plan, _request_plan_errors

    text = CASES["coverage"]["additional_evidence"]
    span = {"evidence_id": "E2", "text": text}
    item = GroundedItem(kind="operation", request_id="Q1", text=text, citations=[GroundedCitation(evidence_id="E2")])
    draft = GroundedDraft.model_validate(
        {
            "request_coverage": [{"request_id": "Q1", "evidence_ids": ["E2"], "evidence_scope": []}],
            "items": [item.model_dump()],
            "summary": text,
            "confidence": "high",
        }
    )
    assert _request_plan_errors(draft, [span])
    assert _outside_request_plan(item, span, draft, [span])


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


def test_same_evidence_used_for_two_claims_can_repeat_a_consistent_scope_decision():
    from rag_engine.generation.grounded import _outside_request_plan, _request_plan_errors

    text = (EXAMPLES / "rows.csv").read_text()
    span = {"evidence_id": "E1", "text": text}
    item = GroundedItem(kind="rule", request_id="Q1", text="残す行は row-a と row-c です。", citations=[GroundedCitation(evidence_id="E1")])
    draft = GroundedDraft.model_validate(
        {
            "request_coverage": [
                {
                    "request_id": "Q1",
                    "evidence_ids": ["E1"],
                    "evidence_scope": [
                        {"evidence_id": "E1", "target": target, "relation": "direct", "link_quote": ""}
                        for target in ["残す行", "削除する行"]
                    ],
                }
            ],
            "items": [item.model_dump()],
            "summary": "行を区別します。",
            "confidence": "high",
        }
    )
    assert not _request_plan_errors(draft, [span])
    assert not _outside_request_plan(item, span, draft, [span])


def test_conflicting_scope_decisions_for_the_same_evidence_are_not_merged():
    from rag_engine.generation.grounded import _outside_request_plan, _request_plan_errors

    span = {"evidence_id": "E1", "text": "対象を選択します。"}
    item = GroundedItem(kind="operation", request_id="Q1", text=span["text"], citations=[GroundedCitation(evidence_id="E1")])
    draft = GroundedDraft.model_validate(
        {
            "request_coverage": [
                {
                    "request_id": "Q1",
                    "evidence_ids": ["E1"],
                    "evidence_scope": [
                        {"evidence_id": "E1", "target": "対象", "relation": relation, "link_quote": ""}
                        for relation in ["direct", "unrelated"]
                    ],
                }
            ],
            "items": [item.model_dump()],
            "summary": "対象を選択します。",
            "confidence": "high",
        }
    )
    assert _request_plan_errors(draft, [span])
    assert _outside_request_plan(item, span, draft, [span])


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
