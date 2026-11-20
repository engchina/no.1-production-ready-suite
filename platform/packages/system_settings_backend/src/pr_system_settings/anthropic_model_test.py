"""クォータナリ接続（Anthropic Claude の Messages API）のモデルの接続テスト（#5546）。

システム設定 > モデル の「テスト」で、クォータナリ接続のモデル
（`EnterpriseAiConnection.uses_anthropic_messages`）は製品の `run_model_test` を呼ばずに、
ここで `pr_anthropic_messages` を使って短い要求を 1 回送る（3 製品で同じ結果になる）。
入力は OpenAI 互換の接続のテストと同じ 1 セット（`model_test_input`）。推論の量と出力の上限は
テストするモデルの設定（保存前の入力値）を送り、受け付けない値を保存の前に確かめられるようにする。

失敗は `AnthropicMessagesError.kind`（`pr_enterprise_ai` の分類）から、クォータナリ接続向けの
案内の文（`pr_anthropic_messages.anthropic_failure_actions`。3 製品の失敗の案内と同じ。#5587）に
する。OCI の IAM policy・Project OCID を前提にした文（OpenAI 互換の接続の案内）は使わない。
"""

from __future__ import annotations

from pr_anthropic_messages import (
    AnthropicImage,
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    acreate_anthropic_message,
    anthropic_failure_actions,
)

from .model_test_input import (
    MODEL_TEST_IMAGE_BYTES,
    MODEL_TEST_IMAGE_MIME_TYPE,
    MODEL_TEST_TEXT_PROMPT,
    MODEL_TEST_VISION_PROMPT,
)


def anthropic_model_test_success_message(*, vision: bool, model_id: str) -> str:
    """成功の文（OpenAI 互換の接続の「Enterprise AI の…」と分ける）。"""
    surface = "画像対応モデル" if vision else "回答生成モデル"
    return f"Anthropic Claude の{surface}「{model_id}」から応答を取得しました。"


def anthropic_model_test_failure_message(model_id: str) -> str:
    """失敗の文。"""
    return f"Anthropic Claude のモデル「{model_id or '未入力'}」のテストに失敗しました。"


async def run_anthropic_model_test(
    *,
    model_id: str,
    vision: bool,
    api_key: str,
    endpoint: str,
    reasoning_effort: str,
    max_output_tokens: int,
    timeout_seconds: float,
) -> dict[str, str | int | float | bool | None]:
    """Messages API へ短い要求を 1 回送り、表示用の details を返す（失敗は例外のまま）。

    `endpoint` は接続の `effective_endpoint`（空の設定では既定の URL）。SDK の自動の再試行は
    使わない。
    """
    request = AnthropicMessagesRequest(
        model_id=model_id,
        user_text=MODEL_TEST_VISION_PROMPT if vision else MODEL_TEST_TEXT_PROMPT,
        images=(
            (AnthropicImage.from_bytes(MODEL_TEST_IMAGE_MIME_TYPE, MODEL_TEST_IMAGE_BYTES),)
            if vision
            else ()
        ),
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )
    result = await acreate_anthropic_message(
        request, api_key=api_key, endpoint_url=endpoint, timeout_seconds=timeout_seconds
    )
    return {
        "surface": "vision" if vision else "llm",
        "api": "anthropic_messages",
        "response_chars": len(result.text),
        "stop_reason": result.stop_reason,
        "input_tokens": result.usage.total_input_tokens,
        "output_tokens": result.usage.output_tokens,
    }


def anthropic_model_test_failure_details(
    exc: BaseException,
) -> dict[str, str | int | float | bool | None]:
    """失敗の種類と HTTP status を details に出す（分類できない例外は空）。"""
    if not isinstance(exc, AnthropicMessagesError):
        return {}
    return {"failure_kind": exc.kind, "http_status": exc.status_code}


def anthropic_model_test_troubleshooting(exc: BaseException, *, endpoint: str) -> list[str]:
    """失敗からクォータナリ接続向けの案内を返す（3 製品の失敗の案内と同じ文。#5587）。"""
    if not isinstance(exc, AnthropicMessagesError):
        return anthropic_failure_actions(None, endpoint=endpoint)
    return anthropic_failure_actions(exc.kind, status_code=exc.status_code, endpoint=endpoint)
