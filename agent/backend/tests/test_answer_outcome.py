"""回答の対応（outcome。#1305）の決定論テスト。

Run の step から、RAG の AnswerEnvelope と同じ語彙の対応を
モデルを呼ばずに決めることを、業務支援の評価（#1289 の D）で取り違えた回答の形で確かめる。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.agent.answer_outcome import ANSWER_OUTCOMES, answer_outcome
from app.features.agent.answer_passages import (
    asks_environment_data,
    case_labels,
    supplement_passages,
)
from app.features.agent.runtime import RunStep, StepStatus
from app.features.agent.tools import ToolCall, ToolResult

EVIDENCE = [{"document_id": "doc-1", "chunk_id": "chunk-1"}]


def _step(name: str, output: dict[str, Any], *, success: bool = True) -> RunStep:
    return RunStep(
        run_id="run-1",
        status=StepStatus.COMPLETED if success else StepStatus.FAILED,
        tool_call=ToolCall(name=name, arguments={}),
        tool_result=ToolResult(name=name, success=success, output=output if success else None),
    )


def _search(outcome: str) -> RunStep:
    return _step("rag__rag_search", {"outcome": outcome, "evidence": EVIDENCE})


def _retrieve() -> RunStep:
    return _step("rag__rag_retrieve_evidence", {"evidence": EVIDENCE})


def _outcome(answer: str, steps: list[RunStep]) -> tuple[str, str]:
    content = answer_outcome(answer, steps=steps)
    assert content["schema_version"] == 1
    assert content["value"] in ANSWER_OUTCOMES
    return content["value"], content["basis"]


CLARIFICATION = "\n".join(
    [
        "権限の付与先によって手順が変わるため、確認させてください。",
        "付与先は、個別の利用者ですか、それともグループですか？",
    ]
)


def test_empty_answer_is_insufficient_evidence() -> None:
    assert _outcome("  \n", []) == ("insufficient_evidence", "empty_answer")


def test_environment_data_is_answered_after_the_environment_tools() -> None:
    answer = "今のセッションの有効期限は 45 分です（NL2SQL の照会の結果）。"
    search = _search("needs_environment_data")
    query = _step("nl2sql__nl2sql_query", {"status": "succeeded", "rows": [[45]]})
    content = answer_outcome(answer, steps=[search, query])
    assert (content["value"], content["basis"]) == ("answered", "environment_tools")
    assert content["rag_outcome"] == "needs_environment_data"
    assert content["signals"] == ["environment_tools"]
    # 道具が無い・失敗したなら、現場のデータが要るまま。
    assert _outcome(answer, [search]) == ("needs_environment_data", "rag_search")
    failed = _step("nl2sql__nl2sql_query", {}, success=False)
    assert _outcome(answer, [search, failed]) == ("needs_environment_data", "rag_search")
    # rag_search より前に呼んだ道具は、確かめたことにしない。
    assert _outcome(answer, [query, search]) == ("needs_environment_data", "rag_search")


def test_answer_without_the_clarification_rag_asked_is_conditional() -> None:
    answer = "個別の利用者なら利用者の詳細画面、グループならグループの詳細画面で付与します。"
    assert _outcome(answer, [_search("needs_clarification")]) == ("conditional", "rag_search")


def test_evidence_gathered_after_rag_search_is_judged_from_the_answer() -> None:
    answer = "契約の更新は 30 日前までに申し出ます。"
    # rag_search の後に根拠を集め直した回答は、rag_search の対応に拠らない。
    content = answer_outcome(answer, steps=[_search("insufficient_evidence"), _retrieve()])
    assert (content["value"], content["basis"]) == ("answered", "answer_passages")
    assert content["rag_outcome"] == "insufficient_evidence"


def test_guide_handoff_without_rag_search_is_needs_human() -> None:
    lookup = _step("rag__rag_lookup_guides", {"guides": [{"guide_id": "g", "decision": "handoff"}]})
    answer = "この操作は情報システム部の担当者が行います。担当者に依頼してください。"
    assert _outcome(answer, [lookup]) == ("needs_human", "guide_handoff")
    # rag_search に拠る回答は、rag_search の対応を使う。
    assert _outcome(answer, [lookup, _search("answered")]) == ("answered", "rag_search")


def test_environment_tools_without_rag_answer_the_question() -> None:
    query = _step("nl2sql__nl2sql_query", {"status": "succeeded"})
    content = answer_outcome("先月の売上は 100 件です。", steps=[query])
    assert (content["value"], content["basis"]) == ("answered", "environment_tools")


# ---- #1317: 実環境の評価（#1305 の D）で外れた回答の形 ----------------------------------


TRIAL_EXPIRY_FACT = "検証用アカウントの有効期限は **最長 30日** です。"
TRIAL_EXPIRY_SECTION = (
    "- セクション: 「サンプル業務ポータル 運用手順書 第3版」 → **2. 検証用アカウントの登録**"
)
TRIAL_EXPIRY = "\n".join(
    [
        TRIAL_EXPIRY_FACT,
        "",
        "**根拠**  ",
        "- 文書名: *portal-operations-manual.pdf*  ",
        f"{TRIAL_EXPIRY_SECTION}  ",
        "- ページ: 1  ",
        "- 抜粋: 「…有効期限は最長 30 日です。」  ",
        "",
        "この記述に基づき、検証用アカウントは30日以内に期限切れとなります。",
    ]
)


LICENSE_FEE_REFUSAL = "\n".join(
    [
        "**回答**  ",
        "現在参照できる資料（「サンプル業務ポータル 運用手順書 第3版」等）には、"
        "サンプル業務ポータルの年間ライセンス費用に関する記載は見当たりませんでした。",
        "",
        "**根拠**  ",
        "- rag__rag_retrieve_evidence で取得した証拠は、アクセス権限の付与手順や検証用アカウントの"
        "登録などの運用手順に関する内容であり、金額や費用に関する記述は含まれていませんでした。  ",
        "- rag__rag_lookup_guides の検索結果でも、今回の質問に該当する業務ガイドは"
        "返ってきませんでした。",
        "",
        "**結論**  ",
        "資料だけでは「サンプル業務ポータルの年間ライセンス費用」がいくらかは特定できません。"
        "現場の契約書や価格表など、別途確認できる情報が必要です。推測で金額を示すことはできません。",
    ]
)
PASSWORD_REFUSAL = (
    "現在参照できる資料（「サンプル業務ポータル 運用手順書」やパラメータ一覧等）には、"
    "パスワードの文字数要件に関する記載がありません。そのため、パスワードは何文字以上にすべきかに"
    "ついて根拠のある回答はできません。該当するパスワードポリシー文書をご確認ください。"
)


E1023_LOG = (
    "※どちらが原因かを判断するには、サーバーの認証ログ（`auth.log`）でエラー発生時刻の行を確認し、"
)
E1023_CAUSE = "\n".join(
    [
        "エラー **E-1023** の原因は次の 2 つのどちらかです（※画面表示だけではどちらかの判別は"
        "できません）【証拠1】  ",
        "",
        "1. **原因 A：認証トークンの期限切れ**  ",
        "2. **原因 B：操作対象に対する権限不足**  ",
        "",
        f"{E1023_LOG}  ",
        "- 「`token expired`」と記録されていれば原因 A、  ",
        "- 「`permission denied`」と記録されていれば原因 B　と判別できます【証拠3】。  ",
        "",
        "**出典**  ",
        "- `error‑e1023‑notes.pdf` 「障害対応メモ: エラー E-1023「処理を続行できません」」 – "
        "1. 考えられる原因（ページ 1）  ",
        "- 同上 – 2. 原因の見分け方（ページ 1）  ",
    ]
)


@pytest.mark.parametrize(
    "text",
    [
        "該当するパスワードポリシー文書をご確認ください。",
        "追加の資料やシステム設定画面の確認が必要です。",
        "いったんログアウトし、ログインし直したことを確認してください。",
        "利用者の詳細画面を開き、「権限」タブの記録を確認してください。",
        "認証ログはどこで確認できますか？",
    ],
    ids=["document", "not-a-request", "login", "operation", "question"],
)
def test_environment_check_is_only_for_data_and_records(text: str) -> None:
    assert asks_environment_data(text) is False


# ---- #1317 の変更の後の実環境の D で、新たに外れた回答の形 ------------------------------

PASSWORD_REFUSAL_WITH_NOTE = "\n".join(
    [
        "**回答**  ",
        "パスワードの文字数要件について、提供された資料（「サンプル業務ポータル 運用手順書 "
        "第3版」）の中に該当する記載は見つかりませんでした。したがって、資料に基づいて正確な文字数要件を"
        "お伝えすることはできません。",
        "",
        "**根拠**  ",
        "- 取得した証拠は、主に「アクセス権限の付与」「検証用アカウントの登録」などの手順に関する"
        "内容であり、パスワードの文字数に関する情報は含まれていませんでした（取得した全証拠の抜粋を"
        "参照）。",
        "",
        "**結論**  ",
        "資料にパスワードの最低文字数に関する記載がなく、根拠がないため回答できません。"
        "必要であれば、別の関連ドキュメントやポリシーを確認してください。",
    ]
)


GRANT_BRANCHES = "\n".join(
    [
        "**アクセス権限の付与手順**",
        "",
        "1. **対象の選択**  ",
        "   - 権限付与は「個別の利用者」か「グループ」のいずれかで行います。",
        "2. **個別利用者に付与する場合**  ",
        "   - 「権限」タブを選択し、付与したい権限をチェックして **「付与」** ボタンを"
        "クリックする。",
        "3. **グループに付与する場合**  ",
        "   - 「権限」タブで付与したい権限を選び **「付与」** する。",
        "4. **検証用アカウントの場合**  ",
        "   - 検証用アカウントは個別に権限を付与し、グループへの付与は行いません。",
    ]
)


@pytest.mark.parametrize(
    "text",
    [
        "15 分を過ぎても反映されない場合は、サポート窓口へ起票してください。",
        "グループに付与する場合は部門長の承認が要ります。",
        "削除した場合",
    ],
    ids=["sentence", "claim", "too-short-context"],
)
def test_case_label_is_only_a_heading_or_a_leading_label(text: str) -> None:
    labels = case_labels(text)
    assert labels == ({"削除した場合"} if text == "削除した場合" else set())


def test_clarification_question_with_an_annotation_is_needs_clarification() -> None:
    # cr-grant-permission（2 回目の実行）: 質問の後ろの「【clarification: target】」の注記で、
    # 確認の質問と見なかった。
    answer = "権限の付与先は、個別の利用者ですか、グループですか？【clarification: target】"
    assert _outcome(answer, [_step("rag__rag_lookup_guides", {"guides": []})]) == (
        "needs_clarification",
        "clarification_question",
    )


# ---- 業務ガイドの条件が分からないまま答えた（#1321・#1322） ---------------------------------


def _lookup(decision: str, *, unknown: list[dict[str, Any]] | None = None) -> RunStep:
    return _step(
        "rag__rag_lookup_guides",
        {
            "guides": [
                {
                    "guide_id": "g-1",
                    "revision": 1,
                    "title": "アカウントの削除",
                    "decision": decision,
                    "known_conditions": [],
                    "unknown_conditions": unknown or [],
                }
            ]
        },
    )


_APPROVAL_UNKNOWN = [{"id": "approved", "label": "部門長の承認", "handling": "ask"}]
_TARGET_UNKNOWN = [{"id": "target", "label": "付与先", "handling": "ask"}]

# #1317 の実環境の D（run3）の cr-delete-account の回答（承認を前提の条件として答えた）。
_DELETE_ANSWER = "\n".join(
    [
        "不要になったアカウントは **削除して構いません**，ただし **所属部署の部門長の承認を"
        "得た場合のみ** 行ってください。",
        "",
        "- 「削除したアカウントは 30 日後に完全に消去され、復元できません。",
        "削除の前に、所属部署の部門長の承認を得てください。",
        "承認の無い削除は行いません」【portal‑operations‑manual.pdf, 6. アカウントの削除, p.2】",
        "",
        "したがって、部門長の承認があることを確認したうえで削除手順を実施してください。"
        "承認が得られない場合は削除は行わないでください。",
    ]
)

# #1317 の実環境の D（run3）の cr-grant-permission の回答（付与先を確かめずに両方の分岐で答えた。
# 見出しに「…の場合」が無いので branches の印は付かない）。
_GRANT_ANSWER = "\n".join(
    [
        "**アクセス権限の付与手順**",
        "",
        "1. **個別の利用者へ付与**",
        "   - 利用者の詳細画面を開き、**「権限」タブ**で付与したい権限を選択し、"
        "**「付与」** ボタンを押す。",
        "",
        "2. **グループへ付与**",
        "   - グループの詳細画面を開き、**「権限」タブ**で同様に権限を選択し付与する。",
        "   - グループに付与した権限はそのグループに所属する全員に適用される。",
        "   - 影響範囲が広いため、**付与前に部門長の承認**を得ること。",
        "",
        "> **注意**：権限が反映されるまで最大 **15 分** かかることがあります。",
    ]
)


def test_clarification_after_the_guide_asked_is_needs_clarification() -> None:
    question = "所属部署の部門長の承認は得ていますか？（はい／いいえ）"
    content = answer_outcome(
        question, steps=[_retrieve(), _lookup("clarify", unknown=_APPROVAL_UNKNOWN)]
    )
    assert (content["value"], content["basis"]) == ("needs_clarification", "clarification_question")


def test_clarification_with_an_options_line_is_needs_clarification() -> None:
    # #1320 の実環境の D の cr-grant-permission の回答（問いの後に「選択肢: …」の行）。
    question = (
        "権限の付与先は、個別の利用者ですか、グループですか？  \n選択肢: **個別**、**グループ**"
    )
    content = answer_outcome(
        question, steps=[_retrieve(), _lookup("clarify", unknown=_TARGET_UNKNOWN)]
    )
    assert (content["value"], content["basis"]) == ("needs_clarification", "clarification_question")
    # 回答を求める依頼を添えた確認の質問（#1320 の実環境の D の run2 の cr-grant-permission）。
    asked = (
        "権限の付与先は、個別の利用者ですか、グループですか？  \n\n- 個別  \n- グループ\n\n"
        "ご回答をお願いします。"
    )
    assert answer_outcome(asked, steps=[_retrieve()])["value"] == "needs_clarification"
    # 選んでもらう依頼を添えた確認の質問（同じく run3 の cr-grant-permission）。
    choose = (
        "権限の付与先はどちらですか？\n\n- 個別の利用者  \n- グループ  \n\n"
        "ご希望の方をお選びください。"
    )
    assert answer_outcome(choose, steps=[_retrieve()])["value"] == "needs_clarification"
    # 画面の操作の指示は主張のまま。
    operation = (
        "権限の付与先はどちらですか？\n権限タブで付与する権限を選び、「付与」を押してください。"
    )
    assert answer_outcome(operation, steps=[_retrieve()])["value"] != "needs_clarification"
    # 選択肢の行でも、文・操作の指示は主張のまま。
    for text in (
        "権限の付与先は、個別の利用者ですか？\n選択肢: 個別を選んで「付与」を押してください。",
        "権限の付与先は、個別の利用者ですか？\n候補: 個別には詳細画面の権限タブで付与します。",
    ):
        assert answer_outcome(text, steps=[_retrieve()])["value"] != "needs_clarification", text


# ---- 利用者に聞けば分かる条件は現場のデータではない（#1453） ------------------------------

# #1362 の deepseek-flash の全量の D の業務支援 mt-grant-permission-individual の 2 回目
# （run_b006caa651204fcfaeea40c1b5f1d2e0）の回答の形。手順を答え、利用者のロールを確かめる。
GRANT_WITH_ROLE_CHECK = "\n".join(
    [
        "個別の利用者への付与手順は、次のとおりです。",
        "",
        "**手順（個別の利用者に付与する場合）**",
        "1. 利用者の詳細画面の「権限」タブを開きます。",
        "2. 付与する権限を選び、「付与」を押します。",
        "",
        "**ご確認いただきたい点**",
        "- 操作を行う方が「ポータル管理者」ロールを持っているかどうかを、画面でご確認ください。"
        "ロールをお持ちでない場合は、所属部署のポータル管理者にご依頼ください。",
    ]
)


@pytest.mark.parametrize(
    "text",
    [
        "操作を行う方が「ポータル管理者」ロールを持っているかどうかを、画面でご確認ください。",
        "**実データ**：対象システムの「重要度」列の実際の値をご確認ください。",
        "削除の前に、所属部署の部門長の承認を得たかの現在の状態をご確認ください。",
    ],
    ids=["role", "target-system", "approval"],
)
def test_user_condition_checks_are_not_environment_data(text: str) -> None:
    assert asks_environment_data(text) is False


def test_record_checks_about_a_target_are_still_environment_data() -> None:
    # 対象のシステムを名指ししても、ログ・履歴そのものを確かめるなら現場のデータ。
    assert asks_environment_data(
        "対象システムの認証ログ（auth.log）でエラーの時刻の行を確認してください。"
    )
    assert asks_environment_data("対象の利用者の権限の付与の履歴を確認してください。")


def test_answer_that_asks_the_users_role_is_not_environment_data() -> None:
    # RAG が利用者に確かめる条件だけで conditional を返したら、その対応を使う。
    assert _outcome(GRANT_WITH_ROLE_CHECK, [_search("conditional")]) == (
        "conditional",
        "rag_search",
    )
    # Agent が自分で組み立てた回答でも、ロールの確認は現場の確認の印にしない。
    content = answer_outcome(GRANT_WITH_ROLE_CHECK, steps=[_retrieve()])
    assert content["value"] == "answered"
    assert "data_confirmation" not in content["signals"]


# ---- #1475: 結論の段落で答えた後の、補足の段落の不足の文 ------------------------------

PARAMETER_DEFAULT_FACT = "session_timeout_minutes の既定値は **30**（分）です。"
PARAMETER_DEFAULT_SUPPLEMENT_ABSENCE = "現在の各環境の設定値は資料からは分かりません。"
# da-parameter-default（e1467、run_c91bafebe4794ecfb266ee6be336968b）の回答。
PARAMETER_DEFAULT = "\n".join(
    [
        f"{PARAMETER_DEFAULT_FACT}説明は「操作が無いときにログインが切れるまでの分数」とされています。",
        "",
        "出典：portal-parameters.xlsx シート「パラメータ一覧」の当該行"
        "（A4:D4、パラメータ名 session_timeout_minutes）",
        "",
        "補足（資料の記載）",
        "- 同表には設定例として 60 も示されていますが、これは既定値ではなく設定の書き方の例です"
        "（同シート A6:D6 など並びの行も同じ構成）。",
        "- 同シートの注記（A1:A2）に「既定値は出荷時の値、設定例は設定の書き方の例で、どちらも"
        "各環境の現在の設定値ではありません」とあり、この 30 は現在の環境の設定値ではありません。"
        f"{PARAMETER_DEFAULT_SUPPLEMENT_ABSENCE}",
    ]
)


def test_supplement_passages_are_the_supplement_section_and_lead_paragraphs() -> None:
    supplement = supplement_passages(PARAMETER_DEFAULT)
    assert PARAMETER_DEFAULT_SUPPLEMENT_ABSENCE in supplement
    assert PARAMETER_DEFAULT_FACT not in supplement
    assert "補足（資料の記載）" not in supplement
    # 「なお、」「補足：」で始まる段落と、太字の見出し「**注記**」の節。
    answer = "\n".join(
        [
            "既定値は 30 分です。",
            "なお、現在の設定値は資料からは分かりません。",
            "補足：設定例は 60 分です。",
            "",
            "**注記**",
            "- 出荷時の値です。",
            "",
            "設定の変更は管理画面で行います。",
        ]
    )
    assert supplement_passages(answer) == {
        "なお、現在の設定値は資料からは分かりません。",
        "補足：設定例は 60 分です。",
        "- 出荷時の値です。",
    }
    # 補足の外にも同じ原文の段落があれば、補足として扱わない。
    repeated = "現在の値は資料からは分かりません。\n\n補足\n- 現在の値は資料からは分かりません。"
    assert supplement_passages(repeated) == {"- 現在の値は資料からは分かりません。"}
    same = "現在の値は資料からは分かりません。\n\n補足\n現在の値は資料からは分かりません。"
    assert supplement_passages(same) == set()


ABSENCE_ANSWER = "ご質問の手続きの期限は、検索した資料には記載がありませんでした。"


# ---- 確かめが終わっていないだけの段落と範囲外の拒答（#104） ---------------------------------

# Run run_a30a298b… の回答（社内規程で、出張の宿泊費の上限はいくらですか？）。「使用したツール」の
# 行は組み込み Runtime が検証の前に外す（`without_lookup_notes`）。
LODGING_ANSWER = "\n".join(
    [
        "社内規程（株式会社サンプル 勤務・出張・経費規程 第3条「出張旅費」、1ページ目）によると、"
        "出張の宿泊費の上限（1泊）は役職で以下のとおりです。",
        "",
        "| 区分 | 宿泊費の上限（1泊） |",
        "|---|---|",
        "| 一般社員 | 12,000円 |",
        "| 課長以上 | 15,000円 |",
        "",
        "根拠資料: **社内規程サンプル.pdf** ／ 株式会社サンプル 勤務・出張・経費規程 ／ "
        "第3条（出張旅費）／ 1ページ目",
    ]
)
OUT_OF_SCOPE_REFUSAL = "\n".join(
    [
        "明日の東京の天気は、社内資料（社内規程サンプル.pdf）にも記載がなく、この環境の資料・"
        "業務データでは確かめられません。",
        "",
        "天気予報は社内の範囲外の情報なので、一般的な気象情報サイトなどでご確認ください。",
    ]
)
OUT_OF_SCOPE_ADVICE = (
    "天気予報は社内の範囲外の情報なので、一般的な気象情報サイトなどでご確認ください。"
)
