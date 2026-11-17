"""利用者が選んだ登録済みQ&Aの回答をそのまま返す段（#5107）。

「回答の前に関連するQ&Aを提示する」がオンの検索・回答プロファイルで、利用者が候補から FAQ を
選んで送った質問は、入口（``_resolve_query_context``）がプロファイルの FAQ から引き直す。回答の
工程は検索・根拠の読み取り・回答の生成を通さず、FAQ の回答を原文のまま返す（LLM を呼ばない。
引用なし）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pr_system_settings.guardrails import GuardrailResult
from rag_engine.generation.answer_envelope import ENVELOPE_SCHEMA_VERSION
from rag_engine.knowledge.approved_faq import ApprovedFaqRecord, ApprovedFaqSuggestion

from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import Settings
from app.rag.answer_engine import AnswerOutcome
from app.rag.input_guardrail import check_question_text
from app.rag.search_answer_profile_knowledge import (
    approved_faq_enabled,
    approved_faq_match_options,
    load_approved_faq,
    suggest_approved_faq,
)

# 処理の経過の段（SSE の ``stage``）。
APPROVED_FAQ_ANSWER_STAGE = "approved_faq_answer"
# 回答の診断の ``answer_source`` の値。
APPROVED_FAQ_ANSWER_SOURCE = "approved_faq"
APPROVED_FAQ_NOT_FOUND_MESSAGE = (
    "選んだQ&Aが見つかりません。Q&Aの提示がオフになったか、削除された可能性があります。"
    "もう一度送信して選び直してください。"
)


def selected_approved_faq(record: ApprovedFaqRecord) -> dict[str, str]:
    """入口で引き直した FAQ を、回答の工程へ渡す形（``Settings.rag_approved_faq_selected``）。"""
    return {"id": record.id, "question": record.question, "answer": record.approved_answer}


def approved_faq_outcome(selected: Mapping[str, str]) -> AnswerOutcome:
    """選んだ FAQ の回答（原文のまま）。"""
    question = selected["question"]
    answer = selected["answer"]
    return AnswerOutcome(
        answer=answer,
        citations=[],
        diagnostics={
            "answer_source": APPROVED_FAQ_ANSWER_SOURCE,
            # 承認済みの回答をそのまま使うので、対応は「答えた」。
            "outcome": "answered",
            "envelope": {"schema_version": ENVELOPE_SCHEMA_VERSION, "outcome": "answered"},
            "approved_faq_id": selected["id"],
            "approved_faq_question": question,
            # 回答した時点の承認済みの回答。FAQ が変わっても根拠の原文を出せる。
            "approved_faq_answer": answer,
            # 回答・検索のモデルは呼んでいない。
            "models": {"embedding": "", "rerank": ""},
        },
        # 回答の安全チェックが照合する根拠は、選んだ FAQ だけ。
        context_text=f"登録済みQ&Aの質問: {question}\n登録済みQ&Aの回答: {answer}",
    )


def is_approved_faq_answer(answer_diagnostics: Mapping[str, Any] | None) -> bool:
    """回答の診断が、選んだ FAQ の回答をそのまま返した回答か。"""
    return (answer_diagnostics or {}).get("answer_source") == APPROVED_FAQ_ANSWER_SOURCE


def approved_faq_snapshot(selected: Mapping[str, str]) -> dict[str, Any]:
    """段の入力の記録（選んだ FAQ。回答の本文は回答の記録に残る）。"""
    return {"FAQ の ID": selected.get("id"), "FAQ の質問": selected.get("question")}


# 質問の補完（チャットだけ）。安全チェックの後の質問と結果を受け取り、補完した質問と token を返す。
PrepareQuestion = Callable[[str, GuardrailResult], Awaitable[tuple[str, str | None]]]


async def approved_faq_candidates(
    oracle: OracleClient,
    search_answer_profile_id: str,
    query: str,
    settings: Settings,
    *,
    prepare: PrepareQuestion | None = None,
) -> tuple[list[ApprovedFaqSuggestion], str | None]:
    """回答の前に提示する登録済みQ&Aの候補と、補完の token（画面の照会・MCP で共通。#5107）。

    入口の順は回答と同じ: 入力の安全チェック → 質問の補完（``prepare``。チャットだけ）→ FAQ の
    照合。候補は一致度が ``RAG_APPROVED_FAQ_MIN_SCORE`` 以上のものを最大
    ``RAG_APPROVED_FAQ_MAX_SUGGESTIONS`` 件。提示がオフ・FAQ が無いときは、安全チェック・補完・
    照合をせずに空を返す。止めた質問も空。
    """
    loaded = await load_approved_faq(oracle, search_answer_profile_id)
    payload, records = loaded
    if not approved_faq_enabled(payload) or not records:
        return [], None
    checked = await check_question_text(query, settings)
    if not checked.allowed:
        return [], None
    question, token = checked.sanitized_text, None
    if prepare is not None:
        question, token = await prepare(question, checked)
    suggestions = await suggest_approved_faq(
        oracle,
        search_answer_profile_id,
        question,
        loaded=loaded,
        **approved_faq_match_options(settings, lambda: OciGenAiClient(settings=settings)),
    )
    return suggestions, token
