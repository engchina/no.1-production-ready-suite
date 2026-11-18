# 評価・観測性・ガードレール

## 評価

「評価の基準」と「品質評価」は検索 2・回答 1 の指標だけを扱います。LLM の判定は回答の正確性の 1 本だけで、ほかはコードで計算します（#4781）。

| 分類 | 指標 | 必要な評価情報・算出方法 |
|---|---|---|
| 検索 | 必要な根拠の取得率 (`required_evidence_recall`) | `required_evidence` の本文・ページ・章節・シート・行を引用と照合した取得数 / 必要数。文書だけの命中とは区別します。 |
| 検索 | 正解文書の順位（MRR） (`mrr`) | `relevant_document_ids` と引用の文書列を比較します。文書の重複を除いた最初の正解の順位 r の 1/r、未取得は 0。 |
| 回答 | 回答の正確性 (`answer_correctness`) | 標準回答または確認済みの必須要点と、質問への結論・数値・適用条件を意味上比較します。取得根拠に支持されるだけでは正解にしません。 |
| 参考 | Precision@10・NDCG@3 / @10・MAP | 引用した文書の順位列（重複を除き、正解でない文書も順位に残す）から計算します。検索候補の全体ではありません。合否と閾値には使いません（WeKnora の式。#4714）。 |
| 参考 | BLEU-1 / 2 / 4・ROUGE-1 / 2 / L | 回答の本文（組み立ての見出し・注記・出典の行を除く）と標準回答を Sudachi の短い単位に分けて比べます。語の並びの一致だけを見るので、言い換えは低く、数値の誤りでも高く出ることがあります。合否と閾値には使いません（WeKnora の式。#4714）。 |

確認・保留を期待するケースは、期待する返し方（例: どの対象かを確認してから案内する、資料に記載がないため答えられないと伝える）を標準回答に文で書き、回答の正確性で採点します。回答の自己申告の対応（`observed_outcome`）は記録だけで、採点には使いません。

各指標は測れるケースだけの平均で、`metric_case_counts` が対象件数です。前提不足は `null`（画面は「未評価」）、0 点・合格にしません。総合状態 `passed` はこれらの指標から導き、別の指標・閾値にはしません。実行エラー・評価の未完了は不合格、測定情報が不足する場合は未評価です。

### 評価の基準

| 基準 | 根拠取得 | MRR | 正確性 |
|---|---|---|---|
| 標準 | 0.9 | 0.6 | 0.9 |
| 厳格 | 1 | 0.8 | 1 |

標準は九割を基本とします。厳格は MRR 以外の完全な達成を求めます。MRR は文書順位として別に設定します。プリセットは評価資料と用途に合わせて選びます。

### API・入力・出力

- `GET/PATCH /api/settings/evaluation-suite`: 基準と各指標の閾値。
- `GET /api/evaluation/cases/template`: Excel テンプレート。列は質問・期待する根拠・標準回答・必須の回答要点・回答の過程。質問だけが必須です。確認・保留の期待は標準回答に文で書きます。今の列に無い列は読みません。
- `POST /api/evaluation/cases/import`: 保存せず検証し、期待値をプレビューします。標準回答の空欄は許し、その行の正確性は未評価です。
- 根拠は `資料A.pdf | p.12 | 章節: 初期設定 | 抜粋: 確認する内容` のように指定します。ファイル名だけなら MRR の対象で、必要な根拠の取得率は測りません。
- 同じ文書とみなす文書: 根拠のファイル名と同じ名前の有効な文書が複数あれば、どれの引用も一致とみなします（取込の警告。#2086）。評価の実行では、期待する文書と解析後の本文の hash（`extracted_text_sha256`。#5313）が同じ別の文書（形式・ファイル名だけが違う同じ本文の文書）の引用も一致とみなします（#5325）。hash の無い文書（#5313 の前に取り込み、解析から再処理していない文書）は今までどおり別の文書です。章節・ページ・抜粋の条件は、引用の chunk 自身の場所と本文で照合します。一致にした引用の文書は、ケースの結果の `equated_document_ids`（正解の文書の ID → 引用の文書の ID）に残ります。
- 回答の過程からの下書きは、利用者が確認した必須要点・条件・根拠を追加します。
- `POST /api/evaluation/jobs/run`: 非同期実行。job の取得・取消は `/api/evaluation/jobs/{id}` と `/cancel`。同期は `/api/evaluation/run`。
- `GET /api/evaluation/jobs`: 過去の評価の一覧（新しい順・offset 型。既定 25 件。#4811）。行は実行日時・検索・回答プロファイル（主と比べる方）・状態・件数・全体の判定・実行者の表示名で、結果の本体は返しません。画面は「評価の実行」の「過去の評価」から選び、`/evaluation?job=<評価 ID>` で保存済みの結果（指標・全体の判定・ケース別の結果）を表示します（モデルは呼び直しません）。
- 評価の見え方（一覧・`/api/evaluation/jobs/{id}`・`results.xlsx`・`cancel` で同じ。#4811）: 評価に使った検索・回答プロファイル（主と、あれば比べる方）を今すべて使える利用者が見られます（NL2SQL の品質評価と同じ。system_admin は全部）。プロファイルの分からない評価（記録の無い古い行・プロファイルを使わない評価）は、実行した本人と system_admin だけです。見えない評価は 404 です。他の利用者の評価は閲覧と結果の Excel のダウンロードだけで、取り消しは実行した本人と system_admin だけ（ほかは 403）。テストセットの作成で元にする評価は自分の評価だけのままです。job は使ったプロファイルと実行した利用者の UUID を記録し、質問・回答の本文は保存しないので、一覧・URL から開いた評価のケースは「N 行目」で出します（質問はアップロードした Excel と結果の Excel にあります）。
- 結果は `four_metrics_v2`、指標と対象件数、ケース詳細、分類・区分・質問の種類・段数別の内訳を返します。比較も同じ順序です。
- 結果の Excel（`POST /api/evaluation/jobs/{id}/results.xlsx`）は、アップロードした Excel がある評価ならそのファイルのケースの行の右に結果の列を足します（元のシート・セル・書式は変えません。行はケースの ID の行番号で決めます。#4212）。他の利用者の評価でも、見られる評価なら実行した利用者がアップロードした Excel に足します（#4811）。回答の本文の列は、見る利用者が読める回答の記録の分だけです（回答の記録の見え方は #304 のまま）。失敗・取り消しの評価は終わったケースまでを出し、残りの行は「未実行」にします（#4208）。Excel の無い評価は結果だけのファイルです。
- 結果の Excel は「回答に使用した根拠」列を追加します（#4852）。最終回答の保存済み引用のうち採用を確認できるものだけを、template と同じ `ファイル名 | p.3` / `シート: Sheet1 | 行: 10-12` / `章節: 見出し` の書式で、セル内 1 行 1 件にします。比較評価は主条件・比較対象を分けます。「期待する根拠」は変えません。採用を確認できない旧記録などは空欄とし、位置は保存値だけを使い、検索候補で補完したりモデルを再実行したりしません。引用の可視範囲は回答記録と同じです。
- 正確性の評価は「品質評価」で実行します。検索・チャットの回答詳細には標準回答による評価操作を置きません。
- CLI (`app.rag.evaluation_cli`) の出力も同じ指標です。業務 Agent との比較（製品横断の評価 `suite/evaluation` の `suite-evaluation`。#3240）は suite が自分の評価器で採点します。照合・順位の部品は platform の `pr_evaluation_scoring` を共有します。

### 判定のモデル（#3574）

正確性を採点する LLM の判定は、共通のモデル設定（システム設定 › モデル › 既定のモデル）の「既定の判定モデル」（`default_judge_model_id`。env は `PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_JUDGE_MODEL`）で選んだモデルで行います。

| 値 | 判定のモデル |
|---|---|
| 空（既定）「既定のテキストモデル」 | 既定のテキストモデル。どの pass・どの回答も同じモデルで判定します。 |
| `same_as_answer`「回答と同じモデル」 | 回答を作ったモデル（回答の診断の `models.llm.model_id`、無ければ実行で解決した回答のモデル）。検索・回答プロファイルの pass はプロファイルの回答のモデル、ナレッジベースの pass と回答のモデルを持たないプロファイルは既定のテキストモデルです。 |
| 登録モデルの ID | そのモデル。どの pass・どの回答も同じモデルで判定します。 |

- 自分の回答を判定すると点が高くなりやすいため、モデルを比べる評価では、比べるモデルと別のモデルに固定することを勧めます。
- 判定の LLM は、判定のモデルの接続・入力の上限・推論の量で呼びます（回答のモデルの切り替えと同じ `build_engine_settings`。解決は platform の `enterprise_ai_judge_model_id` だけ）。
- 判定に使ったモデルは、ケースの結果の `answer_evaluation.judge_model_id`（判定を完了できなかったケースにも残す）と、判定の診断の `judge_diagnostics.judge_model_id`（回答の記録にも保存）に残ります。

### 判定の呼び出しの形（#5037・#5262・#5302）

回答の評価器（`rag_engine.evaluation.answer_eval.evaluate_answer_payload`、`rubric_version=34`）は、1件の評価でモデルを1回呼びます。RAGAS の FactualCorrectness（回答を主張に分け、参照と照らす）を参考に、正確性と網羅を分けて測ります。

- **入力**: 質問・標準回答の文（`S1`…）・網羅の要点（`P1`…）・回答の本文の段落（`A1`…。複数往復は対話の全体を通し番号）を番号つきで渡します。引用の表示行は本文から除きます（#5039）。取得した根拠は渡しません（資料不足と述べたことの正しさ・検索の取れ具合は判定しません。検索は `required_evidence_recall` で見ます）。
- **要点**: 必須の回答要点と確かめる条件（`required_answer_points` + `required_conditions`）。どちらも無いケースは標準回答の文を要点にします。必須の回答要点・条件は標準回答に結合せず、正確性は標準回答だけと比べます。
- **出力**（構造化出力・Pydantic で検証）: 本文の主張ごとの印（`supported` 標準回答と合う / `contradicted` 標準回答と同時には成り立たない / `not_in_standard` 標準回答に記載なし）と、照らした標準回答の文の番号、要点ごとの網羅（`covered`）。原文はモデルに写させず番号で指させ、コードが入力に無い番号・要点の過不足を評価のエラー（`InvalidJudgeReference`）にします。
- **印の区別**（指示の Step 3 に書き、応答 schema の説明には重ねません）: `contradicted` は本文の主張と標準回答の文が述べる事実が同時には真になれないときだけです。本文が「資料では確認できない・確定できない」と述べ、標準回答がその点の答えや進め方を示すときは `not_in_standard`（矛盾にしない）。標準回答も同じ点を「確定できない・原文に記載が無い」と述べるときは `supported`。本文がすべて留保（`supported` が無い）なら正確性は 0 で、理由は「標準回答と合う主張がありません」です（#5398。標準回答が能力の説明だけで具体的な答えが無いのか、回答が質問に答えていないのかは印から区別できないので、理由はどちらとも断定しません。画面と Excel の説明で、標準回答には結論・数値・手順を書くよう求めます）。
- **点**（コードが印から計算します）: 正確性は矛盾する主張が 1 つでもあれば 0、無ければ 1。「標準回答に記載なし」は誤りにしません。標準回答と合う主張が 1 つも無い回答は 0（要点を覆っても同じ。正確性と網羅は独立の指標）。網羅は覆った要点の数 / 要点の数。
- **理由**（コードが印と原文から作ります）: 矛盾した主張ごとに「回答の「…」と標準回答の「…」が食い違う。」。矛盾が無ければ「標準回答と矛盾する主張はありません。」。
- **合否**: 1 問の合否（`answer_evaluation.passed`）は正確性で決めます（閾値 standard 0.9 / strict 1.0 で、0/1 の正確性は 1 のときだけ合格）。網羅は参考の指標（`answer_coverage`）で、全体の平均だけを出し、合否と閾値には使いません。全体の合否は今の閾値（正確性の通過の率）で決めます。
- **退避した問**（#5320）: 回答文を生成できず参考情報へ退避した問（回答の記録の `generation_failure`。失敗理由 `answer_generation_fallback`。#4104）は、正確性に関わらず不合格にします（参考情報の原文は標準回答と合う主張になり得るため）。判定は回答の記録の構造の印だけで行い、本文の語では判定しません。正確性・網羅の値は判定のまま残し、平均にも入れます。退避した問が 1 つでもあれば、全体も合格にしません（判定を終えられなかったケースと同じ扱い）。判定の版（`rubric_version`）は変えず、保存済みの結果の合否は書き換えません。
- **画面と Excel**: ケースの行で、この問の合否と、開いて見る判定の理由・網羅の要点を出します。結果の Excel には「回答の網羅（参考）」「この問の合否」「判定の理由」の列を足します。退避で不合格にした問は、失敗理由の文（「回答文を生成できず参考情報を返した」）を判定の理由の先頭に出します。
- 確認・保留の期待は標準回答の文として比べます（#5068）。生成モデルの自己申告 `outcome` は採点に使いません。
- 標準回答が無ければ呼び出さず（`no_standard_answer`）、測れない値を0や1で補いません（必須の回答要点だけのケースも判定しません）。
- プロンプトは既存の `evaluation.correctness` で編集できます。出力の形が変わったので、画面で上書きした指示を保存している環境は、既定に戻すか、主張に分けて印を付ける手順に書き直してください（既存のカスタム文を自動で上書きしません）。rubric 34 から印の定義と区別は指示の Step 3 にあり、応答 schema の説明には無いため、上書きした指示はこの Step 3 の定義を含めてください。
- 入力予算、Pydanticによる型検証、技術失敗の記録を維持します。技術的な再試行は共有adapterの枠内だけで、採点結果を理由とした回答の再検索・再生成は行いません。
- 指標の版は `four_metrics_v2`。保存済みの結果に残る削除した指標（応答の判断の正しさなど）は読み捨て、再計算しません。

### 主張ごとの判定の前の結果（#5262）

`answer_evaluation.rubric_version` の無い結果は、主張ごとの判定の前（rubric 32 以前）の結果です。書き換えず、画面と Excel では「旧方式」と表示し、網羅を出しません（理由は保存した文のまま）。再評価すると新しい方式になります。

### 保存済みの古い結果（#4781）

以前の評価の結果には、網羅（`requirement_coverage`）・裏付け（`claim_support_rate`）・根拠の網羅率（`evidence_coverage`）の値と、それに基づく合否・失敗の理由が残ります。DB の値は消さず、画面と結果の Excel は正確性とコードで計算する指標だけを出します。古い結果の合否は保存した値をそのまま出し、計算し直しません。判定の校正（人手の判定との一致を測る機能。#3418・#3578）は #4781 で削除しました。

実行時の安全機構、引用の実在と形式の確認、引用リンクは評価指標の整理によって削除しません。

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

production（`PLATFORM_AUTH_MODE=authenticated`）では client の `X-Tenant-ID` と `X-RAG-Allowed-*` header を使わず、tenant なし（単一 tenant）で動かし、対象範囲はログイン中の利用者（MCP ではサービストークンの利用者）から決める（#214 / #225）。local（`PLATFORM_AUTH_MODE=local`）だけは開発・検証のため、`X-Tenant-ID` がある request で document / chunk の `tenant_id_hash` と照合して一覧、詳細、重複判定、retrieval を同一 tenant に限定する（tenant header がない場合は全体を参照できる）。同じく local で `X-RAG-Allowed-Document-Ids` / `X-RAG-Allowed-Category-Names` を付与した場合は、その request の一覧、詳細、chunk count、retrieval も指定 scope に閉じる。scope header が存在するが有効値が 0 件の場合は deny-all とし、未指定の場合だけ制限なしとして扱う。これらの raw scope 値は監査ログへ出さない。

## ガードレール

「セキュリティ設定 > 安全チェック」（権限管理の下）で入力と出力の検査を個別に選びます。
3 製品で共通の全体の設定（`platform/.env`。#5061）であり、検索・回答プロファイルでは上書きしません。
`PLATFORM_GUARDRAILS_INPUT_ENABLED` と `PLATFORM_GUARDRAILS_OUTPUT_ENABLED` の既定は両方 `false` です。
判定・OCI の呼出・設定の API は platform の `pr_system_settings.guardrails` にあり、RAG はチャットの受付時の検査と
会話の履歴の再検査を持ちます。

選択した側だけ、質問または回答のテキストを OCI Generative AI の `ApplyGuardrails` API に送り、
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
uv run python -m app.rag.guardrail_settings_migration --env-file .env --platform-env-file ../../platform/.env
```

再配備（`init_script.sh`）でもサービスの起動前に同じ移行を行います。`backend/.env` の
`RAG_GUARDRAILS_INPUT_ENABLED` / `RAG_GUARDRAILS_OUTPUT_ENABLED` を `platform/.env` の
`PLATFORM_GUARDRAILS_*` へ移し、`RAG_GUARDRAIL` で始まる旧い行を消します。`platform/.env` に値が
あればそちらを残します。実行時に旧名は読みません。
独立した旧ステージのサービス基盤は残しますが、RAG の安全チェックからは呼びません。

冒頭要約は、回答が使う既知の本文見出しより前の段落を対象にします。出典のある本文の段落 ID と、その原文の ID を `judge_diagnostics.summary_citation_candidates` に残します。これは支持の確定ではなく意味判定へ渡す候補です。要約の主張そのものを原文で判断し、本文を事実の出典にしません。通常の本文の無引用段落や、警告・除外の節へこの対応を広げません。候補が入力予算に収まらないときは要約を未評価にし、合格にしません。
