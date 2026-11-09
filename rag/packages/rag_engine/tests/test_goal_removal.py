"""質問の目的（goal）の分類を取り除いた後の扱い（#3498）。

質問を 1 つの目的に分類せず、明示された要求の種類（手順・一覧・件数）と意味を尋ねる項目、根拠の種類で後段を
決める。どれの手がかりも無い質問に既定の目的を付けず、検索・回答・条件の読み取り・要求ごとの確認は続ける。
入力は合成の文で、外部の I/O は使わない。
"""

from unittest.mock import patch

import pytest

from rag_engine.generation import grounded
from rag_engine.retrieval.request_coverage import coverage_targets
from rag_engine.retrieval.task_contract import COUNT_REQUEST, LIST_REQUEST, STEPS_REQUEST, explicit_requests, task_contract

# どの要求の手がかりも無い合成の質問（要求は 2 件、文書の種類の手がかりを含む）。
PLAIN_QUESTION = "操作マニュアルの帳票Aについて。画面Bについて。"


@pytest.mark.parametrize(
    "question,requests,labels",
    [
        ("変更履歴を知りたい", set(), []),
        ("項目Aの件について。", set(), []),
        (PLAIN_QUESTION, set(), []),
        ("「休」の意味は？", set(), ["休"]),
        ("帳票Aを出力する方法を教えてください。", {STEPS_REQUEST}, []),
        ("項目Aを変更したい。", {STEPS_REQUEST}, []),
        ("対象は何件ありますか。", {COUNT_REQUEST}, []),
        ("対象者の一覧を出力したい。", {LIST_REQUEST}, []),
        ("「登録」列の意味と変更方法を教えてください", {STEPS_REQUEST}, ["登録"]),
        ("対象者の一覧を出力したい。何件ありますか。", {LIST_REQUEST, COUNT_REQUEST}, []),
    ],
    ids=["history", "topic-only", "two-requests", "definition", "method", "change", "count", "list", "def-and-steps", "list-and-count"],
)
def test_requests_are_explicit_and_have_no_default(question, requests, labels):
    contract = task_contract(question)
    assert "goal" not in contract
    assert explicit_requests(question) == requests
    assert [target["label"] for target in contract["definition_targets"]] == labels
    # 手順の観点は、手順の要求か一覧（件数ではない）の要求があるときだけ。
    assert ("procedure" in contract["required_aspects"]) is (STEPS_REQUEST in requests or requests == {LIST_REQUEST})


def _fake_tagger(question, span):
    return set(span.get("fake_tags", ()))


def test_pinned_evidence_follows_evidence_types_not_a_question_category():
    """定義の行は常に、操作の入口は手順・一覧・件数の要求があるときに必須根拠にする。質問の分類の既定値では選ばない。"""
    spans = [
        {"text": "項目A: 設定の状態を表す。", "evidence_id": "d", "page": 1, "fake_tags": ["definition"]},
        {"text": "画面Bで項目Aを選び、実行を押す。", "evidence_id": "o", "page": 2, "fake_tags": ["operation_entry"]},
        {"text": "補足の説明。", "evidence_id": "n", "page": 3, "fake_tags": ["completion"]},
    ]
    cases = [
        ("「項目A」の意味は？", [True, False, False]),
        ("項目Aを変更する方法は？", [True, True, False]),
        ("対象者の一覧を出力したい。", [True, True, False]),
        ("対象は何件ありますか。", [True, True, False]),
        # 要求の手がかりが無い質問は、定義の行だけ（操作の入口を既定で必須にしない）。
        ("項目Aについて。", [True, False, False]),
    ]
    for question, pinned in cases:
        with patch.object(grounded, "SPAN_TAGGERS", [_fake_tagger]):
            tagged = grounded.tag_spans(question, spans)
        assert [span["pinned"] for span in tagged] == pinned, question


def test_pinned_evidence_keeps_the_upper_bound():
    spans = [{"text": f"項目{n}: 説明。", "evidence_id": str(n), "page": n, "fake_tags": ["definition"]} for n in range(1, 8)]
    with patch.object(grounded, "SPAN_TAGGERS", [_fake_tagger]):
        tagged = grounded.tag_spans("項目について。", spans)
    assert sum(span["pinned"] for span in tagged) == grounded.MAX_PINNED


def test_per_request_coverage_targets_do_not_depend_on_a_category():
    targets = coverage_targets(PLAIN_QUESTION, lambda text: text.replace("。", " ").split())
    assert [target.id for target in targets] == ["Q1"]
