"""質問の目的が未判定（unknown）の扱い（#3498）。

どの判定の条件にも一致しない質問は unknown にし、規則・定義（rule）や操作方法（procedure）と同一視しない。
未判定でも検索・回答・条件の読み取り・要求ごとの確認は続ける。入力は合成の文で、外部の I/O は使わない。
"""

import unittest
from unittest.mock import patch

import pytest
from test_answer_generation import _answer_output, _chunk_run_stub, _hybrid_result, _stored_child

from rag_engine.config import get_settings
from rag_engine.generation import grounded
from rag_engine.generation.answering import SIMPLE_RETRIEVAL_LABEL, STANDARD_ANSWER_FLOW_LABEL, answer_question_result
from rag_engine.retrieval.request_coverage import coverage_targets
from rag_engine.retrieval.task_contract import GOAL_UNKNOWN, task_contract

# どの判定の条件にも一致しない合成の質問（要求は 2 件、文書の種類の手がかりを含む）。
UNKNOWN_QUESTION = "操作マニュアルの帳票Aについて。画面Bについて。"


@pytest.mark.parametrize(
    "question,expected",
    [
        ("変更履歴を知りたい", GOAL_UNKNOWN),
        ("項目Aの件について。", GOAL_UNKNOWN),
        (UNKNOWN_QUESTION, GOAL_UNKNOWN),
        ("「休」の意味は？", "rule"),
        ("帳票Aを出力する方法を教えてください。", "procedure"),
        ("項目Aを変更したい。", "procedure"),
        ("対象は何件ありますか。", "count"),
        ("対象者の一覧を出力したい。", "recipient_list"),
    ],
    ids=["history", "topic-only", "two-requests", "definition", "method", "change", "count", "list"],
)
def test_goal_is_unknown_only_when_no_rule_matches(question, expected):
    contract = task_contract(question)
    assert contract["goal"] == expected
    # 未判定は操作方法の観点を足さない（操作方法と同一視しない）。
    assert ("procedure" in contract["required_aspects"]) is (expected in {"procedure", "recipient_list"})


def _fake_tagger(question, span):
    return set(span.get("fake_tags", ()))


@pytest.mark.parametrize(
    "goal,pinned",
    [
        ("rule", ["E1"]),
        ("procedure", ["E2"]),
        ("count", ["E2"]),
        (GOAL_UNKNOWN, []),
    ],
    ids=["rule", "procedure", "count", "unknown"],
)
def test_unknown_goal_does_not_pin_definition_or_operation_entry(goal, pinned):
    """未判定は、目的の既定値だけで定義・操作の入口のどちらも必須根拠にしない。"""
    spans = [
        {"text": "項目A: 設定の状態を表す。", "evidence_id": "d", "page": 1, "fake_tags": ["definition"]},
        {"text": "画面Bで項目Aを選び、実行を押す。", "evidence_id": "o", "page": 2, "fake_tags": ["operation_entry"]},
    ]
    with (
        patch.object(grounded, "task_contract", return_value={"goal": goal}),
        patch.object(grounded, "SPAN_TAGGERS", [_fake_tagger]),
    ):
        tagged = grounded.tag_spans("合成の質問", spans)
    assert [span["label"] for span in tagged if span["pinned"]] == pinned
    # タグ自体は残す（根拠としては使える）。
    assert [span["tags"] for span in tagged] == [["definition"], ["operation_entry"]]


def test_unknown_goal_reaches_generation_and_audit_context():
    """生成・監査へ渡す質問の情報でも未判定を保ち、要求単位も落とさない。"""
    view = grounded._contract_view(UNKNOWN_QUESTION)
    assert view["goal"] == GOAL_UNKNOWN
    assert [unit["id"] for unit in view["request_units"]] == ["Q1", "Q2"]
    assert "goal が unknown なら current_request だけで判断する" in grounded.AUDIT_SYSTEM_PROMPT


def test_unknown_goal_keeps_per_request_coverage_targets():
    targets = coverage_targets(UNKNOWN_QUESTION, lambda text: text.replace("。", " ").split())
    assert [target.id for target in targets] == ["Q1", "Q2"]


class UnknownGoalAnswerFlowTests(unittest.TestCase):
    def setUp(self):
        from grounded_stub import stub_generation

        stub_generation(self)

    def test_unknown_goal_continues_search_and_answer_and_shows_undetermined(self):
        search_result = _hybrid_result(_stored_child("chunk-a", 1, "帳票Aは画面Bから出力します。"))

        with (
            patch("rag_engine.generation.answering.load_latest_or_source_chunk_run", return_value=_chunk_run_stub()),
            patch("rag_engine.generation.answering.check_adb_hybrid_search_ready"),
            patch("rag_engine.generation.answering.search_adb_hybrid_chunks", return_value=search_result) as search,
            patch(
                "rag_engine.generation.answering.parse_text_response",
                return_value=_answer_output(answer="帳票Aは画面Bから出力します。"),
            ),
        ):
            result = answer_question_result(
                UNKNOWN_QUESTION,
                "abcdef",
                ["docling"],
                get_settings(),
                query_strategy=SIMPLE_RETRIEVAL_LABEL,
                answer_flow=STANDARD_ANSWER_FLOW_LABEL,
            )

        # 検索は止めず、独立して読み取った条件（文書の種類の絞り込み）を渡す。
        search.assert_called()
        conditions = search.call_args.kwargs["inquiry_conditions"]
        self.assertTrue(conditions.metadata_filter.active)
        self.assertTrue(conditions.document_kinds)
        self.assertTrue(result.answer.startswith("帳票Aは画面Bから出力します。"))
        self.assertEqual(result.task_contract["goal"], GOAL_UNKNOWN)
        display = result.question_display
        self.assertIn("結果: 質問の目的は「未判定」（目的の分類に当てはまらないため、目的を決めずに検索・回答します）", display)
        self.assertIn("答える要求は 2 件", display)
        self.assertIn("回答後、要求ごとに答えたかを確認します", display)
        self.assertIn("絞り込みに合う文書だけを検索します", display)
        # 内部の値・確定したような目的の名前を表示に出さない。
        self.assertNotIn(GOAL_UNKNOWN, display)
        self.assertNotIn("規則・定義・可否", display)
