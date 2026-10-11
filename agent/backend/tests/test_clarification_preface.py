"""前置き・答え方の案内つきの確認の質問（#1454）の決定論テスト。

#1362 の deepseek-flash の全量の評価で、`rag_lookup_guides` だけを呼んで確認の質問を返した回答に、
前置きの文（「その点がまだ分かっていません。」）と答え方の案内の括弧が付き、確認の質問だけの回答と
認められずに「この回答は資料の根拠を使っておらず…」の注記が足されていた。回答はその Run の
回答そのもの（注記を足す前の本文）。
"""

from __future__ import annotations

import pytest

from app.features.agent.answer_outcome import answer_outcome
from app.features.agent.answer_passages import is_clarification_only, non_claim_passages
from app.features.agent.answer_validation import (
    REASON_CLARIFICATION_ONLY,
    STATUS_SKIPPED,
    validation_content,
)
from app.features.agent.runtime import RunStep, StepStatus
from app.features.agent.tools import ToolCall, ToolResult

# cr-grant-permission（run_c70c4b03d01e416291a367c361e91ff6）
GRANT_PERMISSION = (
    "権限の付与先によって手順が変わり、その点がまだ分かっていません。\n\n"
    "**権限の付与先は、個別の利用者ですか、グループですか？**\n\n"
    "（「個別」または「グループ」でお答えください。お答えいただいた内容に応じて、"
    "手順と必要な承認をご案内します。）"
)
# cr-delete-account（run_6e1e5d78acda464c96e14dfa1c65434c）
DELETE_ACCOUNT = (
    "ご質問に答える前に、条件を 1 つ確かめさせてください。\n\n"
    "**所属部署の部門長の承認は得ていますか？**（はい／いいえ）\n\n"
    "（業務ガイド「アカウントの削除」では、この条件によって手順・答えが変わるため、"
    "まず確認しています。）"
)
CLARIFICATIONS = {"cr-grant-permission": GRANT_PERMISSION, "cr-delete-account": DELETE_ACCOUNT}


def _lookup_guides() -> RunStep:
    name = "rag__rag_lookup_guides"
    return RunStep(
        run_id="run-1454",
        status=StepStatus.COMPLETED,
        tool_call=ToolCall(name=name, arguments={"query": "権限の付与"}),
        tool_result=ToolResult(name=name, success=True, output={"guides": []}),
    )


@pytest.mark.parametrize("answer", CLARIFICATIONS.values(), ids=CLARIFICATIONS.keys())
def test_question_with_preface_and_answer_guidance_is_clarification_only(answer: str) -> None:
    assert is_clarification_only(answer)
    validation = validation_content(STATUS_SKIPPED, reason=REASON_CLARIFICATION_ONLY)
    content = answer_outcome(answer, steps=[_lookup_guides()], validation=validation)
    assert (content["value"], content["basis"]) == ("needs_clarification", "clarification_question")


def test_passage_kinds_are_not_changed_by_the_clarification_judgment() -> None:
    """段落の判定（主張ではない段落。RAG とそろえる）は変えない。前置きは確認の質問だけの回答かの
    判定にだけ質問の一部として扱う。"""
    preface = "権限の付与先によって手順が変わり、その点がまだ分かっていません。"
    assert preface not in non_claim_passages(GRANT_PERMISSION)


@pytest.mark.parametrize(
    "answer",
    [
        # 前置きに資料の主張がある（確かめる）。
        "権限の付与には部門長の承認が要ります。\n付与先は個別の利用者ですか、グループですか？",
        # 案内の括弧に操作の指示がある。
        "付与先は個別の利用者ですか？\n（個別の場合は「権限」タブで付与してください。）",
        # 前置きに数量の主張がある。
        "削除したアカウントは 30 日後に消去されるため、確かめさせてください。\n"
        "承認は得ていますか？",
        # 質問が無い。
        "その点がまだ分かっていません。\n（「個別」または「グループ」でお答えください。）",
    ],
    ids=["claim-preface", "operation-guidance", "quantity-preface", "no-question"],
)
def test_preface_with_a_claim_is_not_clarification_only(answer: str) -> None:
    assert not is_clarification_only(answer)
