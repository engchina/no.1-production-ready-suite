# デプロイメント

## ローカル開発

```bash
cd backend
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload

cd ../frontend
cp .env.example .env.local
npm ci
npm run dev
```

Docker Compose:

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

backend は `AI_SERVICE_ADAPTER=local` で起動し、`/tmp/production-ready-rag` を永続ボリュームにする。
コンテナ healthcheck は `/api/ready` を使う。local adapter では保存先の書き込み可否、OCI adapter では `oci_common`、`enterprise_ai`、`genai`、`oracle`、`object_storage` の設定グループを確認する。
`ENVIRONMENT=production` では `deployment_adapter` と `audit_context_salt` も確認し、`AI_SERVICE_ADAPTER=oci` と `AUDIT_CONTEXT_HASH_SALT` を必須にする。すべて `ok` のときだけ 200 になり、`missing`、`invalid`、`missing_credentials`、`wallet_not_found` が含まれる場合は 503 になる。

## 本番構成

推奨:

- Frontend: Next.js container を OCI Container Instances または OKE に配置。
- Backend: FastAPI + Uvicorn/Gunicorn container を OKE に配置。
- Storage: OCI Object Storage。
- DB: Oracle 26ai。RAG チャンクは `VECTOR(1536, FLOAT32)`。
- LLM/VLM: OCI Enterprise AI。
- Embedding/Rerank: OCI Generative AI。
- Observability: Langfuse、Prometheus、OpenTelemetry。
- Secret: OCI Vault から環境変数または workload identity で注入。
- Audit: `app.audit` の `rag_search_audit` / `rag_ingestion_audit` 構造化ログをログ基盤へ転送し、必要に応じて Oracle audit table に永続化する。`X-Tenant-ID` / `X-User-ID` は raw 値を保存せず hash 化し、`AUDIT_CONTEXT_HASH_SALT` は OCI Vault から注入する。

backend container は production entrypoint として Gunicorn + `uvicorn.workers.UvicornWorker` を使う。worker 数と timeout は `WEB_CONCURRENCY`、`GUNICORN_TIMEOUT`、`GUNICORN_GRACEFUL_TIMEOUT`、`GUNICORN_KEEP_ALIVE`、listen port は `PORT` で調整する。local 開発だけ `uvicorn app.main:app --reload` を使い、本番では `/api/ready` と golden set gate で昇格判定する。

frontend container は lockfile ベースの `npm ci` で build し、runtime stage では `npm ci --omit=dev` によって production dependencies のみを含める。`next start` は公式 `node` ユーザーで起動し、`NEXT_TELEMETRY_DISABLED=1` を設定して外向き telemetry を無効化する。
frontend build は `next/font/google` を使わず、CSS の日本語第一 font stack で表示する。これにより CI / OKE build が Google Fonts への外向き通信に依存しない。

## リリース前チェック

Pull Request と `main` への push では `.github/workflows/ci.yml` が以下の品質門を実行する。ローカルで先に確認する場合も同じコマンドを使う。

backend:

```bash
cd backend
uv run ruff check .
uv run black --check .
uv run mypy .
uv run pytest --cov=app
uv run bandit -r app
uv run pip-audit
```

frontend:

```bash
cd frontend
npm run lint
npm run typecheck
npm audit --audit-level=moderate
npm run build
```

container:

```bash
docker compose config
```

RAG 品質:

```bash
cp evaluation/golden-set.example.json evaluation/golden-set.json
# document id と期待キーワードを対象環境に合わせて編集してから実行する
curl -X POST http://localhost:8000/api/evaluation/run \
  -H 'Content-Type: application/json' \
  -d @evaluation/golden-set.json
```

CI / nightly / staging 昇格では curl ではなく評価 gate CLI を使い、レスポンス JSON を artifact として保存する。

```bash
cd backend
uv run python -m app.rag.evaluation_cli \
  ../evaluation/golden-set.json \
  --api-url https://<staging-host>/api/evaluation/run \
  --output ../evaluation/evaluation-result.json
```

`evaluation/golden-set.example.json` は API request schema に合うテンプレートとしてテストで検証する。運用する `evaluation/golden-set.json` には `thresholds` を含める。CLI は `passed=false`、`error_count>0`、または `threshold_failures` 非空なら exit `1` にし、golden set/引数不備は exit `2`、API 接続・HTTP・レスポンス形式の問題は exit `3` にする。tenant 分離を検証する評価では `--tenant-id` / `RAG_EVALUATION_TENANT_ID` を設定する。tenant/user の raw 値は CLI 出力に表示しない。

## 運用パラメータ

- `RAG_CHUNK_SIZE`: まず 600-1200 で評価する。
- `RAG_CHUNK_OVERLAP`: 10-20% を目安にする。`RAG_CHUNK_SIZE` 以上にはできない。
- `RAG_MAX_CHUNKS_PER_DOCUMENT`: 1 文書あたりの chunk 数上限。大きな PDF や異常 OCR で embedding コストが跳ねないよう、golden set と実データ量で調整する。
- `RAG_CONTEXT_WINDOW_CHARS`: LLM の入力制限と citation 数のバランスで決める。レスポンスの citations は実際に context へ入った chunk だけになるため、golden set で必要な引用数を確認して調整する。
- `RAG_MIN_SIMILARITY`: recall を落としすぎないよう、評価セットで確認して調整する。
- `RAG_SEARCH_TIMEOUT_SECONDS`: `/api/search` と `/api/search/stream` の pipeline timeout。OCI / Oracle の p95 latency と worker 数に合わせる。
- `WEB_CONCURRENCY`: backend container の Gunicorn worker 数。OKE / Container Instances の CPU 割当、OCI / Oracle の p95 latency、同時実行数から決める。
- `GUNICORN_TIMEOUT` / `GUNICORN_GRACEFUL_TIMEOUT` / `GUNICORN_KEEP_ALIVE`: worker timeout、停止猶予、keep-alive 秒数。`RAG_SEARCH_TIMEOUT_SECONDS` より短くしない。
- `GUARDRAIL_MAX_QUERY_CHARS`: UI 側の入力制限と合わせる。
- `RATE_LIMIT_ENABLED`: 高コスト API の app 内 limiter。外部 API Gateway / Ingress limiter と併用できる。
- `RATE_LIMIT_WINDOW_SECONDS`: fixed-window limiter の窓幅。
- `RATE_LIMIT_SEARCH_REQUESTS` / `RATE_LIMIT_EVALUATION_RUNS` / `RATE_LIMIT_UPLOADS` / `RATE_LIMIT_ANALYZE_REQUESTS` / `RATE_LIMIT_TABLE_QUERIES`: tenant/user hash 単位の窓内上限。OCI / Oracle / LLM の quota と業務ピークに合わせて調整する。
- `GUARDRAIL_MASK_SENSITIVE_IDENTIFIERS`: query / answer 内の個人番号、口座番号、電話番号、メールアドレスらしき値を `[機微情報]` にマスクする。外部 DLP と責務分担する場合だけ無効化を検討する。

## OCI へ切り替えるときの順序

1. Oracle 26ai に document / chunk / audit tables を作成する。DDL は `oracle_document_schema_sql()`、`oracle_vector_schema_sql()`、`oracle_audit_schema_sql()` をベースにする。document table には `content_sha256`、`file_size_bytes`、`duplicate_of_document_id`、`tenant_id_hash` を含め、`content_sha256` と `tenant_id_hash, status, uploaded_at` に索引を作る。chunk table にも `tenant_id_hash` を含め、retrieval で tenant 条件を必ず適用できるようにする。audit table は query 本文、OCR 原文、tenant/user id の raw 値を保存せず、hash、request id、trace id、guardrail code、retrieval/rerank/citation 件数、context 文字数、設定 fingerprint、error type を保存する。
2. `ObjectStorageClient` の OCI Object Storage SDK 実装を有効化する。`OBJECT_STORAGE_NAMESPACE` / `OBJECT_STORAGE_BUCKET` を設定し、保存 URI が `oci://namespace/bucket/key` になり、取得時に namespace / bucket 不一致を拒否することを staging で確認する。分析前に Object Storage から取得した bytes が document table の `file_size_bytes` / `content_sha256` と一致することも確認する。
3. `OracleClient` の python-oracledb pool、vector search、keyword search、Select AI、document/chunk persistence を有効化する。`ORACLE_USER` / `ORACLE_DSN` / `ORACLE_PASSWORD` または wallet、必要に応じて `ORACLE_SELECT_AI_PROFILE` を設定する。`ANALYZING` / `ERROR` への状態遷移では該当 document の chunk/index 行と古い抽出フィールドを削除し、検索対象は `ANALYZED` / `REGISTERED` に限定する。staging では `VECTOR_DISTANCE`、Oracle Text `CONTAINS`、Select AI result JSON、document 別 chunk count、索引 chunk がない `ANALYZED` 文書を本登録できないことを確認する。
4. `OciGenAiClient` の embedding / rerank は OCI Generative AI Inference SDK 実装を使う。staging では `OCI_CONFIG_FILE` / profile / region / compartment / model id、Cohere Embed v4 の 1536 次元、Cohere Rerank v4 fast の返却件数・候補 index 範囲・index 重複なし・finite score を確認する。
5. `OciEnterpriseAiClient` の VLM と LLM を Enterprise AI endpoint へ接続する。
6. `AI_SERVICE_ADAPTER=oci` で staging にデプロイし、`/api/ready` の checks がすべて `ok` になることを確認する。production 昇格時は `ENVIRONMENT=production` にして、追加 checks の `deployment_adapter` と `audit_context_salt` も `ok` にする。Oracle は `ORACLE_USER` / `ORACLE_DSN` に加えて `ORACLE_PASSWORD` または存在する `ORACLE_WALLET_DIR` が必要。
7. golden set 評価と負荷試験を通してから production へ昇格する。
