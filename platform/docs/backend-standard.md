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
production-ready-backend-core = { path = "../../no.1-production-ready-platform/packages/backend_core", editable = true }
```

> path source 変更時は **`uv lock` 再生成**。CI は platform を sibling に checkout して解決する
> （[`consume-in-ci.md`](./consume-in-ci.md)）。安定後は Release / index 配布へ切替可能。
