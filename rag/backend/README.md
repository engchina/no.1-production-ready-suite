# backend — production-ready RAG API

FastAPI + OCI Enterprise AI（LLM/VLM）+ OCI Generative AI（埋め込み/リランク）+ Oracle 26ai。

## セットアップ

```bash
uv sync                       # 依存解決（dev グループ含む）
cp .env.example .env          # 環境変数を設定
uv run uvicorn app.main:app --reload
# -> http://localhost:8000/docs（Swagger UI）
```

Dockerfile の production entrypoint は Gunicorn + `uvicorn.workers.UvicornWorker` です。
`WEB_CONCURRENCY`、`GUNICORN_TIMEOUT`、`GUNICORN_GRACEFUL_TIMEOUT`、`GUNICORN_KEEP_ALIVE`、`PORT` で worker 数と timeout を調整できます。
local 開発だけ `uvicorn --reload` を使います。

## 開発コマンド

```bash
uv run pytest                 # テスト
uv run ruff check .           # lint
uv run black .                # フォーマット
uv run mypy .                 # 型チェック
uv run bandit -r app          # セキュリティ
```

## 主要 API

| API | 用途 |
|---|---|
| `GET /api/health` | 稼働確認。adapter mode を message に返す |
| `GET /api/ready` | 依存設定を含む readiness。未設定時は 503 |
| `GET /api/dashboard/summary` | ダッシュボード初期表示用の集計・最近の活動・システム情報 |
| `POST /api/documents/upload` | 伝票ファイルを Object Storage 境界へ保存 |
| `GET /api/documents?status=UPLOADED&q=invoice&limit=50&offset=0` | 文書一覧をページング・状態・ファイル名で絞り込み |
| `GET /api/documents/stats` | 状態別ドキュメント件数を取得 |
| `POST /api/documents/{id}/analyze?force=false` | OCR/構造化抽出、chunking、embedding、索引 |
| `POST /api/documents/{id}/register` | 分析済み伝票を本登録状態へ更新 |
| `POST /api/search` | hybrid/vector/keyword 検索 + rerank + citation-grounded 回答生成 |
| `POST /api/search/stream` | SSE 形式で回答・引用をストリーミング |
| `POST /api/evaluation/run` | golden set 評価 |
| `POST /api/table-browser/query` | Select AI による自然言語テーブル参照 |
| `GET /metrics` | Prometheus metrics |

## adapter mode

`AI_SERVICE_ADAPTER=local` は CI/開発向けの deterministic 実装です。アップロードファイルは `LOCAL_STORAGE_DIR` に保存し、embedding/rerank/search/generation も外部 API なしで動きます。

`AI_SERVICE_ADAPTER=oci` では以下の OCI / Oracle 実装を使います。

- `OciEnterpriseAiClient`: OCI Enterprise AI の VLM / LLM
- `OciGenAiClient`: OCI Generative AI の Cohere Embed v4 / Rerank v4 fast
- `OracleClient`: python-oracledb pool + Oracle 26ai AI Vector Search / Oracle Text / Select AI
- `ObjectStorageClient`: OCI Object Storage SDK による原本ファイル保存 / 取得

Embedding は Cohere Embed v4 / Oracle `VECTOR(1536, FLOAT32)` に合わせて 1536 次元を固定契約にしています。`OciGenAiClient` は OCI Generative AI Inference SDK の `embed_text` / `rerank_text` を使い、検索 query は `SEARCH_QUERY`、文書 chunk は `SEARCH_DOCUMENT` として embedding します。`OciGenAiClient.embed()` は返却件数と次元数を検証し、`OracleClient` も chunk 保存・vector search の入口で再検証します。`OciGenAiClient.rerank()` は Cohere Rerank v4 fast の返却 index が候補範囲内で重複せず、返却件数が `top_n` 以内、score が finite number であることを検証してから pipeline に渡します。

Oracle は共有 connection pool を遅延初期化し、アプリ終了時に閉じます。document/chunk の永続化、`VECTOR_DISTANCE` による vector search、Oracle Text `CONTAINS` による keyword search、`DBMS_CLOUD_AI.GENERATE(... action => 'runsql')` による Select AI 境界を同じ tenant filter 付きで実行します。Select AI profile を使う環境では `ORACLE_SELECT_AI_PROFILE` を設定してください。

Object Storage は `OBJECT_STORAGE_NAMESPACE` / `OBJECT_STORAGE_BUCKET` を使って OCI SDK の `put_object` / `get_object` を呼び出し、保存後は `oci://namespace/bucket/key` を document table に保存します。取得時は URI の namespace / bucket が設定と一致することを検証し、別 bucket の object を誤って分析しないようにします。

## Readiness

`GET /api/ready` は adapter mode ごとに起動可否を返します。`local` では `LOCAL_STORAGE_DIR` に probe file を書き込めるか確認します。`oci` では外部 API へ ping せず、デプロイ時に注入される設定を依存グループ単位で検証します。

`oci` の checks は `oci_common`、`enterprise_ai`、`genai`、`oracle`、`object_storage` です。`ENVIRONMENT=production` では追加で `deployment_adapter` と `audit_context_salt` を返し、`AI_SERVICE_ADAPTER=oci` と `AUDIT_CONTEXT_HASH_SALT` の注入を必須にします。すべて `ok` のときだけ HTTP 200 になり、`missing`、`invalid`、`missing_credentials`、`wallet_not_found` のいずれかが含まれる場合は HTTP 503 / `status=degraded` を返します。Oracle は `ORACLE_USER` / `ORACLE_DSN` に加え、`ORACLE_PASSWORD` または存在する `ORACLE_WALLET_DIR` のどちらかを要求します。レスポンスには設定値や secret は含めません。

## ダッシュボード

`GET /api/dashboard/summary` は UI 初期表示向けに、文書件数、月次アップロード/登録件数、カテゴリ数、検索可能チャンク数、最近の活動、readiness check をまとめて返します。local adapter では登録済み in-memory データから算出し、OCI adapter では Oracle document/chunk table の集計 SQL を使います。

## Oracle 26ai schema

`app.clients.oracle` は production 初期化用の DDL 例を返します。`oracle_document_schema_sql()` は文書メタデータ、`oracle_vector_schema_sql()` は `VECTOR(1536, FLOAT32)` の chunk/vector table、`oracle_audit_schema_sql()` は検索・取込の監査 table を生成します。

監査 table は query 本文や OCR 原文を保存せず、`query_hash`、`source_sha256`、件数、guardrail code、trace id、error type などの運用メタデータだけを永続化する設計です。

## アップロード制限

`MAX_UPLOAD_BYTES` で最大ファイルサイズを制御します。既定は 20 MiB です。
`ALLOWED_UPLOAD_CONTENT_TYPES` で MIME type を制限し、既定では PDF、JPEG、PNG、TIFF、text/plain、application/octet-stream を許可しています。
Object Storage adapter の key は保存時に安全な文字へ正規化します。local 取得時は `local://`、OCI 取得時は `oci://namespace/bucket/key` または plain key だけを受け付けます。相対パス要素、16 階層超、1 要素 255 文字超、全体 1024 文字超の key は拒否し、異常な object path を分析処理へ渡しません。

アップロード時は原本 bytes の `content_sha256` と `file_size_bytes` を保存します。同じ content hash の既存ドキュメントがある場合、レスポンスと詳細 API に `duplicate_of_document_id` を返します。重複アップロードでも原本は保存しますが、後続処理や UI で確認・スキップ判断できるよう参照元を明示します。

## 分析ステートマシン

`POST /api/documents/{id}/analyze` は `UPLOADED` / `ERROR` を分析対象にします。`ANALYZED` / `REGISTERED` は既定では既存結果を返す idempotent な no-op です。`force=true` は `ANALYZED` の再分析だけに使えます。`ANALYZING` は二重実行を避けるため 409、`REGISTERED` の force 再分析も本登録済みデータ保護のため 409 を返します。

分析前に Object Storage から取得した原本 bytes を `file_size_bytes` / `content_sha256` と照合します。不一致の場合は OCR/索引へ進まず `ERROR` 状態にし、409 を返します。

chunking は `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` / `RAG_MAX_CHUNKS_PER_DOCUMENT` で制御します。`RAG_CHUNK_OVERLAP >= RAG_CHUNK_SIZE` は起動時に拒否し、1 文書の chunk 数が上限を超えた場合は索引せず `ERROR` 状態にします。

`ANALYZING` / `ERROR` へ移ると、そのドキュメントの既存 chunk/index 行と古い抽出フィールドは検索・表示対象から外します。検索とダッシュボードの searchable rows は `ANALYZED` / `REGISTERED` の chunk だけを数えます。ユーザーが修正できる取込エラーは日本語の原因を残し、未知の内部/SDK エラーは汎用メッセージだけを document error に保存します。

`POST /api/documents/{id}/register` は `ANALYZED` / `REGISTERED` 状態に加えて、その document の検索可能 chunk が 1 件以上あることを確認します。chunk がない場合は 409 を返し、再分析を促します。

## 検索フィルター

HTTP header `X-Tenant-ID` がある場合、アップロード時に tenant id を hash 化して document に保存し、文書一覧、詳細、重複判定、Select AI 代替、検索 retrieval は同じ `tenant_id_hash` のデータだけを対象にします。raw tenant id は DB / レスポンス / 監査ログへ保存しません。tenant header がないローカル開発・CI では全体を参照できます。

`POST /api/search` の `filters` は local adapter でも実際に retrieval に適用されます。対応 key は `document_id`、`file_name`、`category_name`、`status` です。未対応 key や未対応 status は 422 を返します。
`rerank_top_n` は retrieval で取得した候補数を超えられないため、`top_k` 以下に制限します。
keyword retrieval の local score は重複を除いた query token の coverage として 0.0-1.0 に正規化します。vector / keyword / hybrid の同点は document id、chunk index、chunk id で安定順にし、golden set 評価の再現性を保ちます。
検索結果の citation metadata には `retrieval_mode`、`vector_rank`、`keyword_rank`、`vector_score`、`keyword_score`、`rrf_score` を可能な範囲で含めます。query 本文は入れず、hybrid 検索で vector/keyword のどちらが候補を拾ったかを trace id と合わせて調査できます。

retrieval / rerank 後に citation が 0 件の場合は LLM を呼び出さず、固定の no-results 回答と warning を返します。RAG 監査ログと Prometheus metrics の outcome は `no_results` になり、根拠のない生成を避けます。

回答生成の context は rerank 後の上位 chunk から `RAG_CONTEXT_WINDOW_CHARS` に収まる範囲で作ります。レスポンスと監査ログの `citations` は、実際に LLM へ渡した context 内の chunk だけを返します。

検索レスポンスの `diagnostics` は、`top_k`、`rerank_top_n`、retrieval/rerank/citation 件数、context 文字数、filter key、非機密の RAG 設定 fingerprint を返します。query 本文や secret は含めず、no-results や評価回帰の原因調査に使います。

`RAG_SEARCH_TIMEOUT_SECONDS` で `/api/search` と `/api/search/stream` の pipeline 実行時間を制限します。timeout 時は `ApiResponse` 形式の 504 を返し、`rag_search_audit` に `outcome=error` / `error_stage=timeout` を残して、worker を長時間占有しないようにします。

回答生成後は secret leakage をブロックし、citation context との token / n-gram 重なりが少ない場合は `low_groundedness` warning を返します。warning はレスポンスと `rag_search_audit.guardrail_codes` の両方に残るため、UI と運用監視で引用確認を促せます。

## 評価

`POST /api/evaluation/run` は aggregate metrics に加えて `passed`、`error_count`、`threshold_failures`、`case_results` を返します。`thresholds` を指定すると precision / recall / MRR / 回答キーワード命中率の最低値を CI gate として評価できます。検索 API と同じく `rerank_top_n <= top_k` を要求します。各 case の `trace_id`、`status`、取得 document id、関連 document id、hit document id、case 単位の precision / recall / reciprocal rank、回答キーワード命中、guardrail warning、diagnostics、error type を含むため、CI や nightly 評価で失敗した golden case を追跡できます。1 case の検索失敗や timeout は batch 全体を中断せず `status=error` として返し、`passed=false` にします。評価 runner が捕捉した case 失敗は `rag_search_audit` にも残し、timeout は `error_stage=timeout`、その他の case 例外は `error_stage=evaluation` として trace できます。

`evaluation/golden-set.example.json` は評価 API の request schema に合うテンプレートです。実データ投入後に document id と期待キーワードを差し替え、`evaluation/golden-set.json` として CI / staging gate に使います。

CI / nightly では `python -m app.rag.evaluation_cli` を使うと、golden set を評価 API に POST し、レスポンス JSON を artifact として保存しつつ終了コードで gate できます。exit `0` は成功、exit `1` は品質 gate 失敗、exit `2` は golden set / 引数不備、exit `3` は評価 API 接続・HTTP・レスポンス形式の問題です。

## 検索ストリーミング

`POST /api/search/stream` は `text/event-stream` を返します。イベントは `metadata`、`delta`、`citations`、`done` の順です。local adapter では生成済み回答を短い `delta` に分割します。本番で OCI Enterprise AI のストリーミング推論に接続しても同じイベント契約を維持します。

## 監査ログ

Prometheus metrics は `/metrics` で公開します。RAG 全体の latency は `rag_search_duration_seconds`、embedding / retrieval / rerank / generation の stage 別 latency は `rag_search_stage_duration_seconds{mode,stage,outcome}` で確認できます。stage outcome は `success` / `error` / `cancelled` です。評価 case は `rag_evaluation_cases_total{mode,status}` と `rag_evaluation_case_duration_seconds{mode,status}` で記録します。guardrail finding は `rag_guardrail_findings_total{surface,code,severity,action}` で記録し、label に query 本文や回答本文は含めません。

RAG 検索は `app.audit` logger に `rag_search_audit` を構造化ログとして出します。`trace_id`、`request_id`、`outcome`、guardrail code、filter key、retrieval/rerank/citation 件数、context 文字数、設定 fingerprint、引用 document id を含みます。`X-Tenant-ID` / `X-User-ID` がある場合は raw 値ではなく `tenant_id_hash` / `user_id_hash` として記録します。`AUDIT_CONTEXT_HASH_SALT` を OCI Vault 等から注入すると hash に salt を加えられます。`outcome=error` では `error_stage` と `error_type` だけを記録します。query/回答本文/例外 message は保存せず、query は SHA-256 hash と文字数だけを記録します。

取込は `rag_ingestion_audit` を出します。`trace_id`、`request_id`、`tenant_id_hash`、`user_id_hash`、`document_id`、outcome、原本 SHA-256、byte 数、document type、抽出 confidence、field/chunk/vector 件数、エラー種別を含みます。OCR 原文は保存しません。未知の内部/SDK エラーでは例外 message を保存せず、安全な固定メッセージだけを残します。

## テーブルブラウザ

`POST /api/table-browser/query` は Select AI 境界を使った自然言語テーブル参照です。local adapter では登録済みドキュメントの JSON-ready な行を返し、OCI adapter では Oracle Select AI を呼び出します。prompt injection は 422 で拒否し、`drop/delete/update/insert` などのデータ変更意図も参照専用 guardrail として 422 にします。

## エラーレスポンス

HTTP エラー、リクエスト検証エラー、未処理の 500 エラーは `ApiResponse` 形式へ統一しています。
すべての HTTP レスポンスに `X-Request-ID` を付与します。クライアントが `X-Request-ID` を送った場合はその値を引き継ぎます。
ただし、反射する request id は `A-Z a-z 0-9 . _ : -` の 1-128 文字だけに制限し、空白や制御文字を含む値は新しく採番します。
未処理例外のレスポンスには内部詳細を出さず、`app.main` logger の `unhandled_api_error` に `request_id`、HTTP method、path、例外型を記録します。

```json
{
  "data": null,
  "error_messages": ["対応していないファイル形式です。"],
  "warning_messages": []
}
```

## 構成

```
app/
  main.py            FastAPI エントリ
  config.py          設定（pydantic-settings）
  api/routes/        health / documents / categories / search
  clients/           oci_enterprise_ai(LLM/VLM) / oci_genai(embed,rerank) / oracle(26ai) / object_storage
  rag/               chunking / ingestion / pipeline
  schemas/           common / document / search
tests/
```

> ⚠️ LLM/VLM は **OCI Enterprise AI**（OCI Generative AI の chat API は使わない）。埋め込み/リランクは **OCI Generative AI**（Cohere Embed v4 / Rerank v4 fast）。ベクトル検索は **Oracle 26ai**。
