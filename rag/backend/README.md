# backend — production-ready RAG API

FastAPI + OCI Enterprise AI（LLM/VLM）+ OCI Generative AI（埋め込み/リランク）+ Oracle 26ai。

## セットアップ

```bash
uv sync                       # 依存解決（共有 package rag-parser-core を path 依存で取り込む）
cp .env.example .env          # 環境変数を設定
uv run uvicorn app.main:app --reload
# -> http://localhost:8000/docs（Swagger UI）
```

Dockerfile の production entrypoint は Gunicorn + `uvicorn.workers.UvicornWorker` です。
`WEB_CONCURRENCY`、`GUNICORN_TIMEOUT`、`GUNICORN_GRACEFUL_TIMEOUT`、`GUNICORN_KEEP_ALIVE`、`PORT` で worker 数と timeout を調整できます。
local 開発だけ `uvicorn --reload` を使います。

外部 parser(Docling / Marker / Unstructured / MinerU / Dots.OCR)は **backend には載せず**、
独立した FastAPI マイクロサービス(`services/parsers/<name>`)で動かします。backend は取込時に
`app.clients.parser_service` で HTTP 委譲し、未達時は local / Enterprise AI VLM へ fallback します。
重い parser 依存は runtime / 既定 `uv sync` に入りません。marker(pillow<11)と unstructured(pillow>=11.1)は
**同一環境で共存不可**のため combined extra は提供せず、必要時のみ単一 adapter を per-adapter extra
(`uv sync --extra docling` 等、ローカルデバッグ用)で導入できます。
> 依存(`rag-parser-core` path 依存)を追加・変更したら **`uv lock` の再生成**が必要です
> (Docker build context はリポジトリ root)。

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
| `GET /api/health` | 稼働確認。OCI 前提の稼働 message を返す |
| `GET /api/ready` | 依存設定を含む readiness。未設定時は 503 |
| `GET /api/dashboard/summary` | ダッシュボード初期表示用の集計・最近の活動・システム情報 |
| `POST /api/documents/upload` | ドキュメントファイルを Object Storage 境界へ保存 |
| `GET /api/documents?status=UPLOADED&q=manual&limit=50&offset=0` | 文書一覧をページング・状態・ファイル名で絞り込み |
| `GET /api/documents/stats` | 状態別ドキュメント件数を取得 |
| `POST /api/documents/{id}/ingest?force=false` | 旧互換入口。取込 job をキュー投入して即時に `IngestionJob` を返す |
| `POST /api/documents/{id}/ingestion-jobs?force=false` | 保存済みドキュメントを永続取込 job としてキュー投入 |
| `GET /api/documents/ingestion-jobs?status=QUEUED` | 取込 job 履歴・状態をページング取得 |
| `POST /api/documents/ingestion-jobs/drain` | 永続化済み QUEUED job を再実行 |
| `POST /api/documents/ingestion-jobs/{job_id}/retry` | 失敗・完了・キャンセル済み job の対象文書を新規 job として再投入 |
| `POST /api/documents/ingestion-jobs/{job_id}/cancel` | QUEUED/RUNNING job を CANCELLED にし、worker 終了時の上書きを防ぐ |
| `POST /api/search` | hybrid/vector/keyword 検索 + rerank + citation-grounded 回答生成 |
| `POST /api/search/stream` | SSE 形式で回答・引用をストリーミング |
| `POST /api/evaluation/run` | golden set 評価 |
| `GET /metrics` | Prometheus metrics |

## OCI / Oracle 実装

Backend は常に以下の OCI / Oracle 実装を使います。local / oci の実行モード切り替えはありません。

- `OciEnterpriseAiClient`: OCI Enterprise AI の VLM / LLM
- `OciGenAiClient`: OCI Generative AI の Cohere Embed v4 / Rerank v4 fast
- `OracleClient`: python-oracledb pool + Oracle 26ai AI Vector Search / Oracle Text
- `ObjectStorageClient`: OCI Object Storage SDK による原本ファイル保存 / 取得

モデル設定画面で保存した Enterprise AI / Generative AI 設定は `MODEL_SETTINGS_FILE` の JSON を正本として永続化します。既定は `model-settings.json` で、相対パスは `backend/.env` と同じディレクトリを基準に解決されます。`.env` には `MODEL_SETTINGS_FILE=model-settings.json` と書けます。`.env` は初期値・bootstrap 用で、保存済み JSON が存在する場合は JSON が優先されます。ファイルには Enterprise AI API key も含まれるため、backend は親ディレクトリを `0700`、ファイルを `0600` に補正して保存します。

`OciEnterpriseAiClient` は Enterprise AI の実 endpoint / model deployment / gateway が返す JSON envelope の揺れを吸収します。VLM は `structured_extraction`、`extraction`、`prediction(s)`、`output(s)`、JSON 文字列などから `StructuredExtraction` を取り出して Pydantic で検証します。`StructuredExtraction` は `raw_text` と `elements` を持ち、ページ、読み順、見出し、本文、リスト、表、図、header/footer などを同じ JSON で表せます。LLM は `answer`、`text`、`output_text`、`generated_text`、`choices[].message.content`、`inference_response` などから回答 text を取り出します。独自 gateway がさらに深い envelope を返す場合は `OCI_ENTERPRISE_AI_LLM_RESPONSE_PATH` / `OCI_ENTERPRISE_AI_VLM_RESPONSE_PATH` に JSON Pointer 形式(`/payload/results/0/text` など)を設定して候補 node を明示できます。いずれも OCI Generative AI chat API には接続しません。

Enterprise AI endpoint の request shape が標準 payload と異なる場合は、`OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE` / `OCI_ENTERPRISE_AI_VLM_PAYLOAD_TEMPLATE` に JSON object template を設定できます。`${prompt}`、`${context}`、`${mime_type}`、`${data_base64}`、`${structure_instructions}` などの文字列 placeholder と、`"${messages}"`、`"${parameters}"`、`"${structured_extraction_schema}"` などの object placeholder を使えます。テンプレート未設定時は標準 payload を使い、アップロード時の MIME type と構造化抽出 instructions を VLM input に渡します。

VLM input の搬送方式は `OCI_ENTERPRISE_AI_VLM_INPUT_MODE` で選べます。既定の `auto` は画像を inline data URL、PDF など非画像を OCI Enterprise AI `/files` API にアップロードして `file_id` を `/responses` へ渡します。`files_api` は画像も含めて明示的に `/files` 経由にし、`inline_image` は画像だけ inline で送ります。設定画面では「VLM 入力方式」で選択し、API パスは通常 `/responses` のままにします。

Enterprise AI endpoint の request / response 契約だけを Oracle や Object Storage から切り離して確認する場合は、`app.rag.enterprise_ai_probe` を使います。`--dry-run` は endpoint へ送信せず、URL、template 使用有無、payload key、payload shape、JSON byte 数だけを出します。本実行は LLM / VLM を直接呼び、回答本文や OCR 本文は出さず、text 文字数・element 件数などの非機密 summary だけを返します。

```bash
uv run python -m app.rag.enterprise_ai_probe --surface both --dry-run
uv run python -m app.rag.enterprise_ai_probe --surface llm
uv run python -m app.rag.enterprise_ai_probe --surface vlm --mime-type text/plain
```

Embedding は Cohere Embed v4 / Oracle `VECTOR(1536, FLOAT32)` に合わせて 1536 次元を固定契約にしています。`OciGenAiClient` は OCI Generative AI Inference SDK の `embed_text` / `rerank_text` を使い、検索 query は `SEARCH_QUERY`、文書 chunk は `SEARCH_DOCUMENT` として embedding します。`OciGenAiClient.embed()` は返却件数と次元数を検証し、`OracleClient` も chunk 保存・vector search の入口で再検証します。`OciGenAiClient.rerank()` は Cohere Rerank v4 fast の返却 index が候補範囲内で重複せず、返却件数が `top_n` 以内、score が finite number であることを検証してから pipeline に渡します。

Oracle は共有 connection pool を遅延初期化し、アプリ終了時に閉じます。document/chunk の永続化、HNSW vector index + `FETCH APPROX ... WITH TARGET ACCURACY` による vector search、Oracle Text `CONTAINS` による keyword search を同じ tenant filter 付きで実行します。query 側の approximate search 精度は `ORACLE_VECTOR_TARGET_ACCURACY` で調整できます。`ORACLE_SELECT_AI_PROFILE` を設定すると `/api/search/select-ai` で Oracle Select AI を使えます。既定は `showsql` で SQL 生成のみ、`runsql` は明示指定時だけ許可し、データ変更意図は guardrail で拒否します。

Object Storage は `OBJECT_STORAGE_REGION` / `OBJECT_STORAGE_NAMESPACE` / `OBJECT_STORAGE_BUCKET` を使って OCI SDK の `put_object` / `get_object` を呼び出し、保存後は `oci://namespace/bucket/key` を document table に保存します。取得時は URI の namespace / bucket が設定と一致することを検証し、別 bucket の object を誤って取込しないようにします。

## 認証

`AUTH_MODE=local` ではログインを要求せず、UI もログイン画面とログアウト導線を表示しません。開発・CI の既定値です。

`AUTH_MODE=production` では `/api/auth/login` で signed cookie セッションを発行し、`/api/auth/logout` で削除します。保護 API は有効なセッション Cookie がない場合 401 を返します。`AUTH_USERNAME`、`AUTH_PASSWORD`、`AUTH_SESSION_SECRET` を `.env` から注入してください。`AUTH_COOKIE_SECURE=true` は HTTPS 配信時に有効化します。

## Readiness

`GET /api/ready` は外部 API へ ping せず、デプロイ時に注入される設定を依存グループ単位で検証します。

checks は `oci_common`、`enterprise_ai`、`genai`、`oracle`、`object_storage` です。`ENVIRONMENT=production` では追加で `audit_context_salt` を返し、`AUDIT_CONTEXT_HASH_SALT` の注入を必須にします。すべて `ok` のときだけ HTTP 200 になり、`missing`、`invalid`、`missing_credentials`、`wallet_not_found` のいずれかが含まれる場合は HTTP 503 / `status=degraded` を返します。Oracle は `ORACLE_USER` / `ORACLE_DSN` に加え、`ORACLE_PASSWORD` または `ORACLE_CLIENT_LIB_DIR/network/admin` に存在する Wallet のどちらかを要求します。レスポンスには設定値や secret は含めません。

## ダッシュボード

`GET /api/dashboard/summary` は UI 初期表示向けに、文書件数、月次アップロード/索引済み件数、検索可能チャンク数、最近の活動、readiness check をまとめて返します。集計は Oracle document/chunk table の SQL を使います。

## Oracle 26ai schema

`app.rag.oracle_schema` は production 初期化用の DDL artifact と監査 manifest を生成します。`oracle_document_schema_sql()` は文書メタデータ、`oracle_vector_schema_sql()` は `VECTOR(1536, FLOAT32)` の chunk/vector table、`oracle_search_audit_schema_sql()` / `oracle_ingestion_audit_schema_sql()` は検索・取込の監査 table を生成します。

```bash
uv run python -m app.rag.oracle_schema \
  --output ../artifacts/oracle-schema.sql \
  --manifest-output ../artifacts/oracle-schema.manifest.json
```

manifest は artifact 全体と section ごとの SHA-256、statement 数、`VECTOR(1536, FLOAT32)`、HNSW index (`COSINE`, target accuracy `95`, neighbors `32`, efconstruction `500`) の契約を含みます。staging / production では生成物をレビューし、SQLcl や OCI Resource Manager の安全な適用手順で実行してから smoke test へ進みます。

既存 Oracle schema を現行 DDL 契約へ寄せる場合は migration artifact を生成します。現在の migration は `rag_ingestion_jobs.attempt_count NUMBER(5) DEFAULT 0 NOT NULL`、`rag_ingestion_jobs.max_attempts NUMBER(5) DEFAULT 3 NOT NULL`、`rag_ingestion_jobs_attempts_ck` を追加/補正します。

```bash
uv run python -m app.rag.oracle_schema --migration \
  --output ../artifacts/oracle-schema-migration.sql \
  --manifest-output ../artifacts/oracle-schema-migration.manifest.json
```

監査 table は query 本文や OCR 原文を保存せず、`query_hash`、`source_sha256`、件数、guardrail code、trace id、error type などの運用メタデータだけを永続化する設計です。

## アップロード制限

`MAX_UPLOAD_BYTES` で最大ファイルサイズを制御します。既定は 200 MiB です。
`ALLOWED_UPLOAD_CONTENT_TYPES` で MIME type を制限し、既定では PDF、JPEG、PNG、TIFF、text/plain、application/octet-stream を許可しています。
Object Storage client の key は保存時に安全な文字へ正規化します。local 取得時は `local://`、OCI 取得時は `oci://namespace/bucket/key` または plain key だけを受け付けます。相対パス要素、16 階層超、1 要素 255 文字超、全体 1024 文字超の key は拒否し、異常な object path を取込処理へ渡しません。

アップロード時は原本 bytes の `content_sha256` と `file_size_bytes` を保存します。同じ content hash の既存ドキュメントがある場合、レスポンスと詳細 API に `duplicate_of_document_id` を返します。重複アップロードでも原本は保存しますが、後続処理や UI で確認・スキップ判断できるよう参照元を明示します。

## 取込ステートマシン

`POST /api/documents/{id}/ingest` と `POST /api/documents/{id}/ingestion-jobs` は、どちらも HTTP リクエスト内では取込を実行せず、永続化済み `IngestionJob` を返します。`UPLOADED` / `ERROR` は `QUEUED`、`INDEXED` は既定で `SKIPPED(already_indexed)`、`force=true` では再取込用の `QUEUED` になります。`INGESTING` は二重実行を避けるため 409 を返します。実際の OCR/本文抽出、chunking、embedding、Oracle 26ai 索引は `IngestionQueueWorker` が消費します。

ローカル開発の既定では `INGESTION_QUEUE_DEDICATED_WORKER_ENABLED=true`、`INGESTION_QUEUE_INPROCESS_WORKER_ENABLED=true`、`INGESTION_QUEUE_PROCESS_ISOLATION_ENABLED=true` です。API process 内の worker は軽量 dispatcher として動き、job 本体は `python -m app.rag.ingestion_job_runner <job_id>` の subprocess で実行されます。Docling / OCR / CUDA 初期化が API event loop や他画面の設定 API を塞がないようにするためです。Docker Compose / 本番では `backend` と `ingestion-worker` service を分け、worker container は `INGESTION_QUEUE_PROCESS_ISOLATION_ENABLED=false` で直接 job を実行します。

取込前に Object Storage から取得した原本 bytes を `file_size_bytes` / `content_sha256` と照合します。不一致の場合は OCR/索引へ進まず `ERROR` 状態にし、409 を返します。

chunking は `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` で制御します。取込では `chunk_extraction()` が `StructuredExtraction.elements` を優先し、Docling / Marker / Unstructured / RAGFlow DeepDoc の要素単位・章節単位 chunking を OCI-native に再実装した `structure_v1` chunk を作ります。外部 parser 依存は追加せず、旧形式の `raw_text` だけの抽出結果は軽量 element 推定へ fallback します。`RAG_CHUNK_OVERLAP >= RAG_CHUNK_SIZE` は起動時に拒否します。大きな文書でも chunk 総数では拒否せず、生成された全 chunk を embedding / indexing 対象にします。

`structure_v1` では章節境界を跨がず、表は他要素から孤立させ、図・画像説明と図注は `content_kind=figure` として同一 chunk にまとめ、リストは連続性を保ち、header/footer は繰り返しノイズとして主索引から除外します。citation metadata には `chunk_profile`、`content_kind`、`section_title/path/level`、`page_start/page_end`、`element_kinds`、`element_ids`、`text_sha256`、`text_chars` を入れ、Oracle DDL を変えずに `rag_chunks.metadata_json` でトレースできるようにします。

`INGESTING` / `ERROR` へ移ると、そのドキュメントの既存 chunk/index 行と古い抽出結果は検索・表示対象から外します。検索とダッシュボードの searchable rows は `INDEXED` の chunk だけを数えます。ユーザーが修正できる取込エラーは日本語の原因を残し、未知の内部/SDK エラーは汎用メッセージだけを document error に保存します。

## 検索フィルター

HTTP header `X-Tenant-ID` がある場合、アップロード時に tenant id を hash 化して document に保存し、文書一覧、詳細、重複判定、検索 retrieval は同じ `tenant_id_hash` のデータだけを対象にします。raw tenant id は DB / レスポンス / 監査ログへ保存しません。tenant header がないローカル開発・CI では全体を参照できます。

認証ゲートウェイやアプリケーション権限層が `X-RAG-Allowed-Document-Ids` / `X-RAG-Allowed-Category-Names` を付与した場合、文書一覧、詳細、chunk count、検索 retrieval はその document/category scope にも閉じます。header が存在するが有効値が 0 件の場合は deny-all、未指定の場合だけ制限なしです。これらの raw scope 値は監査ログへ出しません。

`POST /api/search` の `filters` は Oracle retrieval に適用されます。対応 key は `document_id`、`file_name`、`category_name`、`status`、`content_kind`、`section_title`、`section_path` です。`content_kind=table` で表 chunk だけ、`content_kind=figure` で図・画像説明 chunk だけ、`section_path` で特定章節だけを候補にできます。未対応 key、未対応 status、未対応 `content_kind` は 422 を返します。
`rerank_top_n` は retrieval で取得した候補数を超えられないため、`top_k` 以下に制限します。
keyword retrieval の local score は重複を除いた query token の coverage として 0.0-1.0 に正規化します。vector / keyword / hybrid の同点は document id、chunk index、chunk id で安定順にし、golden set 評価の再現性を保ちます。
chunk metadata には章節・ページ・要素 ID に加えて `chunk_group_id`、`chunk_group_kind`、`chunk_part_index`、`chunk_part_count` を保存します。長い表・リスト・本文が複数 chunk に分かれても、同じ親要素/章節から来た引用を後で集約できます。
Hybrid retrieval は Reciprocal Rank Fusion を使います。`RAG_RRF_K` で RRF 定数を調整できます。検索結果の citation metadata には `retrieval_mode`、`vector_rank`、`keyword_rank`、`vector_score`、`keyword_score`、`rrf_k`、`rrf_score` を可能な範囲で含めます。query 本文は入れず、hybrid 検索で vector/keyword のどちらが候補を拾ったかを trace id と合わせて調査できます。
retrieval 前には `RAG_QUERY_EXPANSION_ENABLED` / `RAG_QUERY_EXPANSION_MAX_VARIANTS` に従って deterministic な業務同義語 query expansion を行います。元 query は rerank / LLM 生成に維持し、複数 variant の retrieval 結果は chunk id 単位で RRF 融合します。citation metadata には `query_fusion_score`、`query_variant_count`、`matched_query_variant_count` を含められるため、展開が効いたかを query 本文なしで追跡できます。
rerank 後、LLM context を作る前に `text_sha256` または正規化本文 hash で同一本文 chunk を除外します。重複根拠で context window が埋まるのを避け、去重件数は `deduplicated_count` として diagnostics / audit に残します。
`RAG_CONTEXT_DIVERSITY_LAMBDA` を 1.0 未満にすると、rerank anchor を MMR 風に重排し、同質 chunk だけで context window が埋まるのを抑えます。既定の 1.0 は rerank 順を維持します。重排された件数は `context_diversified_count` として diagnostics / audit に残し、順位が変わった citation metadata には `context_diversified`、`context_original_rank`、`context_diversified_rank` を付けます。
`RAG_CONTEXT_GROUP_EXPANSION_ENABLED=true` にすると、rerank anchor と同じ `chunk_group_id` の sibling chunk を Oracle から取得し、分割された表・箇条書き・章節の前後文脈を生成 context へ低優先で追加します。既定は無効です。anchor ごとの追加上限は `RAG_CONTEXT_GROUP_MAX_CHUNKS` で、追加 chunk の citation metadata には `context_group_expanded`、`context_anchor_chunk_id`、`context_group_id`、`context_group_distance` を含め、件数は `context_group_expanded_count` として diagnostics / audit に残します。
`RAG_CONTEXT_NEIGHBOR_WINDOW` を 1-5 にすると、rerank anchor の同一文書前後 chunk を Oracle の `chunk_index` で取得し、生成 context へ低優先で追加します。既定は 0 で無効です。追加 chunk の citation metadata には `context_expanded`、`context_anchor_chunk_id`、`context_neighbor_distance` を含め、件数は `context_expanded_count` として diagnostics / audit に残します。
`RAG_CONTEXT_COMPRESSION_ENABLED=true` にすると、LLM context 作成前に query 関連 sentence / line だけを抽出して長い chunk を圧縮します。既定は無効です。圧縮された citation metadata には `context_compressed`、`context_original_chars`、`context_compressed_chars` を含め、件数と節約文字数は `context_compressed_count` / `context_compression_saved_chars` として diagnostics / audit に残します。query 本文や除外した本文は trace / audit に出しません。

retrieval / rerank 後に citation が 0 件の場合は LLM を呼び出さず、固定の no-results 回答と warning を返します。RAG 監査ログと Prometheus metrics の outcome は `no_results` になり、根拠のない生成を避けます。

回答生成の context は rerank 後の上位 chunk から `RAG_CONTEXT_WINDOW_CHARS` に収まる範囲で作ります。レスポンスと監査ログの `citations` は、実際に LLM へ渡した context 内の chunk だけを返します。

検索レスポンスの `diagnostics` は、`top_k`、`rerank_top_n`、query variant 件数、retrieval/rerank/去重/context diversity/context group expansion/context expansion/context compression/citation 件数、context compression 節約文字数、context 文字数、RRF 定数、Oracle vector target accuracy、filter key、非機密の RAG 設定 fingerprint を返します。query 本文や secret は含めず、no-results や評価回帰の原因調査に使います。

`RAG_SEARCH_TIMEOUT_SECONDS` で `/api/search` と `/api/search/stream` の pipeline 実行時間を制限します。timeout 時は `ApiResponse` 形式の 504 を返し、`rag_search_audit` に `outcome=error` / `error_stage=timeout` を残して、worker を長時間占有しないようにします。

回答生成後は secret leakage をブロックし、citation context との token / n-gram 重なりが少ない場合は `low_groundedness` warning を返します。warning はレスポンスと `rag_search_audit.guardrail_codes` の両方に残るため、UI と運用監視で引用確認を促せます。

## 評価

`POST /api/evaluation/run` は aggregate metrics に加えて `passed`、`error_count`、`threshold_failures`、`case_results` を返します。`thresholds` を指定すると precision / recall / MRR / 回答キーワード命中率 / groundedness pass rate の最低値を CI gate として評価できます。検索 API と同じく `rerank_top_n <= top_k` を要求します。各 case の `trace_id`、`status`、取得 document id、関連 document id、hit document id、case 単位の precision / recall / reciprocal rank、回答キーワード命中、groundedness pass / score / overlap count、guardrail warning、diagnostics、error type を含むため、CI や nightly 評価で失敗した golden case を追跡できます。1 case の検索失敗や timeout は batch 全体を中断せず `status=error` として返し、`passed=false` にします。評価 runner が捕捉した case 失敗は `rag_search_audit` にも残し、timeout は `error_stage=timeout`、その他の case 例外は `error_stage=evaluation` として trace できます。

`evaluation/golden-set.example.json` は評価 API の request schema に合うテンプレートです。実データ投入後に document id と期待キーワードを差し替え、`evaluation/golden-set.json` として CI / staging gate に使います。`evaluation/compare.example.json` は AutoRAG 的に `hybrid` / `vector` / `keyword`、retrieval depth、rerank depth を同じ golden set で比較するテンプレートです。

CI / nightly では `python -m app.rag.evaluation_cli` を使うと、golden set を評価 API に POST し、レスポンス JSON を artifact として保存しつつ終了コードで gate できます。入力 JSON に `experiments` があれば `/api/evaluation/compare` へ送り、rank 1 の best experiment の metrics で gate します。exit `0` は成功、exit `1` は品質 gate 失敗、exit `2` は golden set / 引数不備、exit `3` は評価 API 接続・HTTP・レスポンス形式の問題です。

## Staging smoke test

OCI / Oracle staging では各種接続設定を注入し、`UPLOAD_STORAGE_BACKEND=oci` で原本保存先も OCI Object Storage にした上で、backend container 内から `uv run python -m app.rag.enterprise_ai_probe --surface both --dry-run` と `uv run python -m app.rag.staging_smoke --preflight-only` を先に実行します。Enterprise AI probe は LLM/VLM の request 契約を、staging smoke preflight は `/api/ready` と同じ依存グループに加えて実 smoke が local storage へ逃げていないことを `smoke_object_storage_backend` で確認します。すべて `ok` になったら `uv run python -m app.rag.enterprise_ai_probe --surface both` と `uv run python -m app.rag.staging_smoke` を実行します。

本実行では Object Storage put/get、Oracle document 作成、Enterprise AI VLM、chunking、embedding、Oracle indexing、hybrid search、Enterprise AI LLM 生成を 1 回通し、作成した smoke document が citation に含まれることを JSON で確認できます。既定 query は今回作成した一意な `SMOKE-...` marker の引用を要求し、検索は `document_id` filter で新規 document に限定します。既定 query では LLM 回答にも marker が含まれることを gate し、出力には `marker`、実行 `query`、`answer_contains_marker`、`trace_id`、chunk/citation 件数、非機密 `diagnostics`、`cleanup` status が含まれます。既定では evidence として smoke artifact を残すため `cleanup` は `skipped` です。staging DB/Object Storage を汚したくない確認では `--cleanup` を付け、成功・失敗どちらでも作成済み Oracle document/chunk と Object Storage object の削除を best-effort で試みます。`RAG_CONTEXT_GROUP_EXPANSION_ENABLED`、`RAG_CONTEXT_NEIGHBOR_WINDOW`、`RAG_CONTEXT_COMPRESSION_ENABLED` を staging で有効化した場合は `diagnostics.context_group_expanded_count`、`diagnostics.context_expanded_count`、`diagnostics.context_compressed_count`、`diagnostics.context_compression_saved_chars` も artifact として確認します。失敗時は raw 例外 message を出さず、preflight 失敗では `checks`、実行中の失敗では `stage` と `cause_type`、cleanup 指定時は `cleanup` status だけを JSON に含めます。

```bash
uv run python -m app.rag.staging_smoke --preflight-only
uv run python -m app.rag.staging_smoke
uv run python -m app.rag.staging_smoke --cleanup
uv run python -m app.rag.staging_smoke --query "確認用キーワード {marker} を要約してください"
```

## 検索ストリーミング

`POST /api/search/stream` は `text/event-stream` を返します。既定では検索完了後に `metadata`、`delta`、`citations`、`done` を返します。`RAG_STREAM_REALTIME_ENABLED=true` の場合は OCI Enterprise AI の streaming 推論から generation 中に `delta` を即時送信し、最終的に `metadata`、`citations`、`done` を返します。どちらの場合も SSE event 名と payload 形状は維持します。

## 監査ログ

Prometheus metrics は `/metrics` で公開します。RAG 全体の latency は `rag_search_duration_seconds`、embedding / retrieval / rerank / context diversity / context group expansion / context expansion / context compression / generation の stage 別 latency は `rag_search_stage_duration_seconds{mode,stage,outcome}` で確認できます。stage outcome は `success` / `error` / `cancelled` です。評価 case は `rag_evaluation_cases_total{mode,status}` と `rag_evaluation_case_duration_seconds{mode,status}` で記録します。guardrail finding は `rag_guardrail_findings_total{surface,code,severity,action}` で記録し、label に query 本文や回答本文は含めません。

RAG 検索は `app.audit` logger に `rag_search_audit` を構造化ログとして出します。`trace_id`、`request_id`、`outcome`、guardrail code、filter key、retrieval/rerank/context diversity/context group expansion/context expansion/context compression/citation 件数、context compression 節約文字数、context 文字数、設定 fingerprint、引用 document id を含みます。`X-Tenant-ID` / `X-User-ID` がある場合は raw 値ではなく `tenant_id_hash` / `user_id_hash` として記録します。`AUDIT_CONTEXT_HASH_SALT` を `.env` から注入すると hash に salt を加えられます。`outcome=error` では `error_stage` と `error_type` だけを記録します。query/回答本文/例外 message は保存せず、query は SHA-256 hash と文字数だけを記録します。

取込は `rag_ingestion_audit` を出します。`trace_id`、`request_id`、`tenant_id_hash`、`user_id_hash`、`document_id`、outcome、原本 SHA-256、byte 数、document type、抽出 confidence、chunk/vector 件数、エラー種別を含みます。OCR 原文は保存しません。未知の内部/SDK エラーでは例外 message を保存せず、安全な固定メッセージだけを残します。

`TRACE_EXPORT_HTTP_ENDPOINT` を設定すると、検索・取込 pipeline の `rag.trace_span` event を OpenTelemetry / Langfuse gateway へ非同期 HTTP JSON で送信します。送信 payload は `trace_id`、stage、outcome、duration、低 cardinality attributes、`error_type` に限定し、query 本文、context 本文、OCR 原文、prompt、例外 message は含めません。queue full や送信失敗は `rag_trace_export_dropped` / `rag_trace_export_failed` として `app.trace` logger に残し、RAG request は失敗させません。

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
  api/routes/        health / dashboard / documents / search / evaluation
  clients/           oci_enterprise_ai(LLM/VLM) / oci_genai(embed,rerank) / oracle(26ai) / object_storage
  rag/               chunking / ingestion / pipeline
  schemas/           common / document / search
tests/
```

> ⚠️ LLM/VLM は **OCI Enterprise AI**（OCI Generative AI の chat API は使わない）。埋め込み/リランクは **OCI Generative AI**（Cohere Embed v4 / Rerank v4 fast）。ベクトル検索は **Oracle 26ai**。
