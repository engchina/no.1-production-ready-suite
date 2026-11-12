"""処理の経過の段の詳細（回答の記録の工程・パイプラインの工程。#2294 / #2341）。

`rag_engine` の工程の記録（``execution_steps[].details``）と、パイプラインの工程
（会話を踏まえた質問の書き換え・検索条件の抽出・回答の安全チェック）の詳細を、
3 製品共通の詳細の形（``pr_backend_core.chat_progress`` の ``ChatProgressStepDetails``
= 画面の ``RunStepDetails``: 見出しと値 ``fields``・行 ``lines``・``truncated``）にそろえる。
上限で切り、秘密らしい値を伏せる（共通の規則）。旧い記録の行の list（#2294）は行として読む。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pr_backend_core.chat_progress import ChatProgressStepDetails, chat_progress_details


def step_details_model(value: object) -> ChatProgressStepDetails | None:
    """記録の詳細（共有の形の dict か旧い行の list）を詳細の model にする。無ければ None。"""

    if isinstance(value, list | tuple):
        data: dict[str, Any] = {"lines": [item for item in value if isinstance(item, str)]}
    elif isinstance(value, Mapping):
        data = {
            "fields": value.get("fields"),
            "lines": value.get("lines"),
            "code": value.get("code"),
            "truncated": value.get("truncated") is True or None,
        }
    else:
        return None
    details = ChatProgressStepDetails.model_validate(data)
    return None if details.is_empty() else details


def step_details_payload(value: object) -> dict[str, Any] | None:
    """記録の詳細を保存の JSON（値の無い項目は省く）にする。中身が無ければ None。"""

    details = step_details_model(value)
    return details.model_dump() if details is not None else None


def pipeline_step_details(
    fields: Iterable[tuple[str, object]] = (), lines: Iterable[str] = ()
) -> dict[str, Any] | None:
    """パイプラインの工程の詳細（見出しと値・行）を保存の JSON にする。中身が無ければ None。"""

    details = chat_progress_details(fields=list(fields), lines=list(lines))
    return details.model_dump() if details is not None else None


def history_rewrite_details(
    rewritten: str, history: Iterable[Any], *, intent: str = "search"
) -> dict[str, Any] | None:
    """会話を踏まえた質問の書き換えの詳細（書き換えた質問・参照した会話の数・発話の種類）。"""

    from app.rag.small_talk import SMALL_TALK_REASONS

    turns = sum(1 for _ in history)
    fields: list[tuple[str, object]] = [
        ("参照した会話", f"{turns} 件の発言" if turns else "なし（会話の最初の発話）"),
        ("書き換えた質問", rewritten) if rewritten else ("結果", "書き換えなし（元の質問のまま）"),
    ]
    reason = SMALL_TALK_REASONS.get(intent)
    if reason is not None:
        fields.append(("発話の種類", f"{reason}（モデルによる分類）"))
    return pipeline_step_details(fields=fields)


def small_talk_details(intent: str, *, replied: bool) -> dict[str, Any] | None:
    """検索せずに応じた工程の詳細（理由を含めた結果。畳んだ行にも出る）。"""

    from app.rag.small_talk import SMALL_TALK_REASONS

    reason = SMALL_TALK_REASONS.get(intent, intent)
    return pipeline_step_details(
        fields=[
            ("結果", f"{reason}のため検索せずに応じました（引用なし）")
            if replied
            else ("結果", f"{reason}への応答を作れなかったため、検索して回答します"),
        ]
    )


def faq_direct_details(match: Any) -> dict[str, Any] | None:
    """承認済み FAQ との照合の詳細（結果・一致度と下限・否定の質問で外した FAQ）。

    ``match`` は ``DirectFaqMatch``（1 位の候補 ``best``・下限 ``min_score``・回答に使う候補
    ``answer``・否定の質問で外した候補 ``negative_excluded``）。
    """

    best = match.best
    hit = match.answer
    threshold = f"下限 {match.min_score:.2f}"
    fields: list[tuple[str, object]] = []
    if hit is not None:
        fields.append(
            ("結果", "登録済みQ&Aの回答をそのまま返しました（検索と回答の生成はしていません）")
        )
        fields.append(("一致した Q&A", hit.record.question))
        fields.append(("一致度", f"{hit.score:.2f}（{threshold}）"))
    else:
        fields.append(("結果", "下限以上で一致する登録済みQ&Aが無いため、検索して回答します"))
        fields.append(
            ("最も近い Q&A の一致度", f"{best.score:.2f}（{threshold}）")
            if best is not None
            else ("最も近い Q&A", f"候補なし（{threshold}）")
        )
    excluded = list(match.negative_excluded)
    if excluded:
        fields.append(("当てはめない質問に当たって外した Q&A", f"{len(excluded)} 件"))
    return pipeline_step_details(
        fields=fields,
        lines=[
            f"{item.record.question}（当てはめない質問: {item.negative_question}）"
            for item in excluded
        ],
    )


def answer_guardrail_details(result: Any, answer: str) -> dict[str, Any] | None:
    """回答の安全チェックの結果（止めた・警告の件数・指摘・伏せた箇所）。"""

    findings = list(getattr(result, "findings", ()) or ())
    allowed = bool(getattr(result, "allowed", True))
    unavailable = bool(getattr(result, "backend_degraded", False))
    errors = sum(1 for item in findings if getattr(item, "severity", "") == "error")
    skipped_reason = getattr(result, "skipped_reason", None)
    if skipped_reason is not None:
        verdict = {
            "disabled": "無効のため省略",
            "empty_text": "検査する回答がないため省略",
            "client_unavailable": "検査の接続がないため省略",
        }.get(skipped_reason, "省略")
    elif unavailable:
        verdict = "安全検査を完了できず、回答を止めました"
    elif not allowed:
        verdict = "回答を止めました"
    elif findings:
        verdict = f"警告 {len(findings)} 件（回答は出します）"
    else:
        verdict = "問題なし"
    fields: list[tuple[str, object]] = [("結果", verdict)]
    if errors and allowed:
        fields.append(("重大な指摘", f"{errors} 件"))
    if (
        allowed
        and not unavailable
        and any(getattr(item, "code", "") == "oci_pii_detected" for item in findings)
        and getattr(result, "sanitized_text", answer) != answer
    ):
        fields.append(("回答の書き換え", "機密らしい箇所を伏せました"))
    if unavailable:
        fields.append(("外部の検査", "OCI Guardrails を利用できませんでした"))
    messages = dict.fromkeys(str(getattr(item, "message", "") or "") for item in findings)
    return pipeline_step_details(fields=fields, lines=[message for message in messages if message])
