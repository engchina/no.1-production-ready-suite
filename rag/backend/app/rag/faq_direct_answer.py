"""承認済み FAQ の回答をそのまま返す段（#4788）。

チャットの毎回の発話で通す質問の補完（``history_rewrite``）の後、補完した質問を検索・回答
プロファイルの承認済み FAQ と照合する。一致度が下限以上なら、検索・根拠の読み取り・回答の生成を
通さず、FAQ の承認済みの回答をそのまま返す（LLM を呼ばない。引用なし）。プロファイルの設定が
オフ（既定）なら照合もしない。下限未満・否定の質問に当たった FAQ だけのときは、いつもの検索と
回答に進む。
"""

from __future__ import annotations

from typing import Any

from rag_engine.generation.answer_envelope import ENVELOPE_SCHEMA_VERSION
from rag_engine.knowledge.approved_faq import ApprovedFaqSuggestion

from app.rag.answer_engine import AnswerOutcome, approved_faq_answer_text
from app.rag.search_answer_profile_knowledge import DirectFaqMatch

# 処理の経過の段（SSE の ``stage``）。
FAQ_DIRECT_STAGE = "faq_direct_match"


def direct_faq_outcome(match: DirectFaqMatch, *, embedding_model: str) -> AnswerOutcome | None:
    """下限以上で一致した FAQ の回答（そのまま）。一致が無ければ None。"""
    hit = match.answer
    if hit is None:
        return None
    record = hit.record
    return AnswerOutcome(
        # 承認済みの回答をそのまま使い、利用者が類似問を選んだ回答と同じ出典の行を足す。
        answer=approved_faq_answer_text(record.approved_answer, record.question),
        citations=[],
        diagnostics={
            "answer_source": "approved_faq",
            # 承認済みの回答をそのまま使うので、対応は「答えた」。
            "outcome": "answered",
            "envelope": {"schema_version": ENVELOPE_SCHEMA_VERSION, "outcome": "answered"},
            "approved_faq_question": record.question,
            # 回答した時点の承認済みの回答。FAQ が変わっても根拠の原文を出せる。
            "approved_faq_answer": record.approved_answer,
            "approved_faq_direct": {
                "faq_id": record.id,
                "matched_question": hit.matched_question,
                "score": round(hit.score, 4),
                "min_score": match.min_score,
                "match_method": hit.match_method,
            },
            # 回答のモデルは呼んでいない。意味の照合をしたときだけ embedding のモデルを出す。
            "models": {
                "embedding": embedding_model if hit.semantic_score is not None else "",
                "rerank": "",
            },
        },
        # 回答の安全チェックが照合する根拠は、一致した FAQ だけ。
        context_text=f"類似問: {record.question}\n承認済みの回答: {record.approved_answer}",
    )


def _suggestion_snapshot(item: ApprovedFaqSuggestion) -> dict[str, Any]:
    return {
        "FAQ の ID": item.record.id,
        "FAQ の質問": item.record.question,
        "一致した質問": item.matched_question,
        "一致度": round(item.score, 4),
        "照合の方法": item.match_method,
        "最も近い否定の質問": item.negative_question,
        "否定の質問の一致度": round(item.negative_score, 4),
    }


def direct_faq_snapshot(match: DirectFaqMatch) -> dict[str, Any]:
    """段の出力の記録（照合の結果。回答の本文は回答の記録に残る）。"""
    return {
        "そのまま返す一致度の下限": match.min_score,
        "1 位の候補": _suggestion_snapshot(match.best) if match.best is not None else None,
        "回答をそのまま返した": match.answer is not None,
        "否定の質問で外した FAQ": [_suggestion_snapshot(item) for item in match.negative_excluded],
    }
