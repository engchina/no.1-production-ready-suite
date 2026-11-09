"""確認の項目を、モデルの自己申告の分類ではなく観察できる事実と規則で分ける (#1492)。

評価 e1467 で、手順を答えたうえで「利用者がロールを持つか」「付与先は個別とグループのどちらか」を「実データ」に
挙げ、回答が needs_environment_data になっていた。承認済みの条件（conditions）を聞き直し、廃止済みの第 1 版の
「適用されている版」も実データとして聞いていた。モデルには項目ごとに誰が答えられるか（who_can_answer）・すでに
分かっているか（already_known）・結論がその値で分かれるか（decides）だけを書かせ、対応は規則で決める。
LLM は差し替える。
"""

import itertools
import unittest
from dataclasses import replace

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answer_envelope import (
    EXCLUDED_ALREADY_KNOWN,
    EXCLUDED_KNOWN_CONDITION,
    EXCLUDED_NOT_DECISIVE,
    confirmation_item_facts,
    requires_environment_data,
    settle_confirmation_items,
)
from rag_engine.generation.answer_models import AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer

ROLE = "操作する利用者が「ポータル管理者」ロールを持つか（自分で操作するか、依頼するかを決める）"
APPROVAL = "削除対象アカウントの所属部署の部門長の承認が得られているか（承認の無い削除を行わないため）"
AUTH_LOG = "エラー発生時刻の認証ログ（auth.log）の該当行（原因 A と原因 B のどちらかを確定するため）"
CODE_TABLE = "「支払区分コード表」の区分の定義（区分の意味を確かめる）"


def fact(text, who, *, known=False, decides=True):
    return {"text": text, "who_can_answer": who, "already_known": known, "decides": decides}


class SettleConfirmationItemsTest(unittest.TestCase):
    def test_every_combination_of_facts(self):
        # 分かっている項目と結論を分けない項目は数えず、残りは誰が答えられるかだけで分ける。
        for who, known, decides in itertools.product(("user", "system", "document"), (False, True), (False, True)):
            with self.subTest(who=who, known=known, decides=decides):
                settled = settle_confirmation_items([fact("確かめる点", who, known=known, decides=decides)])
                groups = {"user": settled.user, "system": settled.system, "document": settled.document}
                if known or not decides:
                    self.assertEqual(groups, {"user": (), "system": (), "document": ()})
                    reason = EXCLUDED_ALREADY_KNOWN if known else EXCLUDED_NOT_DECISIVE
                    self.assertEqual(settled.excluded, ({"text": "確かめる点", "who_can_answer": who, "reason": reason},))
                else:
                    self.assertEqual(groups[who], ("確かめる点",))
                    self.assertEqual(sum(len(value) for value in groups.values()), 1)
                    self.assertEqual(settled.excluded, ())

    def test_missing_facts_are_filled_conservatively(self):
        # 文字列だけ・#1453 の種類（kind）だけの項目: 種類から写し、無ければシステムの値。未確認・結論を分ける。
        self.assertEqual(
            confirmation_item_facts("ログ"), {"text": "ログ", "who_can_answer": "system", "already_known": False, "decides": True}
        )
        self.assertEqual(confirmation_item_facts({"text": "ロール", "kind": "user_condition"})["who_can_answer"], "user")
        self.assertEqual(confirmation_item_facts({"text": "ログ", "kind": "environment"})["who_can_answer"], "system")
        self.assertEqual(confirmation_item_facts({"text": "x"})["who_can_answer"], "system")

    def test_known_conditions_remove_the_items_that_ask_them_again(self):
        # mt-delete-account-approved: conditions={approved: はい}（業務ガイドの条件「部門長の承認」）を聞き直さない。
        items = [fact(APPROVAL, "user"), fact(ROLE, "user")]
        settled = settle_confirmation_items(items, known_conditions=[("部門長の承認", "はい")])
        self.assertEqual(settled.user, (ROLE,))
        self.assertEqual([entry["reason"] for entry in settled.excluded], [EXCLUDED_KNOWN_CONDITION])
        # 値の無い条件・1 文字の名前では外さない。
        self.assertEqual(settle_confirmation_items(items, known_conditions=[("部門長の承認", " ")]).user, (APPROVAL, ROLE))
        self.assertEqual(settle_confirmation_items(items, known_conditions=[("承", "はい")]).user, (APPROVAL, ROLE))

    def test_requires_environment_data_follows_the_settled_items(self):
        system = settle_confirmation_items([fact(AUTH_LOG, "system")])
        self.assertTrue(requires_environment_data(system, items=[fact(AUTH_LOG, "system")], external_data_required=False))
        # 項目を挙げてすべて外れたら、モデルが印を立てても要らない。
        known = [fact(AUTH_LOG, "system", known=True)]
        self.assertFalse(requires_environment_data(settle_confirmation_items(known), items=known, external_data_required=True))
        # 項目が無く印だけなら今までどおり印に従う。
        self.assertTrue(requires_environment_data(settle_confirmation_items([]), items=[], external_data_required=True))


# 実サービス m1492-deepseek の grounded_draft（#1500）。cr-grant-permission の「検証用か」と ir-backup-restore の
# 台帳の列の値を、モデルが system にして needs_environment_data になっていた。
GRANT_ITEMS = [
    fact("権限の付与先を「個別の利用者」と「グループ」のどちらにするか（どちらの付与手順を使うかを決める）", "user"),
    fact("操作する利用者が「ポータル管理者」ロールを持つか（自分で操作するか、所属部署のポータル管理者に依頼するかを決める）", "user"),
    fact("グループへ付与する場合に、付与の前に部門長の承認を得ているか（グループへの付与を行えるかを決める）", "user"),
]
GRANT_TEST_ACCOUNT = "付与対象のアカウントが検証用かどうか（グループへの付与を行わず個別付与に限るかの判断）"
RESTORE_SYSTEM = "復元したいデータのシステム(どのシステムのデータを戻すか。承認を求める担当部署と承認者が決まる)"
RESTORE_LEDGER = "対象システムのシステム台帳「担当部署」列の値(承認を求める部署を決める)"
RESTORE_APPROVER = "対象システムを担当する部署の承認者が誰か(組織規程に定める部署ごとの承認者。どの承認者に承認を求めるかが決まる)"
E1023_ITEMS = [
    fact(
        "エラーが出た時刻の認証ログ（auth.log）の該当行が「token expired」か「permission denied」か"
        "（原因 A と原因 B のどちらに当たるかを決める）",
        "system",
    ),
    fact(
        "操作している利用者が「ポータル管理者」ロールを持つか（認証ログを自分で確認できるか、確認を依頼する必要があるかを決める）", "user"
    ),
]


class WhoCanAnswerDefinitionTest(unittest.TestCase):
    """who_can_answer の定義を値の出どころで排他に分ける (#1500)。

    資料（台帳・一覧・表）に載る値は document、利用者が自分の状況として答えられる属性は user、system は稼働中の
    システムにだけある値（ログ・設定の現在値・処理の状態・実績）。
    """

    def schema_text(self):
        from rag_engine.models.llm import ExternalDataItem

        return ExternalDataItem.model_fields["who_can_answer"].description

    def test_ledger_values_are_document_not_system(self):
        text = self.schema_text()
        self.assertIn("台帳・一覧・表の値", text.split("document=", 1)[1].split("system=", 1)[0])
        system = text.split("system=", 1)[1].split("。", 1)[0]
        self.assertNotIn("台帳", system)
        self.assertNotIn("対象レコード", system)
        self.assertIn("ログの行", system)
        # 反例は 1 つ（台帳は資料なので document）。旧い反例（台帳の担当部署は system）を残さない。
        self.assertNotIn("台帳の値なので system", text)
        self.assertEqual(text.count("反例:"), 1)
        self.assertIn("台帳の担当部署は台帳が資料なので", text)
        self.assertIn("取得した根拠に該当の行・記載があれば値は分かるので挙げず、無ければ document", text)

    def test_attributes_of_the_target_the_user_knows_are_user(self):
        text = self.schema_text()
        user = text.split("user=", 1)[1].split("document=", 1)[0]
        self.assertIn("対象のアカウントの種類（検証用か・共用か）", user)
        # 値が対象で決まるときは、対象を確かめる user の項目だけ。
        self.assertIn("対象が質問で特定されていなければ、対象を確かめる user の項目だけを挙げる", text)

    def test_system_prompt_defers_the_item_facts_to_the_response_schema(self):
        # 3 つの事実の定義は schema の 1 か所だけ（指示に重ねない）。
        self.assertNotIn("who_can_answer", grounded.GENERATE_SYSTEM_PROMPT)
        self.assertIn("項目の書き方は応答 schema に従う", grounded.GENERATE_SYSTEM_PROMPT)


V1_TITLE = "地域別集計表 出力手順 第1版"
V2_TITLE = "地域別集計表 出力手順 第2版"
V1_STEP = "メニューの「レポート」を開き、「地域別集計」を選んで対象月を入力し、「出力」を押します。"
V2_STEP = "メニューの「集計」を開き、「地域別」を選びます。続いて対象月を選び、「出力」を押します。"
V2_ABOLISHED = "第2版の施行日は 2026年4月1日で、第1版は第2版の施行により廃止されています。"


def versioned(index, text, title, source, *, superseded=False):
    metadata = {"section_path": [title, "出力の手順"], **({"document_superseded": True} if superseded else {})}
    return AnswerRecord(
        id=f"r{index}",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=index + 1,
        category="Chunk",
        text=text,
        source=source,
        chunk_level="parent",
        chunk_id=f"r{index}",
        source_run_id="run",
        metadata=metadata,
    )


def _run(model, ctx, question, **settings):
    unused = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected I/O"))  # noqa: E731
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        return synthesize_grounded_answer(question, ctx, replace(get_settings(), **settings), image_prompt_mode="text_only").response


def rule(eid, text, kind="rule"):
    return {"kind": kind, "text": text, "citations": [{"evidence_id": eid, "quote": text}], "request_id": "Q1"}


if __name__ == "__main__":
    unittest.main()
