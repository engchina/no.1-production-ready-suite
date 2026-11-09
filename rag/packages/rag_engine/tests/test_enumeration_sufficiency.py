"""列挙・網羅の問の十分性（#1503）。

CRAG の評価器が「足りる」と判定しても、列挙の問は件数・章の範囲の欠けをプログラムで数え、件数・範囲の無い網羅の問は
読む範囲（当たった章節・文書全体）まで章節を広げてから判定する。評価器と章節の読み込みはスタブで、LLM と DB を呼ばない。
"""

from __future__ import annotations

import pytest

from rag_engine.retrieval.enumeration import ENUMERATION_ASPECT, enumeration_request
from rag_engine.retrieval.task_contract import task_contract

# s1437 の評価の問（サーバー室の定期点検要領）と、評価セットの多段・業務支援の問の例。
BUSINESS_DAY_FIVE = "サーバー室の定期点検要領の全体を通して、「営業日」で期限が決められている作業を 5 つすべて挙げてください。"
SHEET_COLUMNS = "サーバー室の定期点検要領の第 2 章から第 6 章までの各章で、点検表の欄に書くことを章ごとにすべて挙げてください。"
STOP_SIX = (
    "サーバー室の定期点検要領の第 4 章には、点検を中止する条件が 6 つ書かれています。"
    "6 つすべてと、それぞれ点検表に書くことを挙げてください。"
)
DO_NOT_TOUCH = "サーバー室の定期点検要領で、作業者が触れない・しないと決められていることをすべて挙げてください。"
PREPARATION = "サーバー室の定期点検の前に行う準備をすべて挙げてください。"


@pytest.mark.parametrize(
    "question, scope, count, chapters, minimum, max_items",
    [
        (BUSINESS_DAY_FIVE, "document", 5, [], None, 7),
        # 2 つ以上の章の範囲は 1 つの章節に入らないので、文書全体まで読んでから判定する。
        (SHEET_COLUMNS, "document", None, [2, 3, 4, 5, 6], "document", 10),
        (STOP_SIX, "section", 6, [], None, 8),
        (DO_NOT_TOUCH, "document", None, [], "document", 5),
        (PREPARATION, "section", None, [], "leaf", 5),
        ("重要度が A で、機密区分が極秘のシステムをすべて挙げてください。", "section", None, [], "leaf", 5),
        ("承認の条件を三つ、漏れなく教えてください。", "section", 3, [], None, 5),
    ],
    ids=["count-document", "chapter-range", "count-section", "open-document", "open-section", "open-table", "kanji-count"],
)
def test_detects_enumeration_requests(question, scope, count, chapters, minimum, max_items):
    request = enumeration_request(question)
    assert request is not None
    assert request.to_payload() == {
        "scope": scope,
        "expected_count": count,
        "chapters": chapters,
        "minimum_scope": minimum,
        "max_items": max_items,
    }
    contract = task_contract(question)
    assert ENUMERATION_ASPECT in contract["required_aspects"]
    assert contract["enumeration"] == request.to_payload()


@pytest.mark.parametrize(
    "question",
    [
        "経費精算ポータルの変更の申請は、誰が承認し、受付から何営業日以内に承認されますか？",
        "承認の記録は何年間保管しますか？",
        "人事評価システムと勤怠管理システムでは、どちらのデータを長く保管しますか？",
        "すべての部署に適用されますか？",
        "第3章の設備ごとの点検の項目を教えて",
        "障害の最初の連絡で、電話がつながらないときはどうしますか？",
    ],
    ids=["bridge", "single-hop", "comparison", "quantifier-only", "no-exhaustive-word", "procedure"],
)
def test_other_questions_are_not_enumerations(question):
    assert enumeration_request(question) is None
    contract = task_contract(question)
    assert contract["enumeration"] is None and ENUMERATION_ASPECT not in contract["required_aspects"]
