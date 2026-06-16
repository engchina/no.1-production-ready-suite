# 評価・観測性・ガードレール

## 評価

API: `POST /api/evaluation/run`

設定比較 API: `POST /api/evaluation/compare`

評価ケースは query、関連 document id、回答に含めたいキーワードで構成する。
リポジトリには `evaluation/golden-set.example.json` と `evaluation/compare.example.json` を同梱している。実データ投入後、document id と期待キーワードを環境に合わせて `evaluation/golden-set.json` として管理する。

```json
{
  "cases": [
    {
      "id": "policy-flow-001",
      "query": "経費申請の承認フローは？",
      "relevant_document_ids": ["<document-id>"],
      "expected_answer_keywords": ["部門長", "承認"]
    }
  ],
  "top_k": 10,
  "rerank_top_n": 5,
  "mode": "hybrid",
  "filters": {
    "status": "INDEXED"
  },
  "thresholds": {
    "precision_at_k": 0.6,
    "recall_at_k": 0.8,
    "mrr": 0.7,
    "answer_keyword_hit_rate": 0.9,
    "groundedness_pass_rate": 0.9
  }
}
```

`cases` は 1 件以上必須です。`rerank_top_n` は `top_k` 以下である必要があります。返却指標:

- `evaluated_k`: 実際に評価した最終 citation 数の上限。`min(top_k, rerank_top_n)`。
- `precision_at_k`: 上位 `evaluated_k` 件の document-level citation のうち relevant document が占める割合。同一 document の複数 chunk は 1 件として扱う。
- `recall_at_k`: relevant document をどれだけ取得できたか。
- `mrr`: 最初の relevant document が何位に出たか。
- `answer_keyword_hit_rate`: 回答が期待キーワードを含んだ割合。
- `groundedness_pass_rate`: 回答の token / n-gram / 数値・ID 特徴が citation context に支えられている case の割合。no-results のように citation context がない case は、根拠なし生成を避ける短絡経路として pass 扱いにする。
- `error_count`: 検索失敗または timeout になった evaluation case 数。
- `passed`: 指定された `thresholds` をすべて満たし、`error_count=0` だったか。`thresholds` 未指定でも case error があれば `false`。
- `threshold_failures`: 閾値を下回った metric、実測値、閾値の一覧。CI gate ではこの配列を失敗理由として出力する。

レスポンスには `case_results` も含める。各 case について `case_id`、`trace_id`、`status`、取得 document id、関連 document id、hit document id、case 単位の precision / recall / reciprocal rank、回答キーワード命中、groundedness pass / score / overlap count、guardrail warning、diagnostics、elapsed ms、error type を返す。aggregate が悪化したときはこの per-case 診断から、検索漏れ・リランク順序・回答生成・根拠不足のどこで落ちたかを確認する。検索失敗または timeout の case は `status=error` として残し、query 本文や例外 message はレスポンスへ出さない。評価 runner が捕捉した case 失敗は `rag_search_audit` にも残し、timeout は `error_stage=timeout`、その他の case 例外は `error_stage=evaluation` とする。

各 case には `failure_reasons` を付与し、集計として `failure_reason_counts` を返す。理由は `retrieval_miss`、`partial_recall`、`unexpected_retrieval`、`answer_keyword_miss`、`low_groundedness`、`guardrail_warning`、`case_error` に固定する。AutoRAG / FlashRAG 的な比較では、この分布を見ることで chunking / filter / hybrid retrieval / rerank / prompt / guardrail のどこを次に調整すべきかを切り分ける。

`/api/evaluation/compare` は `cases` と複数の `experiments` を受け取る。各 experiment は `id`、`mode`、`top_k`、`rerank_top_n`、`filters`、任意の `rag_overrides` を持ち、同じ golden set に対して順番に `/run` 相当の評価を行う。`filters` には document/status 系だけでなく `content_kind`、`section_title`、`section_path` も指定できるため、表・図だけを優先する設定や特定章節へ絞る設定を golden set で比較できる。`rag_overrides` は RRF 定数、query expansion、context window、context diversity、隣接 context、context compression、Oracle vector target accuracy だけを一時的に上書きし、secret やモデル credential は扱わない。結果は `ranking_metric`、`best_experiment_id`、rank 付き experiment results として返る。順位は `passed=true` を優先し、その後 ranking metric 降順、error 数昇順、failure reason 件数昇順、experiment id 昇順で安定化する。これにより AutoRAG のように retrieval depth、hybrid/vector/keyword、scope filter、context 構成の候補を安全に比較できる。

本番運用では、カテゴリごとに 20-50 件の golden set を作り、chunking / embedding model / rerank top N / prompt を変えたときにこの API を CI または nightly job で実行する。CI では `thresholds` を指定し、`passed=false`、`error_count>0`、または `threshold_failures` 非空なら失敗にする。nightly では API レスポンス artifact と trend summary を保存して回帰差分を追う。`python -m app.rag.evaluation_cli` は入力 JSON に `experiments` がある場合 `/api/evaluation/compare` へ送り、rank 1 の best experiment の metrics を終了コードの gate 判定に使う。`--trend-output` は aggregate metrics、best experiment、experiment rank、result hash だけを含む非機密 JSON を出力し、query/context 原文や `case_results` は含めない。

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

`rag_guardrail_findings_total{surface,code,severity,action}` は query、answer の guardrail finding を数える。`surface` は `query` / `answer`、`action` は `blocked` / `warning` に固定し、query 本文、回答本文、OCR 原文、tenant/user id は label に入れない。

`rag_rate_limit_decisions_total{scope,outcome}` は高コスト API の rate limit 判定を数える。`scope` は `search` / `evaluation` / `upload` / `ingest`、`outcome` は `allowed` / `blocked` に固定し、tenant/user id、IP、query 本文は label に入れない。429 応答には `Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset-After` を返す。

RAG 検索レスポンスには `trace_id` を含める。OCI Enterprise AI、OpenTelemetry、Langfuse を接続する場合は、この `trace_id` を親 ID として OCR、embedding、retrieval、rerank、generation の各 span に渡す。

検索・取込 pipeline は `app.trace` logger へ `rag_trace_span` イベントも出す。payload は `trace_event` に入り、`trace_id`、`span_name`、`outcome`、`duration_ms`、低 cardinality の attributes、`error_type` だけを含む。検索 stage は `embedding`、`retrieval`、`rerank`、`context_diversity`、`context_group_expansion`、`context_expansion`、`context_compression`、`generation`、取込 stage は `vlm_extraction`、`chunking`、`embedding`、`indexing` を使う。`context_diversity` は `RAG_CONTEXT_DIVERSITY_LAMBDA < 1.0`、`context_group_expansion` は `RAG_CONTEXT_GROUP_EXPANSION_ENABLED=true`、`context_expansion` は `RAG_CONTEXT_NEIGHBOR_WINDOW > 0`、`context_compression` は `RAG_CONTEXT_COMPRESSION_ENABLED=true` の場合だけ記録する。query 本文、context 本文、OCR 原文、prompt、例外 message、tenant/user id の raw 値は含めない。この構造化ログは OpenTelemetry span や Langfuse trace へ橋渡しするための境界であり、Prometheus の aggregate metrics では追えない単一 request の遅延・失敗箇所を調査するために使う。`TRACE_EXPORT_HTTP_ENDPOINT` を設定すると、同じ脱機密化済み event を非同期 HTTP JSON で collector / gateway へ送信する。queue が満杯または送信失敗しても request は失敗させず、`rag_trace_export_dropped` / `rag_trace_export_failed` を `app.trace` logger に残す。

検索レスポンスと評価ケース結果には `diagnostics` も含める。これは `top_k`、`rerank_top_n`、query variant 件数、retrieval/rerank/去重/context diversity/context group expansion/context expansion/context compression/citation 件数、context compression の節約文字数、context 文字数、context window、hybrid retrieval の RRF 定数、Oracle ベクトル検索の目標精度、filter key、RAG 設定 fingerprint だけで構成し、query 本文や secret は含めない。no-results、低召回、重複 chunk による context window 消費、context window からの引用落ち、query expansion / context diversity / 同一 group context expansion / 隣接 context expansion / context compression 設定変更による品質回帰を trace id と合わせて調査するために使う。

citation の `metadata` には `section_title`、`section_path`、`section_level`、`content_kind`、`chunk_group_id`、`chunk_group_kind`、`chunk_part_index`、`chunk_part_count`、`text_sha256`、`text_chars` と、`retrieval_mode`、`vector_rank`、`keyword_rank`、`vector_score`、`keyword_score`、`rrf_score`、`query_fusion_score`、`query_variant_count`、`matched_query_variant_count`、`context_diversified`、`context_original_rank`、`context_diversified_rank`、`context_group_expanded`、`context_group_id`、`context_group_distance`、`context_expanded`、`context_anchor_chunk_id`、`context_neighbor_distance`、`context_compressed`、`context_original_chars`、`context_compressed_chars` を入れられる場合だけ含める。query 本文や OCR 原文は含めず、hybrid 検索で vector 側の召回漏れか keyword 側の語彙不一致か、また複雑文書のどの章節・親要素・query variant / context diversity / 同一 group context / 隣接 context / context compression が根拠を拾ったかを per-case に確認する。

すべての HTTP レスポンスには `X-Request-ID` を付ける。未処理例外は `ApiResponse` の 500 に統一し、クライアントには内部詳細を返さず、`app.main` logger の `unhandled_api_error` に `request_id`、HTTP method、path、例外型を記録する。

### 監査ログ

RAG 検索ごとに `app.audit` logger へ `rag_search_audit` イベントを出す。payload は `audit_event` に入り、`trace_id`、`request_id`、`outcome`、検索モード、filter key、guardrail code、retrieval/rerank/context diversity/context group expansion/context expansion/context compression/citation 件数、context compression の節約文字数、context 文字数、設定 fingerprint、引用 document id、経過時間を含む。`X-Tenant-ID` / `X-User-ID` がある場合は raw 値ではなく `tenant_id_hash` / `user_id_hash` として保存する。`AUDIT_CONTEXT_HASH_SALT` は production で `.env` から注入する。

`outcome` は `success`、`blocked`、`no_results`、`error` を使う。`no_results` は citation が 0 件で LLM 生成をスキップしたことを示す。embedding / retrieval / rerank / generation / answer guardrail の例外は `error` として記録し、API timeout は `error_stage=timeout` として記録する。監査ログには `error_stage` と `error_type` だけを残す。例外 message は query や回答本文を含む可能性があるため検索監査ログへ出さない。

取込ごとに `rag_ingestion_audit` イベントも出す。`trace_id`、`request_id`、`tenant_id_hash`、`user_id_hash`、`document_id`、`outcome`、原本 SHA-256、byte 数、document type、抽出 confidence、chunk/vector 件数、経過時間、エラー種別を含む。ユーザーが修正できる取込エラーだけ短い message を残し、未知の内部/SDK エラーでは例外 message を保存しない。

監査ログには query、回答本文、OCR 原文、tenant/user id の raw 値を出さない。query と原本は SHA-256 hash とメタデータのみ、tenant/user id は hash のみを保存し、契約番号・金額・取引先名などの機密業務データをログへ漏らさない。

`X-Tenant-ID` がある request では、document / chunk の `tenant_id_hash` と照合して一覧、詳細、重複判定、retrieval を同一 tenant に限定する。tenant header がない場合は全体を参照できる。認証ゲートウェイやアプリケーション権限層が `X-RAG-Allowed-Document-Ids` / `X-RAG-Allowed-Category-Names` を付与した場合は、その request の一覧、詳細、chunk count、retrieval も指定 scope に閉じる。scope header が存在するが有効値が 0 件の場合は deny-all とし、未指定の場合だけ制限なしとして扱う。これらの raw scope 値は監査ログへ出さない。

## ガードレール

実装: `backend/app/rag/guardrails.py`

現在の参照ポリシー:

- 長すぎる query を拒否する。
- system prompt や過去指示の無視を求める prompt injection を拒否する。
- `drop/delete/update/insert` などの SQL 変更文らしさは警告し、検索のみ実行する。
- query と answer の個人番号、口座番号、電話番号、メールアドレスらしき値は `[機微情報]` へマスクし、`sensitive_identifier_redacted` warning として返す。マスク後の query を embedding / retrieval に使い、raw 値を監査ログや metrics へ出さない。
- citation がない場合は LLM を呼び出さず、no-results 回答に短絡する。
- 回答に secret らしき文字列が含まれる場合は表示を止める。
- citation がある回答でも、回答と検索根拠の token / n-gram 重なりが少ない場合は `low_groundedness` warning を返し、監査ログにも guardrail code を残す。これは軽量なヒューリスティックであり、本番では LLM-as-judge や RAGAS 等の groundedness 評価で補強する。
- `search`、`evaluation`、`upload`、`ingest` は app 内 rate limit で保護する。bucket key は hash 済み tenant/user context を優先し、匿名時も client host を hash 化して使う。raw tenant/user id、IP、query 本文はログや metrics に出さない。本番では OCI API Gateway / Ingress / Redis などの共有 limiter と併用する。
- 認可済み document/category scope header がある場合は document 一覧、詳細、chunk count、retrieval のすべてに適用する。

本番では以下を追加する。

- 業務固有の個人情報分類器や DLP サービスによる追加マスキング。
- citation がある回答に対する LLM-as-judge / RAGAS 等の追加 groundedness 評価。
- 権限・ロール情報を hash context へ追加する場合は、raw 値を出さず環境変数で管理する salt または HMAC key で相関する。
