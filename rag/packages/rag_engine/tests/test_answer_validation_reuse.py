"""最終の検証での、RAG の回答の監査の判定の再利用（#1527）。モデルはスタブ。

RAG の回答の監査を通った説明と本文・根拠が同じ段落は、その判定（supported）を使い、モデルに渡さない。
"""
import json
from unittest.mock import patch

from rag_engine.evaluation.answer_validation import (
    REUSED_CLAIM_REASON,
    ClaimAuditOutput,
    audited_answer_items,
    audited_line_key,
    validate_answer_claims,
)

RAG_ANSWER = "\n".join([
    "承認の記録は、承認した日から5年間保管します。",
    "",
    "確認できる内容",
    "",
    "・承認の記録は、承認した日から5年間保管します。",
    "根拠：approval-rules.pdf p.1",
    "",
    "操作手順（申請）",
    "",
    "1. 申請画面で「承認依頼」を押します。",
    "2. 承認者を選びます。",
    "根拠：manual.pdf p.3",
    "",
    "答えていない点",
    "",
    "・保管の場所は資料に記載がありません。",
])
ITEMS = [
    {"kind": "rule", "text": "承認の記録は、承認した日から5年間保管します。", "applies": "matched"},
    {"kind": "operation", "text": "申請画面で「承認依頼」を押します。", "applies": "matched"},
    {"kind": "operation", "text": "承認者を選びます。", "applies": "conditional", "condition": "部長の承認"},
    {"kind": "gap", "text": "保管の場所は資料に記載がありません。", "applies": "matched"},
]
CITATION_LINES = {"c-rule": [1], "c-manual": [2]}
EVIDENCE = [
    {"id": "c-rule", "source": "approval-rules.pdf", "page_start": 1, "page_end": 1,
     "text": "承認の記録は、承認した日から 5 年間保管します。"},
    {"id": "c-manual", "source": "manual.pdf", "page_start": 3, "page_end": 3,
     "text": "申請画面で「承認依頼」を押します。承認者を選びます。"},
]


def test_audited_items_follow_the_citation_lines() -> None:
    audited = audited_answer_items(RAG_ANSWER, ITEMS, CITATION_LINES)
    # 条件つきの説明と gap は再利用しない。
    assert audited == {"承認の記録は、承認した日から5年間保管します。": ["c-rule"],
                       "申請画面で「承認依頼」を押します。": ["c-manual"]}
    assert audited_line_key("- 1. x") == "1. x"  # 印は先頭の 1 つだけ除く
    assert audited_line_key("  2)  承認者を  選びます。 ") == "承認者を 選びます。"
    # Markdown の強調は書式だけなので除く（Agent が「**最長 30 日**」と書き直した回答。m1527 の実測）。
    assert audited_line_key("- 有効期限は**最長 30 日**です。") == "有効期限は最長 30 日です。"
    assert audited_line_key("**承認者**を__選びます__。") == "承認者を選びます。"


def test_all_passages_reused_does_not_call_the_model() -> None:
    audited = audited_answer_items(RAG_ANSWER, ITEMS, CITATION_LINES)
    answer = "承認の記録は、承認した日から**5年間**保管します。\n\n- 申請画面で「承認依頼」を押します。"
    with patch("rag_engine.evaluation.answer_eval.parse_text_response") as never:
        result = validate_answer_claims("保管期間は？", answer, EVIDENCE, settings=None, audited=audited)
        never.assert_not_called()
    assert result["status"] == "completed"
    assert result["counts"] == {"supported": 2}
    assert [c["source_id"] for c in result["claim_checks"]] == ["c-rule", "c-manual"]
    assert all(c["reason"] == REUSED_CLAIM_REASON for c in result["claim_checks"])
    assert len(result["reused_passage_ids"]) == 2


def test_only_unmatched_passages_go_to_the_model() -> None:
    audited = audited_answer_items(RAG_ANSWER, ITEMS, CITATION_LINES)
    # 1 行目は監査済みと同じ。2 行目は言い換え（本文が違う）、3 行目は条件つきの説明なので再利用しない。
    answer = "\n".join(["承認の記録は、承認した日から5年間保管します。", "記録は 5 年で破棄します。", "・承認者を選びます。"])
    seen: list[list[str]] = []

    def parse(system, inputs, settings, schema, provider_id=None):
        payload = json.loads(inputs)
        seen.append([item["text"] for item in payload["answer_passages"]])
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": item["id"], "status": "supported",
             "evidence_id": payload["evidence_items"][0]["evidence_id"], "source_id": "", "evidence_quote": "",
             "reason": "根拠に記載"} for item in payload["answer_passages"]]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims("保管期間は？", answer, EVIDENCE, settings=None, audited=audited)
    assert seen == [["記録は 5 年で破棄します。", "・承認者を選びます。"]]
    assert result["counts"] == {"supported": 3}
    assert result["claim_checks"][0]["reason"] == REUSED_CLAIM_REASON
    assert len(result["reused_passage_ids"]) == 1


def test_reuse_needs_the_evidence_to_be_reread() -> None:
    """再利用する説明の根拠が今読めない（検証に渡した根拠に無い）なら、モデルで確かめる。"""
    audited = audited_answer_items(RAG_ANSWER, ITEMS, CITATION_LINES)
    answer = "承認の記録は、承認した日から5年間保管します。"

    def parse(system, inputs, settings, schema, provider_id=None):
        payload = json.loads(inputs)
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": payload["answer_passages"][0]["id"], "status": "unsupported",
             "evidence_id": "", "source_id": "", "evidence_quote": "", "reason": "根拠に無い"}]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse) as model:
        result = validate_answer_claims("保管期間は？", answer, EVIDENCE[1:], settings=None, audited=audited)
    assert model.call_count == 1
    assert result["counts"] == {"unsupported": 1}
    assert result["reused_passage_ids"] == []
