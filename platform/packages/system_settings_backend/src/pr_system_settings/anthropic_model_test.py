"""クォータナリ接続（Anthropic Claude の Messages API）のモデルの接続テスト（#5546）。

システム設定 > モデル の「テスト」で、クォータナリ接続のモデル
（`EnterpriseAiConnection.uses_anthropic_messages`）は製品の `run_model_test` を呼ばずに、
ここで `pr_anthropic_messages` を使って短い要求を 1 回送る（3 製品で同じ結果になる）。
入力は OpenAI 互換の接続のテストと同じ 1 セット（`model_test_input`）。推論の量と出力の上限は
テストするモデルの設定（保存前の入力値）を送り、受け付けない値を保存の前に確かめられるようにする。

失敗は `AnthropicMessagesError.kind`（`pr_enterprise_ai` の分類）から、クォータナリ接続向けの
案内の文にする。OCI の IAM policy・Project OCID を前提にした文（OpenAI 互換の接続の案内）は
使わない。
"""

from __future__ import annotations

from typing import Final

from pr_anthropic_messages import (
    AnthropicImage,
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    acreate_anthropic_message,
)

from .model_test_input import (
    MODEL_TEST_IMAGE_BYTES,
    MODEL_TEST_IMAGE_MIME_TYPE,
    MODEL_TEST_TEXT_PROMPT,
    MODEL_TEST_VISION_PROMPT,
)

# 失敗の種類（`AnthropicMessagesError.kind`）ごとの案内。`{endpoint}` は呼んだ Endpoint URL
# （空の設定では既定の URL）。secret を含めない。
_TIPS_BY_KIND: Final[dict[str, str]] = {
    "authentication": (
        "API key が接続先に受け付けられませんでした。クォータナリ接続の API key が有効か"
        "（入力の誤り・失効・削除）を確かめ、必要なら発行し直して保存してください。"
    ),
    "authorization": (
        "この API key では、このモデルを呼び出す権限がありません。API key の属する組織・"
        "workspace でこのモデルが使えるかを確かめてください。"
    ),
    "model_not_found": (
        "モデル ID が見つからないか、この API key では使えません。登録モデルのモデル ID が接続先の"
        " Messages API で使える ID か（綴り・提供の終了）を確かめてください。"
    ),
    "request": (
        "要求が受け付けられませんでした。接続の応答（raw error）を確かめ、登録モデルの推論の量"
        "（思考を受け付けないモデルは「なし」）と出力の上限がモデルに合っているか、利用の残高・"
        "上限を確かめてください。"
    ),
    "rate_limit": (
        "呼び出しの上限（rate limit）に達しました。しばらく待って再試行し、接続先の利用の上限を"
        "確かめてください。"
    ),
    "unavailable": (
        "接続先のサービスが一時的に応答できません（過負荷・障害）。少し待って再試行してください。"
    ),
    "timeout": (
        "応答が待ち時間の上限までに届きませんでした。ネットワーク経路を確かめ、必要ならタイムアウト"
        "秒数を一時的に長くしてください。"
    ),
    "connection": (
        "接続先（{endpoint}）へ接続できませんでした。バックエンドの実行環境から Endpoint URL の"
        "ホストへ到達できるか（DNS・proxy・firewall）を確かめてください。"
    ),
    "response_format": (
        "応答の形式が想定と違います。Endpoint URL（{endpoint}）が Anthropic の Messages API を"
        "指しているか（base URL まで。`/v1` は含めない）を確かめてください。"
    ),
    "refusal": "モデルがテストの入力への応答を拒否しました。別のモデルで確かめてください。",
    "incomplete": (
        "出力の上限で応答が途中で終わりました。登録モデルの出力の上限を広げるか、推論の量を"
        "下げてください。"
    ),
}
# 404 で本文がモデルを指していない（`request`）ときは、Endpoint URL の path の誤りが多い。
_NOT_FOUND_PATH_TIP: Final = (
    "接続先に Messages API の path が見つかりません。Endpoint URL（{endpoint}）は base URL まで"
    "書き、`/v1` や `/v1/messages` を含めないでください（SDK が `/v1/messages` を足します）。"
)
_UNKNOWN_FAILURE_TIP: Final = (
    "接続の応答（raw error）を確かめ、クォータナリ接続の Endpoint URL と API key を"
    "確かめてください。"
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
    """失敗からクォータナリ接続向けの案内を返す（OCI の IAM 前提の文を使わない）。"""
    if not isinstance(exc, AnthropicMessagesError):
        return [_UNKNOWN_FAILURE_TIP]
    if exc.kind == "request" and exc.status_code == 404:
        return [_NOT_FOUND_PATH_TIP.format(endpoint=endpoint)]
    tip = _TIPS_BY_KIND.get(exc.kind, _UNKNOWN_FAILURE_TIP)
    return [tip.format(endpoint=endpoint)]
