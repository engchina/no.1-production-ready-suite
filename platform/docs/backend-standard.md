# Backend standard（RAG を正本）

3 サービス（RAG / NL2SQL / Agent）の backend は同一スタックと同一プラットフォーム挙動を共有する。
正本は RAG backend。新サービスは技術選定をやり直さず、本標準と
[`packages/backend_core`](../packages/backend_core)（`production-ready-backend-core`）から派生する。

## 確定スタック

| 層 | 採用 |
|---|---|
| Language / PM | Python 3.12 / **uv** |
| Web | FastAPI（本番: Gunicorn + `uvicorn.workers.UvicornWorker` / 開発: `uvicorn --reload`） |
| Validation / Config | Pydantic v2 + pydantic-settings（`.env` + 任意 JSON 設定） |
| HTTP client | httpx |
| DB / Vector | Oracle 26ai + python-oracledb（AI Vector Search / Oracle Text） |
| LLM・VLM | OCI Enterprise AI |
| Embedding・Rerank | OCI Generative AI（Cohere） |
| Observability | Prometheus metrics + JSON logging + request-id |
| Test / Lint / Type | pytest(+asyncio,cov) / Ruff + Black / mypy strict |
| Security | Bandit + pip-audit + gitleaks |
| CI | GitHub Actions（`uv sync --locked`） |

> ⚠️ LLM/VLM = Enterprise AI、embedding/rerank = OCI GenAI、ベクトル DB = Oracle 26ai。
> 外部ベクトル DB・別 LLM provider は導入しない（各業務 repo のルールに従う）。

## 役割分担（backend_core ↔ 業務 repo）

| backend_core（共通） | 業務 repo（features/*） |
|---|---|
| app factory `create_app` / CORS / security | ドメイン API ルーター |
| `/api/health` `/api/ready` / `/metrics` | readiness の業務依存チェック追加 |
| JSON logging / request-id / trace | プロジェクト固有 DB schema |
| 例外 → `ApiResponse` 統一 / pagination | RAG: ingestion/chunking/retrieval/eval |
| `ApiResponse` / `Page` / `HealthData` envelope | NL2SQL: NL→SQL / SQL 検証・実行 |
| `BaseServiceSettings` | Agent: run orchestration / tool registry |

## 統一 API レイアウト

```
backend/
  app/
    main.py            # create_app で薄く構成
    settings.py        # BaseServiceSettings を継承
    readiness.py       # 依存設定チェック
    api/router.py      # 業務ルーター集約（/api 配下）
    features/<domain>/ # RAG / NL2SQL / Agent の業務
  tests/
  pyproject.toml
  uv.lock
  .env.example
  Dockerfile           # 本番 Gunicorn + UvicornWorker
```

共通エンドポイント: `GET /api/health` `GET /api/ready` `GET /metrics`。
業務エンドポイントは `features/<domain>` 配下に置く。

## 製品間の連携（MCP とサービストークン、#230）

Agent が RAG / NL2SQL を呼ぶときは、呼び先の `POST /api/mcp`（MCP の Streamable HTTP、JSON 応答）を使う。製品同士はコードで依存しない。

- サーバー: `pr_backend_core.mcp` の `McpServer` / `McpTool`（入力は Pydantic model、`permissions` はグループをすべて満たす・グループ内はどれか）と `mcp_http_response`。`initialize` / `ping` / `tools/list` / `tools/call` だけを扱う。`HTTPException` / `SecurityApiError` は `isError: true` の `structuredContent.error_code` / `message` / `status` になる。
- 認証: 呼び出し元は `pr_system_settings.auth.service_token.issue_service_token` で `sub` = 利用者の `user_uuid`、`aud` = 呼び先（`rag` / `nl2sql`）の短命の token（HS256、既定 60 秒）を作り、`Authorization: Bearer` で送る。鍵は共通 `.env` の `PLATFORM_SERVICE_TOKEN_SECRET`（32 文字以上。空なら 503）。
- 呼び先は `authorize_request(..., service_token_paths={"/mcp"}, service_token_audience="<製品>")` を渡す。その path では Cookie の代わりに token の利用者を `principal_for_worker` で組み立てる（現在のロール・権限・対象範囲を使い、無効・初回パスワード変更待ちの利用者は 403）。Cookie を使わないので CSRF は照合しない。claims（`run_id` など）は `request.state.service_token_claims`。
- ツールの権限と対象範囲（業務ビュー / KB / 業務プロファイル / DeepSec）は、画面と同じ service 層で判定する。
- ツールの契約（名前・`inputSchema`・`outputSchema`（`McpTool.output_model`。#250）・`annotations`）の正本は `platform/contracts/mcp/<製品>-tools.json`（#248）。RAG / NL2SQL の `tests/test_mcp_contract.py` が実装と一致を、Agent の `tests/test_product_mcp_contract.py` が送る引数の収まりと、読む出力の項目（入れ子を含む）が呼び先の出力にあることを確かめる。ツールを変えたら呼び先の製品で `UPDATE_MCP_CONTRACT=1 uv run pytest tests/test_mcp_contract.py` で契約を書き直し、Agent のテストも通す。

## 統一レスポンス envelope

```jsonc
// 成功
{ "data": { /* ... */ }, "error_messages": [], "warning_messages": [] }
// 失敗（HTTPException / 検証 / 未処理例外を統一）
{ "data": null, "error_messages": ["..."], "warning_messages": [] }
```

`X-Request-ID` ヘッダを常に付与（受信値を検証し、無ければ発行）。フロントの共通エラーハンドリングはこの形を前提にできる。

## 依存方法（dev は path source）

各 backend の `pyproject.toml`:

```toml
dependencies = ["production-ready-backend-core"]

[tool.uv.sources]
production-ready-backend-core = { path = "../../platform/packages/backend_core", editable = true }
```

> path source 変更時は **`uv lock` 再生成**。monorepo なので CI もローカルも同じ相対パス（`../../platform/…`）で解決する。
