# 評価・観測性・ガードレール

## 評価

API: `POST /api/evaluation/run`

評価ケースは query、関連 document id、回答に含めたいキーワードで構成する。
リポジトリには `evaluation/golden-set.example.json` を同梱している。実データ投入後、document id と期待キーワードを環境に合わせて `evaluation/golden-set.json` として管理する。

```json
{
  "cases": [
    {
      "id": "invoice-total-001",
      "query": "INV-001 の請求金額は？",
      "relevant_document_ids": ["<document-id>"],
      "expected_answer_keywords": ["120000"]
    }
  ],
  "top_k": 10,
  "rerank_top_n": 5,
  "mode": "hybrid",
  "filters": {
    "status": "REGISTERED"
  },
  "thresholds": {
    "precision_at_k": 0.6,
    "recall_at_k": 0.8,
    "mrr": 0.7,
    "answer_keyword_hit_rate": 0.9
  }
}
```

`cases` は 1 件以上必須です。`rerank_top_n` は `top_k` 以下である必要があります。返却指標:

- `evaluated_k`: 実際に評価した最終 citation 数の上限。`min(top_k, rerank_top_n)`。
- `precision_at_k`: 上位 `evaluated_k` 件の document-level citation のうち relevant document が占める割合。同一 document の複数 chunk は 1 件として扱う。
- `recall_at_k`: relevant document をどれだけ取得できたか。
- `mrr`: 最初の relevant document が何位に出たか。
- `answer_keyword_hit_rate`: 回答が期待キーワードを含んだ割合。
- `error_count`: 検索失敗または timeout になった evaluation case 数。
- `passed`: 指定された `thresholds` をすべて満たし、`error_count=0` だったか。`thresholds` 未指定でも case error があれば `false`。
- `threshold_failures`: 閾値を下回った metric、実測値、閾値の一覧。CI gate ではこの配列を失敗理由として出力する。

レスポンスには `case_results` も含める。各 case について `case_id`、`trace_id`、`status`、取得 document id、関連 document id、hit document id、case 単位の precision / recall / reciprocal rank、回答キーワード命中、guardrail warning、diagnostics、elapsed ms、error type を返す。aggregate が悪化したときはこの per-case 診断から、検索漏れ・リランク順序・回答生成のどこで落ちたかを確認する。検索失敗または timeout の case は `status=error` として残し、query 本文や例外 message はレスポンスへ出さない。評価 runner が捕捉した case 失敗は `rag_search_audit` にも残し、timeout は `error_stage=timeout`、その他の case 例外は `error_stage=evaluation` とする。

本番運用では、カテゴリごとに 20-50 件の golden set を作り、chunking / embedding model / rerank top N / prompt を変えたときにこの API を CI または nightly job で実行する。CI では `thresholds` を指定し、`passed=false`、`error_count>0`、または `threshold_failures` 非空なら失敗にする。nightly では `case_results` を保存して回帰差分を追う。

## 観測性

Prometheus metrics は `/metrics` で公開する。

主なメトリクス:

- `rag_http_requests_total`
- `rag_http_request_duration_seconds`
- `rag_search_requests_total`
- `rag_search_duration_seconds`
- `rag_search_stage_duration_seconds`
- `rag_retrieval_hits`
- `rag_evaluation_cases_total`
- `rag_evaluation_case_duration_seconds`
- `rag_ingestion_documents_total`
- `rag_ingestion_chunks`
- `rag_ingestion_stage_duration_seconds`
- `rag_guardrail_findings_total`
- `rag_rate_limit_decisions_total`

`rag_search_stage_duration_seconds` は `mode`、`stage`、`outcome` label を持つ。`stage` は `embedding`、`retrieval`、`rerank`、`generation`、`outcome` は `success`、`error`、`cancelled` を使い、OCI / Oracle / LLM のどこが遅いかを切り分ける。

`rag_ingestion_stage_duration_seconds` は `stage`、`outcome` label を持つ。`stage` は `vlm_extraction`、`chunking`、`embedding`、`indexing`、`outcome` は `success`、`error`、`cancelled` を使い、OCI Enterprise AI の OCR/構造化、chunking、OCI Generative AI embedding、Oracle 26ai indexing のどこで遅延・失敗しているかを切り分ける。

`rag_evaluation_cases_total{mode,status}` と `rag_evaluation_case_duration_seconds{mode,status}` は golden set の case 単位で記録する。`status` は `success` / `error` に固定し、case id や query 本文は label に入れない。

`rag_guardrail_findings_total{surface,code,severity,action}` は query、answer、table query の guardrail finding を数える。`surface` は `query` / `answer` / `table_query`、`action` は `blocked` / `warning` に固定し、query 本文、回答本文、OCR 原文、tenant/user id は label に入れない。

`rag_rate_limit_decisions_total{scope,outcome}` は高コスト API の rate limit 判定を数える。`scope` は `search` / `evaluation` / `upload` / `analyze` / `table_query`、`outcome` は `allowed` / `blocked` に固定し、tenant/user id、IP、query 本文は label に入れない。429 応答には `Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset-After` を返す。

RAG 検索レスポンスには `trace_id` を含める。OCI Enterprise AI や Langfuse を接続する場合は、この `trace_id` を親 ID として OCR、embedding、retrieval、rerank、generation の各 span に渡す。

検索・取込 pipeline は `app.trace` logger へ `rag_trace_span` イベントも出す。payload は `trace_event` に入り、`trace_id`、`span_name`、`outcome`、`duration_ms`、低 cardinality の attributes、`error_type` だけを含む。検索 stage は `embedding`、`retrieval`、`rerank`、`generation`、取込 stage は `vlm_extraction`、`chunking`、`embedding`、`indexing` を使う。query 本文、context 本文、OCR 原文、prompt、field 値、例外 message、tenant/user id の raw 値は含めない。この構造化ログは OpenTelemetry span や Langfuse trace へ橋渡しするための境界であり、Prometheus の aggregate metrics では追えない単一 request の遅延・失敗箇所を調査するために使う。

検索レスポンスと evaluation case result には `diagnostics` も含める。これは `top_k`、`rerank_top_n`、retrieval/rerank/citation 件数、context 文字数、context window、filter key、adapter、RAG 設定 fingerprint だけで構成し、query 本文や secret は含めない。no-results、low recall、context window からの引用落ち、設定変更による品質回帰を trace id と合わせて調査するために使う。

citation の `metadata` には `retrieval_mode`、`vector_rank`、`keyword_rank`、`vector_score`、`keyword_score`、`rrf_score` を入れられる場合だけ含める。query 本文や OCR 原文は含めず、hybrid 検索で vector 側の召回漏れか keyword 側の語彙不一致かを per-case に確認する。

すべての HTTP レスポンスには `X-Request-ID` を付ける。未処理例外は `ApiResponse` の 500 に統一し、クライアントには内部詳細を返さず、`app.main` logger の `unhandled_api_error` に `request_id`、HTTP method、path、例外型を記録する。

### 監査ログ

RAG 検索ごとに `app.audit` logger へ `rag_search_audit` イベントを出す。payload は `audit_event` に入り、`trace_id`、`request_id`、`outcome`、検索モード、filter key、guardrail code、retrieval/rerank/citation 件数、context 文字数、設定 fingerprint、引用 document id、経過時間を含む。`X-Tenant-ID` / `X-User-ID` がある場合は raw 値ではなく `tenant_id_hash` / `user_id_hash` として保存する。`AUDIT_CONTEXT_HASH_SALT` は production で OCI Vault から注入する。

`outcome` は `success`、`blocked`、`no_results`、`error` を使う。`no_results` は citation が 0 件で LLM 生成をスキップしたことを示す。embedding / retrieval / rerank / generation / answer guardrail の例外は `error` として記録し、API timeout は `error_stage=timeout` として記録する。監査ログには `error_stage` と `error_type` だけを残す。例外 message は query や回答本文を含む可能性があるため検索監査ログへ出さない。

取込ごとに `rag_ingestion_audit` イベントも出す。`trace_id`、`request_id`、`tenant_id_hash`、`user_id_hash`、`document_id`、`outcome`、原本 SHA-256、byte 数、document type、抽出 confidence、field/chunk/vector 件数、経過時間、エラー種別を含む。ユーザーが修正できる取込エラーだけ短い message を残し、未知の内部/SDK エラーでは例外 message を保存しない。

監査ログには query、回答本文、OCR 原文、tenant/user id の raw 値を出さない。query と原本は SHA-256 hash とメタデータのみ、tenant/user id は hash のみを保存し、請求書番号・金額・取引先名などの業務データをログへ漏らさない。

`X-Tenant-ID` がある request では、document / chunk の `tenant_id_hash` と照合して一覧、詳細、重複判定、Select AI 代替、retrieval を同一 tenant に限定する。tenant header がない local/CI 実行では全体を参照できる。

## ガードレール

実装: `backend/app/rag/guardrails.py`

現在の参照ポリシー:

- 長すぎる query を拒否する。
- system prompt や過去指示の無視を求める prompt injection を拒否する。
- `drop/delete/update/insert` などの SQL 変更文らしさは警告し、検索のみ実行する。
- Select AI / table browser 境界では SQL 変更文らしさを警告ではなく 422 として拒否する。
- query と answer の個人番号、口座番号、電話番号、メールアドレスらしき値は `[機微情報]` へマスクし、`sensitive_identifier_redacted` warning として返す。マスク後の query を embedding / retrieval に使い、raw 値を監査ログや metrics へ出さない。
- citation がない場合は LLM を呼び出さず、no-results 回答に短絡する。
- 回答に secret らしき文字列が含まれる場合は表示を止める。
- citation がある回答でも、回答と検索根拠の token / n-gram 重なりが少ない場合は `low_groundedness` warning を返し、監査ログにも guardrail code を残す。これは軽量なヒューリスティックであり、本番では LLM-as-judge や RAGAS 等の groundedness 評価で補強する。
- `search`、`evaluation`、`upload`、`analyze`、`table_query` は app 内 rate limit で保護する。bucket key は hash 済み tenant/user context を優先し、匿名時も client host を hash 化して使う。raw tenant/user id、IP、query 本文はログや metrics に出さない。本番では OCI API Gateway / Ingress / Redis などの共有 limiter と併用する。

本番では以下を追加する。

- 権限に基づく document / category filter。
- 業務固有の個人情報分類器や DLP サービスによる追加マスキング。
- citation がある回答に対する LLM-as-judge / RAGAS 等の追加 groundedness 評価。
- 権限・ロール情報を hash context へ追加する場合は、raw 値を出さず Vault 管理の salt または HMAC key で相関する。
