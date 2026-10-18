"""回答の注意書き（3 製品で共通の文。#2405）。

文はシステム設定の「回答の注意書き」（platform の `pr_system_settings.answer_notice`）で決まる。
RAG は回答の確定（回答側の安全チェック）の後に、回答とは別の項目として付ける。モデルへの指示・
会話の履歴・回答の検証には入れない。確認の質問（`needs_clarification`）には付けない。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from pr_system_settings.answer_notice import DEFAULT_ANSWER_NOTICE_TEXT

logger = logging.getLogger(__name__)

AnswerNoticeSource = Callable[[], str | None]
# 回答ではなく確認の質問の outcome（回答の封筒の `outcome`）。注意書きを付けない。
NOTICE_EXCLUDED_OUTCOMES = frozenset({"needs_clarification"})


def current_answer_notice() -> str | None:
    """いまの注意書きの文（無効なら None）。設定を読めないときは既定の文（回答は止めない）。"""
    try:
        from app.security.service import get_security_service

        return get_security_service().answer_notice.current_text()
    except Exception as exc:  # 共通認証の service を作れない構成でも回答は返す
        logger.warning("answer_notice_unavailable", extra={"error_type": type(exc).__name__})
        return DEFAULT_ANSWER_NOTICE_TEXT


def answer_outcome_takes_notice(diagnostics: Mapping[str, object]) -> bool:
    """回答の診断の outcome が注意書きを付ける回答か（確認の質問は付けない）。"""
    return diagnostics.get("outcome") not in NOTICE_EXCLUDED_OUTCOMES
