"""長い候補末尾・後順位・操作境界・誤採点の回帰条件を検証する。"""

from unittest.mock import patch

import pytest
from judge_stub import calls_of, judge

from rag_engine.evaluation.answer_eval import (
    AnswerEvaluationOutput,
    ClaimCheck,
    CoverageCheck,
    CoverageOutput,
    StandardAnswerScope,
    StandardRequirement,
    _evidence_fragments,
    evaluate_answer_payload,
)
from rag_engine.generation.answering import AnswerContext, AnswerRecord, answer_image_evidence, merge_evidence
from rag_engine.retrieval.context_builder import ContextBuildRequest, build_chunk_context_bundle
from rag_engine.retrieval.evidence_selection import evidence_packet

PARSE = "rag_engine.evaluation.answer_eval.parse_text_response"


def record(key, text, **kwargs):
    return AnswerRecord(
        id=key,
        chunk_id=key,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="manual.pdf",
        **kwargs,
    )


def test_late_parameter_rule_survives_long_html_and_low_rank():
    rule = "会計年度の初期表示の切替日はパラメータ FiscalSwitch で設定する。"
    records = [record(f"p{i}", "無関係の表" * 800) for i in range(11)]
    records.append(record("p12", "<td>入力例</td>" * 300 + rule + "<td>項目</td>" * 100))
    original = records[-1].text
    AnswerContext(records=records, text="full")
    assert rule in evidence_packet("会計年度のデフォルトはいつ切り替わるか", records)
    assert records[-1].text == original


def test_crag_keeps_earlier_rule_when_rewrite_finds_other_context():
    first = record("p1", "パラメータによる切替規則")
    second = record("p2", "現在値は実環境で確認する")
    result = merge_evidence(
        AnswerContext(records=[first], text=first.text),
        AnswerContext(records=[second], text=second.text),
        "切替日",
        max_records=2,
        max_chars=2000,
    )
    assert {r.id for r in result.records} == {"p1", "p2"}
    assert first.text in result.text and second.text in result.text


def test_image_selection_reaches_relevant_operation_after_first_four(tmp_path):
    records = []
    for i in range(6):
        text = "別帳票の出力例" if i < 5 else "返品理由の直接入力には鉛筆ボタンを使う"
        records.append(
            record(f"p{i}", text, metadata={"image_evidence": [{"image_id": f"img{i}", "raw_type": "picture", "caption": text}]})
        )
    selected = answer_image_evidence(records, tmp_path, question="返品理由を直接入力したい", max_images=2)
    assert selected[0]["image_id"] == "img5"
    assert len(selected) == 2
    assert answer_image_evidence(records, tmp_path, max_images=0) == ()


def test_parent_context_does_not_use_operation_support_capacity():
    primary = record("p1", "画面", chunk_level="parent", metadata={"section_path": ["〔返品理由入力〕"]})
    support = record("p2", "鉛筆の操作", chunk_level="parent", metadata={"section_path": ["〔返品理由入力〕"]})
    child = record("c1", "画面", chunk_level="child", parent_chunk_id="p1")
    result = build_chunk_context_bundle(
        ContextBuildRequest(
            question="直接入力",
            ranked_children=[child],
            active_records=[primary, child, support],
            top_k=1,
            neighbor_child_count=1,
            max_records=1,
            max_chars=2000,
            support_record_limit=1,
        )
    )
    assert [r.id for r in result.records] == ["p1"]
    assert all(p.reason != "same_operation_context" for p in result.evidence_tree)


def test_evaluation_does_not_repeat_child_text_already_in_parent():
    fragments = list(
        _evidence_fragments(
            [
                {
                    "id": "p",
                    "text": "前文。操作本文。後文。",
                    "children": [{"id": "c", "text": "操作本文。"}, {"id": "c2", "text": "追加の操作"}],
                }
            ]
        )
    )
    assert sum(f["text"].count("操作本文。") for f in fragments) == 1
    assert any(f["id"] == "c2" and f["text"] == "追加の操作" for f in fragments)


def evaluation_case(status):
    """主張の監査の判定と根拠への結び付けを確かめる評価ケース（段落の出典の行が根拠を指す）。"""
    data = {
        "question": "原因欄を編集する操作は？",
        "standard_answer": "※で開き鉛筆で編集する。",
        "answer_text": "鉛筆で画面を開く。\n根拠：manual.pdf",
        "evidence_items": [{"id": "p1", "source": "manual.pdf", "text": "※で画面を開く。鉛筆で編集する。"}],
    }
    scope = StandardAnswerScope(
        requirements=[StandardRequirement(standard_answer_unit_ids=["S1"], requirement="入口と編集ボタンの説明")],
        excluded_case_data=[],
    )
    output = AnswerEvaluationOutput(
        coverage_checks=[CoverageCheck(requirement_index=1, status="addressed", answer_quote="鉛筆で画面を開く。")],
        claim_checks=[ClaimCheck(answer_quote="鉛筆で画面を開く。", status=status, source_id="", evidence_quote="", reason="役割を確認")],
    )
    return data, scope, output


@pytest.mark.parametrize("status", ["contradicted", "unsupported"])
def test_unsupported_or_reversed_operations_keep_their_claim_status(status):
    # 合否は backend が主張の裏付け(claim_support_rate)で決める。rag_engine は判定をそのまま残す(#680)。
    data, scope, output = evaluation_case(status)
    with patch(PARSE, side_effect=judge(output)):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "completed"
    assert [claim["status"] for claim in result["claim_checks"] if claim["status"] != "not_a_claim"] == [status]


def test_invented_evaluation_source_cannot_confirm_contradiction():
    data, scope, output = evaluation_case("contradicted")
    output.claim_checks[0].evidence_id = "E0000000000"
    with patch(PARSE, side_effect=judge(output)):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "completed"
    assert result["claim_checks"][0]["status"] == "citation_error"
    assert result["claim_checks"][0]["audit_status"] == "contradicted"


def test_audit_records_the_original_passage_including_parentheses():
    data, scope, output = evaluation_case("data_confirmation")
    data["answer_text"] = "鉛筆（編集用）で画面を開く。\n根拠：manual.pdf"
    output.claim_checks[0].answer_passage_id = "A1"
    with patch(PARSE, side_effect=judge(output)):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "completed"
    assert result["claim_checks"][0]["answer_quote"] == "鉛筆（編集用）で画面を開く。"
    assert result["claim_checks"][0]["answer_passage_id"] == "A1"


def test_coverage_binds_original_passage_instead_of_joined_steps():
    data, scope, output = evaluation_case("data_confirmation")
    data["answer_text"] = "最初に画面を開く。次に条件を入力する。最後に保存する。"
    output.coverage_checks[0].answer_passage_id = "A3"
    output.coverage_checks[0].answer_quote = "画面を開く。保存する。"
    with patch(PARSE, side_effect=judge(output)):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "completed"
    assert result["coverage_checks"][0]["answer_quote"] == "最後に保存する。"


def test_coverage_rejects_nonexistent_answer_passage():
    data, scope, output = evaluation_case("data_confirmation")
    output.coverage_checks[0].answer_passage_id = "A999"
    calls = []
    with patch(PARSE, side_effect=judge(output, calls=calls)):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "error"
    assert len(calls_of(calls, CoverageOutput)) == 2


def test_fragmented_native_ranges_are_rejoined_so_a_sentence_can_be_quoted():
    """解析が1文を数字ごとの原文範囲へ分けても、由来と頁が同じなら1つの根拠として引用できる。"""
    from rag_engine.retrieval.evidence_selection import _merge_adjacent_spans

    base = dict(source_id="p1", origin="document_text", page=1, section_path=[], excerpt_only=False, value_context="reference_document")
    # 解析結果では、原文範囲の間に改行や、範囲から漏れた短い語（「月」など）が未分類で挟まる。
    parts = [
        "支払変更サービスは、",
        "\n",
        "10",
        "\n",
        "連休中も受付けています。なお期限は",
        "\n",
        "5",
        "\n月\n",
        "7",
        "\n",
        "日（火）正午までです。",
    ]
    spans, cursor = [], 0
    for text in parts:
        origin = "document_text" if text.strip() and text != "\n月\n" else "unclassified"
        spans.append({**base, "origin": origin, "evidence_id": f"E{cursor}", "start": cursor, "end": cursor + len(text), "text": text})
        cursor += len(text)
    image = {**base, "origin": "image_extraction", "evidence_id": "Eimg", "start": cursor, "end": cursor + 3, "text": "図の値"}
    merged = _merge_adjacent_spans([*spans, image])
    assert [s["text"] for s in merged] == ["".join(parts), "図の値"]  # 由来が変わる境界は残す
    assert merged[0]["end"] == cursor and merged[0]["evidence_id"] not in {s["evidence_id"] for s in spans}
    assert spans[0]["text"] == parts[0]  # 入力は変更しない
