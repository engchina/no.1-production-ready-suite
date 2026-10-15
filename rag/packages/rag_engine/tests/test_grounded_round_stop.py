"""是正の round の「改善なし」の打ち切り（#1527）を、LLM を差し替えて確かめる。

作り直した組が採用されない・指摘が前の組と同じ・残った指摘が根拠の不足（草稿が「資料に記載が無い」と答えた
要求）だけのときは、次の組を始めない。写し誤り・降格・引用なしの指摘があるときは、同じ根拠のまま作り直す。
"""

import unittest
from dataclasses import replace
from types import SimpleNamespace

from test_grounded_answer import LIST_TEXT, FakeModel, audit, context, draft, ids, item, record, run

from rag_engine.generation import answering, grounded
from rag_engine.models.llm import GroundedItem

GOOD = "一覧表を選択し、処理を選択します。"


def _drafts(model):
    return [prompt for name, prompt in model.prompts if name == "GroundedDraft"]


def _gap(request_id):
    return {"kind": "gap", "text": "資料に抽出条件の記載がありません。", "evidence_id": "", "quote": "", "request_id": request_id}


def _adopted(*kinds_and_requests):
    """``evidence_shortage_only`` に渡す採用済みの組（説明の kind と request_id だけを持つ）。"""
    return SimpleNamespace(
        checked=[SimpleNamespace(item=GroundedItem(kind=kind, text="t", request_id=request_id)) for kind, request_id in kinds_and_requests]
    )


class NoImprovementUnitTest(unittest.TestCase):
    def test_signature_ignores_the_free_text_of_the_audit(self):
        first = {
            "unanswered_requests": [{"request_id": "Q1", "status": "partial", "reason": "一部だけ"}],
            "downgraded_items": [{"text": "a", "reasons": ["x"]}],
            "goal_alignment": "partial",
        }
        second = {
            "unanswered_requests": [{"request_id": "Q1", "status": "partial", "reason": "言い換えた理由"}],
            "downgraded_items": [{"text": "b", "reasons": ["y"]}],
            "goal_alignment": "partial",
            "supported_items": [{"text": "c"}],
        }
        self.assertEqual(grounded.feedback_signature(first), grounded.feedback_signature(second))
        third = {**second, "unanswered_requests": [{"request_id": "Q2", "status": "partial"}]}
        self.assertNotEqual(grounded.feedback_signature(first), grounded.feedback_signature(third))

    def test_no_improvement_reason(self):
        same = {"dropped_items": [{"reason": "r"}]}
        # 1 組目の後は是正をまだ試していないので止めない。
        self.assertEqual(grounded.no_improvement_reason(0, accepted=True, feedback=same, previous=None), "")
        self.assertEqual(
            grounded.no_improvement_reason(1, accepted=False, feedback=same, previous=same), grounded.NO_IMPROVEMENT_NOT_ACCEPTED
        )
        self.assertEqual(
            grounded.no_improvement_reason(1, accepted=True, feedback=same, previous=same), grounded.NO_IMPROVEMENT_SAME_FEEDBACK
        )
        # 指摘が変われば作り直す（降格だけが残ったときは #1912 の打ち切り。test_answer_round_latency）。
        changed = {"unused_evidence_ids": [{"evidence_id": "e"}]}
        self.assertEqual(grounded.no_improvement_reason(1, accepted=True, feedback=changed, previous=same), "")

    def test_evidence_shortage_only(self):
        unanswered = {"unanswered_requests": [{"request_id": "Q2", "status": "missing"}], "goal_alignment": "partial"}
        # 草稿が Q2 を「資料に記載が無い」と答えた: 同じ根拠では直らない。
        self.assertTrue(grounded.evidence_shortage_only(unanswered, _adopted(("operation", "Q1"), ("gap", "Q2"))))
        # 草稿が答えようとして監査が不足とした要求は、指摘を渡せば同じ根拠で答えられることがある（#621）。
        self.assertFalse(grounded.evidence_shortage_only(unanswered, _adopted(("operation", "Q2"))))
        # 写し誤り・降格・引用なし・使われていない根拠があれば作り直す。
        for key, value in (
            ("dropped_items", [{}]),
            ("downgraded_items", [{}]),
            ("no_cited_evidence", "x"),
            ("unused_evidence_ids", [{"evidence_id": "e"}]),
        ):
            with self.subTest(key=key):
                self.assertFalse(grounded.evidence_shortage_only({**unanswered, key: value}, _adopted(("gap", "Q2"))))
        # 答えていない要求が無い（目的のずれだけ）なら作り直す。
        self.assertFalse(grounded.evidence_shortage_only({"goal_alignment": "partial"}, _adopted(("gap", "Q2"))))

    def test_every_stop_reason_has_a_step_text(self):
        self.assertEqual(set(answering._NO_IMPROVEMENT_RESULTS), set(grounded.NO_IMPROVEMENT_REASONS))


class SynthesizeRoundStopTest(unittest.TestCase):
    def test_context_round_that_is_not_adopted_stops_the_correction(self):
        """取得済みの資料を足した組が採用されなければ、同じ指摘での作り直し（3 組目）を始めない。"""
        shown = replace(record(0, "●集計表出力\n一覧表を選択します。", "売上-(3)年次集計明細作成"), chunk_uid="run:r0")
        hidden = replace(record(1, "抽出条件で店舗を指定します。", "売上-(3)年次集計明細作成", page=21), chunk_uid="run:r1", seq_no=2)
        ctx = replace(context(shown), expansion_records=(shown, hidden))
        eid = ids(ctx)[shown.text]
        first = draft(item(eid, "一覧表を選択します。", "一覧表を選択します。", request_id="Q1"))
        model = FakeModel([first], [audit((0, "supported", "matched", ""), requests=[("Q1", "partial")])])
        result = run(model, ctx).response
        rounds = result.generation_trace["rounds"]
        self.assertEqual(len(_drafts(model)), 2)
        self.assertEqual([r["accepted"] for r in rounds], [True, False])
        self.assertEqual(rounds[0]["remedy"]["remedy"], "context")
        self.assertEqual(rounds[1]["improvement_stop"], grounded.NO_IMPROVEMENT_NOT_ACCEPTED)
        budget = result.generation_trace["round_budget"]
        self.assertEqual(
            budget["stop"], {"reason": grounded.NO_IMPROVEMENT_NOT_ACCEPTED, "round": 3, "feedback": ["答えていない要求 1 件"]}
        )
        self.assertEqual([note["accepted"] for note in budget["rounds"]], [True, False])
        # 時間の上限ではないので「回答に未反映の関連章節」に作り直しの打ち切りを出さない。
        self.assertNotIn("作り直しをしていません", result.answer_text)
        self.assertIn("一覧表を選択します。", result.answer_text)

    def test_gap_only_feedback_does_not_regenerate_with_the_same_evidence(self):
        """草稿が「資料に記載が無い」と答えた要求だけが残れば、指摘の反映の組を始めない。"""
        ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
        eid = ids(ctx)[LIST_TEXT]
        first = draft(item(eid, GOOD, GOOD, request_id="Q1"), _gap("Q2"))
        model = FakeModel(
            [first],
            [audit((0, "supported", "matched", ""), (1, "supported", "matched", ""), requests=[("Q1", "addressed"), ("Q2", "missing")])],
        )
        result = run(model, ctx, question="明細一覧表を出力する手順と、店舗の抽出条件を教えてください").response
        self.assertEqual(len(_drafts(model)), 1)
        rounds = result.generation_trace["rounds"]
        self.assertEqual(rounds[0]["improvement_stop"], grounded.NO_IMPROVEMENT_EVIDENCE_ONLY)
        self.assertNotIn("remedy", rounds[0])
        self.assertEqual(result.generation_trace["round_budget"]["stop"]["reason"], grounded.NO_IMPROVEMENT_EVIDENCE_ONLY)
        self.assertIn(GOOD, result.answer_text)

    def test_correction_still_runs_for_an_unanswered_request_the_draft_tried(self):
        """草稿が答えようとして不足とされた要求は、従来どおり指摘を渡して作り直す（#621 の型）。"""
        ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
        eid = ids(ctx)[LIST_TEXT]
        select = item(eid, GOOD, GOOD, request_id="Q1")
        execute = item(eid, "実行ボタンを押して完了します。", "実行ボタンを押して完了します。", request_id="Q1")
        model = FakeModel(
            [draft(select), draft(select, execute)],
            [
                audit((0, "supported", "matched", ""), requests=[("Q1", "partial")]),
                audit((0, "supported", "matched", ""), (1, "supported", "matched", ""), requests=[("Q1", "addressed")]),
            ],
        )
        result = run(model, ctx).response
        self.assertEqual([r["accepted"] for r in result.generation_trace["rounds"]], [True, True])
        self.assertNotIn("stop", result.generation_trace["round_budget"])
        self.assertIn("実行ボタンを押して完了します。", result.answer_text)

    def test_dropped_quote_still_regenerates(self):
        """写し誤り（原文と一致せず除いた説明）があれば、同じ根拠のまま作り直す。"""
        ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
        eid = ids(ctx)[LIST_TEXT]
        wrong = draft(item(eid, GOOD, GOOD), item(eid, "CSVを保存先へ出力します。", "CSVを C:\\temp へ出力します。"))
        fixed = draft(item(eid, GOOD, GOOD), item(eid, "実行ボタンを押して完了します。", "実行ボタンを押して完了します。"))
        model = FakeModel([wrong, fixed], [audit((0, "supported", "matched", ""), (1, "supported", "matched", ""))])
        result = run(model, ctx).response
        self.assertEqual(len(_drafts(model)), 2)
        self.assertEqual(result.generation_trace["rounds"][0]["remedy"], {"remedy": "correction"})
        self.assertIn("実行ボタンを押して完了します。", result.answer_text)


if __name__ == "__main__":
    unittest.main()
