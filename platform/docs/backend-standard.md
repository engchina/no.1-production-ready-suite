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
| DB / Vector | Oracle AI Database + python-oracledb（Oracle AI Vector Search / Oracle Text）。対応バージョンは [terraform/README.md](../../terraform/README.md) |
| LLM・VLM | OCI Enterprise AI |
| Embedding・Rerank | OCI Generative AI（Cohere） |
| Observability | Prometheus metrics + JSON logging + request-id |
| Test / Lint / Type | pytest(+asyncio,cov) / Ruff + Black / mypy strict |
| Security | Bandit + pip-audit + gitleaks |
| CI | GitHub Actions（`uv sync --locked`） |

> ⚠️ LLM/VLM = Enterprise AI、embedding/rerank = OCI GenAI、ベクトル DB = Oracle AI Database。
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
```

Dockerfile は持たない（自前のコードは Docker イメージを作らない。#286 / #356）。本番は各製品の `init_script.sh` が
作る systemd の unit で、uv の venv（`uv sync --locked --no-dev`）の Gunicorn + UvicornWorker として動かす。

共通エンドポイント: `GET /api/health` `GET /api/ready` `GET /metrics`。
業務エンドポイントは `features/<domain>` 配下に置く。

FastAPI の API ドキュメント（Swagger UI `/docs`・ReDoc `/redoc`・`/openapi.json`）は、環境を問わず公開しない（#748）。
`create_app` は `docs_url` / `redoc_url` / `openapi_url` を `None` にする。`create_app` を使わずに `FastAPI(...)` を作るアプリ
（RAG の backend・parser / 前処理 / pipeline のサービス）も同じ 3 つを `None` で渡す。schema が要るテストは `app.openapi()` を使う。

### DB の状態 API（`GET /api/ready/database`、#325）

画面の DB ゲートが使う DB の状態は、3製品とも `pr_system_settings.database_status.build_database_status_router` で提供する（製品が独自に実装しない）。ログイン不要の公開 path にする（各製品の `PUBLIC_API_PATHS`）。

- 応答: `{status: ok|not_configured|unreachable|setup_required, check, detail, context_id, schema_status, adb_lifecycle_state}`。常に HTTP 200。
- 判定の順: `short_circuit`（DB を使わない構成）→ システム設定画面と同じ `database_readiness`（`ok` 以外は `not_configured`。接続は試さない）→ 製品の `test_connection`（bounded。失敗は `unreachable`）→ 製品の `schema_probe`（準備状態の確認）→ `ok`。
- `detail` は接続先・資格情報・Wallet の path を返さない（ORA / DPY / DPI のコードだけ）。`context_id` は接続先の値の SHA-256 で、生値は返さない。
- 製品が注入するもの: `test_connection`（接続 pool と接続処理は製品が持つ）、`extra_readiness`（システム設定画面の `build_database_router` と同じもの）、`schema_probe`（RAG の system schema、NL2SQL の incremental store）、`short_circuit`（NL2SQL の memory モード、Agent のローカル認証）、`context_fields`（NL2SQL は実行モード・保存モードも含める）。
- 各製品の `/api/ready` の `oracle` check も同じ `database_readiness` を使う。
- 画面側は `@engchina/production-ready-system-settings` の `DatabaseGate` / `useDatabaseStatus` / `DatabaseUnavailableNotice` を使う（製品は API・導線・製品名の入る文言だけを渡す）。ゲートを通さない画面は3製品ともシステム設定の 5 画面（OCI 認証・アップロード保存先・モデル・データベース・外観）だけ。NL2SQL の保存領域の確認のような製品固有の確認は `secondaryGate` で差し込む。

### システムテーブルの管理（`/api/settings/database/system-tables`、#325）

製品のシステムテーブル（versioned Oracle system schema）の状態の取得・作成 / 更新・全再作成は、`pr_system_settings.system_schema.SystemSchemaManagerBase` の上に製品の manager を作る（RAG の `app.rag.system_schema`、NL2SQL の `app.features.settings.system_schema`）。Agent はシステムテーブルの管理を持たない（DDL は `agent_security_migrate` の CLI だけ）。

- API: `GET /api/settings/database/system-tables`（DDL を実行しない。取得できなければ 503、公開するのは ORA コードだけ）と `POST /api/settings/database/system-tables/initialize`（`{recreate, confirmation}`）。応答の骨格は `{status, schema_head, applied_versions, pending_versions, expected_/existing_object_count, expected_/existing_table_count, missing_objects, tables, operation_state}` で、製品が項目を足す（RAG の `schema_version` / `retired_objects`、NL2SQL の `objects`）。
- 骨格が持つもの: 状態の分類（業務テーブルが無い → `missing`、必須 object の不足 → `partial`、廃止 object の残り・未適用 / checksum 不一致の migration → `outdated`、それ以外 → `ready`）、操作の lease（`<製品>_SCHEMA_OPERATIONS` の 1 行。期限切れは奪える。成功で `schema_epoch` を 1 増やす）、台帳（`<製品>_SCHEMA_MIGRATIONS` の MERGE と読み込み）、確認語の検証（DB に触る前に完全一致だけを通す。不一致は 422）、失敗の記録と安全化（`LAST_ERROR_CODE` に ORA コード。ORA-00054 は 409 と `Retry-After: 5`）、`ALTER SESSION SET DDL_LOCK_TIMEOUT`（0〜120 秒）。
- 製品が持つもの: manifest（テーブル・索引などの一覧）と DDL の正本、migration の適用方法、`_status_on`（状態の組み立て）・`_ensure_control_schema`・`_initialize_on`（lease を取った後の手順）、実行中の job の確認、接続 pool、製品名が入る文言と確認語（`RECREATE_<製品>_SYSTEM_TABLES`）。
- 画面側は `@engchina/production-ready-system-settings` の `SystemTablesCard`（と `useSystemTablesStatus` / `useInitializeSystemTables`）を使う（NL2SQL の見た目が基準）。製品は API・権限（`canManage`）・確認語・製品名の入る文言・成功時に捨てる cache を渡す。NL2SQL は所有者付きの object 名の表示（`renderObjectName`）、RAG は確認語に加えた確認ダイアログ（`confirmRecreate`）を差し込む。
- 外部キー（製品が `managed_foreign_keys` を渡したときだけ。今は RAG）: 正本の `CREATE TABLE` の FK と USER_CONSTRAINTS を定義（表・列・参照先）で比べ、不足（#505）・削除規則の違い・無効化（DISABLED。#511）を `outdated` にして、状態の `missing_foreign_keys` / `mismatched_foreign_keys` / `disabled_foreign_keys` に出す。作成・更新で、追加・DROP と正本の ADD・ENABLE で直す（参照先のない行があれば `NOVALIDATE`。既存の行は消さない）。参照先のない行が残る FK は `orphaned_foreign_keys` に警告として出す。行の削除は利用者の明示操作だけで行う（`SystemSchemaManagerBase.delete_orphaned_rows`。RAG は `POST /api/settings/database/system-tables/orphaned-rows/delete` の `{constraint_name, expected_orphan_rows}` と CLI の `delete-orphans`。権限は作成・更新と同じ）。確認した件数より増えていたら 409 で削除せず、削除の後に FK を `VALIDATE` する。画面は `SystemTablesCard` の `confirmDeleteOrphans`（確認ダイアログ）を渡したときだけ削除の操作を出す。

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
