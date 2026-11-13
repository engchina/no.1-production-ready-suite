"""多段の質問の回答: 根拠の段をつないだ結論を本文に書き、根拠に無い主張は書かない (#1383)。

#1362 の評価で、台帳の行（システム → 担当部署の略号）・組織規程（略号 → 部署 → 承認者）・承認規程（役職 →
期限）が文脈にそろっているのに、回答が台帳の行を「参考情報」に置き、冒頭に結論を
書かなかった。LLM を差し替えて、その回答の形（初回の草稿が段を 1 つの item に混ぜて不支持、是正で段を分けて
支持）を決定的に再現する。
"""

from test_grounded_answer import FakeModel, audit, context, draft, ids, item, record, run

from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedItem

QUESTION = "人事給与システムの変更の承認者と、承認の期限を教えてください。"
LEDGER = "システムID: SYS-111 / 正式名: 人事給与システム / 略称・別表記: HRP / 担当部署: 労 / 重要度: A / 機密区分: 社外秘"
CODES = "略号「経」: 経理部。管理本部に属します。\n略号「労」: 労務部。管理本部に属します。\n略号「製」: 製造部。生産本部に属します。"
APPROVERS = "経理部の承認者は管理本部長です。\n労務部の承認者は労務部長です。"
DEADLINES = "部長が承認する申請は、受付から 3 営業日以内に承認します。\n本部長が承認する申請は、受付から 5 営業日以内に承認します。"
CONCLUSION = "人事給与システムの変更は労務部長が承認し、受付から 3 営業日以内に承認します。"
MERGED = "人事給与システムは労務部が担当で、労務部長が承認者です。"


def _context():
    return context(
        record(0, LEDGER, "システム台帳", page=1, source="system-ledger.xlsx"),
        record(1, CODES, "第 2 章 部署と略号", page=1, source="organization-rules.pdf"),
        record(2, APPROVERS, "第 3 章 部署の承認者", page=2, source="organization-rules.pdf"),
        record(3, DEADLINES, "承認の期限", page=1, source="approval-rules.pdf"),
    )


def _hops(by_text):
    """段ごとに 1 item（台帳の行 → 略号 → 承認者 → 期限）。"""
    return (
        item(by_text[LEDGER], "人事給与システムの担当部署は略号「労」です。", LEDGER, kind="rule", request_id="Q1"),
        item(by_text[CODES], "略号「労」は労務部です。", "略号「労」: 労務部。", kind="rule", request_id="Q1"),
        item(by_text[APPROVERS], "労務部の承認者は労務部長です。", "労務部の承認者は労務部長です。", kind="rule", request_id="Q1"),
        item(
            by_text[DEADLINES],
            "部長が承認する申請は、受付から 3 営業日以内に承認します。",
            "部長が承認する申請は、受付から 3 営業日以内に承認します。",
            kind="rule",
            request_id="Q1",
        ),
    )


def _supported(count, **extra):
    return audit(*[(index, "supported", "matched", "") for index in range(count)], requests=[("Q1", "addressed")], **extra)


def test_conclusion_chained_from_split_hops_is_written_and_the_ledger_row_is_not_left_unverified():
    """段を分けた item がすべて支持されれば、つないだ結論を冒頭に書き、台帳の行も資料により確認できた事項に出す。"""
    ctx = _context()
    by_text = ids(ctx, QUESTION)
    model = FakeModel([draft(*_hops(by_text), summary=CONCLUSION)], [_supported(4)])

    text = run(model, ctx, QUESTION).response.answer_text

    assert text.startswith(CONCLUSION)
    assert grounded.answer_labels().quote_only_section not in text
    assert grounded.answer_labels().quote_only_note not in text
    assert "人事給与システムの担当部署は略号「労」です。" in text


def test_invalid_table_citation_does_not_get_a_guessed_replacement_in_feedback():
    """不正範囲は未確認のまま残し、別の行を写し直す候補を作らない。"""
    ctx = _context()
    by_text = ids(ctx, QUESTION)
    # 説明が写し誤った文の語（管理本部）を使うので、逐語の先頭の文にも結び付けない。
    miscopied = item(
        by_text[CODES],
        "略号「製」は製造部で、管理本部に属します。",
        "略号「製」: 製造部。管理本部に属します。",
        kind="rule",
        request_id="Q1",
    )
    model = FakeModel([draft(miscopied, *_hops(by_text)[1:], summary=CONCLUSION)], [_supported(3)])

    result = run(model, ctx, QUESTION).response

    dropped = result.generation_trace["rounds"][0]["dropped"]
    assert "miscopied" not in dropped[0]
    assert "nearest_quote" not in dropped[0]
    assert dropped[0]["citation_resolution"]["reason_code"] == "citation_range_out_of_bounds"
    assert dropped[0]["citation_resolution"]["requested_quote"] == miscopied["citations"][0]["quote"]


def test_generation_asks_for_every_supporting_document_and_keeps_scope_checks():
    """同じ内容を述べた規則の原文と手順書の両方を出典にし、出典を 1 つに絞る言い方を残さない（#3526）。"""
    from rag_engine.models.llm import RequestCoverageRange, RequestEvidenceScopeRange

    generate = grounded.generation_system_prompt()
    section = generate.split("本文を完成させて引用を選ぶ", 1)[1].split("## Step ", 1)[0]
    assert "文書ごとに引用する" in section
    # 別の対象の根拠を basis に選ばない判断は basis の選び方に置く（生成の出力に計画の行は無い。#4611）。
    assert "無関係な原文を引用しない" in section
    assert "言い換えていても省かず" in section
    assert "説明の内容を支えない原文は出典にしない" in section
    # 1つの説明を実際の引用集合で共同支持し、未引用の資料で不足を埋めない。
    citations = GroundedItem.model_json_schema()["properties"]["citations"]["description"]
    assert "指定した出典の原文の集合で text 全体を支える" in citations
    assert "共同して支えてよく" in citations
    assert "各出典が text 全体を単独で支える必要はない" in citations
    assert "実際に指定していない原文で不足を補わない" in citations
    assert "重複する item をまとめ" not in generate
    assert "同じ根拠の同じ説明をまとめ" in generate
    relation = RequestEvidenceScopeRange.model_json_schema()["properties"]["relation"]["description"]
    plan_ids = RequestCoverageRange.model_json_schema()["properties"]["evidence_scope"]["description"]
    assert "直接答える" not in relation + plan_ids
    # 計画だけを立てる経路（answer.scope）の、別の対象の根拠を外す判断と、計画の根拠だけで items を作る契約は残す。
    assert "unrelated=別の対象・独立した操作" in relation
    assert "文書ごとに行にする" in plan_ids
    assert "items はこの計画の根拠だけで作る" in plan_ids
