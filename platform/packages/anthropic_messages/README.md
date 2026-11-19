# production-ready-anthropic-messages (`pr_anthropic_messages`)

Anthropic Claude の Messages API を、公式の Python SDK（`anthropic`）で呼ぶ共通の部品（#5546 の 2 段目）。システム設定 › モデル の「クォータナリ接続」（Anthropic Claude 用）を選んだ登録モデルを、3 製品の呼び出しの層がこの部品で呼ぶ（製品への接続は 3 段目の PR）。OpenAI 互換の shim は使わない。

- 3 製品の OCI Enterprise AI の層（Responses API）と同じ形の結果を返す: 本文・usage（Responses API の形にも写せる）・停止の理由・失敗の分類（`pr_enterprise_ai` の種類と error code）。
- 製品ごとの処理の流れ（プロンプト・再試行の方針・出力の上限の決め方）は変えない。この部品は 1 回の呼び出しを組み、応答と失敗を写すだけ。SDK の自動の再試行は既定で使わない（`max_retries=0`）。
- 依存の向き: `anthropic` と `pr_enterprise_ai` だけに依存し、製品・suite・ほかの platform の package を import しない。

## 置き場所

`pr_enterprise_ai` は標準 library だけ（suite の評価が FastAPI なしで使う）なので入れない。`pr_backend_core` は RAG の前処理・parser のサービス（9 個の独立した venv）も依存するので、`anthropic` をそこへ足すと使わない venv にも入る。そのため独立の package にし、呼ぶ層（3 製品の backend・RAG の package）だけが path で依存する。

## 使い方

```python
from pr_anthropic_messages import (
    AnthropicImage,
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    create_anthropic_message,  # 非同期は acreate_anthropic_message（引数・結果は同じ）
)

result = create_anthropic_message(
    AnthropicMessagesRequest(
        model_id=model_id,
        system=system_prompt,
        user_text=prompt,
        images=(AnthropicImage.from_data_url(data_url),),
        response_schema=schema,  # JSON schema（無ければ None）
        reasoning_effort=reasoning_effort,  # 設定の値 none / low / medium / high
        max_output_tokens=budget,
    ),
    api_key=connection.api_key,
    endpoint_url=connection.endpoint_url,  # 空なら SDK の既定（https://api.anthropic.com）
    timeout_seconds=timeout_seconds,
)
result.text  # 構造化出力なら JSON の文字列（Pydantic で検証して使う）
result.usage.as_responses_usage()  # llm_call_usage・record_genai_usage に渡す
```

失敗は `AnthropicMessagesError`（`kind`・`retryable`・`configuration_error`・`error_code`・`incomplete`・`http_failure`）。`incomplete` は `enterprise_ai_incomplete_message` に、`http_failure` は `enterprise_ai_failure_actions` にそのまま渡せる。

## 要求の組み方

| 入力 | Messages API |
|---|---|
| `system` | `system`（空なら送らない） |
| `user_text`・`images` | 1 つの user の message（画像の base64 の block を本文の前に置く） |
| `response_schema` | `output_config.format`（`{"type": "json_schema", "schema": ...}`） |
| `reasoning_effort` | 下の対応表 |
| `max_output_tokens` | `max_tokens` |
| `endpoint_url` | 空なら SDK の既定、指定があれば `base_url`（前後の空白と末尾の `/` だけを除く。`/v1` は SDK が足すので含めない） |
| `api_key` | SDK の client に渡すだけ（空なら呼ばずに `authentication` の失敗。環境変数の credential は読まない） |

常に streaming（`messages.stream()` と `get_final_message()`）で送る（出力が長くても HTTP の時間切れにならない）。`temperature` などのサンプリングの引数は送らない。拒否の時の別モデルへの切り替え（server-side fallback）は使わない（呼ぶモデル・接続を暗黙に変えない）。

### 推論の量（effort）の対応表

| 設定の値（`reasoning_effort`） | `thinking` | `output_config.effort` |
|---|---|---|
| `none`・空 | 送らない | 送らない（モデルの既定。Claude Opus 5.5 は adaptive・medium、Claude Opus 4.8 / 4.7 は思考なし） |
| `low` | `{"type": "adaptive"}` | `low` |
| `medium` | `{"type": "adaptive"}` | `medium` |
| `high` | `{"type": "adaptive"}` | `high` |
| `xhigh`・`max`（設定の選択肢には無い） | `{"type": "adaptive"}` | そのまま |

## 応答・失敗の写し方

| 応答・SDK の例外 | 結果・種類（kind） |
|---|---|
| `stop_reason` が `end_turn`・`stop_sequence` | 成功。本文は text の block をつなぐ（思考の block は使わない） |
| 本文が空 | `response_format` |
| `stop_reason=refusal` | `refusal`（`stop_details.category` を `refusal_category` に） |
| `stop_reason=max_tokens` | `incomplete`（理由 `max_output_tokens`。製品の「上限を広げて送り直す」判定がそのまま使える） |
| `stop_reason=model_context_window_exceeded` ほか | `incomplete`（理由はその値） |
| `APITimeoutError` / `APIConnectionError` | `timeout` / `connection` |
| `AuthenticationError`（401）/ `PermissionDeniedError`（403） | `authentication` / `authorization` |
| `NotFoundError`（404） | 本文が `model:` で始まれば `model_not_found`、ほかは `request` |
| `RateLimitError`（429）/ `InternalServerError`（5xx・529） | `rate_limit` / `unavailable` |
| ほかの `APIStatusError` | `error.type` が `rate_limit_error` なら `rate_limit`、`overloaded_error`・`api_error`（streaming の途中の error event）なら `unavailable`、ほかは `request` |

usage: `input_tokens` は cache の外の入力（Messages API の意味）、`total_input_tokens` と `as_responses_usage()` の `input_tokens` は cache の読み書きを含む全体、`thinking_tokens` は出力のうち思考の token（`output_tokens_details.reasoning_tokens` に写す）。

## テスト

```bash
uv sync --dev
uv run pytest -n auto
uv run ruff format --check . && uv run ruff check . && uv run mypy src
```

テストは SDK の HTTP（`httpx2`）を `MockTransport` で録画の応答（Messages API の SSE の event の並び）に置き換え、実の API は呼ばない。
