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

- Frontend: Vite build artifact を配信する nginx container を OCI Container Instances または OKE に配置。
- Backend: FastAPI + Uvicorn/Gunicorn container を OKE に配置。
- Storage: OCI Object Storage。
- DB: Oracle 26ai。RAG チャンクは `VECTOR(1536, FLOAT32)`。
- LLM/VLM: OCI Enterprise AI。
- Embedding/Rerank: OCI Generative AI。
- Observability: Prometheus、OpenTelemetry、Langfuse gateway。`TRACE_EXPORT_HTTP_ENDPOINT` を設定すると、脱機密化済み RAG span event を非同期 HTTP JSON で転送する。
- Secret: OCI Vault から環境変数または workload identity で注入。
- Audit: `app.audit` の `rag_search_audit` / `rag_ingestion_audit` 構造化ログをログ基盤へ転送し、必要に応じて Oracle audit table に永続化する。`X-Tenant-ID` / `X-User-ID` は raw 値を保存せず hash 化し、`AUDIT_CONTEXT_HASH_SALT` は OCI Vault から注入する。

backend container は production entrypoint として Gunicorn + `uvicorn.workers.UvicornWorker` を使う。worker 数と timeout は `WEB_CONCURRENCY`、`GUNICORN_TIMEOUT`、`GUNICORN_GRACEFUL_TIMEOUT`、`GUNICORN_KEEP_ALIVE`、listen port は `PORT` で調整する。local 開発だけ `uvicorn app.main:app --reload` を使い、本番では `/api/ready` と golden set gate で昇格判定する。

frontend container は lockfile ベースの `npm ci` で build し、runtime stage では Vite の `dist/` を nginx で静的配信する。`/api/*` は `BACKEND_URL` へリバースプロキシし、SSE のため proxy buffering は無効化する。
frontend build は外部 font service に依存せず、CSS の日本語第一 font stack で表示する。これにより CI / OKE build が Google Fonts などへの外向き通信に依存しない。

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
  --api-base-url https://<staging-host> \
  --output ../evaluation/evaluation-result.json

uv run python -m app.rag.evaluation_cli \
  ../evaluation/compare.example.json \
  --api-base-url https://<staging-host> \
  --output ../evaluation/evaluation-compare-result.json
```

`evaluation/golden-set.example.json` と `evaluation/compare.example.json` は API request schema に合うテンプレートとしてテストで検証する。運用する `evaluation/golden-set.json` には `thresholds` を含める。compare の `experiments[].rag_overrides` では RRF 定数、query expansion、context window、context diversity、隣接 context、context compression、Oracle vector target accuracy を一時的に上書きし、staging の golden set で安全に比較できる。CLI は入力 JSON に `experiments` がある場合は compare request とみなし、`--api-base-url` から `/api/evaluation/compare` を自動で選ぶ。CLI は `passed=false`、`error_count>0`、または `threshold_failures` 非空なら exit `1` にし、golden set/引数不備は exit `2`、API 接続・HTTP・レスポンス形式の問題は exit `3` にする。tenant 分離を検証する評価では `--tenant-id` / `RAG_EVALUATION_TENANT_ID` を設定する。tenant/user の raw 値は CLI 出力に表示しない。

## 運用パラメータ

- `RAG_CHUNK_SIZE`: まず 600-1200 で評価する。
- `RAG_CHUNK_OVERLAP`: 10-20% を目安にする。`RAG_CHUNK_SIZE` 以上にはできない。
- `RAG_MAX_CHUNKS_PER_DOCUMENT`: 1 文書あたりの chunk 数上限。大きな PDF や異常 OCR で embedding コストが跳ねないよう、golden set と実データ量で調整する。
- `RAG_CONTEXT_WINDOW_CHARS`: LLM の入力制限と citation 数のバランスで決める。レスポンスの citations は実際に context へ入った chunk だけになるため、golden set で必要な引用数を確認して調整する。
- `RAG_CONTEXT_DIVERSITY_LAMBDA`: rerank anchor の MMR 風 diversity 重み。既定 1.0 は rerank 順を維持する。0.2-0.8 を golden set で比較し、`diagnostics.context_diversified_count`、recall、answer keyword hit、context window からの引用落ちを見て調整する。
- `RAG_CONTEXT_GROUP_EXPANSION_ENABLED` / `RAG_CONTEXT_GROUP_MAX_CHUNKS`: `chunk_group_id` が同じ sibling chunk を生成 context へ追加する。既定は無効。表・箇条書き・長い章節を複数 chunk に分割した文書で `diagnostics.context_group_expanded_count`、citation 数、context 文字数、groundedness、p95 latency を golden set / staging smoke で確認してから有効化する。
- `RAG_CONTEXT_NEIGHBOR_WINDOW`: rerank anchor の同一文書前後 chunk を生成 context へ追加する window。既定 0 は無効。1-2 から試し、`diagnostics.context_expanded_count`、citation 数、LLM context 文字数、p95 latency を golden set / staging smoke で確認してから広げる。
- `RAG_CONTEXT_COMPRESSION_ENABLED` / `RAG_CONTEXT_COMPRESSION_MAX_SENTENCES` / `RAG_CONTEXT_COMPRESSION_MAX_CHARS_PER_CHUNK`: 長い chunk から query 関連 sentence / line だけを抽出して LLM context を節約する。既定は無効。表や規程 PDF の長い chunk で `diagnostics.context_compressed_count`、`context_compression_saved_chars`、groundedness、answer keyword hit を golden set で見ながら有効化する。
- `RAG_MIN_SIMILARITY`: recall を落としすぎないよう、評価セットで確認して調整する。
- `RAG_RRF_K`: hybrid retrieval の Reciprocal Rank Fusion 定数。小さいほど上位 rank を強く優先する。golden set で keyword/vector の寄与と citation 安定性を確認して調整する。
- `RAG_QUERY_EXPANSION_ENABLED` / `RAG_QUERY_EXPANSION_MAX_VARIANTS`: retrieval 前に deterministic な業務同義語 query expansion を行う。日本語/英語混在 query の recall を上げる目的で既定有効。variant 数を増やすと embedding / Oracle retrieval 呼び出し数も増えるため、golden set と OCI / Oracle の p95 latency を見て 1-3 から調整する。audit / trace には query 本文や展開語ではなく variant 件数だけを残す。
- `RAG_SEARCH_TIMEOUT_SECONDS`: `/api/search` と `/api/search/stream` の pipeline timeout。OCI / Oracle の p95 latency と worker 数に合わせる。
- `ORACLE_VECTOR_TARGET_ACCURACY`: Oracle AI Vector Search の問い合わせ側 `FETCH APPROX ... WITH TARGET ACCURACY`。既定は 95。staging / golden set で召回率とレイテンシを見ながら調整する。
- `ORACLE_SELECT_AI_PROFILE` / `ORACLE_SELECT_AI_MAX_RESULT_CHARS`: Select AI を使う場合の DBMS_CLOUD_AI profile 名と API レスポンス最大文字数。未設定なら `/api/search/select-ai` は 503 を返す。profile は Oracle 側で対象 schema / 権限 / model credential を最小化して管理する。
- `OCI_ENTERPRISE_AI_ENDPOINT` / `OCI_ENTERPRISE_AI_LLM_PATH` / `OCI_ENTERPRISE_AI_VLM_PATH`: OCI Enterprise AI の OpenAI-compatible gateway endpoint と LLM/VLM path。Enterprise AI は `OCI_ENTERPRISE_AI_API_KEY` による Bearer 認証で呼び出し、staging smoke で VLM/LLM 契約を確認する。Enterprise AI の model deployment / gateway response は `prediction(s)`、`output(s)`、`inference_response`、OpenAI 風 `choices`、JSON 文字列 envelope を正規化してから Pydantic schema / text 抽出へ進める。
- `OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE` / `OCI_ENTERPRISE_AI_VLM_PAYLOAD_TEMPLATE`: Enterprise AI gateway ごとの request shape が標準 payload と異なる場合にだけ設定する JSON object template。文字列 placeholder は `${prompt}` / `${context}` / `${mime_type}` / `${data_base64}`、object placeholder は `"${messages}"` / `"${parameters}"` / `"${response_format}"` / `"${structured_extraction_schema}"` のように完全な文字列値として置く。未設定なら標準 payload を使い、VLM には upload metadata の MIME type を渡す。
- `OCI_ENTERPRISE_AI_LLM_RESPONSE_PATH` / `OCI_ENTERPRISE_AI_VLM_RESPONSE_PATH`: Enterprise AI gateway の response が既知 envelope ではなく独自の深い JSON 構造に包まれる場合だけ指定する JSON Pointer。例: `/payload/results/0/generated/text`、`/payload/results/0/document`。未設定なら既知 envelope を自動判定する。
- `WEB_CONCURRENCY`: backend container の Gunicorn worker 数。OKE / Container Instances の CPU 割当、OCI / Oracle の p95 latency、同時実行数から決める。
- `GUNICORN_TIMEOUT` / `GUNICORN_GRACEFUL_TIMEOUT` / `GUNICORN_KEEP_ALIVE`: worker timeout、停止猶予、keep-alive 秒数。`RAG_SEARCH_TIMEOUT_SECONDS` より短くしない。
- `TRACE_EXPORT_HTTP_ENDPOINT`: 空なら構造化ログ + Prometheus のみ。設定時は `rag.trace_span` event を OpenTelemetry / Langfuse gateway へ非同期 POST する。query 本文、context 本文、OCR 原文、prompt、例外 message は送らない。
- `TRACE_EXPORT_HTTP_BEARER_TOKEN` / `TRACE_EXPORT_TIMEOUT_SECONDS` / `TRACE_EXPORT_QUEUE_SIZE`: trace export の認証、送信 timeout、queue 上限。queue full や送信失敗は `rag_trace_export_dropped` / `rag_trace_export_failed` として記録し、RAG request は失敗させない。
- `GUARDRAIL_MAX_QUERY_CHARS`: UI 側の入力制限と合わせる。
- `RATE_LIMIT_ENABLED`: 高コスト API の app 内 limiter。外部 API Gateway / Ingress limiter と併用できる。
- `RATE_LIMIT_WINDOW_SECONDS`: fixed-window limiter の窓幅。
- `RATE_LIMIT_SEARCH_REQUESTS` / `RATE_LIMIT_EVALUATION_RUNS` / `RATE_LIMIT_UPLOADS` / `RATE_LIMIT_INGEST_REQUESTS`: tenant/user hash 単位の窓内上限。OCI / Oracle / LLM の quota と業務ピークに合わせて調整する。
- `X-RAG-Allowed-Document-Ids` / `X-RAG-Allowed-Category-Names`: 認証ゲートウェイまたはアプリケーション権限層が認可済み scope として backend へ付与する request header。backend は raw 値を監査ログへ出さず、document 一覧、詳細、chunk count、retrieval に deny-by-default の scope filter として適用する。外部クライアントから直接信頼しない。
- `GUARDRAIL_MASK_SENSITIVE_IDENTIFIERS`: query / answer 内の個人番号、口座番号、電話番号、メールアドレスらしき値を `[機微情報]` にマスクする。外部 DLP と責務分担する場合だけ無効化を検討する。

## OCI へ切り替えるときの順序

1. Oracle 26ai に document / chunk / audit tables を作成する。まず backend container または CI runner で `uv run python -m app.rag.oracle_schema --output ../artifacts/oracle-schema.sql --manifest-output ../artifacts/oracle-schema.manifest.json` を実行し、DDL 成果物と manifest の hash / statement 数をレビューする。生成 SQL は document table に `content_sha256`、`file_size_bytes`、`duplicate_of_document_id`、`tenant_id_hash` を含め、`content_sha256` と `tenant_id_hash, status, uploaded_at` に索引を作る。chunk table は `VECTOR(1536, FLOAT32)`、HNSW ベクトル索引(`COSINE`、目標精度 `95`、neighbors `32`、efconstruction `500`)、`tenant_id_hash`、`document_id + chunk_index` 用索引を含め、retrieval で tenant 条件と request access scope 条件を必ず適用できるようにする。audit table は query 本文、OCR 原文、tenant/user id の raw 値を保存せず、hash、request id、trace id、guardrail code、retrieval/rerank/context diversity/context group expansion/context expansion/context compression/citation 件数、context compression 節約文字数、context 文字数、設定 fingerprint、error type を保存する。レビュー済み SQL を SQLcl や管理された migration 手順で適用してから次へ進む。
2. `ObjectStorageClient` の OCI Object Storage SDK 実装を有効化する。`OBJECT_STORAGE_REGION` / `OBJECT_STORAGE_NAMESPACE` / `OBJECT_STORAGE_BUCKET` を設定し、保存 URI が `oci://namespace/bucket/key` になり、取得時に namespace / bucket 不一致を拒否することを staging で確認する。取込前に Object Storage から取得した bytes が document table の `file_size_bytes` / `content_sha256` と一致することも確認する。
3. `OracleClient` の python-oracledb pool、vector search、keyword search、document/chunk persistence、隣接 context 取得を有効化する。`ORACLE_USER` / `ORACLE_DSN` / `ORACLE_PASSWORD` または `ORACLE_CLIENT_LIB_DIR/network/admin` に配置した wallet を設定する。`INGESTING` / `ERROR` への状態遷移では該当 document の chunk/index 行と古い抽出結果を削除し、検索対象は `INDEXED` に限定する。staging では `VECTOR_DISTANCE`、`FETCH APPROX ... WITH TARGET ACCURACY`、Oracle Text `CONTAINS`、document 別 chunk count、`chunk_index` window による同一 document 前後 chunk 取得、`INDEXED` 文書が hybrid search の citation に含まれることを確認する。Select AI を有効にする場合は `ORACLE_SELECT_AI_PROFILE` を設定し、`/api/search/select-ai` の `showsql` が bind 付き `DBMS_CLOUD_AI.GENERATE` 経由で生成 SQL を返すこと、`runsql` が guardrail でデータ変更意図を拒否することを確認する。
4. `OciGenAiClient` の embedding / rerank は OCI Generative AI Inference SDK 実装を使う。staging では `OCI_CONFIG_FILE` / profile / region / compartment / model id、Cohere Embed v4 の 1536 次元、Cohere Rerank v4 fast の返却件数・候補 index 範囲・index 重複なし・finite score を確認する。
5. `OciEnterpriseAiClient` は `OCI_ENTERPRISE_AI_ENDPOINT`、`OCI_ENTERPRISE_AI_API_KEY`、`OCI_ENTERPRISE_AI_LLM_MODEL`、`OCI_ENTERPRISE_AI_VLM_MODEL`、`OCI_ENTERPRISE_AI_LLM_PATH`、`OCI_ENTERPRISE_AI_VLM_PATH` を使って Enterprise AI endpoint を呼び出す。標準 payload で合わない model deployment / gateway は `OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE` / `OCI_ENTERPRISE_AI_VLM_PAYLOAD_TEMPLATE` で request shape を差し替え、response envelope が独自の場合は `OCI_ENTERPRISE_AI_LLM_RESPONSE_PATH` / `OCI_ENTERPRISE_AI_VLM_RESPONSE_PATH` で候補 node を指定する。staging ではまず `uv run python -m app.rag.enterprise_ai_probe --surface both --dry-run` で URL、template 使用有無、response path 使用有無、payload key / shape、JSON byte 数を確認し、その後 `uv run python -m app.rag.enterprise_ai_probe --surface both` で LLM/VLM を直接呼び出す。probe は回答本文や OCR 本文を出さず、text 文字数・element 件数だけを artifact に残す。ここで Bearer 認証、timeout/retry、VLM の MIME type / 構造化抽出 JSON schema、LLM の citation-grounded 生成 payload、response parsing を実 endpoint で確認する。VLM response は `StructuredExtraction` へ検証し、LLM response は空 text を fail fast する。
6. `AI_SERVICE_ADAPTER=oci` で staging にデプロイし、`/api/ready` の checks がすべて `ok` になることを確認する。production 昇格時は `ENVIRONMENT=production` にして、追加 checks の `deployment_adapter` と `audit_context_salt` も `ok` にする。Oracle は `ORACLE_USER` / `ORACLE_DSN` に加えて `ORACLE_PASSWORD` または `ORACLE_CLIENT_LIB_DIR/network/admin` に存在する Wallet が必要。
7. staging 環境でまず `uv run python -m app.rag.staging_smoke --preflight-only` を実行する。`AI_SERVICE_ADAPTER=oci`、OCI/Oracle 接続設定、Enterprise AI LLM/VLM 設定、Cohere embedding/rerank 設定、`UPLOAD_STORAGE_BACKEND=oci`、Object Storage namespace/bucket がそろっていることを JSON の `checks` で確認する。preflight 失敗時は外部依存へ接続せず、secret 値も出力しない。
8. preflight が `ok=true` なら `uv run python -m app.rag.staging_smoke` を実行する。Object Storage put/get、Oracle document 作成、Enterprise AI VLM、chunking、embedding、Oracle indexing、hybrid search、Enterprise AI LLM 生成を 1 回通し、作成した smoke document が citation に含まれることを確認する。既定 query は一意な `SMOKE-...` marker の原文引用を要求し、検索は新規 `document_id` に限定される。既定 query では LLM 回答にも marker が含まれない場合に `stage=rag_answer_marker` で失敗する。JSON 出力の `ok`、`marker`、`query`、`answer_contains_marker`、`trace_id`、`chunk_count`、`citation_count`、`cleanup`、`diagnostics.oracle_vector_target_accuracy`、必要に応じて `diagnostics.context_diversified_count` / `diagnostics.context_group_expanded_count` / `diagnostics.context_expanded_count` / `diagnostics.context_compressed_count` / `diagnostics.context_compression_saved_chars` を保存し、staging gate の artifact にする。既定では evidence として作成物を保持し、`cleanup` は `skipped` になる。DB/Object Storage を汚したくない一時確認では `uv run python -m app.rag.staging_smoke --cleanup` を使い、成功・失敗どちらでも作成済み Oracle document/chunk と Object Storage object の削除 status を確認する。失敗時は preflight の `checks` または本実行の `stage` / `cause_type` を見て、Object Storage、Oracle、VLM、embedding、retrieval、context diversity、context group expansion、context expansion、context compression、generation のどこで止まったかを切り分ける。query を変える場合は `--query "確認用キーワード {marker} を要約してください"` のように `{marker}` placeholder を残す。
9. golden set 評価と負荷試験を通してから production へ昇格する。
