"""OCI Enterprise AI の HTTP の失敗の分類と、確かめる所の案内（#1684。3 製品で共通の対応表）。

3 製品は OCI Enterprise AI（OpenAI 互換の `/responses`）の失敗を、ここで分類して案内する。
分類は status code と応答の本文で決め、設定の誤り（時間をおいても直らない）と一時的な失敗を分ける。

2026-10-13 の実測（us-chicago-1 / ap-osaka-1 の `/openai/v1/responses`。#1684）:
- 実在しない・そのリージョンに無いモデル: 400 `Entity with key <model> not found`
  （例: 大阪の `xai.grok-4.3` / `xai.grok-4.6`、両リージョンの `xai.grok-9.9`）
- 無効な API key・別のリージョンの API key: 401 `The required information to complete
  authentication was not provided or was incorrect.`
- 有効な API key でも、その key に推論を許す IAM policy が無い: 404 `Authorization failed or
  requested resource not found.`（実在するモデルでも 404。モデルが無いときの 400 とは違う）
- 非 OpenAI のモデルで `OpenAI-Project` ヘッダーが無い: 400 `invalid_value`

OCI の 404 は「モデルが無い」ではなく「認可されない」ことが多いので、403 と同じ `authorization` に
分ける。OpenAI 互換の別の API（ターシャリ接続など）の 404 は Endpoint URL / API パスの誤りのことも
あるので、案内に両方を書く。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import urlsplit

EnterpriseAiFailureKind = Literal[
    "model_not_found",
    "authentication",
    "authorization",
    "rate_limit",
    "unavailable",
    "request",
]

# 再試行してよい status code（一時的な失敗）。これ以外の 4xx は再試行しても直らない。
ENTERPRISE_AI_RETRYABLE_STATUS_CODES: Final = frozenset({429, 500, 502, 503, 504})
# 設定（モデル・接続・API key・IAM policy）の誤りで、時間をおいても直らない種類。
ENTERPRISE_AI_CONFIGURATION_FAILURE_KINDS: Final[frozenset[EnterpriseAiFailureKind]] = frozenset(
    {"model_not_found", "authentication", "authorization"}
)
# 種類ごとの error code（ジョブ・API の `error_code`。3 製品で同じ値）。
ENTERPRISE_AI_FAILURE_ERROR_CODES: Final[dict[EnterpriseAiFailureKind, str]] = {
    "model_not_found": "ENTERPRISE_AI_MODEL_NOT_FOUND",
    "authentication": "ENTERPRISE_AI_AUTHENTICATION_FAILED",
    "authorization": "ENTERPRISE_AI_AUTHORIZATION_FAILED",
    "rate_limit": "ENTERPRISE_AI_RATE_LIMITED",
    "unavailable": "ENTERPRISE_AI_UNAVAILABLE",
    "request": "ENTERPRISE_AI_REQUEST_REJECTED",
}

_MODEL_NOT_FOUND_RE: Final = re.compile(r"entity with key .+ not found", re.IGNORECASE)
# 3 製品の失敗の文の書き方: NL2SQL / Agent `OCI Enterprise AI HTTP 404: …`、
# RAG `… returned 404: …`、httpx の既定 `Client error '404 Not Found' for url …`。
_HTTP_STATUS_RE: Final = re.compile(
    r"\bHTTP (\d{3})\b|\breturned (\d{3})\b|\b(?:Client|Server) error '(\d{3})\b"
)


@dataclass(frozen=True)
class EnterpriseAiHttpFailure:
    """OCI Enterprise AI の HTTP の失敗の分類。"""

    kind: EnterpriseAiFailureKind
    status_code: int

    @property
    def retryable(self) -> bool:
        """再試行で直る見込みがあるか（429 / 5xx）。"""
        return self.status_code in ENTERPRISE_AI_RETRYABLE_STATUS_CODES

    @property
    def configuration_error(self) -> bool:
        """設定の誤りか（時間をおいても直らない）。"""
        return self.kind in ENTERPRISE_AI_CONFIGURATION_FAILURE_KINDS

    @property
    def error_code(self) -> str:
        return ENTERPRISE_AI_FAILURE_ERROR_CODES[self.kind]


def classify_enterprise_ai_http_failure(
    status_code: int, body: str = ""
) -> EnterpriseAiHttpFailure:
    """HTTP の失敗（status code と応答の本文）を分類する。"""
    if status_code == 401:
        kind: EnterpriseAiFailureKind = "authentication"
    elif status_code in {403, 404}:
        kind = "authorization"
    elif status_code == 429:
        kind = "rate_limit"
    elif status_code >= 500:
        kind = "unavailable"
    elif status_code == 400 and _MODEL_NOT_FOUND_RE.search(body or ""):
        kind = "model_not_found"
    else:
        kind = "request"
    return EnterpriseAiHttpFailure(kind=kind, status_code=status_code)


def enterprise_ai_http_status_from_message(message: str) -> int | None:
    """`OCI Enterprise AI HTTP 404: …` のような文から status code を取り出す（無ければ None）。"""
    match = _HTTP_STATUS_RE.search(message or "")
    if match is None:
        return None
    return int(next(group for group in match.groups() if group))


def enterprise_ai_endpoint_host(endpoint: str) -> str:
    """案内に出す接続の名前（Endpoint URL のホスト。path・query は出さない）。"""
    text = (endpoint or "").strip()
    if not text:
        return ""
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return ""
    return host


def enterprise_ai_failure_actions(
    failure: EnterpriseAiHttpFailure,
    *,
    model_id: str = "",
    endpoint_host: str = "",
) -> list[str]:
    """失敗の種類ごとの、確かめる所の案内（利用者・管理者向けの日本語の文）。"""
    model = f"「{model_id}」" if model_id else ""
    connection = f"（{endpoint_host}）" if endpoint_host else ""
    status = failure.status_code
    if failure.kind == "model_not_found":
        return [
            f"モデル{model}は、この接続{connection}のリージョンにありません（HTTP {status}）。"
            "システム設定の「モデル」で、接続のリージョンにあるモデル ID を登録するか、"
            "モデルを登録する接続を変えてください。"
        ]
    if failure.kind == "authentication":
        return [
            f"接続{connection}の API key が受け付けられませんでした（HTTP {status}）。"
            "システム設定の「モデル」で、接続の API key が Endpoint URL と同じリージョンで"
            "発行した有効な key か確かめ、保存し直してください。"
        ]
    if failure.kind == "authorization":
        return [
            f"接続{connection}でモデル{model}の呼び出しが認可されませんでした（HTTP {status}）。",
            "OCI の IAM で、この API key（request.principal.type = 'generativeaiapikey'）に"
            "Generative AI の推論を許す policy が、Project の compartment にあるか"
            "確かめてください。",
            "システム設定の「モデル」で、接続の Project OCID が Endpoint URL と同じリージョンの"
            " Project か確かめてください。OpenAI 互換の別の API では、Endpoint URL と API パスも"
            "確かめてください。",
        ]
    if failure.kind == "rate_limit":
        return [
            f"レート制限または quota の可能性があります（HTTP {status}）。"
            "しばらく待つか service limit を確認してください。"
        ]
    if failure.kind == "unavailable":
        return [
            f"サービス側または gateway 側の一時障害の可能性があります（HTTP {status}）。"
            "少し待って再試行し、OCI 側の稼働状況を確認してください。"
        ]
    return [
        f"リクエストが受け付けられませんでした（HTTP {status}）。"
        "「詳細」の応答の理由（モデルが受け付けない設定など）を確かめてください。"
    ]
