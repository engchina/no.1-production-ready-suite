"""回答の対応（outcome。#1305）の決定論テスト。

Run の step・最終の検証の成果物の内容・回答の段落から、RAG の AnswerEnvelope と同じ語彙の対応を
モデルを呼ばずに決めることを、業務支援の評価（#1289 の D）で取り違えた回答の形で確かめる。
最終の検証の内容は `combine_validations` で実際と同じ形に組み立てる。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.agent.answer_outcome import ANSWER_OUTCOMES, answer_outcome
from app.features.agent.answer_passages import asks_environment_data, case_labels, passage_spans
from app.features.agent.answer_validation import (
    REASON_CLARIFICATION_ONLY,
    REASON_NO_RAG_EVIDENCE,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    STATUS_UNVALIDATED,
    combine_validations,
    validation_content,
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


def _claim(quote: str, status: str) -> dict[str, Any]:
    return {"answer_quote": quote, "status": status, "reason": ""}


def _validated(answer: str, *claims: dict[str, Any], status: str = "completed") -> dict[str, Any]:
    """`rag_validate_answer` の判定から、最終の検証の成果物の内容を組み立てる。"""
    counts: dict[str, int] = {}
    for claim in claims:
        counts[claim["status"]] = counts.get(claim["status"], 0) + 1
    supported = counts.get("supported", 0)
    result = {
        "valid": status == "completed" and supported > 0 and len(claims) == supported,
        "status": status,
        "counts": counts,
        "claims": list(claims),
        "missing_evidence": [],
        "stale_evidence": [],
        "evidence_truncated": False,
    }
    content, _published = combine_validations(
        answer, [validation_content(STATUS_COMPLETED, result=result, evidence=EVIDENCE)]
    )
    return content


def _outcome(
    answer: str, steps: list[RunStep], validation: dict[str, Any] | None = None
) -> tuple[str, str]:
    content = answer_outcome(answer, steps=steps, validation=validation)
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


def test_clarification_question_without_rag_search_is_needs_clarification() -> None:
    # cr-grant-permission: 確認の質問として扱う（推定は「根拠不足」だった）。
    validation = validation_content(STATUS_SKIPPED, reason=REASON_CLARIFICATION_ONLY)
    content = answer_outcome(
        CLARIFICATION,
        steps=[_step("rag__rag_lookup_guides", {"guides": []})],
        validation=validation,
    )
    assert (content["value"], content["basis"]) == ("needs_clarification", "clarification_question")
    assert content["rag_outcome"] is None
    assert content["signals"] == ["question"]


def test_validation_that_withholds_the_whole_answer_is_insufficient_evidence() -> None:
    answer = "契約は 60 日前までに解約できます。"
    validation = _validated(answer, _claim(answer, "unsupported"), status="no_evidence")
    assert validation["withheld"]["all"] is True
    assert _outcome(answer, [_retrieve()], validation) == (
        "insufficient_evidence",
        "validation_withheld_all",
    )


def test_answer_that_relies_on_rag_search_uses_its_outcome() -> None:
    answer = "契約の更新は 30 日前までに申し出ます。"
    validation = _validated(answer, _claim(answer, "supported"))
    content = answer_outcome(answer, steps=[_search("answered")], validation=validation)
    assert (content["value"], content["basis"], content["rag_outcome"]) == (
        "answered",
        "rag_search",
        "answered",
    )
    assert content["signals"] == []
    assert _outcome(answer, [_search("needs_human")]) == ("needs_human", "rag_search")
    # 最後に成功した rag_search の対応を使う（失敗した呼び出しは数えない）。
    steps = [_search("conditional"), _step("rag__rag_search", {}, success=False)]
    assert _outcome(answer, steps) == ("conditional", "rag_search")


def test_answered_rag_search_with_withheld_paragraphs_is_conditional() -> None:
    kept = "契約の更新は 30 日前までに申し出ます。"
    fee = "手数料として 5,000 円を支払います。"
    answer = f"{kept}\n{fee}"
    validation = _validated(answer, _claim(kept, "supported"), _claim(fee, "unsupported"))
    content = answer_outcome(answer, steps=[_search("answered")], validation=validation)
    assert (content["value"], content["basis"]) == ("conditional", "rag_search")
    assert content["signals"] == ["withheld_claims"]


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


def test_answer_with_an_absence_statement_is_conditional() -> None:
    # ed-current-session-timeout: 既定値を答え、今の値は資料で確かめられないと示した。
    answer = "セッションの有効期限の既定値は 30 分です。\n今の設定値は資料からは確認できません。"
    content = answer_outcome(answer, steps=[_retrieve()])
    assert (content["value"], content["basis"]) == ("conditional", "answer_passages")
    assert content["signals"] == ["absence"]


def test_absence_only_answer_is_insufficient_evidence() -> None:
    # km-license-fee: 資料に記載が無いことだけを述べた回答（推定は「回答」）。
    answer = "ライセンス費用は資料に記載がありません。"
    assert _outcome(answer, [_retrieve()]) == ("insufficient_evidence", "answer_passages")
    # 根拠で確かめられない助言を外した後に、不足の文だけが残っても拒答。
    advice = "営業部に問い合わせると 1 日で分かります。"
    with_advice = f"{answer}\n{advice}"
    validation = _validated(with_advice, _claim(advice, "unsupported"))
    assert validation["withheld"] == {"claims": 1, "findings": 0, "all": False}
    assert _outcome(with_advice, [_retrieve()], validation) == (
        "insufficient_evidence",
        "answer_passages",
    )


def test_data_confirmation_paragraphs_need_environment_data() -> None:
    # ed-error-e1023-cause: 実データ（ログ）の確認を促すだけの回答。
    confirm = "認証ログで E1023 が出た時刻のトークンの状態を確認してください。"
    validation = _validated(confirm, _claim(confirm, "data_confirmation"))
    content = answer_outcome(confirm, steps=[_retrieve()], validation=validation)
    assert (content["value"], content["basis"]) == ("needs_environment_data", "answer_passages")
    assert content["signals"] == ["data_confirmation"]
    # 原因を資料で答え、見分け方として実データの確認を促すなら条件付き。
    cause = "E1023 はトークンの期限切れか、時刻のずれで起きます。"
    answer = f"{cause}\n{confirm}"
    validation = _validated(
        answer, _claim(cause, "supported"), _claim(confirm, "data_confirmation")
    )
    assert _outcome(answer, [_retrieve()], validation) == ("conditional", "answer_passages")


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


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("契約の更新は 30 日前までに申し出ます。", "answered"),
        (
            "契約の更新は 30 日前までに申し出ます。\n\n**確かめられていない点**\n\n- 違約金の額",
            "conditional",
        ),
        ("契約の更新は 30 日前までに申し出ます。\n更新の種類は年間ですか？", "conditional"),
    ],
    ids=["claims", "unverified-section", "question"],
)
def test_unvalidated_answer_is_judged_from_its_passages(answer: str, expected: str) -> None:
    validation = validation_content(STATUS_UNVALIDATED, reason=REASON_NO_RAG_EVIDENCE)
    assert _outcome(answer, [], validation) == (expected, "answer_passages")


# ---- #1317: 実環境の評価（#1305 の D）で外れた回答の形 ----------------------------------


def _verdicts(answer: str, statuses: dict[str, str]) -> list[dict[str, Any]]:
    """回答の段落ごとの判定（`statuses` に無い段落は supported）。"""
    return [
        _claim(text, statuses.get(text, "supported"))
        for line in answer.split("\n")
        for _start, _end, text in passage_spans(line)
    ]


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


def test_citation_location_lines_are_not_withheld_and_the_answer_is_answered() -> None:
    # da-trial-account-expiry: 出典の行（セクション・ページ）を unassessed として外し、
    # withheld_claims で conditional になっていた（期待は answered）。
    statuses = {
        "- 文書名: *portal-operations-manual.pdf*": "unassessed",
        TRIAL_EXPIRY_SECTION: "unassessed",
        "- ページ: 1": "unassessed",
    }
    validation = _validated(TRIAL_EXPIRY, *_verdicts(TRIAL_EXPIRY, statuses))
    assert validation["withheld"] == {"claims": 0, "findings": 0, "all": False}
    marked = {
        item["answer_quote"]: item.get("non_claim") for item in validation["result"]["claims"]
    }
    assert marked[TRIAL_EXPIRY_SECTION] == "citation"
    assert marked["- ページ: 1"] == "citation"
    assert marked[TRIAL_EXPIRY_FACT] is None
    content = answer_outcome(TRIAL_EXPIRY, steps=[_retrieve()], validation=validation)
    assert (content["value"], content["basis"], content["signals"]) == (
        "answered",
        "answer_passages",
        [],
    )


def test_only_unsupported_or_contradicted_withheld_paragraphs_are_a_gap() -> None:
    # 確かめが終わらなかった段落（unassessed）を外しただけでは、条件・不足を示したことにしない。
    fact = "検証用アカウントの有効期限は最長 30 日です。"
    pending = "1. **対象の選択**"
    extension = "延長は 90 日までできます。"
    answer = f"{fact}\n{pending}\n{extension}"
    unassessed = _validated(
        answer,
        _claim(fact, "supported"),
        _claim(pending, "unassessed"),
        _claim(extension, "supported"),
    )
    assert unassessed["withheld"]["claims"] == 1
    content = answer_outcome(answer, steps=[_retrieve()], validation=unassessed)
    assert (content["value"], content["signals"]) == ("answered", [])
    # rag_search に拠る回答でも同じ（da-regional-report-export）。
    content = answer_outcome(answer, steps=[_search("answered")], validation=unassessed)
    assert (content["value"], content["basis"]) == ("answered", "rag_search")
    for status in ("unsupported", "contradicted"):
        disputed = _validated(
            answer,
            _claim(fact, "supported"),
            _claim(pending, "unassessed"),
            _claim(extension, status),
        )
        content = answer_outcome(answer, steps=[_retrieve()], validation=disputed)
        assert (content["value"], content["signals"]) == ("conditional", ["withheld_claims"])


def test_withheld_unassessed_claims_are_not_counted_as_claims() -> None:
    # 外した段落（unassessed も）は利用者に見えないので主張に数えない（拒答のまま）。
    absence = "パスワードの文字数要件は、資料に記載がありません。"
    advice = "現場の契約書や価格表など、別途確認できる情報が必要です。"
    answer = f"{absence}\n{advice}"
    validation = _validated(answer, _claim(absence, "supported"), _claim(advice, "unassessed"))
    assert validation["withheld"]["claims"] == 1
    assert _outcome(answer, [_retrieve()], validation) == (
        "insufficient_evidence",
        "answer_passages",
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


@pytest.mark.parametrize(
    ("answer", "statuses"),
    [
        (
            LICENSE_FEE_REFUSAL,
            {
                "- rag__rag_lookup_guides の検索結果でも、今回の質問に該当する業務ガイドは"
                "返ってきませんでした。": "unsupported",
                "現場の契約書や価格表など、別途確認できる情報が必要です。": "unassessed",
            },
        ),
        (PASSWORD_REFUSAL, {"該当するパスワードポリシー文書をご確認ください。": "unassessed"}),
    ],
    ids=["km-license-fee", "km-password-policy"],
)
def test_refusal_that_ends_with_cannot_answer_is_insufficient_evidence(
    answer: str, statuses: dict[str, str]
) -> None:
    # 「推測で金額を示すことはできません。」「…根拠のある回答はできません。」が主張として数えられ、
    # conditional になっていた（期待は insufficient_evidence）。
    validation = _validated(answer, *_verdicts(answer, statuses))
    content = answer_outcome(answer, steps=[_retrieve()], validation=validation)
    assert (content["value"], content["basis"]) == ("insufficient_evidence", "answer_passages")
    assert content["signals"] == ["absence"]
    # rag_search の後に根拠を集め直した回答も同じ（km-password-policy）。
    steps = [_search("insufficient_evidence"), _retrieve()]
    assert answer_outcome(answer, steps=steps, validation=validation)["value"] == (
        "insufficient_evidence"
    )


def test_refusal_with_an_amount_is_still_a_claim() -> None:
    # 金額を含む文は拒答の文にしない（#1306 の安全側の規則）。
    answer = "年間ライセンス費用は 120,000 円のため、それ以上の金額はお示しできません。"
    assert _outcome(answer, [_retrieve()]) == ("answered", "answer_passages")


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


def test_paragraph_asking_to_check_logs_needs_environment_data_even_if_supported() -> None:
    # ed-error-e1023-cause: 資料が「認証ログを確かめる」と書いているので検証は supported にし、
    # answered になっていた（期待は needs_environment_data / conditional）。
    validation = _validated(E1023_CAUSE, *_verdicts(E1023_CAUSE, {}))
    checks = [
        item["answer_quote"]
        for item in validation["result"]["claims"]
        if item.get("environment_check")
    ]
    assert checks == [E1023_LOG]
    content = answer_outcome(E1023_CAUSE, steps=[_retrieve()], validation=validation)
    assert (content["value"], content["basis"], content["signals"]) == (
        "conditional",
        "answer_passages",
        ["data_confirmation"],
    )
    # 確認を求めるだけの回答は needs_environment_data。
    only = "認証ログ（auth.log）でエラー発生時刻の行を確認してください。"
    validation = _validated(only, _claim(only, "supported"))
    assert _outcome(only, [_retrieve()], validation) == (
        "needs_environment_data",
        "answer_passages",
    )
    # 検証が外した確認の段落は、利用者に見えないので印にしない。
    fact = "E-1023 の原因は、認証トークンの期限切れか権限不足です。"
    answer = f"{fact}\n{only}"
    validation = _validated(answer, _claim(fact, "supported"), _claim(only, "unassessed"))
    assert answer_outcome(answer, steps=[_retrieve()], validation=validation)["signals"] == []


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


def test_refusal_with_other_wording_and_a_trailing_note_is_insufficient_evidence() -> None:
    # km-password-policy: 「お伝えすることはできません」と、文末の補足の括弧の付いた
    # 「…含まれていませんでした（…を参照）。」が主張として数えられた。
    advice = "必要であれば、別の関連ドキュメントやポリシーを確認してください。"
    validation = _validated(
        PASSWORD_REFUSAL_WITH_NOTE,
        *_verdicts(PASSWORD_REFUSAL_WITH_NOTE, {advice: "unassessed"}),
    )
    content = answer_outcome(PASSWORD_REFUSAL_WITH_NOTE, steps=[_retrieve()], validation=validation)
    assert (content["value"], content["signals"]) == ("insufficient_evidence", ["absence"])


def test_unknown_current_value_is_a_gap() -> None:
    # ed-current-session-timeout: 既定値を答え、今の値は「資料に記載がないため、**不明**です」と
    # 示した回答（answered になっていた）。
    answer = "\n".join(
        [
            "この資料では **session_timeout_minutes の既定値は 30 分、設定例は 60 分** と記載されて"
            "いますが、各環境ごとの現在の設定値は示されていません。",
            "",
            "現時点での実際の設定値は資料に記載がないため、**不明**です。",
        ]
    )
    validation = _validated(answer, *_verdicts(answer, {}))
    content = answer_outcome(answer, steps=[_retrieve()], validation=validation)
    assert (content["value"], content["signals"]) == ("conditional", ["absence"])


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


def test_answer_split_into_branches_without_asking_is_conditional() -> None:
    # cr-grant-permission: 付与先を確かめずに分岐ごとに答えた回答。分岐の見出しは unassessed で
    # 外れるだけなので、#1317 の前は withheld_claims で偶然 conditional になっていた。
    statuses = {
        "1. **対象の選択**": "unassessed",
        "2. **個別利用者に付与する場合**": "unassessed",
        "3. **グループに付与する場合**": "unassessed",
        "4. **検証用アカウントの場合**": "unassessed",
    }
    validation = _validated(GRANT_BRANCHES, *_verdicts(GRANT_BRANCHES, statuses))
    content = answer_outcome(GRANT_BRANCHES, steps=[_retrieve()], validation=validation)
    assert (content["value"], content["signals"]) == ("conditional", ["branches"])
    assert case_labels(GRANT_BRANCHES) == {
        "個別利用者に付与する場合",
        "グループに付与する場合",
        "検証用アカウントの場合",
    }
    # 分岐のラベルが 1 つだけなら、条件を添えた回答として answered のまま。
    single = "検証用アカウントは最長 30 日です。\n- 延長する場合: 新しく登録し直します。"
    assert case_labels(single) == {"延長する場合"}
    assert _outcome(single, [_retrieve()]) == ("answered", "answer_passages")


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


def test_need_to_check_the_log_is_a_data_confirmation() -> None:
    # ed-error-e1023-cause（2 回目の実行）: 「認証ログ…の記述を確認する必要があります」
    # 「認証ログの確認が必要です」で結んだ回答。
    answer = "\n".join(
        [
            "エラー **E‑1023**（「処理を続行できません」）の原因は次の 2 つのいずれかです。",
            "",
            "1. **認証トークンの期限切れ**（原因 A）  ",
            "2. **操作対象に対する権限不足**（原因 B）  ",
            "",
            "※画面だけではどちらかは判断できず、サーバーの認証ログ（auth.log）で"
            "「`token expired`」か「`permission denied`」の記述を確認する必要があります。  ",
            "",
            "以上が、資料に基づくエラー E‑1023 の原因です。※具体的にどちらかを確定するには、"
            "認証ログの確認が必要です。",
        ]
    )
    validation = _validated(answer, *_verdicts(answer, {}))
    content = answer_outcome(answer, steps=[_retrieve()], validation=validation)
    assert (content["value"], content["signals"]) == ("conditional", ["data_confirmation"])


def test_enclosed_absence_and_a_stray_emphasis_mark_are_not_claims() -> None:
    # km-password-policy（2 回目の実行）: 「（根拠：検索結果に…記述は含まれていません）」と、
    # 句点の後に分かれた強調の閉じ「**」。
    answer = "\n".join(
        [
            "資料を検索しましたが、パスワードの文字数要件に関する記載は見つかりませんでした。  ",
            "**→ 資料で確かめられませんでした。**  ",
            "",
            "（根拠：検索結果にパスワード長に関する記述は含まれていません）",
        ]
    )
    validation = _validated(answer, *_verdicts(answer, {"**": "unassessed"}))
    assert validation["withheld"]["claims"] == 0
    content = answer_outcome(answer, steps=[_retrieve()], validation=validation)
    assert (content["value"], content["signals"]) == ("insufficient_evidence", ["absence"])


def test_a_claim_with_a_file_citation_is_still_a_claim() -> None:
    # cs-closing-day（2 回目の実行）: 句点ではなく「.」で終わる、出典を添えた主張の 1 文が
    # 出典の行と判定され、主張が無い（拒答）になった。
    answer = (
        "地域別集計表の締め日は **毎月 10 日** です（第1版の毎月 5 日から変更されています）"
        "【regional-report-guide-v2.pdf, 「地域別集計表 出力手順 第2版」 > 「1. 締め日」】."
    )
    validation = _validated(answer, _claim(answer, "supported"))
    assert validation["result"]["claims"][0].get("non_claim") is None
    assert _outcome(answer, [_retrieve()], validation) == ("answered", "answer_passages")
