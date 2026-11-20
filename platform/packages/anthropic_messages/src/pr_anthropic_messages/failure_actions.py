"""クォータナリ接続（Anthropic Claude の Messages API）の失敗の、確かめる所の案内（#5587）。

失敗の種類（`AnthropicMessagesError.kind`。pr_enterprise_ai の分類）と HTTP status から、
利用者・管理者向けの日本語の文を返す。システム設定 › モデル の「テスト」と、3 製品の失敗の案内
（NL2SQL の失敗の文・上限つきの並行の実行器の hint）が同じ文を使う。OCI の IAM policy・
Project OCID・リージョンを前提にした文（OpenAI 互換の接続の案内。
`pr_enterprise_ai.errors.enterprise_ai_failure_actions`）は使わない。
"""

from __future__ import annotations

from typing import Final

from pr_anthropic_messages.messages import AnthropicMessagesError

__all__ = [
    "anthropic_failure_actions",
    "anthropic_failure_hint",
    "find_anthropic_error",
]

# 失敗の種類ごとの案内。`{where}` は呼んだ Endpoint URL を括弧で囲んだもの（分からなければ空）。
# secret を含めない。
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
        "接続先{where}へ接続できませんでした。バックエンドの実行環境から Endpoint URL の"
        "ホストへ到達できるか（DNS・proxy・firewall）を確かめてください。"
    ),
    "response_format": (
        "応答の形式が想定と違います。Endpoint URL{where}が Anthropic の Messages API を"
        "指しているか（base URL まで。`/v1` は含めない）を確かめてください。"
    ),
    "refusal": "モデルが入力への応答を拒否しました。別のモデルで確かめてください。",
    "incomplete": (
        "出力の上限で応答が途中で終わりました。登録モデルの出力の上限を広げるか、推論の量を"
        "下げてください。"
    ),
}
# 404 で本文がモデルを指していない（`request`）ときは、Endpoint URL の path の誤りが多い。
_NOT_FOUND_PATH_TIP: Final = (
    "接続先に Messages API の path が見つかりません。Endpoint URL{where}は base URL まで"
    "書き、`/v1` や `/v1/messages` を含めないでください（SDK が `/v1/messages` を足します）。"
)
_UNKNOWN_FAILURE_TIP: Final = (
    "接続の応答（raw error）を確かめ、クォータナリ接続の Endpoint URL と API key を"
    "確かめてください。"
)


def anthropic_failure_actions(
    kind: str | None, *, status_code: int | None = None, endpoint: str = ""
) -> list[str]:
    """失敗の種類と HTTP status から、クォータナリ接続向けの確かめる所の案内を返す。

    `kind` は `AnthropicMessagesError.kind`（分からなければ None）。`endpoint` は呼んだ
    Endpoint URL かそのホスト（空なら文に出さない）。
    """
    where = f"（{endpoint}）" if endpoint else ""
    if kind == "request" and status_code == 404:
        return [_NOT_FOUND_PATH_TIP.format(where=where)]
    tip = _TIPS_BY_KIND.get(kind or "", _UNKNOWN_FAILURE_TIP)
    return [tip.format(where=where)]


def find_anthropic_error(exc: BaseException | None) -> AnthropicMessagesError | None:
    """例外（原因の連鎖を含む）から、Messages API の呼び出しの失敗を探す（無ければ None）。"""
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, AnthropicMessagesError):
            return current
        current = current.__cause__ or current.__context__
    return None


def anthropic_failure_hint(exc: BaseException) -> str | None:
    """失敗が Messages API の呼び出しのものなら、その案内の文（そうでなければ None）。

    上限つきの並行の実行器（`pr_backend_core.bounded_calls.chain_classifiers` の `hint`）に
    渡す形。Endpoint URL は分からないので文に出さない。
    """
    error = find_anthropic_error(exc)
    if error is None:
        return None
    return " ".join(anthropic_failure_actions(error.kind, status_code=error.status_code))
