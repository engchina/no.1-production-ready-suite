"""回答の対応（outcome。#1305）の決定論テスト。

Run の step・最終の検証の成果物の内容・回答の段落から、RAG の AnswerEnvelope と同じ語彙の対応を
モデルを呼ばずに決めることを、業務支援の評価（#1289 の D）で取り違えた回答の形で確かめる。
最終の検証の内容は `combine_validations` で実際と同じ形に組み立てる。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.agent.answer_outcome import ANSWER_OUTCOMES, answer_outcome
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
