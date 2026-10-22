# 評価・観測性・ガードレール

## 評価

「評価の基準」と「品質評価」は六指標だけを扱います。新システムのため旧結果の互換・移行は設けません。

| 分類 | 指標 | 必要な評価情報・算出方法 |
|---|---|---|
| 検索 | 必要な根拠の取得率 (`required_evidence_recall`) | `required_evidence` の本文・ページ・章節・シート・行を引用と照合した取得数 / 必要数。文書だけの命中とは区別します。 |
| 検索 | 正解文書の順位（MRR） (`mrr`) | `relevant_document_ids` と引用の文書列を比較します。文書の重複を除いた最初の正解の順位 r の 1/r、未取得は 0。 |
| 回答 | 回答の正確性 (`answer_correctness`) | 標準回答または確認済みの必須要点と、質問への結論・数値・適用条件を意味上比較します。取得根拠に支持されるだけでは正解にしません。 |
| 回答 | 回答の網羅性 (`requirement_coverage`) | 標準回答・`required_answer_points`・`required_conditions` の必須項目への意味上の対応率。説明済み 1・部分的 0.5・不足 0。 |
| 回答 | 根拠への忠実性 (`claim_support_rate`) | 根拠が支持する主張 / 監査した実質的な主張。標準回答は不要。引用を検証できない・監査しきれない主張があれば未評価。 |
| 応答の判断 | 回答・確認・保留の判断の正しさ (`response_decision_accuracy`) | `expected_response_decision` に `answer` / `clarify` / `hold` を明示します。標準回答は不要。省略時は推定しません。 |

`expected_outcomes` に明示した回答結果も三分類へ集約します。`answered`・`conditional` は回答、`needs_clarification` は確認、それ以外は保留です。複数往復は各往復の明示的な期待に合っているか確認します。

各指標は測れるケースだけの平均で、`metric_case_counts` が対象件数です。前提不足は `null`（画面は「未評価」）、0 点・合格にしません。判断は期待する回答・確認・保留ごとの内訳も表示します。総合状態 `passed` は六指標から導き、別の指標・閾値にはしません。実行エラー・評価の未完了は不合格、測定情報が不足する場合は未評価です。

### 評価の基準

| 基準 | 根拠取得 | MRR | 正確性 | 網羅性 | 忠実性 | 応答判断 |
|---|---|---|---|---|---|---|
| 標準 | 0.9 | 0.6 | 0.9 | 0.9 | 0.95 | 0.9 |
| 厳格 | 1 | 0.8 | 1 | 1 | 1 | 1 |

標準は九割を基本とし、捏造・矛盾を測る忠実性はより高い支持率を求めます。厳格は MRR 以外の完全な達成を求めます。MRR は文書順位として別に設定します。プリセットは評価資料と用途に合わせて選びます。

### API・入力・出力

- `GET/PATCH /api/settings/evaluation-suite`: 基準と六指標の閾値。
- `GET /api/evaluation/cases/template`: Excel テンプレート。列は質問・期待する根拠・標準回答・必須の回答要点・期待する応答の判断・回答の過程。質問だけが必須です。判断には回答・確認・保留を指定します。
- `POST /api/evaluation/cases/import`: 保存せず検証し、期待値をプレビューします。空の標準回答から保留の期待は推定しません。
- 根拠は `資料A.pdf | p.12 | 章節: 初期設定 | 抜粋: 確認する内容` のように指定します。ファイル名だけなら MRR の対象で、必要な根拠の取得率は測りません。
- 回答の過程からの下書きは、利用者が確認した必須要点・条件・期待する判断・根拠を追加します。
- `POST /api/evaluation/jobs/run`: 非同期実行。job の取得・取消は `/api/evaluation/jobs/{id}` と `/cancel`。同期は `/api/evaluation/run`。
- 結果は `six_metrics_v1`、六指標と対象件数、ケース詳細、分類・区分・質問の種類・段数・期待する判断別の内訳を返します。比較も同じ順序です。
- 単件回答の評価は正確性・網羅性・忠実性を扱い、標準回答なしなら忠実性だけが評価対象になります。
- CLI (`app.rag.evaluation_cli` / `app.rag.agent_evaluation_cli`) の出力・比較も同じ六指標です。

実行時の安全機構、回答生成の監査、引用リンクは評価指標の整理によって削除しません。

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

`rag_search_stage_duration_seconds` は `mode`、`stage`、`outcome` label を持つ。`stage` は `history_rewrite`、`answer`、`retrieval`（検索だけの経路）、`outcome` は `success`、`error`、`cancelled` を使う。回答フローの中のどの工程（検索・Rerank・根拠確認・回答の生成など）が遅いかは、`diagnostics.answer.execution_steps`（負荷試験 CLI `search_load_cli` が工程ごとの p95 を集計する）で切り分ける。

`rag_ingestion_stage_duration_seconds` は `stage`、`outcome` label を持つ。`stage` は `vlm_extraction`、`chunking`、`embedding`、`indexing`、`outcome` は `success`、`error`、`cancelled` を使い、OCI Enterprise AI の OCR/構造化、chunking、OCI Generative AI embedding、Oracle AI Database indexing のどこで遅延・失敗しているかを切り分ける。

`rag_evaluation_cases_total{mode,status}` と `rag_evaluation_case_duration_seconds{mode,status}` は golden set の case 単位で記録する。`status` は `success` / `error` に固定し、case id や query 本文は label に入れない。

`rag_guardrail_findings_total{surface,code,severity,action}` は query、answer の guardrail finding を数える。`surface` は `query` / `answer`、`action` は `blocked` / `warning` に固定し、query 本文、回答本文、OCR 原文、tenant/user id は label に入れない。

`rag_rate_limit_decisions_total{scope,outcome}` は高コスト API の rate limit 判定を数える。`scope` は `search` / `evaluation` / `upload` / `ingest`、`outcome` は `allowed` / `blocked` に固定し、tenant/user id、IP、query 本文は label に入れない。429 応答には `Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset-After` を返す。

RAG 検索レスポンスには `trace_id` を含める。OCI Enterprise AI、OpenTelemetry、Langfuse を接続する場合は、この `trace_id` を親 ID として OCR、取込の各工程と、回答の `history_rewrite`・`answer`・`retrieval` の各 span に渡す。

検索・取込 pipeline は `app.trace` logger へ `rag_trace_span` イベントも出す。payload は `trace_event` に入り、`trace_id`、`span_name`、`outcome`、`duration_ms`、低 cardinality の attributes、`error_type` だけを含む。検索 stage は `history_rewrite`（チャットの会話履歴による質問の書き換え）、`answer`（回答フロー）、検索だけの経路の `retrieval`、取込 stage は `vlm_extraction`、`chunking`、`embedding`、`indexing` を使う。回答フローの中の工程（質問の理解・文書検索・Rerank・根拠確認など）の時間は、span ではなく `diagnostics.answer.execution_steps` と回答の記録に残る（旧 standard の `embedding`・`rerank`・`context_*`・`generation` などの stage は #595 で削除した）。query 本文、context 本文、OCR 原文、prompt、例外 message、tenant/user id の raw 値は含めない。この構造化ログは OpenTelemetry span や Langfuse trace へ橋渡しするための境界であり、Prometheus の aggregate metrics では追えない単一 request の遅延・失敗箇所を調査するために使う。`RAG_TRACE_EXPORT_HTTP_ENDPOINT` を設定すると、同じ脱機密化済み event を非同期 HTTP JSON で collector / gateway へ送信する。queue が満杯または送信失敗しても request は失敗させず、`rag_trace_export_dropped` / `rag_trace_export_failed` を `app.trace` logger に残す。

検索レスポンスと評価ケース結果には `diagnostics` も含める。これは `retrieval_strategy`（常に `hybrid`）、`retrieval_strategy_adapter`（`grounded` / `retrieval_only` / `blocked`）、`answer`（回答フローの診断。工程ごとの実行記録 `execution_steps`・根拠の構成・信頼度など）、安全チェックの policy / backend / 縮退、filter key、ナレッジベースの件数、適用した KB / 検索・回答プロファイルの設定、RAG 設定 fingerprint だけで構成し、query 本文や secret は含めない。低召回や設定変更による品質回帰を trace id と合わせて調査するために使う。旧 standard の診断（`top_k`・`rerank_top_n`・query variant 件数・検索 / rerank / 去重 / context の加工の件数・context window・`stream_stage_timings` など）と、検索 UI の「適応展開」「依存昇格」の件数の表示は #595 で削除した。

citation の `metadata` には `section_title`、`section_path`、`section_level`、`content_kind`、`chunk_group_id`、`chunk_group_kind`、`chunk_part_index`、`chunk_part_count`、`text_sha256`、`text_chars` と、`retrieval_mode`、`vector_rank`、`keyword_rank`、`vector_score`、`keyword_score`、`rrf_score` を入れられる場合だけ含める。query 本文や OCR 原文は含めず、hybrid 検索で vector 側の召回漏れか keyword 側の語彙不一致か、また複雑文書のどの章節・親要素が根拠を拾ったかを per-case に確認する。

すべての HTTP レスポンスには `X-Request-ID` を付ける。未処理例外は `ApiResponse` の 500 に統一し、クライアントには内部詳細を返さず、`app.main` logger の `unhandled_api_error` に `request_id`、HTTP method、path、例外型を記録する。

### 監査ログ

RAG 検索ごとに `app.audit` logger へ `rag_search_audit` イベントを出す。payload は `audit_event` に入り、`trace_id`、`request_id`、`outcome`、filter key、guardrail code、検索件数・citation 件数、設定 fingerprint、引用 document id、経過時間を含む（検索モードは常に `hybrid`。旧 standard の検索・context の内訳の列は #595 以降は既定値で、既存の監査の行を変えないため列は残す。#596）。`X-Tenant-ID` / `X-User-ID` がある場合は raw 値ではなく `tenant_id_hash` / `user_id_hash` として保存する。`RAG_AUDIT_CONTEXT_HASH_SALT` は production で `.env` から注入する。

`outcome` は `success`、`blocked`、`error` を使う（`no_results` は旧 standard で citation が 0 件のとき LLM 生成をスキップしたことを示していた。#595 以降は記録しないが、列の値として残る）。回答フロー（検索・rerank・生成を含む）と answer guardrail の例外は `error` として記録し、API timeout は `error_stage=timeout` として記録する。監査ログには `error_stage` と `error_type` だけを残す。例外 message は query や回答本文を含む可能性があるため検索監査ログへ出さない。

取込ごとに `rag_ingestion_audit` イベントも出す。`trace_id`、`request_id`、`tenant_id_hash`、`user_id_hash`、`document_id`、`outcome`、原本 SHA-256、byte 数、document type、抽出 confidence、chunk/vector 件数、経過時間、エラー種別を含む。ユーザーが修正できる取込エラーだけ短い message を残し、未知の内部/SDK エラーでは例外 message を保存しない。

監査ログには query、回答本文、OCR 原文、tenant/user id の raw 値を出さない。query と原本は SHA-256 hash とメタデータのみ、tenant/user id は hash のみを保存し、契約番号・金額・取引先名などの機密業務データをログへ漏らさない。

production（`RAG_AUTH_MODE=production`）では client の `X-Tenant-ID` と `X-RAG-Allowed-*` header を使わず、tenant なし（単一 tenant）で動かし、対象範囲はログイン中の利用者（MCP ではサービストークンの利用者）から決める（#214 / #225）。local（`RAG_AUTH_MODE=local`）だけは開発・検証のため、`X-Tenant-ID` がある request で document / chunk の `tenant_id_hash` と照合して一覧、詳細、重複判定、retrieval を同一 tenant に限定する（tenant header がない場合は全体を参照できる）。同じく local で `X-RAG-Allowed-Document-Ids` / `X-RAG-Allowed-Category-Names` を付与した場合は、その request の一覧、詳細、chunk count、retrieval も指定 scope に閉じる。scope header が存在するが有効値が 0 件の場合は deny-all とし、未指定の場合だけ制限なしとして扱う。これらの raw scope 値は監査ログへ出さない。

## ガードレール

「取込・検索・回答設定 > 安全チェック」で OCI Guardrails の有効・無効を選びます。
全体の設定であり、検索・回答プロファイルでは上書きしません。
`RAG_GUARDRAILS_ENABLED` の既定は `false` です。

有効なときだけ、質問と回答のテキストを OCI Generative AI の `ApplyGuardrails` API に送り、
PII・プロンプト攻撃・不適切な内容を検出します。API は検出結果を返し、アプリが PII の位置を
マスクし、攻撃・不適切な内容を検出したときは処理・回答の表示を止めます。
検査を完了できないときも停止します。無効時は OCI を呼ばず、ローカル検査も行いません。

ローカルの機微情報検査・攻撃パターン検査・秘密文字列検査と、根拠との一致数・一致率による
安全方針は使いません。引用の整合性の確認や評価専用の指標は、別の目的の処理です。

OCI 認証と compartment はシステム設定で設定します。検出した PII の原値は診断へ残さず、
位置・長さ・種類だけを扱います。現在の実装はテキストを検査し、画像は送りません。

[Oracle 公式: Guardrails for OCI Generative AI](https://docs.oracle.com/en-us/iaas/Content/generative-ai/guardrails.htm)

### 既存環境の更新

backend の更新前に設定ファイルを安全な場所へバックアップし、更新と合わせて実行します。

```bash
cd rag/backend
uv run python -m app.rag.guardrail_settings_migration --env-file .env
```

旧 OCI 選択は有効、旧 Local 選択は無効へ移します。すでに新しいスイッチを明示した場合は
その値を保持します。旧方針・ローカル検査の環境変数は削除し、実行時に互換読み込みしません。
保存済みプロファイルの旧 `guardrail_policy` は読み捨て、次に保存すると除外します。
独立した旧ステージのサービス基盤は残しますが、RAG の安全チェックからは呼びません。
