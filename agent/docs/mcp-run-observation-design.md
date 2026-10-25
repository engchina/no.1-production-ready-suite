# Agent の公開 MCP の出力の拡張と実行の取消のツールの設計（#3240 の段 2）

> 状態: 設計（未実装）。決めてほしいこと（§11）の答えを受けて、§10 の PR の分割で実装する。実装の PR は、決まった規則を
> 正本の文書（[docs/agents/mcp.md](./agents/mcp.md)・[platform/docs/mcp-api-keys.md](../../platform/docs/mcp-api-keys.md)・
> [agent-control-plane-design.md](./agent-control-plane-design.md) §4・[platform/docs/backend-standard.md](../../platform/docs/backend-standard.md)
> 「製品間の連携（MCP とサービストークン）」）へ移し、このファイルは最後の PR で消す（同じ規則を 2 か所に書かない）。
> 内容はすべて技術の言葉と合成の例で書く（業務の内容を書かない。#2019）。

## 1. 目的と範囲

#3240 は製品をまたぐ評価を `suite/evaluation/` に移し、公開の MCP の契約だけで RAG と Agent を比べる。段 1（#3283）は今の比較評価の
CLI（`rag/backend/app/rag/agent_evaluation_cli.py`）の採点・集計を合成の fixture で固定し、公開の Agent MCP に無い観測を
`rag/backend/tests/fixtures/agent_evaluation_baseline/agent_internal_dependencies.json` に一覧にした。この文書はその一覧を入力に、
公開の契約に足す最小の項目とツールを決める。

**欠けているのは「外部のクライアント → Agent」の MCP（`platform/contracts/mcp/agent-tools.json`）の出力である。** 「Agent → RAG /
NL2SQL」の MCP は、評価に要る `document_id`・`chunk_id`・構造の場所・`outcome` をすでに公開の契約で返している（§2.2）。今の CLI が
それを使えたのは、Agent の画面の API（`GET /api/runs/{id}`）から Run の内部の記録（`steps[].tool_result.output`・成果物）を読んで
いたからで、公開の Agent MCP の `sources[]` は title・location・excerpt だけを返す。

範囲:

- Agent の `agent_chat` / `agent_get_run` / `agent_answer_confirmation` の出力（`RunOutput`）に足す項目。
- 新しいツール `agent_cancel_run`（実行の取消）と `agent_get_run_observation`（評価・監査の観測）。
- RAG / NL2SQL の MCP の出力の確認と、3 製品でそろえる最小の変更（`schema_version`）。

範囲外: suite の adapter・Evaluator の実装（段 3）、旧形式の処理の移設（段 4）、業務 Agent の作成・公開の MCP のツール（§11 の Q4 で
足さないことを勧める）、Skill の不変条件（#3247）、NL2SQL の評価。

## 2. 今の出力（項目の単位）

### 2.1 Agent（`production-ready-agent`。`features/agent/mcp_server.py`）

| ツール | 出力の項目 | 評価に関わる性質 |
|---|---|---|
| `agent_chat` / `agent_get_run` / `agent_answer_confirmation` | `RunOutput`（`schema_version`=2）: `run_id`・`agent_id`・`thread_id`・`status`（queued / running / waiting_confirmation / waiting_approval / completed / failed / cancelled）・`in_progress`・`answer`・`confirmation`（`id`・`source`・`items[]`・`round`・`max_rounds`・`can_skip`）・`sources[]`・`passage_marks[]`・`removed_passages[]`・`answer_warnings[]`・`tools_used[]`・`skipped_approval_tools[]`・`pending_approvals`・`message`・`notice` | 下の行 |
| 同上の `sources[]`（`SourceOutput`） | `title`（`file_name`、無ければ `document_id`）・`location`（`_location` が locator から作る表示の文字列。例 `シート · p.3-4 · 章 > 節`）・`excerpt`（400 文字で切る） | 文書と chunk の ID が無い。場所は文字列だけ（行・セル範囲・要素の定位子は落ちる）。回答で引用した根拠（`cited`）を選び、1 件も無ければ AI に渡した根拠の全件を出す（どちらかを区別する項目が無い）。最大 10 件。`agent_get_run` は見る人の今の権限で確かめ直し（#3252）、見られない根拠を除くが、除いた件数を返さない |
| `agent_list` | `agents[]`（`id`・`name`・`description`・`skills[]`） | `schema_version` が無い |
| `agent_skill_export` | `name`・`files[]` | `schema_version` が無い |
| （無い） | 実行の取消・業務 Agent の作成・公開のツールは無い | 時間切れの Run を止められない |

成果物 `answer` の `content.outcome`（`{schema_version, value, basis, rag_outcome, signals}`。`answer_outcome.py`・
`tool_grounding.py`）、成果物 `answer_validation` の `status`（completed / skipped / unvalidated）、`usage`（`RunUsage`）、
`metadata.agent_version`、`trace_id` は Run に保存しているが、MCP には出していない。

### 2.2 RAG（`production-ready-rag`。`rag/backend/app/mcp/tools.py`。出力の `schema_version`=31）

| ツール | 評価に要る項目（すでに公開） |
|---|---|
| `rag_search` | `outcome`（answered / conditional / needs_clarification / needs_environment_data / needs_human / insufficient_evidence）・`insufficient_reason`・`clarifications`・`evidence[]`・`trace_id` |
| `rag_retrieve_evidence` | `evidence[]`・`clarifications`・`trace_id` |
| `evidence[]`（`RagEvidence`） | `evidence_id`・`document_id`・`chunk_id`（必須）・`file_name`・`chunk_set_id`・`evidence_type`・`origin`・`superseded`・`retrieval_rank`・`used_in_answer`・`answer_use`・`excerpt`（最大 1000 文字）・`truncated`・`text_length`・`section`・`locator` |
| `locator`（`EvidenceLocator`） | `section_path`・`page_start` / `page_end`・`page_label_start` / `page_label_end`・`sheet_name`・`row_start` / `row_end`・`cell_range`・`bbox` / `bbox_unit`・`element_locator` |
| `rag_read_source` | `document_id`・`chunk_id`・`locator`・`text` |
| `rag_read_document` / `rag_read_section` | `document_id`・`text`・`chunks[]`（`chunk_id`・`start` / `end`・`element_locator`・`section_path`・`page_start` / `page_end`・`sheet_name`・`row_start` / `row_end`・`cell_range`） |

RAG の MCP は評価に要る観測をすべて返す。段 2 で RAG の出力の形は変えない（§4.5）。

### 2.3 NL2SQL（`production-ready-nl2sql`。`nl2sql/backend/app/features/mcp/tools.py`）

| ツール | 出力の項目 |
|---|---|
| `nl2sql_query` / `nl2sql_get_job` | `job_id`・`status`（pending / running / done / error / needs_clarification / unanswerable）・`profile_id`・`generated_sql`・`executable_sql`・`explanation`・`is_safe`・`safety_issues`・`safety_check`・`columns`・`rows`・`returned_count`・`total`・`has_more`・`truncated`・`history_id`・`error_code`・`error_message`・`requirement`・`auto_selected_profile`・`notice` |
| `nl2sql_cancel_requirement` | `requirement_session_id`・`status`（cancelled / closed） |
| `nl2sql_list_profiles` / `nl2sql_recommend_profile` | 一覧・推薦 |

どの出力にも `schema_version` が無い。ジョブの取消は画面の REST（`POST /nl2sql/jobs/{id}/cancel`）にだけあり、MCP には無い。
`nl2sql_get_job` はサービストークンの claim `profile_ids` を当てない（`nl2sql/docs/security-rbac-deepsec.md`「業務 Agent のデータの範囲」に
「変えない」と明記した決定。§11 の Q6）。

## 3. 評価の必要ごとの不足

段 1 の `agent_internal_dependencies.json` の項目ごとに、今の CLI が読む所・公開の Agent MCP・この設計の答えを並べる。

| 依存（段 1 の id） | 使う採点・記録 | 今の CLI が読む所 | 公開の Agent MCP | この設計 |
|---|---|---|---|---|
| `citation-identity` | MRR・必要な根拠の取得率・引用の文書・`retrieval_miss` / `evidence_miss`・重複の除去 | `steps[].tool_result.output` の `evidence[]` と読み取りのツールの `document_id` / `chunk_id` | 無い | `agent_get_run_observation` の `evidence[].document_id` / `chunk_id`。`sources[]` にも ID を足す |
| `structured-locator` | 必要な根拠の取得率（頁・章節・シート・行の条件） | 同上の `locator`・`chunks[]` の場所の項目 | 文字列だけ | `evidence[].locator`（RAG の `EvidenceLocator` と同じ名前の部分集合） |
| `answer-outcome` | 対応・出所・判断の正しさ・拒答・検索の指標の対象外の判定 | 成果物 `answer` の `outcome.value` | 無い | `RunOutput.outcome`（RAG の `outcome` と同じ語彙） |
| `rag-search-signals` | 旧形式の対応の出所・診断 | `rag_search` の `outcome`・`clarifications`・`insufficient_reason` | 無い | 足さない。MCP で作る新しい Run は完了すれば `outcome` を持つ（`complete_builtin_run` のすべての経路が渡す。決める処理が例外で失敗したときだけ null で、ログ `builtin_runtime_answer_outcome_failed`。suite は「評価不能」にする）。保存した旧形式の観測の推定は段 4 で suite の adapter に移す。診断の 2 項目は採点に使わない（段 1 の witness も `observed_outcome`・`outcome_source` だけが変わる） |
| `validation-status` | Run の記録の `validation_status` | 成果物 `answer_validation.status` | 一部 | `agent_get_run_observation.validation_status` |
| `model-usage` | Run の記録の `model_requests` | `usage.requests` | 無い | `agent_get_run_observation.usage` |
| `tool-call-arguments` | `superseded_searches`・旧版を検索したケース | `steps[].tool_call.arguments.include_superseded` | 無い | `tool_calls[].include_superseded`（真偽だけ。引数の本体は出さない） |
| `retrieved-not-only-used` | CLI は返った根拠をすべて引用に数える | ツールの出力の全件 | `sources[]` は使った根拠だけ（最大 10） | `evidence[]` は AI に渡した根拠と読んだ chunk の全件（ページング）。`cited` で引用を区別 |
| `tool-call-counts` | Run の記録の `tool_calls` | `steps[]` | 名前だけ | `tool_calls[]`（呼び出しごと。失敗・承認で止めた呼び出しを含む） |
| `run-lifecycle-operations` | 時間切れの取消・`--create-agent` | 画面の REST（`/cancel`・`/api/agents`・`/publish`） | 無い | 取消は `agent_cancel_run`。作成・公開は足さない（§11 の Q4） |

公開の MCP にしても変わる観測（意図した差。段 3 で採点の version と差を記録する）:

- **承認の要るツール**: MCP から作った Run は承認の要るツールを呼ばず（`mcp.approval_required`）、`waiting_approval` にならない。
  画面の API で作った Run の `waiting_approval`（段 1 の `waiting-approval-run`。ケースの失敗）の代わりに、`skipped_approval_tools` が
  空でない完了の Run になる（§11 の Q7）。
- **根拠の本文の長さ**: CLI は読み取りのツールの本文を chunk の範囲で切り出して採点に使う（上限なし）。`evidence[].excerpt` は RAG の
  `excerpt` と同じ 1000 文字が上限で、`truncated` を返す。本文の条件（`EvaluationEvidence.text`）が 1000 文字より後ろにある根拠は
  取れなくなりうる（§11 の Q8）。

## 4. 提案する契約

### 4.1 共通の方針

- **名前と語彙は RAG の公開の契約にそろえる。** 根拠の項目は `RagEvidence` / `EvidenceLocator` と同じ名前・型の部分集合にし、
  `outcome` は `rag_search.outcome` と同じ enum にする。Agent が別の名前を作らない（suite の adapter が製品ごとの対応表を持たない）。
  契約の一致は Agent のテストが `platform/contracts/mcp/rag-tools.json` と比べて確かめる（§9）。
- **出力の形は `outputSchema` で示し、`structuredContent` で返す**（MCP 2025-06-18 の structured tool output。今の
  `pr_backend_core.mcp` と同じく text にも同じ JSON を入れる）。形が変われば `schema_version` を上げる。3 製品のすべての MCP の
  出力に `schema_version` を持たせる（RAG は持つ。Agent の `AgentListOutput`・`SkillExportOutput` と NL2SQL の出力に足す）。
- **null は「無い・分からない」、空の一覧は「調べて 0 件」。** 状態で意味が無い項目（実行中の `outcome` など）は null。
- **内部の判断の道筋は出さない。** `outcome.basis`・`signals`・`rag_outcome`、ツールの引数の本体、スコア（`score`・`rerank_score`）、
  エラーの文は出さない。採点に要らず、出すと呼び出し側が内部の算法に依存する（#3240 の「依存してはいけないもの」）。
- **後方互換は持たない**（未リリース。`RunOutput` の `schema_version` を 3 に上げ、旧い形は残さない）。
- **観測の名前は OpenTelemetry の GenAI の semantic conventions に対応させる**（JSON の名前は製品の MCP の snake_case のまま）:
  `tool_calls[].name` ↔ `gen_ai.tool.name`、`tool_calls[].call_id` ↔ `gen_ai.tool.call.id`、`tool_calls[].error_code` ↔ `error.type`、
  `usage.input_tokens` / `output_tokens` ↔ `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`、`usage.model` ↔
  `gen_ai.request.model`、`thread_id` ↔ `gen_ai.conversation.id`、`agent_id` ↔ `gen_ai.agent.id`、`trace_id` ↔ W3C Trace Context の
  trace id（Run の `agent.execute` の span と同じ値）。

### 4.2 `agent_chat` / `agent_get_run` / `agent_answer_confirmation`（`RunOutput` の `schema_version` 3）

外部の LLM のクライアントが毎回読む出力なので、足すのは小さな項目だけにする。根拠の全件・構造の場所・ツールの経過は §4.4 に分ける。

| 項目 | 型 | null / 既定 | 意味 |
|---|---|---|---|
| `outcome` | `"answered" \| "conditional" \| "needs_clarification" \| "needs_environment_data" \| "needs_human" \| "insufficient_evidence" \| null` | `status` が completed でなければ null | 回答の対応（成果物 `answer` の `outcome.value`）。完了で値が無い Run は null（呼び出し側は「評価不能」として扱う） |
| `withheld_evidence_count` | `int`（≥0） | 0 | 見る人が今見られないため `sources[]` に出さなかった根拠の数（画面の `withheld_evidence_count` と同じ。#3252） |
| `sources[].document_id` | `string \| null` | null | RAG の文書の ID。会話の添付の根拠（`attachment:`）は null（MCP に添付を読むツールが無い） |
| `sources[].chunk_id` | `string \| null` | null | RAG の chunk の ID（`rag_read_source` にそのまま渡せる）。添付は null |
| `sources[].cited` | `bool` | false | 回答で引用した根拠か。引用が 1 件も無い回答は今どおり AI に渡した根拠を出し、すべて false |

変えない項目: `title`・`location`（表示の文字列）・`excerpt`（400 文字）・最大 10 件・その他の項目。`message` は状態で変わる案内なので、
実行中（`in_progress`）の文に「止めるときは agent_cancel_run」を足す（§7）。

### 4.3 新しいツール `agent_cancel_run`

| 項目 | 内容 |
|---|---|
| title | 実行の取り消し |
| description | `agent_chat の run_id の実行を止める（回答前の確認の待ちも閉じる）。利用者が止めると言ったとき、回答を待たないと決めたときに使う。` |
| 入力 | `run_id: string`（1〜200 文字。`agent_chat の run_id`） |
| 出力（`CancelRunOutput`） | `schema_version: int`（1）・`run_id: string`・`status: string`（取消の後の Run の状態）・`result: "cancelled" \| "already_cancelled" \| "already_finished"`・`message: string` |
| annotations | `readOnlyHint: false`・`destructiveHint: true`・`idempotentHint: true`・`openWorldHint: false`（§6。`pr_backend_core.mcp.McpTool` に `destructive` / `idempotent` を足す） |
| 権限 | `agent_chat` と同じ `_OPERATE`（`agent.runs.operate` か `agent.admin`。画面の `POST /runs/{id}/cancel` の `require_operator` に当たる） |

意味の詳細は §6。

### 4.4 新しいツール `agent_get_run_observation`

評価・監査の機械（suite の adapter など）が、Run の観測を公開の契約で読むためのツール。会話の回答には使わない。

| 項目 | 内容 |
|---|---|
| title | 実行の観測の取得 |
| description | `実行（run_id）の評価・監査に使う観測（調べた根拠の全件と場所・ツールの呼び出しの経過・利用量）を返す。利用者への回答には agent_get_run を使う。` |
| 入力 | `run_id: string`・`cursor: string \| null`（前の応答の `next_cursor`。省略で先頭）・`limit: int`（根拠の件数。既定 20・最大 100） |
| annotations | `readOnlyHint: true` |
| 権限 | `agent_get_run` と同じ `_READ` |

出力 `RunObservation`（`schema_version` 1）:

| 項目 | 型 | 意味 |
|---|---|---|
| `run_id`・`agent_id`・`thread_id` | string / string / string \| null | `RunOutput` と同じ |
| `agent_version` | `int \| null` | Run に使った業務 Agent の公開の版（`metadata.agent_version`）。対象構成の記録に使う |
| `trace_id` | `string \| null` | Run の trace id（32 桁の hex）。受け付けただけの Run は null |
| `status`・`outcome` | `RunOutput` と同じ | |
| `validation_status` | `"completed" \| "skipped" \| "unvalidated" \| null` | 最終の検証の状態（成果物 `answer_validation.status`）。無ければ null |
| `created_at`・`finished_at` | RFC 3339（UTC） / null | 受け付けた時刻と、終わった時刻（終わっていなければ null） |
| `usage` | `{model: string \| null, requests: int, input_tokens: int, output_tokens: int} \| null` | モデルの利用量（承認の後の再開を含む累計。`RunUsage`） |
| `tool_calls[]` | `ObservedToolCall` の一覧（Run の順。上限 200） | 下の表 |
| `evidence[]` | `ObservedEvidence` の一覧（`cursor` からの `limit` 件） | 下の表 |
| `evidence_total` | `int` | 見る人が見られる根拠の全件の数（ページングの分母） |
| `withheld_evidence_count` | `int` | 見る人が今見られないため除いた根拠の数 |
| `next_cursor` | `string \| null` | 続きがあれば次の `cursor` |

`ObservedToolCall`:

| 項目 | 型 | 意味 |
|---|---|---|
| `index` | `int`（0 始まり） | Run の中の呼び出しの順 |
| `call_id` | `string` | 呼び出しの ID（Run の step の ID。trace の span と結ぶ） |
| `name` | `string` | Agent の中のツールの名前（`<接続>__<ツール>`。`tools_used` と同じ） |
| `status` | `"pending" \| "completed" \| "failed" \| "approval_skipped" \| "cancelled"` | 呼び出しの結果。`approval_skipped` は MCP の Run が承認の要るツールを呼ばなかったもの |
| `error_code` | `string \| null` | 失敗の分類（ツールの結果の `error_code`）。エラーの文は出さない |
| `started_at`・`completed_at` | RFC 3339 / null | 呼び出しの開始と終了 |
| `evidence_count` | `int \| null` | RAG の根拠のツール・読み取りのツールが返した根拠の数（見られない根拠を除く）。ほかのツールは null |
| `include_superseded` | `bool \| null` | `rag_search` / `rag_retrieve_evidence` の引数の値（旧版も検索したか）。ほかのツールは null |
| `job_status` | `string \| null` | `nl2sql_query` / `nl2sql_get_job` の `status`。ほかのツールは null |
| `row_count`・`rows_truncated` | `int \| null`・`bool \| null` | NL2SQL の `returned_count`・`truncated`。SQL・行・列の名前は出さない（§5） |

`ObservedEvidence`（RAG の根拠。会話の添付の根拠は含めない）:

| 項目 | 型 | 意味 |
|---|---|---|
| `rank` | `int`（1 始まり） | Run の中で初めて現れた順（`(document_id, chunk_id)` で重複を除く）。MRR はこの順で数える（今の CLI と同じ） |
| `tool_call_index` | `int` | 初めて返した呼び出しの `index` |
| `source` | `"search" \| "read"` | 根拠のツール（`rag_search` / `rag_retrieve_evidence`）か、読み取りのツール（`rag_read_source` / `rag_read_document` / `rag_read_section`）か |
| `document_id`・`chunk_id` | string | RAG と同じ |
| `file_name`・`chunk_set_id` | string \| null | RAG と同じ（`chunk_set_id` は根拠を作った処理の版。再現の記録に使う） |
| `evidence_type`・`origin`・`superseded` | RAG と同じ | 読み取りのツールの chunk は `evidence_type`・`origin` が null |
| `retrieval_rank` | `int \| null` | RAG の検索の順位（読み取りのツール・前後の文脈は null） |
| `cited`・`answer_use` | `bool`・`"confirmed" \| "reference" \| null` | 回答で引用したか（成果物「回答の根拠」と同じ判定。#2328）と、使われ方 |
| `locator` | `EvidenceLocator` | `section_path`・`page_start` / `page_end`・`page_label_start` / `page_label_end`・`sheet_name`・`row_start` / `row_end`・`cell_range`・`element_locator`（RAG の `EvidenceLocator` と同じ名前。`bbox` は出さない） |
| `excerpt`・`truncated` | `string`（最大 1000 文字）・`bool` | 本文。読み取りのツールの chunk は `text[start:end]` を 1000 文字で切る |

`cursor` は Run の ID とずれ（offset）に結んだ不透明な文字列にし、別の Run の `cursor`・壊れた `cursor` はツールのエラー
（`INVALID_CURSOR`・400）。根拠は Run の順に足されるだけなので、実行中の Run でもずれで読み進められる。見る人の権限がページの間で
変われば、`evidence_total` と `withheld_evidence_count` はその時点の値になる（読むたびに認可する）。

### 4.5 RAG

**出力の形は変えない。** §2.2 のとおり評価に要る観測を公開の契約で返している。足すのはテストだけ: Agent の `outcome` の enum と
`EvidenceLocator` の名前が `rag-tools.json` と一致することを Agent のテストが確かめ、RAG が名前・語彙を変えたら Agent のテストが
落ちるようにする（§9）。

### 4.6 NL2SQL

- `nl2sql_query` / `nl2sql_get_job` / `nl2sql_cancel_requirement` / `nl2sql_list_profiles` / `nl2sql_recommend_profile` の出力に
  `schema_version: int`（1）を足す（3 製品の出力の外枠をそろえる。suite の Observation が形の版を記録する）。
- ジョブの取消のツール（`nl2sql_cancel_job`）と、`nl2sql_get_job` に claim `profile_ids` を当てるかは段 2 に入れない（§11 の Q6）。

## 5. 認可と出してよい範囲

基準は OWASP API Security Top 10 2023 の **API1（Broken Object Level Authorization）** と **API3（Broken Object Property Level
Authorization。項目を出しすぎない）**: 物を読むたびにその時点の主体で認可し、出す項目は呼び出し側の目的に要るものに限る。

- **主体と範囲は今の判定をそのまま使う。** 新しいツールも `_readable_run`（実行した利用者・会話の持ち主・system_admin。ほかの
  利用者の Run は `RUN_NOT_FOUND` の 404 で存在を漏らさない）と、キーの業務 Agent の範囲（`AGENT_OUT_OF_KEY_SCOPE` の 403）、
  ツールの capability（`MCP_TOOL_FORBIDDEN`）を通す。request の値（user・role・actor）を認可に使わない。
- **観測の中身は、Run を実行したときの主体と範囲で得たものだけ。** Run の RAG / NL2SQL の呼び出しは `sub` = Run の利用者、claim
  `profile_ids` = 業務 Agent のデータの範囲のサービストークンで行い、呼び先が「利用者の権限 ∩ claim」で絞った結果だけが Run に残る。
  観測はその記録から作り、新しく RAG / NL2SQL のデータを読みに行かない（例外は下の確かめ直しだけ）。
- **根拠は、見る人の今の権限で確かめ直す（#3252）。** `agent_get_run_observation` は Run の状態・呼ばれた時期に関わらず毎回
  `rag_readable_documents`（`sub` = 見る人・`purpose=evidence_access`）で確かめ、見られない文書の根拠を `evidence[]` から除いて
  `withheld_evidence_count` に数え、`tool_calls[].evidence_count` からも除く。確かめられないときはすべて見られないものとする
  （fail closed）。`agent_get_run` の `sources[]` は今の確かめ直しに `withheld_evidence_count` を足すだけ。`agent_chat` /
  `agent_answer_confirmation` の直後の応答は、見る人が今実行した結果なので今どおり確かめない
  （[security-rbac.md](./security-rbac.md) §4.2）。
- **ID は権限ではない。** `document_id`・`chunk_id` を受け取った呼び出し側が RAG の MCP で読むときも、RAG がその時点の利用者・キーの
  製品の範囲で判定する（キーに `rag` が無ければ読めない）。ID を出しても読める範囲は広がらない。
- **出さないもの（API3）**: ツールの引数の本体（検索の質問の文・条件。モデルが前の根拠の本文を引数に写すことがあり、権限を外した
  後も文として残るため）、NL2SQL の SQL・行・列の名前・`profile_id`（NL2SQL には `rag_readable_documents` に当たる今の権限の
  確かめ直しが無く、後から読む人に今の権限の外の結果を見せうる。§11 の Q5）、RAG の `search_answer_profile_id`・スコア・`bbox`・
  `references` の原文、エラーの文、`outcome` の `basis` / `signals`、Run の `metadata`（キーの ID・名前など）、承認・確認の中身
  （確認は今の `confirmation` で出す）。
- **system_admin（#3244）**: 他の利用者の Run の観測は「閲覧」なので読めるが、根拠は system_admin の今の権限で確かめ直す。取消は
  「運用の操作」なのでできる（§6）。続きの質問・確認への答えは今どおり 403（`OTHERS_CONTENT_FORBIDDEN`）。
- **業務の内容**: 新しく出す文は無い（文書名・抜粋は今の `sources[]` と RAG の契約がすでに返すもの）。新しい項目は ID・列挙・数・
  時刻・真偽だけ。テストの fixture は合成の値で書く。
- **既知の限界（変えない）**: `answer` の本文は保存した文のまま出す（その資料から作った文で、該当の部分を消せない）。

## 6. 取消（`agent_cancel_run`）の意味

- **だれが止められるか**: Run を見て運用の操作をしてよい人（`_run_visible_to`）。実行した利用者、その会話の持ち主（system_admin が
  持ち主の会話で実行した Run も止められる）、system_admin（他の利用者の Run も。#3244 の「運用の操作」）。加えてキーの業務 Agent の
  範囲と capability `_OPERATE`。画面の `POST /runs/{id}/cancel` と同じ判定にし、MCP 専用の規則を作らない。
- **冪等**: 同じ Run に何度送っても結果は変わらない。終わっていない Run は取り消して `result=cancelled`、取消済みは
  `already_cancelled`、完了・失敗は変えずに `already_finished`（`status` は completed / failed）。どれもツールのエラーにしない
  （呼び出し側が timeout の後に送り直しても安全にする。「未対応の操作を成功にしない」には `result` と `status` で答え、取り消せなかった
  ことを成功の取消と見分けられる）。画面の REST の 409（`run_not_cancellable`）は変えない（§11 の Q3）。
- **状態**: queued・running・waiting_confirmation・waiting_approval を取り消せる（`_cancel_run_locked` が承認・回答前の確認の待ちも
  閉じる）。完了と取消が重なっても、保存先の lock で先に決まった方を残す（完了の結果を取消で上書きしない。#911）。応答の `result` は
  `cancel_run` の後の状態と、呼ぶ前の状態から決める。
- **呼び先への伝わり方**: 今の Run の取消と同じ（#2691）。読み取り専用のツールの実行中の MCP の呼び出しは接続を切って止め（RAG /
  NL2SQL は切断で処理を止める）、書き込みのツール（`nl2sql_query` など）は送った後は結果を待つ。NL2SQL のジョブは Agent の取消では
  止まらず最後まで動く（今の振る舞い。§11 の Q6）。
- **記録**: 取消のイベントに操作した人（`cancelled_by` はキーの持ち主のログイン ID）と入口（`via=mcp`・キーの ID）を残し、監査の
  「MCP（キーの名前）」に出す。Run・会話の持ち主は変えない。
- **上限**: 取消は Run を作らないので、キーの直近 24 時間の Run の数（`PLATFORM_API_KEY_RUNS_PER_DAY`）に数えない。1 分あたりの
  呼び出しの回数（`PLATFORM_API_KEY_CALLS_PER_MINUTE`）には数える。
- **MCP の仕様との関係**: MCP の `notifications/cancelled` は送った要求（`tools/call`）を止めるもので、要求より長く生きる Run は
  止められない。MCP 2025-11-25 の実験的な Tasks（`tasks/cancel` など）は `pr_backend_core.mcp` が対応する版（2025-06-18 /
  2025-03-26）の外なので使わない。ツールで止める形は、NL2SQL の `nl2sql_cancel_requirement` と同じで、将来 Tasks に移すときも
  `run_id` をそのまま task の ID にできる。

## 7. LLM に渡す文と字数（[docs/agents/prompts.md](../../docs/agents/prompts.md)）

- **層の置き場所**: いつ止めるかは `agent_cancel_run` のツールの説明、観測をいつ使うかは `agent_get_run_observation` の説明、
  実行中に止められることは `RunOutput.message`（状態で変わる案内）に置く。MCP の server instructions は変えない（agent_chat →
  agent_get_run の流れだけを書いた今の文のまま）。項目の意味は `outputSchema` の description に置き、説明文に繰り返さない。
- **内部を説明しない**: `outcome` の決め方・根拠の確かめ直しの仕組み・ページングの作り方は文に書かない。
- **字数**（`tools/list` の name・title・description・inputSchema の JSON の文字数。今は Agent の 5 ツールで 3,551 文字）:
  `agent_cancel_run` が約 350 文字、`agent_get_run_observation` が約 650 文字で、合計は約 4,550 文字（+28%）。outputSchema は
  `RunOutput` に 5 項目、新しい 2 つの出力の分だけ増える。実装の PR は実際の値の前後を書く。
- **出力の字数**: `sources[]` の 1 件（抜粋 400 文字）が JSON で約 470 → 590 文字（ID 2 つと `cited`）、10 件で約 +1,200 文字。
  `outcome`・`withheld_evidence_count` で約 +50 文字。`agent_get_run_observation` の根拠は 1 件約 1,500 文字（抜粋 1000 文字）で、
  既定の 20 件で約 30,000 文字。LLM のクライアントが誤って呼んでも既定の件数で収まるようにし、機械（suite の adapter）は `limit`
  を上げてページングで読む。
- **スキル**: `skills/` の外部向けのスキルは Agent の MCP のフィールドを参照していない（`skills/README.md` はツールの流れだけ）。
  変えない（`agent/backend/tests/test_skill_md_spec.py` は変わらない）。

## 8. 利用側への影響

- **外部の MCP クライアント（Claude Code など）**: `RunOutput` の項目が増えるだけで、今の項目の意味は変えない（`schema_version` 3）。
  `sources[].document_id` / `chunk_id` で RAG の `rag_read_source` を呼んで原文を確かめられる（キーに `rag` があるとき）。
- **Agent の中**: Agent が RAG / NL2SQL の出力を読む処理（`support_task.py`・`answer_evidence.py`・`answer_outcome.py`・
  `evidence_access.py`）は変えない。観測は Agent が公開の RAG の契約の項目から作る（「契約に基づく適合」）。読み取りのツールの chunk を
  根拠にする規則（今は CLI の `_read_items`）は Agent の観測の関数に移り、CLI と同じ結果になることを段 1 の基線で確かめる。
- **suite の adapter（段 3）**: 段 1 の依存の一覧の各項目を、§3 の表の公開の項目から読む。`outcome` が null の完了の Run・
  `schema_version` が知らない値の応答は「評価不能」と理由を記録する。時間切れは `agent_cancel_run` を送り、`result` を記録する。
  業務 Agent は既存の公開した版を ID で指定し、`agent_version` を対象構成に記録する。API キーの上限（既定は 1 分 60 回・24 時間に
  200 Run）に収まるよう、`wait_seconds` を長めにして呼び出しの回数を減らす（評価は 1 回 5 問）。評価セットの文書の ID の解決
  （`file:<名前>` → 配備先の `document_id`）は RAG の側の手順のまま（段 3 で扱う）。
- **文書**: 実装の PR で `docs/agents/mcp.md`・`platform/docs/mcp-api-keys.md`（Agent のツールの表）・
  `agent-control-plane-design.md` §4・`security-rbac.md` §4.2（入口に `agent_get_run_observation` を足す）を直す。

## 9. テスト

- **契約**: `UPDATE_MCP_CONTRACT=1` で `agent-tools.json` / `nl2sql-tools.json` を書き直し、3 製品の `tests/test_mcp_contract.py` で
  実装と一致させる。新しい Agent のテスト（`tests/test_product_mcp.py` に足す）: `RunOutput.outcome` の enum が
  `rag-tools.json` の `rag_search.outcome` と同じ、`ObservedEvidence.locator` の項目が RAG の `EvidenceLocator` の部分集合、3 製品の
  すべての `outputSchema` が `schema_version` を持つ。
- **出力**（`tests/test_agent_mcp.py`）: 引用あり・引用なし（全件を出し `cited=false`）・添付の根拠（ID が null）・完了で outcome が
  無い Run（null）・実行中（outcome が null・`message` に取消の案内）。観測: 根拠のツールと読み取りのツールの重複の除去と `rank`、
  `retrieval_rank` の写し、`tool_calls` の状態（失敗・承認で止めた・取消）、`include_superseded`、ページング（`next_cursor`・
  `INVALID_CURSOR`・別の Run の cursor）、実行中の Run。
- **認可の行列**（`tests/test_evidence_read_authz.py` と `tests/test_admin_operates_others.py` に入口を足す）: 実行した利用者 /
  会話の持ち主 / system_admin / ほかの利用者（404）/ キーの範囲の外（403）/ capability なし（`MCP_TOOL_FORBIDDEN`）×
  `agent_get_run`・`agent_get_run_observation`・`agent_cancel_run`。権限を外した文書の根拠が `evidence[]`・`sources[]` から消え
  `withheld_evidence_count` に数えられること、RAG の確かめの失敗で全件を除くこと（fail closed）、system_admin が他の利用者の Run を
  止められて actor が残り、続きの質問は 403 のままのこと。
- **出しすぎの検査（canary）**: 合成の Run のツールの引数・NL2SQL の SQL / 行・エラーの文・`outcome.basis` / `signals`・metadata に
  目印の文字列を入れ、`agent_get_run` と `agent_get_run_observation` の直列化した応答に目印が 1 つも無いことを確かめる。
- **取消**: 各状態（queued / running / waiting_confirmation / waiting_approval）からの取消、2 回目の `already_cancelled`、完了・失敗の
  `already_finished`（状態を変えない）、完了と取消の競合（#911 のテストと同じ形）、実行中の読み取り専用の MCP の呼び出しが切れること
  （#2691 の既存のテストを MCP の入口から呼ぶ）、Run の数の上限に数えないこと。
- **基線**: 段 1 の fixture の Run から作った公開の観測で採点し、`expected.scores` と一致することを確かめる（§10 の PR 5。意図した差は
  §3 の 2 つだけで、合成の fixture では出ない見込み。出れば採点の version を上げて記録する）。
- 並列（`-n auto`）でも直列でも通るように書き、OCI / LLM は呼ばない（Runtime は `ScriptedModel`、RAG は `tests/mcp_support.py` の
  契約どおりの fake）。

## 10. 実装の分割（PR の単位）

| # | PR | 内容 | 依存 |
|---|---|---|---|
| 1 | `feat(agent): agent_chat / agent_get_run に回答の対応・根拠の ID・見られない根拠の数を足す (#3240)` | §4.2（`schema_version` 3）。`AgentListOutput`・`SkillExportOutput` に `schema_version`。契約・出力・認可の行列・enum の一致のテスト。`mcp-api-keys.md`・`mcp.md` | なし |
| 2 | `feat(agent,platform): 実行を取り消す MCP のツール agent_cancel_run を足す (#3240)` | §4.3・§6。`McpTool` に任意の `destructive` / `idempotent` の annotations（platform。指定したツールだけ `tools/list` に出し、既定のツールの出力は変えない）。取消のテスト・#3244 の行列 | なし（1 と並行できる） |
| 3 | `feat(nl2sql): MCP の出力に schema_version を足す (#3240)` | §4.6。契約のテスト | なし |
| 4 | `feat(agent): 評価・監査の観測を返す MCP のツール agent_get_run_observation を足す (#3240)` | §4.4・§5。観測の関数（読み取りのツールの chunk を含む）・ページング・確かめ直し・canary。`security-rbac.md` §4.2 | 1（`outcome`・`withheld_evidence_count` の作り方を共有） |
| 5 | `test(rag,agent): 段 1 の基線を公開の観測で再生して採点の一致を確かめる (#3240)` | 段 1 の fixture の Run から作った公開の観測（`agent_get_run_observation` の形の合成の JSON。置き場所は `platform/contracts/mcp/fixtures/` などの共有の場所）で採点し、`agent_internal_dependencies.json` の各項目の `public_agent_mcp` を公開の項目に更新する。段 3 の adapter の入力にする | 1・2・4 |

各 PR は Issue #3240 を `Refs` で結び、§11 の答えで変わる部分だけを直す。段 2 の完了は PR 5 の merge（依存の一覧の各項目が公開の
項目で採点できる、または「足さない」理由がある状態）。

## 11. 決めてほしいこと（推奨つき）

| # | 問い | 推奨 | 理由 |
|---|---|---|---|
| Q1 | 根拠の全件・ツールの経過を、`agent_get_run` の任意の引数で返すか、別のツールにするか。別のツールなら `tools/list` に出すか | **別のツール `agent_get_run_observation` にし、`tools/list` に出す** | 回答に使う出力を軽く保ち、1 つの規則を 1 つのツールに置ける。`listed=False` は「製品間の画面の操作だけが名前で呼ぶ内部のツール」の規則（`McpTool`）で、外部の機械が使うツールに広げると規則が崩れる。出す字数は約 650 文字 |
| Q2 | `agent_cancel_run` を LLM のクライアント（Claude Code など）にも見せるか | **見せる** | 利用者が「止めて」と言ったときと、長い実行を待たないと決めたときに外部のクライアントも要る。字数は約 350 文字 |
| Q3 | 終わった Run の取消を、MCP でツールのエラー（REST の 409 と同じ）にするか、`result` で返すか | **エラーにせず `result`（already_finished / already_cancelled）で返す。REST の 409 は変えない** | MCP のクライアントは timeout の後に送り直すので冪等が要る。`status` と `result` で取り消せなかったことを見分けられ、成功の取消と誤認させない |
| Q4 | 業務 Agent の作成・公開の MCP のツールを足すか（今の CLI の `--create-agent`） | **足さない。suite は既存の公開した業務 Agent を ID で指定し、`agent_version` を記録する** | 作成・公開は Agent 構築の権限の操作で、API キーの外部のクライアントに開くと攻撃面が広がる。評価の対象構成は人が画面で用意する方が版の記録と合う |
| Q5 | 観測に NL2SQL の SQL・行・`profile_id` を入れるか | **段 2 では入れない（状態・行の数・切り詰めだけ）** | NL2SQL には後から読む人の今の権限の確かめ直しが無い。NL2SQL の評価を suite に入れるとき、確かめ直しの仕組み（`rag_readable_documents` に当たる内部のツール）と一緒に決める |
| Q6 | NL2SQL のジョブの取消の MCP のツール（`nl2sql_cancel_job`）と、`nl2sql_get_job` に claim `profile_ids` を当てるかを段 2 に入れるか | **入れない。別の Issue にする** | 評価の移設に要らない。claim を当てるかは NL2SQL の文書が「変えない」と決めた事項で、変えるなら NL2SQL の権限の設計として決め直す。Agent の取消の後もジョブが最後まで動く今の振る舞いは、その Issue で扱う |
| Q7 | MCP の Run が承認の要るツールを呼ばなかったケース（`skipped_approval_tools` が空でない）を評価でどう数えるか | **ケースの失敗（種類 `approval_skipped`）にし、採点の version を上げて記録する** | 段 1 の基線は `waiting_approval` をケースの失敗にしている。公開の MCP ではその代わりに承認のツールを飛ばして答えるので、成功に数えると必要な操作をしていない回答を採点してしまう |
| Q8 | 観測の根拠の本文の上限を RAG と同じ 1000 文字にするか、読み取りのツールの chunk は全文にするか | **1000 文字（`truncated` つき）** | RAG の `excerpt` と同じ上限で、出力の大きさを予測できる。本文の条件が後ろにある根拠は `truncated` で「評価不能」と分けられる。合成の基線で差が出たら見直す |

## 12. 参考

- MCP の仕様（2025-06-18）: Tools の structured content と `outputSchema`（出力は schema に合わせ、互換のため text にも直列化した
  JSON を入れる）、tool annotations（`readOnlyHint`・`destructiveHint`・`idempotentHint`・`openWorldHint`。注釈は信頼した server の
  ものだけを使う）、`notifications/cancelled`。2025-11-25 の実験的な Tasks。
- OWASP API Security Top 10 2023: API1 Broken Object Level Authorization・API3 Broken Object Property Level Authorization。
- OpenTelemetry semantic conventions for generative AI（`gen_ai.tool.name`・`gen_ai.tool.call.id`・`gen_ai.usage.*`・
  `gen_ai.conversation.id`・`gen_ai.agent.id`）と W3C Trace Context。
- 関連: #3283（段 1 の基線）、#3252（保存した根拠の確かめ直し）、#3244（system_admin の運用の操作）、#2691（Run の取消と MCP の
  呼び出しの打ち切り）、#1874（Agent の MCP の公開）、#1305（回答の対応）、#2328（回答の根拠）、#1379（データの範囲の claim）、
  #911（完了の結果を取消で上書きしない）。
