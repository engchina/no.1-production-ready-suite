"""利用者に聞けば分かる条件と、現場の実データの分け方 (#1453)。

業務支援の評価（#1362 の deepseek-flash の全量）で、手順を答えたうえで「操作する利用者が「ポータル管理者」
ロールを持つか」「部門長の承認の有無」「対象のシステム」を「実データ」に挙げ、回答の対応が
needs_environment_data になっていた。草稿の external_data_items の種類（kind）で分け、利用者に確かめる
条件（user_condition）だけなら conditional、現場の実データ（environment）があれば needs_environment_data にする。
LLM は差し替え、評価の質問の形（評価用の合成資料の原文）で入出力だけを確かめる。
"""
import unittest

from test_grounded_answer import FakeModel, audit, context, draft, ids, record, run

from rag_engine.generation import grounded
from rag_engine.generation.answer_envelope import build_envelope
from rag_engine.models.llm import GroundedDraft

MANUAL = "portal-operations-manual.pdf"
PREMISE = "本書の操作ができるのは「ポータル管理者」ロールを持つ利用者だけです。"
GRANT_INDIVIDUAL = "個別の利用者に付与する場合: 利用者の詳細画面の「権限」タブで、付与する権限を選んで「付与」を押します。"
GRANT_GROUP = "グループに付与する場合: グループの詳細画面の「権限」タブで付与します。"
DELETE_APPROVAL = "削除の前に、所属部署の部門長の承認を得てください。承認の無い削除は行いません。"
E1023_LOG = "サーバーの認証ログ（auth.log）で、エラーが出た時刻の行を確かめます。「token expired」とあれば原因 A、「permission denied」とあれば原因 B です。"

ROLE = "操作する利用者自身が「ポータル管理者」ロールを持つか（自分で操作するか、所属部署のポータル管理者への依頼になるかの確認）"
TARGET = "付与対象が「個別の利用者」か「グループ」か（操作する画面を決めるため）"
APPROVAL = "対象アカウントの所属部署の部門長による削除の承認の有無（今回の削除を実施してよいかを確定する）"
AUTH_LOG = "エラー発生時刻に対応する認証ログ（auth.log）の該当行（原因 A と原因 B のどちらかを確定するため）"


def user_condition(text):
    return {"text": text, "kind": "user_condition"}


def environment(text):
    return {"text": text, "kind": "environment"}


def rule(eid, text, request_id="Q1"):
    # 原文どおりの説明（言い換えの検査で落ちないよう text = quote にする）。
    return {"kind": "rule", "text": text, "evidence_id": eid, "quote": text, "request_id": request_id}


def answer(question, texts, external_data_items, *, external_data_required=True):
    ctx = context(*(record(i, text, "アクセス権限の付与", page=1, source=MANUAL) for i, text in enumerate(texts)))
    eids = ids(ctx, question)
    steps = {**draft(*(rule(eids[text], text) for text in texts), summary=""),
             "external_data_required": external_data_required, "external_data_items": external_data_items}
    reviews = [(index, "supported", "matched", "") for index in range(len(texts))]
    return run(FakeModel([steps], [audit(*reviews, requests=[("Q1", "addressed")])]), ctx, question).response


class UserConditionOutcomeTest(unittest.TestCase):
    def test_grant_permission_with_role_and_target_conditions_is_conditional(self):
        # cr-grant-permission-known-target: 手順を答え、ロールと付与先の種類だけを確かめる。
        response = answer("アクセス権限を付与したいです。どの画面で操作しますか？", [PREMISE, GRANT_INDIVIDUAL, GRANT_GROUP],
                          [user_condition(TARGET), user_condition(ROLE)])
        envelope = response.envelope
        self.assertEqual(envelope["outcome"], "conditional")
        self.assertEqual(envelope["conditions"], [TARGET, ROLE])
        self.assertEqual(envelope["confirmations"], [])
        self.assertNotIn("environment_data", envelope["handoff_reasons"])
        self.assertIn("user_conditions", envelope["handoff_reasons"])
        # 現場の実データの印は立てない（モデルが external_data_required を立てても）。
        self.assertFalse(response.external_data_required)
        self.assertEqual(response.external_data_items, ())
        # 本文では確認の節に「条件」として出し、「実データ」とは書かない。
        self.assertIn(f"{grounded.CONFIRMATIONS_SECTION_TITLE}\n\n・条件: {TARGET}\n・条件: {ROLE}", response.answer_text)
        self.assertNotIn("実データ", response.answer_text)
        # 確かめる条件が無ければ、同じ手順は answered（conditional は条件だけによる）。
        plain = answer("アクセス権限を付与したいです。どの画面で操作しますか？", [PREMISE, GRANT_INDIVIDUAL, GRANT_GROUP], [],
                       external_data_required=False)
        self.assertEqual(plain.envelope["outcome"], "answered")

    def test_delete_account_with_approval_condition_is_conditional(self):
        # cr-delete-account: 部門長の承認を得たかは利用者に確かめる条件。
        response = answer("不要になったアカウントを削除してもよいですか？", [PREMISE, DELETE_APPROVAL], [user_condition(APPROVAL)])
        self.assertEqual(response.envelope["outcome"], "conditional")
        self.assertIn(APPROVAL, response.envelope["conditions"])
        self.assertFalse(response.external_data_required)

    def test_authentication_log_is_environment_data(self):
        # ed-error-e1023-cause: 認証ログの時刻の行は、利用者に聞いても分からない現場の記録。
        response = answer("エラー E-1023 が出ました。原因は何ですか？", [E1023_LOG], [environment(AUTH_LOG)])
        self.assertEqual(response.envelope["outcome"], "needs_environment_data")
        self.assertEqual(response.envelope["confirmations"], [f"実データ: {AUTH_LOG}"])
        self.assertTrue(response.external_data_required)
        self.assertEqual(response.external_data_items, (AUTH_LOG,))

    def test_environment_data_with_a_user_condition_still_needs_environment_data(self):
        response = answer("エラー E-1023 が出ました。原因は何ですか？", [E1023_LOG], [environment(AUTH_LOG), user_condition(ROLE)])
        envelope = response.envelope
        self.assertEqual(envelope["outcome"], "needs_environment_data")
        self.assertIn(ROLE, envelope["conditions"])
        self.assertEqual(response.external_data_items, (AUTH_LOG,))
        self.assertIn(f"・条件: {ROLE}\n・実データ: {AUTH_LOG}", response.answer_text)

    def test_flag_without_items_still_needs_environment_data(self):
        # 項目を挙げずに印だけを立てたときは、今までどおり印に従う。
        response = answer("エラー E-1023 が出ました。原因は何ですか？", [E1023_LOG], [])
        self.assertEqual(response.envelope["outcome"], "needs_environment_data")


class ExternalDataItemTest(unittest.TestCase):
    def _draft(self, items, required=True):
        return GroundedDraft.model_validate({"summary": "", "items": [], "confidence": "low",
                                             "external_data_required": required, "external_data_items": items})

    def test_plain_text_items_are_treated_as_environment_data(self):
        value = self._draft(["当該実行のログ"])
        self.assertEqual(value.environment_data_items(), ["当該実行のログ"])
        self.assertEqual(value.user_condition_items(), [])
        self.assertTrue(value.requires_environment_data())

    def test_only_user_conditions_do_not_require_environment_data(self):
        value = self._draft([user_condition(ROLE), user_condition(" ")])
        self.assertEqual(value.user_condition_items(), [ROLE])
        self.assertFalse(value.requires_environment_data())
        self.assertEqual(grounded.user_condition_lines(value), [f"条件: {ROLE}"])
        self.assertEqual(grounded.confirmation_lines(value, "", []), [])
        self.assertFalse(self._draft([], required=False).requires_environment_data())
        self.assertTrue(self._draft([]).requires_environment_data())

    def test_schema_asks_the_model_for_the_kind(self):
        schema = GroundedDraft.model_json_schema()
        item = schema["$defs"]["ExternalDataItem"]
        self.assertEqual(set(item["required"]), {"text", "kind"})
        self.assertEqual(set(item["properties"]["kind"]["enum"]), {"environment", "user_condition"})


class BuildEnvelopeUserConditionsTest(unittest.TestCase):
    def test_user_conditions_make_an_answered_envelope_conditional(self):
        matched = {"kind": "operation", "text": "手順", "applies": "matched", "condition": ""}
        common = {"requests": (), "reviews": [], "items": [matched], "gaps": (), "confirmations": (),
                  "external_data_required": False}
        self.assertEqual(build_envelope(**common)["outcome"], "answered")
        envelope = build_envelope(user_conditions=[ROLE], **common)
        self.assertEqual(envelope["outcome"], "conditional")
        self.assertEqual(envelope["conditions"], [ROLE])
        self.assertEqual(envelope["confirmations"], [])


if __name__ == "__main__":
    unittest.main()
