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

- 応答: `{status: ok|not_configured|unreachable|setup_required, check, detail, context_id, schema_status, adb_lifecycle_state}`。常に HTTP 200。`adb_lifecycle_state` は `unreachable` のときだけ入る（#820）。
- 判定の順: `short_circuit`（DB を使わない構成）→ システム設定画面と同じ `database_readiness`（`ok` 以外は `not_configured`。接続は試さない）→ 製品の `test_connection`（bounded。失敗は `unreachable`）→ 製品の `schema_probe`（準備状態の確認）→ `ok`。
- `schema_probe` の例外は、接続・pool のエラー（`pr_backend_core.oracle_errors.is_oracle_connection_error`。`DPY-4005`・`DPY-6xxx`・`DPY-4011`・`ORA-125xx` / `12170`・`ORA-03113` / `03114`・`ORA-01017` など。`raise ... from` の原因の連鎖も見る）なら `unreachable`、それ以外（辞書・DDL・権限など）だけを `setup_required`（`check=schema_check_failed`）にする（#820）。接続確認の後に pool が接続を返さないとき、「初期化が必要」と案内しない。製品の `schema_probe` が自分で例外を正規化するとき（NL2SQL の `migration_check_failed`）も同じ関数で分ける。
- `unreachable` のときは、ADB OCID（`PLATFORM_ORACLE_ADB_OCID`）があれば OCI の API で ADB のライフサイクル状態を取り（上限 5 秒、`adb_lifecycle`。失敗しても判定は変えない）、`adb_lifecycle_state` に入れる（#820）。画面は停止中・起動中・利用できない状態・起動済み（ネットワーク・接続情報の問題）を分けて案内する。
- `detail` は接続先・資格情報・Wallet の path を返さない（ORA / DPY / DPI のコードだけ）。`context_id` は接続先の値の SHA-256 で、生値は返さない。
- 製品が注入するもの: `test_connection`（接続 pool と接続処理は製品が持つ）、`extra_readiness`（システム設定画面の `build_database_router` と同じもの）、`schema_probe`（RAG の system schema、NL2SQL の incremental store）、`short_circuit`（NL2SQL の memory モード、Agent のローカル認証）、`context_fields`（NL2SQL は実行モード・保存モードも含める）。
- `ok` の結果だけを、接続先と資格情報の指紋（hash。生値は持たない）ごとに 30 秒サーバー側で cache する（`ok_cache_seconds`。#793）。設定の判定は毎回行い、`unreachable` / `setup_required` は cache しない。DB 設定の保存（`build_database_router`）とシステムテーブルの操作（`SystemSchemaManagerBase.initialize` / `delete_orphaned_rows`）で `clear_database_status_cache` が捨てる。テストは autouse の fixture で捨てる。
- 接続 pool の回復（#820）: DB の停止中に作った python-oracledb の pool は、DB の起動後も接続を作らず `DPY-4005`（pool の待ちの timeout）を返し続けることがある（プロセスを再起動するまで直らなかった）。`SharedOraclePool.acquire` は、判定 `recover_pool_error(pool, exc)` に従い、`DPY-4005` で貸し出し中の接続が上限に達していない（本当の枯渇ではない）なら pool を捨てて新しい pool で 1 回だけやり直し、やり直しも失敗したらその例外（本当の接続エラー）を返す。接続できない（`DPY-6005` など）ときは同じ要求の中ではやり直さず、pool だけを捨てて例外を返す（次の要求は新しい pool から）。本当の枯渇と SQL のエラーでは pool を捨てない。捨てた pool は別のスレッドで閉じる（`pool.close()` は試している接続が終わるまで戻らず、接続先が応答しないと `tcp_connect_timeout` の間止まる。手元で 16 秒を観測）。RAG の共有 pool（`app.clients.oracle` の `acquire_connection`。`getmode` を `TIMEDWAIT`・30 秒にした）も同じ判定を使う。NL2SQL の DeepSec の pool（`OraclePoolManager`）は未対応。
- Wallet / mTLS の ADB では 1 回の接続に handshake と認証の往復がかかるため、要求ごとに新しい接続を張らないようにする。Agent の接続確認・準備状態の確認は `pr_backend_core.oracle_pool.SharedOraclePool` の pool から借りる（#793）。RAG / NL2SQL の `test_connection` は単発の接続（Wallet の retry を外した DSN）のままで、上の cache で回数を減らす。システム設定の「接続テスト」は保存前の候補を試すため、3 製品とも単発の接続のまま。NL2SQL の状態の保存先（業務プロファイル・ジョブ・履歴・オントロジー・評価の `NL2SQL_*` の表）は `nl2sql-state` の pool から借りる（`OracleNl2SqlAdapter.state_connection`、上限は `NL2SQL_ORACLE_STATE_POOL_MAX`。#830）。状態の保存先の接続は JSON CLOB を文字列で fetch し（行ごとの LOB の read の往復を無くす）、書き込みの commit は最後の文の実行に載せる（`autocommit`。#904）。NL2SQL の業務データの読み取り（SELECT の実行）と Select AI の生成（`DBMS_CLOUD_AI.GENERATE`。profile は引数で渡し session の profile を変えない）は、アプリの接続（system_admin と DeepSec 無効のとき）なら pool から借りる（`OracleNl2SqlAdapter.runtime_connection`、上限は pool ごとに `NL2SQL_ORACLE_RUNTIME_POOL_MAX`。#904）。pool は system_admin 用（`nl2sql-runtime-admin`）と非 system_admin 用（`nl2sql-runtime-user`）に分け、同じ pool・同じ接続を共有しない。actor の無い処理は単発の接続（`connection()`）。DeepSec 有効の非 system_admin は今までどおり DeepSec の DATA USER の pool（借りるたびに利用者の context を設定し、返すときに消す。消せなければ捨てる）で、アプリの接続の pool・状態の保存先の pool と共有しない。session の状態を変え得る処理（ALTER SESSION・`DBMS_CLOUD_AI.SET_PROFILE`・DDL / DML・Select AI Agent・オントロジーの操作のトランザクション）は単発の接続（`connection()`）のまま。
- 各製品の `/api/ready` の `oracle` check も同じ `database_readiness` を使う。
- 画面側は `@production-ready/system-settings` の `DatabaseGate` / `useDatabaseStatus` / `DatabaseUnavailableNotice` を使う（製品は API・導線・製品名の入る文言と、利用者がデータベース設定・システムテーブルを開けるか（`canManageDatabase` / `canManageSystemTables`。#820）だけを渡す。状態ごとの案内は UX 契約 messaging §3.4.1）。ゲートを通さない画面は3製品ともシステム設定の 5 画面（OCI 認証・アップロード保存先・モデル・データベース・外観と証明書）だけ。NL2SQL の保存領域の確認のような製品固有の確認は `secondaryGate` で差し込む。

### システムテーブルの管理（`/api/settings/database/system-tables`、#325）

製品のシステムテーブル（versioned Oracle system schema）の状態の取得・作成 / 更新・全再作成は、`pr_system_settings.system_schema.SystemSchemaManagerBase` の上に製品の manager を作る（RAG の `app.rag.system_schema`、NL2SQL の `app.features.settings.system_schema`、Agent の `app.system_schema`。#751）。

- API: `GET /api/settings/database/system-tables`（DDL を実行しない。取得できなければ 503、公開するのは ORA コードだけ）と `POST /api/settings/database/system-tables/initialize`（`{recreate, confirmation}`）。応答の骨格は `{status, schema_head, applied_versions, pending_versions, expected_/existing_object_count, expected_/existing_table_count, missing_objects, tables, operation_state}` で、製品が項目を足す（RAG の `schema_version` / `retired_objects`、NL2SQL の `objects`）。
- 骨格が持つもの: 状態の分類（業務テーブルが無い → `missing`、必須 object の不足 → `partial`、廃止 object の残り・未適用 / checksum 不一致の migration → `outdated`、それ以外 → `ready`）、操作の lease（`<製品>_SCHEMA_OPERATIONS` の 1 行。期限切れは奪える。成功で `schema_epoch` を 1 増やす）、台帳（`<製品>_SCHEMA_MIGRATIONS` の MERGE と読み込み）、確認語の検証（DB に触る前に完全一致だけを通す。不一致は 422）、失敗の記録と安全化（`LAST_ERROR_CODE` に ORA コード。ORA-00054 は 409 と `Retry-After: 5`）、`ALTER SESSION SET DDL_LOCK_TIMEOUT`（0〜120 秒）。
- 製品が持つもの: manifest（テーブル・索引などの一覧）と DDL の正本、migration の適用方法、`_status_on`（状態の組み立て）・`_ensure_control_schema`・`_initialize_on`（lease を取った後の手順）、実行中の job の確認、接続 pool、製品名が入る文言と確認語（`RECREATE_<製品>_SYSTEM_TABLES`）。
- 画面側は `@production-ready/system-settings` の `SystemTablesCard`（と `useSystemTablesStatus` / `useInitializeSystemTables`）を使う（NL2SQL の見た目が基準）。製品は API・権限（`canManage`）・確認語・製品名の入る文言・成功時に捨てる cache を渡す。NL2SQL は所有者付きの object 名の表示（`renderObjectName`）、RAG は確認語に加えた確認ダイアログ（`confirmRecreate`）を差し込む。
- 外部キー（製品が `managed_foreign_keys` を渡したときだけ。今は RAG）: 正本の `CREATE TABLE` の FK と USER_CONSTRAINTS を定義（表・列・参照先）で比べ、不足（#505）・削除規則の違い・無効化（DISABLED。#511）を `outdated` にして、状態の `missing_foreign_keys` / `mismatched_foreign_keys` / `disabled_foreign_keys` に出す。作成・更新で、追加・DROP と正本の ADD・ENABLE で直す（参照先のない行があれば `NOVALIDATE`。既存の行は消さない）。参照先のない行が残る FK は `orphaned_foreign_keys` に警告として出す。行の削除は利用者の明示操作だけで行う（`SystemSchemaManagerBase.delete_orphaned_rows`。RAG は `POST /api/settings/database/system-tables/orphaned-rows/delete` の `{constraint_name, expected_orphan_rows}` と CLI の `delete-orphans`。権限は作成・更新と同じ）。確認した件数より増えていたら 409 で削除せず、削除の後に FK を `VALIDATE` する。画面は `SystemTablesCard` の `confirmDeleteOrphans`（確認ダイアログ）を渡したときだけ削除の操作を出す。

## 製品間の連携（MCP とサービストークン、#230）

Agent が RAG / NL2SQL を呼ぶときは、呼び先の `POST /api/mcp`（MCP の Streamable HTTP、JSON 応答）を使う。製品同士はコードで依存しない。

- サーバー: `pr_backend_core.mcp` の `McpServer` / `McpTool`（入力は Pydantic model、`permissions` はグループをすべて満たす・グループ内はどれか）と `mcp_http_response`。`initialize` / `ping` / `tools/list` / `tools/call` だけを扱う。`HTTPException` / `SecurityApiError` は `isError: true` の `structuredContent.error_code` / `message` / `status` になる。
- 認証: 呼び出し元は `pr_system_settings.auth.service_token.issue_service_token` で `sub` = 利用者の `user_uuid`、`aud` = 呼び先（`rag` / `nl2sql`）の短命の token（HS256、既定 60 秒）を作り、`Authorization: Bearer` で送る。鍵は共通 `.env` の `PLATFORM_SERVICE_TOKEN_SECRET`（32 文字以上。空なら 503）。
- 呼び先は `authorize_request(..., service_token_paths={"/mcp"}, service_token_audience="<製品>")` を渡す。その path では Cookie の代わりに token の利用者を `principal_for_worker` で組み立てる（現在のロール・権限・対象範囲を使い、無効・初回パスワード変更待ちの利用者は 403）。Cookie を使わないので CSRF は照合しない。claims（`run_id` など）は `request.state.service_token_claims`。
- ツールの権限と対象範囲（検索・回答プロファイル / KB / 業務プロファイル / DeepSec）は、画面と同じ service 層で判定する。
- 呼び先がブラウザで開く短命の URL を返すとき（RAG の図の根拠。#1311）は、呼び出し元の画面の操作だけに限るため claim の
  `purpose`（例: `figure_url`）を確かめ、Run の中でモデルが呼んだ token（`purpose` 無し）では作らない。URL のトークンは
  サービストークンの鍵そのものでは署名せず、`PLATFORM_SERVICE_TOKEN_SECRET` から HKDF-SHA256 で用途の印を変えて導いた鍵で
  署名し、利用者・対象・版・期限（5 分以内）に縛る。トークンは URL の path に置いてアクセスログで伏せ、読むたびに
  利用者の今の権限と対象の版を確かめ直す（RAG の `app/rag/figure_url.py`・`app/api/routes/figures.py`）。
- 呼び出し元の再試行と timeout（#854。Agent の `McpSession`）: 送信前の失敗（接続できない・接続の timeout）と 429 / 503 はどのメッセージも、502 / 504 は状態を変えない手順（`initialize`・`tools/list`）と読み取り専用（`annotations.readOnlyHint=true`）のツールの `tools/call` だけ、上限付きの指数 backoff + jitter（`Retry-After` に従う）で再試行する。書き込みのツールは呼び先に届いた可能性がある失敗（読み取りの timeout・500 / 502 / 504）では再送しない。1 回のツールの呼び出し全体は接続の timeout に収める。呼び先は、副作用のあるツールに `readOnlyHint` を付けない・429 / 503 は処理を始める前にだけ返す（`Retry-After` を付けてよい）こと。
- ツールの契約（名前・`inputSchema`・`outputSchema`（`McpTool.output_model`。#250）・`annotations`）の正本は `platform/contracts/mcp/<製品>-tools.json`（#248）。RAG / NL2SQL の `tests/test_mcp_contract.py` が実装と一致を、Agent の `tests/test_product_mcp_contract.py` が送る引数の収まりと、読む出力の項目（入れ子を含む）が呼び先の出力にあることを確かめる。ツールを変えたら呼び先の製品で `UPDATE_MCP_CONTRACT=1 uv run pytest tests/test_mcp_contract.py` で契約を書き直し、Agent のテストも通す。

## 内部の HTTP と環境のプロキシ（#852）

OCI に届くために `HTTP_PROXY` / `HTTPS_PROXY`（社内プロキシ）を設定した環境でも、同じマシン・private network の
サービスへの HTTP は環境のプロキシを通さない。プロキシは 127.0.0.1 などへ届けられず `502 cannotconnect` などを返し、
未起動のサービスへの「未到達」が「サービスの応答した失敗」に見えてしまう（#852 で RAG の pipeline の stage が
in-process へ縮退せず、MCP の `rag_search` が止まった）。

- httpx の client（`Client` / `AsyncClient`）は、宛先の URL を `pr_backend_core.internal_http.http_client_options(url)`
  に通した引数で作る: `httpx.Client(timeout=..., **http_client_options(url))`。宛先が内部（loopback `127.0.0.0/8`・`::1`・
  `localhost` / `*.localhost`・`0.0.0.0`・link-local・RFC 1918 / ULA の private・`*.local` / `*.internal`・unix socket）なら
  `trust_env=False`（環境のプロキシ・`SSL_CERT_FILE`・`.netrc` を読まない）、外部（OCI・外部の MCP など）なら空で、
  今までどおり環境のプロキシを使う。DNS は引かず、名前の形と IP の値だけで判定する（`is_internal_url`）。
- 対象: RAG の前処理・parser・外部 parser・pipeline の stage・サービス管理の health・parser の readiness・trace の送信・
  評価 / 検証 / 負荷の CLI、Agent の MCP 接続（RAG / NL2SQL / 外部）と OAuth の token・マーケットプレイスの取得・trace の
  送信。宛先が設定値の接続も、内部の宛先のときだけプロキシを外す。OCI Enterprise AI など外部だけへの client は対象外。
- 多重の防御として、各製品の `scripts/start-backend.sh` は既存の値を残したまま `NO_PROXY` / `no_proxy` に
  `localhost,127.0.0.1,::1` を足して backend を起動する（起動の readiness の確認は #781 の `curl --noproxy "*"`）。
- 内部の宛先をホスト名（例: VCN の DNS 名）で設定する場合は、名前からは判定できないため `NO_PROXY` に加える。RAG の
  pipeline の stage は、内部と判定できない宛先で環境のプロキシを通り 502 / 504 が返ったときだけ、プロキシの応答として
  未到達（in-process へ縮退）にする。サービス自身が応答した失敗は今までどおり縮退せず止める。

## 統一レスポンス envelope

```jsonc
// 成功
{ "data": { /* ... */ }, "error_messages": [], "warning_messages": [] }
// 失敗（HTTPException / 検証 / 未処理例外を統一）
{ "data": null, "error_messages": ["..."], "warning_messages": [] }
```

`X-Request-ID` ヘッダを常に付与（受信値を検証し、無ければ発行）。フロントの共通エラーハンドリングはこの形を前提にできる。

### 一覧のページング（#1266）

一覧の endpoint は `pr_backend_core.api` の部品で受け取り・返す。製品で `Page(...)` を手で組み立てない、limit を黙って clamp しない、カーソルの codec と `OFFSET … FETCH NEXT` の文を書き写さない。

| 型 | query の受け取り | 応答 | 組み立て |
|---|---|---|---|
| offset 型（任意のページへ移る一覧） | `paging: Annotated[OffsetParams, Depends(offset_params(default=50, max_limit=200))]`（`limit` 1..max は `Query(le=)` で 422、`offset` 0 以上） | `Page[T]`（`items` / `total` / `limit` / `offset` / `has_next`） | DB のページ: `paginate(items, total=, limit=paging.limit, offset=paging.offset)`。メモリ上の全件: `paginate_slice(items, paging)`。縮退の fallback: `empty_page(paging)` |
| カーソル型（「前へ / 次へ」と「さらに読み込む」） | `paging: Annotated[CursorParams, Depends(cursor_params(default=50, max_limit=100))]`（`cursor` は最初のページで None、最大 512 文字） | `CursorPage[T]`（`items` / `next_cursor: str \| None` / `total: int \| None`）。製品の付加情報（件数の内訳・version）は継承して足す | `cursor_page(items, next_cursor=, total=)`。`next_cursor` は続きが無ければ `None`（空文字は使わない） |

- カーソルの文字列は `encode_cursor(dict)` / `decode_cursor(cursor, required=(...))`（base64url の JSON）か、offset をそのまま続きの印にする `encode_offset_cursor` / `decode_offset_cursor` / `next_offset_cursor`。壊れたカーソルは `InvalidCursorError` で、router が 422 にする。
- Oracle の `OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY` は `offset_fetch_clause()`（bind 名は `offset_bind` / `limit_bind` で変えられる）と `offset_fetch_binds()`。
- 権限管理の「利用できる対象」の一覧の上限は共通の `ACCESS_TARGET_PAGE_LIMIT_MAX`（100）。
- frontend は共通の `OffsetPagination` / `CursorPagination`（ルートの AGENTS.md「読み込み中・一覧・ページング」）。

### 入力の検証エラー（422）の文（#1065）

FastAPI / Pydantic の検証エラー（`RequestValidationError`）は、3 製品とも `pr_backend_core.api.validation` で整形する（共通の handler は `install_exception_handlers`。RAG は `validation_error_response`、NL2SQL は problem 契約の `api_problem_response` から同じ関数を使う）。

- `error_messages` は利用者向けの日本語の文だけにする。
  - 自前の検証（`ValueError` / `assert` / `PydanticCustomError`）の日本語の文は、位置も `Value error, ` の接頭辞も付けずにそのまま出す。欄の名前は文に書く（例「ケースの id が重複しています。」）。英語の文の `ValueError` は利用者に見せず、位置と「入力内容を確認してください。」にする。
  - Pydantic の組み込みのエラーは `type` ごとの日本語の文に、利用者が書いた JSON の位置（`cases[0].query`。`body` / `query` などの出どころと union の候補の型の名前は外す）を前に付ける（例 `cases[0].query: 必須の項目です。入力してください。`）。
- `error_code` は `REQUEST_VALIDATION_FAILED`、`problem` は NL2SQL の problem 契約と同じ形（`title` / `status` / `detail` / `code` / `request_id` / `retryable` / `field_errors`）。
- `problem.field_errors[]` は欄ごとに `pointer`（JSON Pointer）・`code`（Pydantic の type）・`message`（位置を含まない文）・`location`・`raw_location`（`body.cases.0.query`）・`raw_message`（Pydantic の原文）を持つ。画面は `message` を欄の横に、`raw_*` を「詳細」（`ApiErrorBanner`）に出す。入力値（`input`）は応答に含めない。
- MCP のツールの引数の誤り（`MCP_TOOL_ARGUMENTS_INVALID`）の `details.errors[]` も同じ整形（`loc` / `message` / `type` / `raw_message`）にする。

## 依存方法（dev は path source）

各 backend の `pyproject.toml`:

```toml
dependencies = ["production-ready-backend-core"]

[tool.uv.sources]
production-ready-backend-core = { path = "../../platform/packages/backend_core", editable = true }
```

> path source 変更時は **`uv lock` 再生成**。monorepo なので CI もローカルも同じ相対パス（`../../platform/…`）で解決する。

## 共通の診断ログ

API・worker・RAG の独立微サービス・browser の共通 schema、JST 時刻、相関、例外の安全化、HTTP summary と運用方法は [logging-standard.md](./logging-standard.md) を正本とする（#858）。`configure_logging` を各 process の bootstrap で呼び、製品の自由な handler / format を追加しない。
