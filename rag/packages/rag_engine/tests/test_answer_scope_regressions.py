"""同じ原文の別主張の保持と、質問ごとの根拠計画の適用を合成資料で再現する。"""

import json
from pathlib import Path

from rag_engine.generation.grounded import CheckedItem, carry_over, verify, without_redundant_quotes
from rag_engine.models.llm import GroundedDraft, GroundedItem

EXAMPLES = Path(__file__).with_name("fixtures") / "answer_accuracy"
CASES = json.loads((EXAMPLES / "cases.json").read_text())


def checked(text, quote, *, request="Q1", evidence="E1", kind="operation", condition=""):
    return CheckedItem(
        GroundedItem(kind=kind, text=text, quote=quote, evidence_id=evidence, request_id=request, condition=condition),
        {"evidence_id": evidence, "text": quote},
    )


def test_distinct_steps_from_the_same_quote_survive_deduplication():
    quote = CASES["coverage"]["additional_evidence"]
    entries = [checked("一覧から対象を選びます。", quote), checked("設定タブで名称を入力して登録します。", quote)]
    assert without_redundant_quotes(entries) == entries


def test_csv_retained_and_deleted_ids_from_the_same_quote_survive():
    quote = (EXAMPLES / "rows.csv").read_text()
    entries = [checked("残す id は row-a と row-c です。", quote, kind="rule"), checked("削除する id は row-b です。", quote, kind="rule")]
    assert without_redundant_quotes(entries) == entries


def test_carry_over_keeps_a_missing_claim_even_when_its_quote_is_already_used():
    quote = CASES["coverage"]["additional_evidence"]
    previous = checked("設定タブで名称を入力します。", quote)
    result = carry_over([checked("対象を一覧から選びます。", quote)], [previous])
    assert len(result) == 2
    assert result[1].item.text == previous.item.text and result[1].carried


def test_identical_claims_answering_different_requests_are_kept():
    entries = [checked("対象を選択します。", "対象を選択します。", request=r) for r in ("Q1", "Q2")]
    assert without_redundant_quotes(entries) == entries


def test_unplanned_operations_are_excluded_even_when_their_quotes_are_real():
    correct = CASES["summary"]["evidence"]
    other = CASES["coverage"]["additional_evidence"]
    spans = [
        dict(
            evidence_id=eid,
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
                {"kind": "operation", "request_id": "Q1", "text": correct, "quote": correct, "evidence_id": "E1"},
                {"kind": "operation", "request_id": "Q1", "text": other, "quote": other, "evidence_id": "E2"},
            ],
            "summary": "CSV を更新します。",
            "confidence": "high",
        }
    )
    result, dropped = verify(CASES["summary"]["question"], draft, spans)
    assert all(x.item.evidence_id != "E2" for x in result)
    assert any(x.get("scope_excluded") for x in dropped)


def test_identical_claims_under_different_conditions_are_not_duplicates():
    entries = [checked("対象を選択します。", "対象を選択します。", condition=c) for c in ("条件 A", "条件 B")]
    assert without_redundant_quotes(entries) == entries


def test_request_plan_keeps_related_steps_without_question_word_overlap():
    source = "確定ボタンを押して完了します。"
    spans = [{"evidence_id": "actual-id", "label": "E2", "text": source, "source": "operations.txt"}]
    draft = GroundedDraft.model_validate(
        {
            "request_coverage": [{"request_id": "Q1", "evidence_ids": ["E2"]}],
            "items": [{"kind": "operation", "request_id": "Q1.R1", "text": source, "quote": source, "evidence_id": "E2"}],
            "summary": "確定します。",
            "confidence": "high",
        }
    )
    result, dropped = verify("設定を登録する方法を教えてください。", draft, spans)
    assert any(entry.item.text == source for entry in result)
    assert not any(row.get("scope_excluded") for row in dropped)


def test_excluded_operation_is_not_reintroduced_by_the_summary_without_an_audit():
    from dataclasses import replace

    from test_grounded_answer import FakeModel, context, ids, record

    from rag_engine.config import get_settings
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answering import synthesize_grounded_answer

    question = CASES["summary"]["question"]
    correct, other = CASES["summary"]["evidence"], CASES["coverage"]["additional_evidence"]
    ctx = context(record(0, correct, "CSV の更新"), record(1, other, "対象選択の設定"))
    labels = ids(ctx, question)
    model = FakeModel(
        [
            {
                "request_coverage": [{"request_id": "Q1", "evidence_ids": [labels[correct]]}],
                "items": [
                    {"kind": "operation", "request_id": "Q1", "text": text, "quote": text, "evidence_id": labels[text]}
                    for text in (correct, other)
                ],
                "summary": other,
                "confidence": "high",
            }
        ],
        [],
    )

    def unused(*args, **kwargs):
        raise AssertionError("外部呼び出しは不要")

    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = synthesize_grounded_answer(
            question, ctx, replace(get_settings(), answer_audit_mode="none"), image_prompt_mode="text_only"
        ).response
    assert other not in result.answer_text
    assert correct in result.answer_text
    assert result.generation_trace["llm_calls"] == 1
    assert result.generation_trace["rounds"][0]["summary_reason"]


def test_an_independent_operation_is_excluded_even_if_listed_in_evidence_ids():
    from rag_engine.generation.grounded import _outside_request_plan

    quote = CASES["coverage"]["additional_evidence"]
    span = {"evidence_id": "actual-id", "label": "E2", "text": quote}
    item = GroundedItem(kind="operation", request_id="Q1", text=quote, evidence_id="E2")
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
    item = GroundedItem(kind="operation", request_id="Q1", text=text, evidence_id="E2")

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

    assert _outside_request_plan(item, span, plan("記載のない連携関係。"), [span, link])
    assert _outside_request_plan(item, span, plan(""), [span, link])
    assert not _outside_request_plan(item, span, plan(link["text"]), [span, link])


def test_inconsistent_scope_plan_is_repaired_once_and_the_call_is_counted():
    from dataclasses import replace

    from test_grounded_answer import FakeModel, context, ids, record

    from rag_engine.config import get_settings
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answering import synthesize_grounded_answer

    question = CASES["summary"]["question"]
    correct, other = CASES["summary"]["evidence"], CASES["coverage"]["additional_evidence"]
    ctx = context(record(0, correct, "CSV の更新"), record(1, other, "対象選択の設定"))
    labels = ids(ctx, question)
    entries = [
        {"kind": "operation", "request_id": "Q1", "text": text, "quote": text, "evidence_id": labels[text]} for text in (correct, other)
    ]
    first = {
        "request_coverage": [{"request_id": "Q1", "evidence_ids": list(labels.values()), "evidence_scope": []}],
        "items": entries,
        "summary": other,
        "confidence": "high",
    }
    fixed = {
        "request_coverage": [
            {
                "request_id": "Q1",
                "evidence_ids": [labels[correct]],
                "evidence_scope": [
                    {"evidence_id": labels[correct], "target": "CSV", "relation": "direct", "link_quote": ""},
                    {"evidence_id": labels[other], "target": "対象選択の設定", "relation": "unrelated", "link_quote": ""},
                ],
            }
        ],
        "items": entries[:1],
        "summary": correct,
        "confidence": "high",
    }
    model = FakeModel([first, fixed], [])

    def unused(*args, **kwargs):
        raise AssertionError("外部呼び出しは不要")

    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = synthesize_grounded_answer(
            question, ctx, replace(get_settings(), answer_audit_mode="none"), image_prompt_mode="text_only"
        ).response
    assert correct in result.answer_text and other not in result.answer_text
    assert result.generation_trace["llm_calls"] == 2
    assert "invalid_request_plan" in model.prompts[1][1]


def test_an_explicitly_empty_scope_does_not_authorize_an_operation():
    from rag_engine.generation.grounded import _outside_request_plan, _request_plan_errors

    text = CASES["coverage"]["additional_evidence"]
    span = {"evidence_id": "E2", "text": text}
    item = GroundedItem(kind="operation", request_id="Q1", text=text, evidence_id="E2")
    draft = GroundedDraft.model_validate(
        {
            "request_coverage": [{"request_id": "Q1", "evidence_ids": ["E2"], "evidence_scope": []}],
            "items": [item.model_dump()],
            "summary": text,
            "confidence": "high",
        }
    )
    assert _request_plan_errors(draft)
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
    item = GroundedItem(kind="rule", request_id="Q1", text="残す行は row-a と row-c です。", evidence_id="E1")
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
    assert not _request_plan_errors(draft)
    assert not _outside_request_plan(item, span, draft, [span])


def test_conflicting_scope_decisions_for_the_same_evidence_are_not_merged():
    from rag_engine.generation.grounded import _outside_request_plan, _request_plan_errors

    span = {"evidence_id": "E1", "text": "対象を選択します。"}
    item = GroundedItem(kind="operation", request_id="Q1", text=span["text"], evidence_id="E1")
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
    assert _request_plan_errors(draft)
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


def test_missing_section_metadata_is_planned_before_the_answer_and_cannot_be_overridden():
    from dataclasses import replace

    from test_grounded_answer import FakeModel, context, ids, record

    from rag_engine.config import get_settings
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answering import synthesize_grounded_answer
    from rag_engine.models.llm import RequestScopePlan

    correct, other = CASES["summary"]["evidence"], CASES["coverage"]["additional_evidence"]
    texts = ["第1節 対象選択の設定", other, "第2節 CSV の更新", correct]
    records = [replace(record(i, text, "", source="operations.txt"), metadata={}, chunk_seq=i) for i, text in enumerate(texts)]
    ctx = context(records[2], records[3], records[1], records[0])
    labels = ids(ctx, CASES["summary"]["question"])
    plan = {
        "request_id": "Q1",
        "evidence_ids": [labels[correct]],
        "evidence_scope": [{"evidence_id": labels[correct], "target": "CSV の更新", "relation": "direct", "link_quote": ""}],
    }
    model = FakeModel(
        [
            {
                "request_coverage": [{"request_id": "Q1", "evidence_ids": list(labels.values())}],
                "items": [
                    {"kind": "operation", "request_id": "Q1", "text": text, "quote": text, "evidence_id": labels[text]}
                    for text in (correct, other)
                ],
                "summary": other,
                "confidence": "high",
            }
        ],
        [],
    )
    calls = []

    def parse(system, prompt, settings, schema, **options):
        calls.append(schema)
        if schema is RequestScopePlan:
            return schema.model_validate({"request_coverage": [plan]})
        assert other not in prompt
        return model(system, prompt, settings, schema, **options)

    def unused(*args, **kwargs):
        raise AssertionError("追加の外部呼び出しは不可")

    with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
        result = synthesize_grounded_answer(
            CASES["summary"]["question"], ctx, replace(get_settings(), answer_audit_mode="none"), image_prompt_mode="text_only"
        ).response
    assert correct in result.answer_text and other not in result.answer_text
    assert calls == [RequestScopePlan, GroundedDraft]
    assert result.generation_trace["llm_calls"] == 2
