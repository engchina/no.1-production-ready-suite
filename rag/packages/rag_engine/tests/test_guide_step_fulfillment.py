"""業務ガイドの手順ごとの充足（#2491）を、合成の資料と決定論の LLM の差し替えで確かめる。

手順 A → B のガイドで、手順 B の根拠を回答の材料に取れているのに、B の説明が「参考情報」（原文のみ提示）に
留まった回答を、全手順を充足したとして確定しない。根拠が材料にあれば B を対象に作り直し、直らなければ
本文の不足と回答の構造の要求（`guide:<手順 id>`）に示す。
"""

import json
import unittest
from dataclasses import replace
from pathlib import Path

from test_grounded_answer import context, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answering import _grounded_spans, synthesize_grounded_answer
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext
from rag_engine.retrieval.guide_steps import GuidePlan

QUESTION = "設定 A を変更する手順を教えてください"
STEP_A = "設定画面で設定 A の値を入力します。"
STEP_B = "入力したあとは、反映画面で反映ボタンを押して変更を確定します。"
STEP_C = "種類が乙の場合は、承認画面で承認を依頼します。"


def _record(index, text, function, uid):
    return replace(record(index, text, function, page=10 + index, source="操作説明.pdf"), chunk_uid=uid)


def _context(*records):
    return context(*records)


def _knowledge(guide_steps, optional=(), terms=()):
    return RuntimeKnowledgeContext(
        original_question=QUESTION,
        expanded_question=QUESTION,
        path=Path("rk.json"),
        guide_steps=guide_steps,
        guide_optional_steps=tuple(optional),
        guide_step_terms=tuple(terms),
    )


def _run(model, ctx, knowledge, question=QUESTION):
    def unused(*a, **k):
        return (_ for _ in ()).throw(AssertionError("unexpected I/O"))

    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        return synthesize_grounded_answer(question, ctx, get_settings(), image_prompt_mode="text_only", runtime_knowledge=knowledge)


def _ids(ctx, knowledge, question=QUESTION):
    return {span["text"]: span["evidence_id"] for span in _grounded_spans(question, ctx, (), knowledge)}


def _drafts(model):
    return [prompt for name, prompt in model.prompts if name.endswith("GroundedDraftWire")]


def _audits(model):
    return [json.loads(prompt) for name, prompt in model.prompts if name == "GroundedAudit"]


def _requests(response):
    return {entry["id"]: entry["status"] for entry in response.envelope["requests"]}


GUIDE = (("s1", "設定 A を入力する", ("run:a",)), ("s2", "反映ボタンで確定する", ("run:b",)))


def _ab_context():
    return _context(_record(0, STEP_A, "設定画面", "run:a"), _record(1, STEP_B, "反映画面", "run:b"))


# 画面の図の項目の種類で分かれる業務ガイド（#2689）。共通の画面の確認 → 選択の項目ならコードの登録・自由入力なら画面で
# 書き換え・種類が分からなければ確認先へ問い合わせる。合成の項目名と手順だけを使う。
KIND_QUESTION = "区分コードの項目の選択肢を追加したい"
KIND_SCREEN = "設定画面で区分コードの項目を開きます。"
KIND_CODE = "コード登録画面で選択肢のコードを追加します。"
KIND_ASK = "項目の種類が分からない場合は、管理担当に確認します。"
KIND_GUIDE = (
    ("screen", "画面で項目を確かめる", ("run:screen",)),
    ("code", "コードの登録で選択肢を追加する", ("run:code",)),
    ("ask", "種類が分からなければ確認する", ("run:ask",)),
)
# backend が照合で分からなかった条件（選択肢と言い換え）と、決まった値ごとの手順の計画として渡す形。
KIND_CONDITIONS = (("field_kind", "項目の種類", (("選択の項目", ("選択",)), ("入力の項目", ("自由入力",)))),)
KIND_PLANS = (
    ((("field_kind", "選択の項目"),), (("screen", True), ("code", True)), "guide-g1", "分かっている条件: 項目の種類=選択の項目"),
    ((("field_kind", "入力の項目"),), (("screen", True), ("edit", True)), "guide-g1", "分かっている条件: 項目の種類=入力の項目"),
)


# 2 つの手順が共有する根拠の chunk（#2649）。手順 1 の主の操作・手順 2 の主の操作・両手順に共通の末尾の操作。
SHARED_A = "設定画面で設定 A の値を入力します。"
SHARED_B = "反映画面で反映ボタンを押して変更を反映します。"
SHARED_END = "最後に保存ボタンを押して画面を閉じます。"
SHARED = SHARED_A + SHARED_B + SHARED_END
SHARED_GUIDE = (("s1", "設定 A を入力する", ("run:s",)), ("s2", "反映ボタンで反映する", ("run:s",)))
SHARED_TERMS = (("s1", ("設定画面",)), ("s2", ("反映ボタン",)))


class StubEmbedder:
    """決定論の embedding（#2649）。文に含まれる鍵で合成の vector を返し、呼び出しの入力を記録する。"""

    def __init__(self, table, *, fail=False):
        self.table = table
        self.fail = fail
        self.calls = []

    def embed(self, texts, *, query=False):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("embedding unavailable")
        return [self._vector(text) for text in texts]

    def _vector(self, text):
        for key, vector in self.table:
            if key in text:
                return list(vector)
        raise AssertionError(f"unexpected text: {text}")


# 手順の記述と根拠の文の合成の vector。手順 1 の主の操作・手順 2 の主の操作は各手順に近く、共通の末尾の操作は
# 両手順に同じくらい近い。
SENTENCE_VECTORS = (("設定 A の値", (1.0, 0.0, 0.3)), ("反映ボタンを押", (0.0, 1.0, 0.3)), ("保存ボタンを押", (0.5, 0.5, 1.0)))
# 手順の題名・手がかりと根拠の言い回しが違う手順（手順の語が根拠の文に無い。手順 2 の手がかりは共通の文にだけ当たる）。
REWORDED_GUIDE = (("s1", "値の登録", ("run:s",)), ("s2", "内容の確定", ("run:s",)))
REWORDED_VECTORS = (("値の登録", (1.0, 0.0, 0.0)), ("内容の確定", (0.0, 1.0, 0.0)), *SENTENCE_VECTORS)
# 手順 2 の語（ボタン）を主の操作の文と共通の文が同じ数だけ含む手順。
TIED_GUIDE = (("s1", "設定 A を入力する", ("run:s",)), ("s2", "ボタンを押す", ("run:s",)))
TIED_VECTORS = (("設定 A を入力", (1.0, 0.0, 0.0)), ("ボタンを押す", (0.0, 1.0, 0.0)), *SENTENCE_VECTORS)


# 言い回しの違う手順の根拠の章節（#2685）。手順 1・2 の主の操作の文、章節全体に掛かる共通の文、末尾の共通の操作、
# 別の操作の文が 1 つの chunk に並ぶ。手順 2 の手がかりは共通の文の 1 語（「必要」）にだけ当たり、手順 1 の語は根拠に無い。
SECTION_COMMON = "全体で必要な項目をすべて確認します。"
SECTION = SHARED_A + SHARED_B + SECTION_COMMON + SHARED_END + STEP_C
SECTION_TERMS = (("s2", ("必要な手続",)),)
# 合成の vector（成分: 章節の話題・手順 1 の操作・手順 2 の操作・確認の一般の語・保存・承認・手順 2 の主の操作だけの内容）。
# 共通の文は話題と一般の語を多く持つので、手順 2 の記述に主の操作の文より近く、手順 1 との差も大きい（絶対の近さで
# 選ぶと主の操作の文になる）。末尾の共通の操作は両手順に同じくらい近い。
SECTION_DESCRIPTIONS = (("値の登録", (0.6, 0.6, 0.0, 0.3, 0.0, 0.0, 0.0)), ("内容の確定", (0.6, 0.0, 0.5, 0.5, 0.0, 0.0, 0.0)))
SECTION_SENTENCES = (
    ("設定 A の値", (0.6, 0.7, 0.0, 0.1, 0.0, 0.0, 0.0)),
    ("反映ボタンを押", (0.5, 0.0, 0.45, 0.1, 0.0, 0.0, 0.5)),
    ("全体で必要な項目", (0.8, 0.0, 0.15, 0.6, 0.0, 0.0, 0.0)),
    ("保存ボタンを押", (0.7, 0.0, 0.0, 0.2, 0.6, 0.0, 0.0)),
    ("承認を依頼", (0.6, 0.0, 0.0, 0.0, 0.0, 0.7, 0.0)),
)
SECTION_VECTORS = (*SECTION_DESCRIPTIONS, *SECTION_SENTENCES)
# 公開した item（引用と原文のみ提示か）と、正しい手順の状態（Issue #2685 の表の 4 行と、末尾の共通の操作だけ）。
SECTION_ROWS = (
    ("共通の文だけ", ((SECTION_COMMON, False),), {"s1": "unmatched", "s2": "unmatched"}),
    ("手順 1 の主の操作と共通の文", ((SHARED_A, False), (SECTION_COMMON, False)), {"s1": "matched", "s2": "unmatched"}),
    ("手順 1・2 の主の操作", ((SHARED_A, False), (SHARED_B, False)), {"s1": "matched", "s2": "matched"}),
    ("手順 2 の主の操作の原文のみ提示と共通の文", ((SHARED_B, True), (SECTION_COMMON, False)), {"s1": "unmatched", "s2": "reference_only"}),
    ("末尾の共通の操作だけ", ((SHARED_END, False),), {"s1": "unmatched", "s2": "unmatched"}),
)


class GuidePlanRequiredTest(unittest.TestCase):
    def test_required_flag_from_payload(self):
        plan = GuidePlan.from_payload(
            "g",
            1,
            [{"id": "s1", "title": "A"}, {"id": "s2", "title": "B", "required": False}, {"id": "s3", "title": "C", "required": True}],
        )
        assert plan is not None
        self.assertEqual([step.required for step in plan.steps], [True, False, True])
        self.assertEqual(plan.optional_step_ids, ("s2",))

    def test_step_terms_from_payload(self):
        plan = GuidePlan.from_payload("g", 1, [{"id": "s1", "title": "A", "retrieval_hints": ["x", " ", "y"]}, {"id": "s2", "title": "B"}])
        assert plan is not None
        self.assertEqual(plan.step_terms, (("s1", ("x", "y")),))


if __name__ == "__main__":
    unittest.main()
