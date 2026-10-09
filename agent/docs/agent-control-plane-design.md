# Agent Platform 設計

## 1. Positioning

業務・業界の Agent を作り、Skill と MCP（RAG・NL2SQL・その他）を安全に使わせる製品。業務 Agent の定義と、
その実行（組み込み Runtime）、共通の Run・Event・Artifact・Approval・Audit を 1 つの製品で持つ。

#754 で、外部の Runtime（OpenClaw / Hermes / DeerFlow）への Binding による配置をやめ、Control Plane の中の
組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI の Responses API）で実行する形にした。
今後の作り変えは「Agent Platform 再設計案」（2026-10-02）に沿う。

```mermaid
flowchart TD
  M["Marketplace"] --> P["Plugin（配布 package）"]
  P --> R["Registry"]
  R --> S["Skill"]
  S --> T["ツール（RAG / NL2SQL / 外部 MCP）"]
  A["業務 Agent（指示・Skill・モデル）"] --> S
  A --> X["Run"]
  X --> B["組み込み Runtime（OpenAI Agents SDK）"]
  B --> O["OCI Enterprise AI（Responses API）"]
  B --> T
```

## 2. Domain contracts

### Business Agent

`AgentProfile` の正式な編集対象は `name / description / instructions / skill_ids / model_id / enabled`。

版（#770）: 名前・説明・指示・Skill・モデルは「下書き」で、`POST /agents/{id}/publish` で版（`AgentVersion`: 版の番号・
内容・公開日時・公開者・メモ）を作り、`published_version` にする。通常の Run は公開中の版の内容で実行し
（`RunState.metadata.agent_version`）、公開していない Agent の Run は 409（`agent_unpublished`）。Agent 管理
（admin）は `RunCreateRequest.draft=true` で下書きを試せる（`agent_version="draft"`）。
`POST /agents/{id}/versions/{version}/restore` はその版を公開し直し、下書きもその内容にする（ロールバック）。
画面・API で作る Agent は下書きから始め、#770 より前の Agent（`versioned=false`）は読み込み時に現在の内容を
v1 として公開する。
利用者の Run を作る入口（チャットの選択肢・MCP の `agent_list_agents` / `agent_ask`・自動実行の作成と実行）は、
公開した版の無い Agent を出さず・選ばせない（`runtime.agent_unavailable_reason`。#792）。品質評価は下書きでも評価できる。
品質評価（#810）は評価の開始で「公開中の版」か「下書き」を選ぶ（`EvaluationRequest.agent_version`。省略時は公開して
いない変更があれば下書き、なければ公開中の版。公開した版が無ければ下書きだけ）。job は `agent_version`（版の番号か
`"draft"`）を残し、公開中の版は始めたときの版に固定して Run を作る（`create_builtin_run(agent_version=...)`。Run の
`metadata.agent_version` は #770 のまま）。前回との比較は同じ評価セット・同じ評価ケース（質問・期待・期待するツール）の
完了した job とだけ行い、比べた版（`previous_agent_version`）と日時を返す。
業種テンプレートから作った Agent は `template_id` を持ち（#810。以前の Agent は空のまま読める）、
`POST /evaluation-sets/from-template` でテンプレートの評価ケースの評価セットを作れる。フィードバック・Run の詳細の
「評価ケースに追加」は `GET /runs/{id}/evaluation-case`（質問・管理者のコメント・呼んだツールの下書き）と
`POST /evaluation-sets/{id}/cases`（同じ質問・50 件の上限は 409。出どころの `source_run_id` を残す）を使う。
Plugin、MCP、Tool、Runtime を Agent に埋め込まない。`tool_names` は移行リリースの読取互換だけである
（`command_allowed_prefixes` は #756 で削除した）。

### Skill

Skill は AgentSkills 互換の指示本体であり、次の内部依存を持つ。

```json
{
  "id": "business_rag_research",
  "instructions": "...",
  "mcp_requirements": [
    {"server_id": "rag", "tool_names": ["rag_search", "rag_list_search_answer_profiles", "rag_read_source"]}
  ],
  "resource_ids": []
}
```

`server_id` は MCP 接続の ID（`control-plane` は Control Plane のツール）。`tool_names` が空なら接続のすべての
ツールを使う。組み込み Runtime は、Agent の選択 Skill が要求するツールの和集合だけをモデルに渡す（指示への列挙だけで
権限を表現せず、渡すツールの一覧を実体として絞る）。

### Marketplace package

正式 manifest は `skills[] / mcp_servers[] / resources[]`。resource kind は `prompt / workflow /
template` で、初期実装は inline JSON/text のみ。resource を実行しない。

Install は事前に Skill/MCP/resource の重複と参照を検証し、衝突時に package 全体を拒否する。
旧 `agents[]` は Agent を作らず template resource に変換して warning を返す。参照中 Skill を持つ
package の disable/uninstall は `409`。

#### 外部カタログの互換インポート（#862）

`MarketplaceEntry` は配布先を示す一覧の項目であり、導入済みの `PluginManifest` と分ける。
更新ではカタログだけを取得し、1 項目の未対応・重複で他の項目を失わない。状態は
`not_fetched / ready / failed`。取得失敗では旧一覧を保持し、成功通知を出さず古い一覧と案内する。
URL がある配布元も、最後の一覧・revision・失敗状態を保存して復元する。

- 配布物の取得元は公開 GitHub の HTTPS。repository 内の相対 path、`url`、`github`、`git-subdir`、
  明示的な `skills` ディレクトリ、`ref` / 40 桁の `sha` に対応。raw カタログ URL の ref は 1 path segment。
  slash を含む branch は source の `ref` で指定する。`strict:false` の明示的な構成は既定のディレクトリより優先する。
- ref を commit SHA に確定して Git tree と raw の blob hash を照合。root 外の path、symlink、転送先への追従を断る。
  上限はカタログ 1,000 項目、Skill 40 件、HTTP 80 件、1 ファイル 1 MiB、全体 12 MiB、参照文書 4 MiB。
  Git tree は 8 MiB、取得期限は更新 10 秒・導入内容の取得 120 秒。同じ revision のファイルは取得単位で再利用する。
- `SKILL.md` の name・description・非空の instructions を保持する。Markdown の参照文書と利用条件の表示を
  非実行 resource に保存し、本文には resource ID / path だけを列挙する。割り当てた Skill の resource だけを
  `skill_reference_read` で 8,000 文字ずつ読む。指示の外部 URL や配布コードを自動で実行しない。
- 資格情報や動的設定を含まない HTTPS の HTTP MCP だけを登録する。stdio、commands、agents、hooks、LSP、
  scripts、Markdown 以外の補助ファイルは導入・実行の対象外とし、具体的な制約を表示する。
  元製品の `allowed-tools` は引き継がない。必要なツールはこの製品の MCP 接続で設定する。
- サービス外の保持を明示的に制限する利用条件を検出した Skill は本文の取得前に拒否する。
  その他の利用条件も導入前の確認対象であり、ライセンス適合性や業務実行を保証する機能ではない。
- `POST /api/plugins/marketplaces/{marketplace_id}/plugins/{plugin_id}/preview` は管理者だけが呼ぶ。
  導入は `preview_digest` と `accept_limitations` を必須とし、取得し直した内容が変わったら `409` で再確認する。
  ID は marketplace / plugin / path の namespace で分け、revision の更新だけでは変えない。
  変換した manifest は既存の原子的 install を使い、登録・保存の失敗は今回の全コンポーネントを撤去する。

固定実形式の fixture と実 HTTP の確認結果は `backend/tests/fixtures/marketplaces/README.md` を参照。
Claude Code の全機能の互換性や、Skill が業務のモデル・ツールで動くことの検証とは区別する。

### 組み込み Runtime（#754）

- `features/agent/builtin_runtime.py`。SDK は `openai-agents`（`pyproject.toml` で版を固定）。
- モデル: Agent の `model_id`（空なら「システム設定 > モデル」の既定のテキストモデル）と、その接続（Endpoint・API key・
  Project OCID）。`OpenAIResponsesModel` に OCI の OpenAI 互換の base URL を渡す。xAI のモデルは空の `tools` を
  拒否するため、ツールが無い呼び出しでは `tools` / `tool_choice` を送らない（`OciResponsesModel`）。
- 指示: 共通の前置き（日本語・根拠・推測しない）＋ Agent の指示 ＋ 割り当てた Skill の指示。
  Skill の指示は MCP のツールを素の名前（`rag_search`）で書いてよい。Run を作るときに、Skill の requirement が
  名前で宣言し、この Run でモデルに渡すツールの名前（`rag__rag_search`）へ書き直す（Agent の指示・支援タスクの指示も同じ。
  同じ素の名前が複数の接続にあって 1 つに決まらないものは書き換えない。#1303）。
- 無いツールの呼び出し: モデルが渡していない名前のツールを呼んでも Run を落とさない（SDK の
  `tool_not_found_behavior="return_error_to_model"`）。モデルへは「呼ぶときの名前は `<接続>__<ツール>`」
  （候補が複数なら候補の一覧）を返して呼び直させる。Control Plane が素の名前を別のツールへ読み替えて実行することは
  しない（呼び出し・承認・監査はモデルが呼んだ名前のまま。#1303）。
- ツール: Skill の requirement が要求するツールを `FunctionTool` にする。`control-plane` は `tool_registry` の
  ツール、それ以外は MCP 接続の `tools/list`（Run の利用者として取得。名前は `<接続>__<ツール>`、英数字・`_`・`-`
  で 64 文字以内）。取得できない接続は飛ばして Run に `runtime.event`（warning）を残す（#757）。
  実行は `tool_registry.invoke`（ポリシー・ガードレール・監査・成果物、サービストークン）。
  ポリシーの「拒否」は渡さず、「承認」は `needs_approval=True`（MCP のツールは `readOnlyHint` が無ければ既定で承認）。
- 承認: SDK の中断（`result.interruptions`）で承認待ちの step と ApprovalRequest を作り、`result.to_state().to_string()`
  を Run の metadata（`_builtin_sdk_state`）に保存して `waiting_approval` にする。すべて決まると `queued` に戻り、
  状態を復元して承認・却下を反映し再開する。承認済みのツールは中断時の step を実行中にして結果を記録する。
  `_` で始まる key と Control Plane の予約の key（`RESERVED_RUN_METADATA_KEYS`。評価・自動実行・MCP・再実行の印）は、
  利用者が `POST /api/runs` の `metadata` で付けると 422 にする（再開の状態や出所を偽らせない。#1130）。
- tracing: `set_tracing_disabled(True)`（業務データを外部へ送らない）。
- テスト: SDK の `agents.testing.ScriptedModel` でモデルを台本にする（`tests/test_builtin_runtime.py`）。

### RunState

共通 Run は状態/Event/Artifact/Approval/Audit と `runtime_id`（新しい Run は `builtin`）を持つ。
`POST /api/runs` は `agent_id` と `goal` だけを受け取り、組み込み Runtime で実行する。モデルの最終回答は
`kind="answer"` の Artifact に残す。

## 3. ツールとポリシー

Control Plane のツール（`echo` / `agent_skill_list`）は `tool_registry` に登録する。MCP 接続のツールは登録せず、
Run ごとに `tools/list` から作ったツール定義を `tool_registry.invoke` に渡し、ToolPolicy、PII/secret masking、
audit metadata、成果物の保存を再利用する（#757）。外部 Runtime 向けの Binding MCP（`/api/mcp/{binding_id}`）
と adapter 契約（`probe_capabilities / sync_binding / submit_run / ...`）は #754 で削除した。

## 4. 製品の MCP

### 4.1 MCP 接続（RAG / NL2SQL / 外部 MCP。#233 / #757）

RAG / NL2SQL / 外部 MCP は「MCP 接続」（`config.McpConnectionConfig`、API `/api/settings/mcp-connections`）で
管理する。RAG / NL2SQL は標準の接続 `rag` / `nl2sql`（各製品の `POST /api/mcp`。認証はサービストークン、
aud は製品名。削除できない）。表示名は「ナレッジ検索（RAG）」「データ問い合わせ（NL2SQL）」で変えられない（#1325）。
URL は `AGENT_EXTERNAL_RAG_MCP_URL` / `AGENT_EXTERNAL_NL2SQL_MCP_URL` で、配備がこれを与えた接続は `base_url_locked`
になり、画面・API で URL を変えられない（PATCH は 400。保存した URL・名前は起動時の復元でも使わない）。環境変数が空の
構成（ローカルの開発・その製品を配備しない構成）だけ、画面で URL を設定して保存できる。外部の MCP は画面・`AGENT_EXTERNAL_MCP_SERVERS_JSON`・プラグインで追加し、
認証方式は なし / API キー / OAuth client credentials / サービストークン。ツールは呼び先の契約（RAG / NL2SQL は
`platform/contracts/mcp/`）をそのまま使い、Agent は引数を作り変えない。

| ツール（モデルに渡す名前） | readOnlyHint | 既定の policy |
|---|---|---|
| `rag__rag_search` / `rag__rag_list_search_answer_profiles` / `rag__rag_read_source` | true | 承認なし（`rag_search` は回答生成に LLM を使う） |
| `nl2sql__nl2sql_list_profiles` / `nl2sql__nl2sql_recommend_profile` / `nl2sql__nl2sql_get_job` | true | 承認なし |
| `nl2sql__nl2sql_query` | false | 承認が必要（業務 DB へ SQL を実行する） |

RAG のチャットは MCP で提供しない（#787）。RAG の MCP は検索（`rag_search`）・根拠の読み取り（`rag_read_source`）・
検索・回答プロファイルの一覧だけを持つ。`rag_search` の根拠は `evidence[]`（場所 `locator` の節・頁、版 `chunk_set_id` /
`recipe_id`、回答に使ったか `used_in_answer`、切り詰めの有無 `truncated`）で返し、回答に使った根拠を先にして `evidence_limit`
件まで返す。切り詰めた本文と親の本文は `rag_read_source`（検索と同じ見え方の条件。見えない根拠は `source_not_found`、古い版は
`source_stale`）で読む（#1219）。チャットの出典は回答に使った根拠だけを、場所を添えて出す。

ツール権限（`/settings/tool-policy`）は `<接続>__<ツール>` の名前で allow / ask / deny を上書きできる。
NL2SQL の SQL に書き込みの文があれば `nl2sql.non_readonly_sql_returned_as_audit_only` の警告を残す（実行はしない）。

- **利用者**: Run の作成時に、ログイン中の利用者（Cookie のセッション。local mode ではローカル利用者）の
  `user_uuid` を `RunState.created_by_user_uuid` に記録する（checkpoint の JSON に入る。項目がない既存の Run は
  None）。Run からのツール呼び出し
  （承認後の再実行を含む）は `ToolInvocationContext.user_uuid` / `run_id` にこの値を入れるため、承認者ではなく
  Run を作った利用者として呼ぶ。再実行（replay）の Run は、再実行を指示した利用者になる。単発の
  `POST /tools/invoke` と画面の「ツールを取得」（`GET /settings/mcp-connections/{id}/tools`）は、ログイン中の利用者として呼ぶ。
- **token**（サービストークンの接続）: 呼び出しごとに `issue_service_token(PLATFORM_SERVICE_TOKEN_SECRET, subject=<Run の利用者 or
  サービス利用者>, audience=<接続の audience>, issuer="agent", claims={"run_id", "agent_id"})`（HS256、60 秒）を
  作り、`Authorization: Bearer` で送る。署名鍵は 3 製品の共通 `.env` で同じ値にする（32 文字以上。未設定なら
  `mcp.service_token_not_configured`）。サービス利用者の login ID → `user_uuid` は共通認証の store で解決し、
  プロセス内にキャッシュする。
- **MCP の手順**: 呼び出しごとに `initialize`（`2025-06-18`）→ `notifications/initialized` → `tools/call`。
  応答の `Mcp-Session-Id` を以降の request に付け、`Accept: application/json, text/event-stream` と
  `MCP-Protocol-Version` を送る。結果は `structuredContent`（無ければ text の `content`）、`isError: true` は
  `mcp.tool_error`（呼び先の `error_code` / `message` / `status` を `error_details` に入れる）。SSE の途中経過と
  paging は扱わない。すべての接続が同じ client（`McpConnectionClient`）を使う。固定の session id を設定した接続には
  `initialize` を送らず、`initialize` を持たない接続（`-32601`）はそのまま `tools/*` を呼ぶ。
- **再試行と期限（#854）**: 1 メッセージを `AGENT_EXTERNAL_MCP_MAX_RETRIES`（既定 3）回まで再試行する。分類は MCP の意味に合わせる。

  | 失敗 | 手順（`initialize`・`notifications/initialized`・`tools/list`） | 読み取り専用（`readOnlyHint=true`）の `tools/call` | それ以外の `tools/call` |
  |---|---|---|---|
  | 送信前（`ConnectError`・`ConnectTimeout`・`PoolTimeout`）・429・503 | 再試行 | 再試行 | 再試行 |
  | 502・504・通信の途中の切断 | 再試行 | 再試行 | しない |
  | 500・読み取りの timeout | 再試行 | しない | しない |

  書き込みのツール（`nl2sql_query` など）は、呼び先に届いた可能性がある失敗では再送しない（SQL の実行・LLM の呼び出しを
  重複させない）。読み取り専用のツールも、読み取りの timeout は再送しない（`rag_search` のように呼び先で LLM が動いている）。
  待ちは 0.5 秒から 2 倍ずつ（上限 8 秒、後半を jitter）、429 / 503 の `Retry-After`（秒・HTTP 日付）があればその値（上限 30 秒）。
  1 回のツールの呼び出し（`initialize` から `tools/call` まで）全体を接続の timeout で区切り、各 POST の timeout は残りの時間
  （接続の確立は 10 秒以下）、待つと期限まで 1 秒を切るときは再試行しない。待ちは `tools._retry_sleep` で差し替える（テストは
  待たない）。最後の失敗は利用者の言葉のメッセージ（例:「MCP 接続「RAG」に接続できません（RAG のサービスが起動しているか、
  接続の URL が正しいかを確認してください）。」）と code（送信前の失敗 `mcp.unreachable`、ほか `mcp.timeout` / `mcp.http_error` /
  `mcp.request_error`。`error_details` に `attempts`・`retryable`）のツールの失敗としてモデルへ返し、Run は続く（モデルが
  利用者に伝える）。ツールの一覧が取れない接続は従来どおり飛ばして warning を残す。
- **NL2SQL のジョブの完了を待つ（#848）**: 組み込みの接続 `nl2sql` の `nl2sql_query` / `nl2sql_get_job` の結果が
  `pending` / `running`（`job_id` あり）なら、ツールの handler の中で `nl2sql_get_job` を `wait_seconds`（最大 20 秒。
  接続の timeout から 15 秒引いた値以下）付きで繰り返し呼び、完了した結果をモデルに返す。待つ合計の上限は
  `AGENT_NL2SQL_JOB_WAIT_SECONDS`（既定 300 秒、0 で待たない）で、1 回の待ちごとに Run がキャンセル・終了していないかを
  確かめる。上限を超えた・Run が終わった・続きの取得に失敗したときは最後の結果（`running` と `job_id`）を返す。
  SDK の function tool の timeout は待つ分を足した値にする。外部の MCP 接続のツールは対象外。
- **設定**: `AGENT_EXTERNAL_RAG_MCP_URL` / `AGENT_EXTERNAL_NL2SQL_MCP_URL`（例 `http://rag-host/api/mcp`）、
  `AGENT_EXTERNAL_RAG_TIMEOUT_SECONDS` / `AGENT_EXTERNAL_NL2SQL_TIMEOUT_SECONDS`（既定 60 秒）は接続 `rag` / `nl2sql` の
  初期値。画面の「MCP 接続」で URL・タイムアウトを変更でき（保存先に残り、.env の値より優先する。#764）、署名鍵と
  サービス利用者は設定済みかどうかだけを表示する。旧「外部 MCP」の単一の設定（`AGENT_EXTERNAL_MCP_BASE_URL` 等）・
  既定のサーバー・NL2SQL の既定取得件数（`AGENT_EXTERNAL_NL2SQL_DEFAULT_LIMIT`）は #757 で削除した。
- **RAG の図の根拠を画面で開く（#1311）**: チャットの出典・実行の詳細の根拠のうち、RAG の `rag_search` の根拠に
  `image_ref` がある図は「図を開く」を出す。押すと `GET /api/runs/{run_id}/figure-url`（実行の詳細と同じ権限・対象範囲）が、
  Run の `rag_evidence` の成果物にその図があることを確かめてから、組み込みの接続 `rag` の `rag_read_source` を
  `include_image_url=true` で呼ぶ。サービストークンの `sub` は **画面を見ている利用者**（Run を見られる管理者が Run の
  利用者の権限を借りないように）、claim に `purpose=figure_url` を入れる（RAG はこの印の無い呼び出し、つまり Run の中で
  モデルが呼んだときは URL を作らない）。画面の操作で Run のツール呼び出しではないので `tool_registry` を通さず、
  URL（トークンを含む）は保存・ログに残さない（応答も `Cache-Control: private, no-store`）。RAG は利用者・chunk・版に
  縛った 5 分の HMAC 署名の URL（`GET /api/figures/{token}`）を返し、読むたびに権限・版を確かめ直す。ブラウザから
  開く起点は `AGENT_EXTERNAL_RAG_PUBLIC_URL`（空なら RAG が返す URL。MCP の URL の host）で、画面は開くたびに
  URL を取り直し、画像を読めなければ（期限切れ・資料の更新・権限）取り直しの操作を出す。

## 5. Dispatcher and persistence

開発時は API のプロセスで実行する（`AGENT_RUNTIME_DISPATCH_MODE=in_process`。asyncio の task）。本番で別プロセスに
する場合は `runtime-dispatcher`（`python -m app.features.agent.runtime_dispatcher`）が Oracle checkpoint row を
`SELECT ... FOR UPDATE` し、queued の組み込み Runtime の Run に期限付き lease を付けて claim し、実行・再開する。
実行を始めたら lease を外す（承認の決定で queued に戻った Run を、すぐ claim できるようにする）。別 queue 製品は追加しない。

外部 dispatcher は `AGENT_RUNTIME_REPOSITORY_BACKEND=oracle_checkpoint|oracle_normalized` が前提。
memory backend は process 間共有されないため production dispatcher に使用しない。

### 5.1 保存先（#764）

保存先は `AGENT_RUNTIME_REPOSITORY_BACKEND`（既定 `auto`。#839）。`auto` は起動時に 1 回だけ、共通の `PLATFORM_ORACLE_*` の
設定がそろっていれば `oracle_checkpoint`、無ければ `memory` に決め（`app/features/agent/storage_backend.py`）、Run の repository・
定義の store・Run の事実が同じ決定に従う。Oracle に接続できない（接続のエラー）ときも memory に縮退せず、backend は起動して
接続できた時点で読み込む（#1212。§5.1.1）。今の保存先は `GET /api/runtime/storage`（`backend`・
`persistent`・`database_configured`・`reason`。接続先は返さない。#839）で分かり、「運用設定 > 実行環境」の「保存先」の
カードが `StatusBadge` と直し方を出す。保存していない（`persistent=false`）ときは、業務 Agent・スキル・プラグイン・
マーケットプレイス・実行履歴・自動実行・品質評価・MCP 接続・API キー・ツール権限・バックアップと復元・システムテーブルの
画面の先頭に warning の Banner（`NonPersistentStorageNotice`）を出し、実行環境へ案内する。理由は、DB の設定がそろって
いれば `memory_backend`（memory などを明示。保存先の設定を直す）か `restart_required`（`auto` で起動時は DB が未設定だった。
再起動する）、そろっていなければ `database_not_configured`（DB の設定から直す）。`auto` で保存済みの checkpoint 全体が読めず memory で
起動したときは `checkpoint_invalid`（#853。§5.1.1）。

| 対象 | memory | file | oracle_checkpoint / oracle_normalized |
|---|---|---|---|
| Run・業務 Agent | プロセス内 | `AGENT_RUNTIME_SNAPSHOT_PATH` の JSON | `AGENT_RUNTIME_CHECKPOINTS`（snapshot の CLOB）。normalized は監査用の `AGENT_RUNTIME_RUNS/EVENTS/STEPS/APPROVALS/ARTIFACTS` も書く |
| 画面・API で変えた定義（Skill・プラグイン・マーケットプレイス・MCP 接続・ツール権限） | 保存しない | snapshot の隣の `<名前>.control-plane.json` | `AGENT_CONTROL_PLANE_ITEMS`（`ITEM_KIND` × `ITEM_ID` の JSON） |
| 利用状況・フィードバックの集計に使う Run の事実（#794） | 保存しない（メモリの Run をその場で集計） | 保存しない（同左） | `AGENT_RUN_FACTS`（1 Run = 1 行。SQL で集計） |

- Oracle は共通の `PLATFORM_ORACLE_*` で接続する（旧 `AGENT_RUNTIME_ORACLE_*` は読まない）。テーブルはシステムテーブル
  （`app.system_schema` の migration 006）が作り、アプリは DDL を実行しない。テーブルが無いあいだは空の状態で起動し、
  定義の保存は「システムテーブルで作成・更新してください」で断る。全再作成はこれらのテーブルも消す（Run の履歴も消える）。
- 定義は API の変更の後に保存し（`control_plane_store.save_*`）、保存先の読み込み（`storage_bootstrap`。§5.1.1）で
  `restore_control_plane()` を呼び、`.env` の宣言の後に重ねる。RAG / NL2SQL の接続は画面で変えた URL・タイムアウトだけを
  上書きする（認証方式は変えない）。`.env` の宣言・組み込みの定義は保存しない。
- MCP 接続の API キー・OAuth の client secret・セッション ID は、`PLATFORM_SERVICE_TOKEN_SECRET` から HKDF-SHA256 で
  導いた鍵の Fernet で暗号化して保存する（`app.secret_box`。`enc:v1:...`）。署名鍵を変えると復号できないため、その接続の
  秘密は画面で入れ直す。署名鍵が無いと秘密を含む接続は保存できない（503）。
  - プラグインの manifest（`manifest.mcp_servers[]`）とマーケットプレイスの一覧の native manifest
    （`listing.plugins[].mcp_servers[]`）の MCP サーバーも同じ項目を同じ方法で暗号化し、復元で復号する（#1101）。
    メモリ上の manifest と実際の接続は復号した値を使う。導入時の確認の digest は取得した manifest（メモリ）から
    計算するので、保存の暗号化では変わらない。
  - #1101 より前に平文で保存した行も読める（`enc:v1:` で始まらない値は平文として扱う）。起動時の復元で平文の秘密を
    見つけたら、その行を暗号化して保存し直す（暗号化済みの値は作り直さない。署名鍵が無い・保存できないときは平文のまま
    読み続け、起動は止めない）。
- 1 worker・`in_process` の前提は変えない（checkpoint は process 内の状態を丸ごと書くため）。

### 5.1.1 保存先の読み込みと再試行（#853 / #1212）

DB に接続できないあいだも backend は起動する（RAG / NL2SQL と同じ。#1212）。Run の repository は module の import 時に
作らず（`runtime.runtime_repository` は遅延の参照。`get_runtime_repository()`）、`app.features.agent.storage_bootstrap` が
Run の repository → 定義の復元 → 履歴の準備（評価の整理・Run の事実の backfill）の順に 1 回だけ読み込む。

- 起動時（lifespan）はバックグラウンド（daemon thread）で読み込みを始め、済むまで 15 秒ごとに再試行する（ログ
  `agent_storage_not_ready`。済んだらログ `agent_storage_ready`）。自動実行のスケジューラは読み込みが済むまで判定しない。
- 業務の API は、読み込みが済むまで最初の要求で読み込みを試み（`app.api.router.require_agent_storage`。認証の前。API キーの
  認証も読み込んだ定義を使う）、読み込めなければ 503（`error_code=agent_storage_unavailable`、`Retry-After: 15`）。
  DB の状態・システム設定（データベース・システムテーブル・モデル・OCI・アップロード保存先）・認証・ユーザーとロールの API は待たない。
  画面は DB ゲートで案内し、DB に接続できるようになれば再起動せずに読み込む。
- DB に接続できない（`is_oracle_connection_error`）ときは memory に縮退しない（`AgentRuntimeStorageUnavailableError`）。
  済んだ段階は持ち越す。失敗の直後 5 秒は接続を試さずに同じ理由を返す（wallet の接続記述子の `retry_count` /
  `retry_delay` で 1 回の接続に 1 分以上かかることがあるため、要求ごとに接続を待たない）。別のスレッドが読み込んで
  いるあいだの要求は 10 秒まで待ち、超えたら 503。
- 定義の読み込みの接続以外の障害（権限など）は記録して先へ進む。Run の repository の接続以外の障害（明示した Oracle の
  checkpoint の破損・SQL・権限）は 503 で返す（checkpoint の破損は直し方の文、それ以外はログ `agent_storage_load_failed`
  への案内）。
- runtime-dispatcher（別 process）は最初の claim で repository を作り、作れなければ下の backoff で続ける。

保存先の 1 件の不整合で読み込みを止めない。読み込み（Oracle の checkpoint・file の snapshot・
dispatcher の claim）は `runtime.load_snapshot_tolerant` で、snapshot を JSON として読み、Run・業務 Agent を 1 件ずつ検証する。

- **直す**（`_repair_run`。待ちを終わらせる方向だけで、承認・再開の方向には直さない）: 終わった Run に残った pending の承認 →
  `cancelled`（`decided_by="system:storage-repair"`、承認待ちの step も `cancelled`）、承認待ちなのに pending の承認が無い Run →
  `failed`（`runtime.inconsistent_state`）、Event / step / 承認の `run_id` のずれ → 入っている Run に合わせる、無い承認を指す
  step → 参照を外す。直した内容は Run の `runtime.event`（`payload.source="storage_repair"`）とログ
  `agent_runtime_snapshot_run_repaired` に残す。
- **退避する**: schema に合わない・id が空 / 重複・直しても整合しない（承認が無い step を指すなど）Run・業務 Agent は読み込まず、
  元の JSON と理由を snapshot の `quarantined` に入れる（ログ `agent_runtime_snapshot_record_quarantined`）。次の保存でも
  `quarantined` は残り、バックアップの書き出しで中身を確かめられる（黙って消さない）。
- 直した・退避したら、読み込みの直後に保存する（失敗しても起動は止めない）。件数は `GET /api/runtime/storage` の
  `repaired_runs`（この起動で直した Run）・`skipped_runs` / `skipped_agents`（退避している数）と「保存先」のカードに出す。
- snapshot 全体が読めない（JSON の破損・object でない・未対応の版）ときだけ止める。`auto` は checkpoint を上書きしないよう
  memory にし（定義の store も memory にそろえる）、`reason=checkpoint_invalid` で案内する。明示した `oracle_*` / `file` は、
  直し方（行・ファイルを退避して削除するか、バックアップを戻す）を書いた例外で業務の API を止める（データの保護）。
- 利用者が明示的に行う復元（`replace_snapshot`）は今までどおり厳密に検証して拒否する。
- runtime-dispatcher は 1 回の claim・実行の失敗で止まらず、ログを残して poll の間隔から 2 倍ずつ（上限 60 秒）待って続ける。

### 5.2 Run の事実と集計（#794）

- 利用状況（#772）・フィードバック（#774）は、Oracle の構成では `AGENT_RUN_FACTS`（migration 007）を SQL で集計する。
  期間は 7 / 30 / 90 / 180 / 365 日。日は `FROM_TZ(CREATED_AT, 'UTC') AT TIME ZONE :timezone`（画面のブラウザの
  タイムゾーン）で区切り、対象は Run の一覧と同じく利用できる業務 Agent だけ。フィードバックの一覧は
  `offset` / `limit` でサーバー側でページングする（新しい順。`RATED_AT DESC, RUN_ID DESC`）。
- 1 Run = 1 行（業務 Agent・版・利用者・状態・起点・モデルの利用量・本人と管理者の評価・質問・回答・日時）。
  日時は UTC の `TIMESTAMP`（タイムゾーンなし）。Runtime repository が作成・状態の変化・利用量・評価の時点で
  事実をキューに入れ、バックグラウンドのスレッドがまとめて MERGE する（`run_facts_store`。Run を待たせない・止めない。
  失敗はログ `agent_run_facts_not_saved` に残して捨てる）。起動時に Runtime repository の全 Run を backfill する。
- テーブルが無い・DB の障害のあいだは、メモリの Run の集計に戻す（応答の `source` が `memory`。画面に集計元を出す）。
  memory / file の構成は保存せず、メモリの Run を集計する（`source=memory`）。
- 品質評価（#776）の job は件数（旧 50 件）ではなく期間（終わった job を 365 日。安全のため 2,000 件まで）で残し、
  `GET /api/evaluations` を `offset` / `limit` でページングする。前回との比較は残っている全 job から探す。

## 7. Snapshot migration

Snapshot v2 は runs/agents を持つ（旧版の `control_plane_state.runtimes/bindings`（#754）と `memory`（#756）は読み込んでも使わない）。

- v1 tool を一意に対応できる Skill へ推定。
- 変換不能 tool があれば `migration_required=true`, `enabled=false`。
- 既存 Run は model default により `runtime_id=legacy-native`。
- 旧エンジンの Memory・v1 Run（`X-Agent-API-Version: 1`）・planner は #756 で削除した。

## 8. UI information architecture

サイドナビは上に一般の利用者の画面（AI 活用：チャット / 実行履歴 / 承認）、その下に管理者の画面（Agent 構築：業務 Agent / スキル / 自動実行 / マーケットプレイス、改善・運用、セキュリティ設定、ユーザーとロール、運用設定、システム設定）を置く（#791。構成は docs/frontend-page-archetypes-spec.md）。Agent 画面では指示・Skill・モデルを選ぶ。

チャット（`/chat`。#768）は業務利用者の入口。使ってよい Agent を選び、会話の履歴（既定で閉じ、会話の欄の上端の行の左端の
開閉ボタンで開く。lg 以上は左のパネル、未満は side sheet。#889）・会話・入力欄を出す（RAG のチャットと同じ型）。1 往復が 1 Run で、`RunCreateRequest.thread_id` で会話を続ける
（省略すると新しい会話。`RunState.thread_id` に入る）。組み込み Runtime は同じ会話の完了した前の Run の質問と回答
（`kind="answer"` の成果物）を直近 10 往復までモデルの入力に付ける。会話の一覧・詳細は `GET /threads`・
`GET /threads/{thread_id}`（作った利用者だけ。別の利用者・別の Agent の会話は 404 で、続けることもできない）。
回答の処理の段階（画面の `ChatProgress`。#1359）は 3 製品共通の追記型のイベント（`pr_backend_core.chat_progress`）で、
repository が Run のイベントを足すたびに（Run の lock の中で）`features/agent/chat_progress.py` が Run の状態から照合して
`RunState.progress_events` に記録する（変わらなければ記録しない。checkpoint の snapshot に入るので保存先に関係なく残る）。
段階は「進め方の検討（`plan`）」→ ツールの呼び出し（`tool:<名前>`、2 回目から `#n`）→ 承認待ち（`approval_wait`。承認を
求めた回ごと、ツールの前。ツールは承認まで待機中）→「回答の作成（`respond`、2 回目から `#n`。その後にツールを呼んだら
進め方の検討の完了に変える）」、終わりは完了・失敗・停止の終端。画面は会話の取得に入っている記録に加えて、実行中は
`GET /runs/{run_id}/progress/stream`（SSE。`Last-Event-ID` / `since` の続きから。承認待ちの間も heartbeat）で受け取り、
使えなければ `GET /runs/{run_id}/progress?since=` の polling に縮退する（共通の `useChatProgressStream`）。
支援タスクの状態（#1243）: 組み込み Runtime は Run の終わり（承認待ちで止めるときも）に、ツールの呼び出しから
支援タスクの状態を成果物（`kind="support_task"`・「支援タスクの状態」、`schema_version` 1）に残す。項目は会話・持ち主・
目的（会話の最初の質問）、分かっている条件（`rag_search` に渡した `conditions` と出力の `guide.known_conditions`。
値・出所・時刻、上書きした古い値）、確かめ中の問い（`clarifications`）、使った業務ガイド（id・版）、残った不足（`gaps`）、
根拠の参照（document_id・chunk_id・chunk_set_id・ファイル名。本文は持たない）、予算の消費（Run と会話の通しの
ツール・RAG の呼び出しの回数と時間）。同じ会話の次の Run は、同じ持ち主の前の完了した Run の状態を読み、短い
「支援タスクの状態」として指示の末尾に足す（条件は聞き直さず `conditions` に入れ、新しい発言の値で上書きする）。
状態は補助で、正本は RAG の回答と根拠。ツールを呼ばず、引き継ぐ状態も無い Run には残さない。
予算は `AGENT_MAX_RAG_CALLS_PER_RUN`（既定 6〔#1345。5 段の多段の質問と 1 回の言い換え〕。Run ごとの `rag_search`・`rag_retrieve_evidence`。
本文を読む `rag_read_source`・`rag_outline`・`rag_read_document` は軽いので数えない）と
`AGENT_MAX_TOOL_CALLS_PER_TASK`（既定 60。同じ会話の通しのツールの呼び出し）。超える呼び出しは実行せず、ツールの結果
（`error_code="budget_exceeded"`、step は失敗）でモデルに知らせ、Run は失敗にしない。消費は Run の step から数えるため、
承認の後の再開で 0 に戻らない。失敗・取消の Run は状態を残さないが、その消費（実行したツールと、結果を受け取る前に
止まった呼び出し）は、前の完了した Run の状態に足して次の Run の会話の通しの予算に数える（#1277。失敗する Run を
繰り返して上限を超えさせない）。
回答の経路（#1283。RAG から Agent へは呼ばない・案内しない）: `rag_search` の結果の `outcome` が
`needs_environment_data`（資料だけでは確定できず、現場の値・記録の確認が要る）のとき、Control Plane がモデルへの
結果に `next_step` を足す（`rag_next_step`。記録する step の結果は RAG の結果のまま）。この Run に現場のデータを確かめる
道具（RAG 以外の MCP 接続のツール）があれば `action="continue_with_tools"`（`tools` に名前）、無ければ
`"answer_with_confirmations"`。スキルの指示は `next_step` に従うことを書く。支援タスクの状態の `route` に、この Run の
経路（`rag` / `rag_then_tools` / `tools` / `none`）・理由（最後の `rag_search` の対応）・`environment_data_required`・
続けた道具（`continued_with`）を Run の step から決定的に残す（`run_route`）。`outcome` が `needs_clarification` の
ときは `action="ask_clarification"`（`questions` に確かめる問い）を足し、手順・分岐ごとの答えより先に問いを返させる
（#1322）。
多段の質問（#1345・#1351。RAG は部品を出し、組み立ては Agent が行う。planner・workflow engine は作らない〔#756〕）: スキル `business_rag_research` の指示が、問いを段に分け、段ごとに前の段で分かった実体で `rag_retrieve_evidence` を呼び、
段の query は質問全体ではなく「実体（正式名・略号・ID）＋引く属性」だけにし（`evidence_limit` は既定より小さくしない）、
資料が台帳・一覧で確かめると示したらその実体でその台帳・一覧を引き、同じ段で 2 回言い換えても根拠が出なければその段を
確かめられなかった点として次へ進み、比べる質問は実体ごとに引き、同じ文書の中の参照は `rag_outline` → `rag_read_document`
で読み、最後に質問全体で `rag_search` を呼び直さずに集めた根拠で答える手順を書く。Control Plane は根拠を集める・読む
ツールの結果に、本文の「第 N 章を参照」「別表 N 参照」など同じ文書の別の箇所への参照（`references`。文書・参照・読み方。
`text_references`）と、この Run で残る RAG の検索の回数（`rag_calls_remaining`）をモデルへの結果に足す（記録する step の
結果は RAG の結果のまま）。
段の検索の決定的な補助（#1351。#1335 の評価で、Agent が `evidence_limit=5` を選んで答えの chunk が上限で切れ、同じ話題の
言い換えを繰り返して予算に達した）: (1) モデルが `rag_search`・`rag_retrieve_evidence` の `evidence_limit` を既定（ツールの
入力 schema の `default`。RAG の契約の `rag_search` は 12、`rag_retrieve_evidence` は検索する件数と同じ 20〔#1365〕）より
小さくしたら、Control Plane が既定に引き上げて呼ぶ（step には送った値を残す。既定より大きい値は変えない）。(2) 同じ Run で、同じツール・同じ範囲（検索・回答プロファイル・ナレッジベース・
フィルター・条件）の成功した検索と query が同じかほぼ同じ（NFKC・大文字小文字・空白・句読点と記号・助詞を除いて、
文字の 2-gram の Jaccard 係数が 0.8 以上。`repeated_query_note`）なら、モデルへの結果に繰り返しを止める案内
（`repeated_query`。回数・似た query・次の手）を足す（記録する step の結果は RAG の結果のまま。呼び出しは止めない）。
(3) 台帳の行の略号・区分（#1365。#1335 の再評価の D で、69 Run のうち 44 Run が 1 回の検索で答え、台帳の行の「担当部署: 経」の
「経」を引く次の段をしなかった。略号の表は 1 回目の結果に前後の文脈として 21〜142 位にしか出ない）: 根拠を集めた結果
（`rag_retrieve_evidence`・`rag_search`）に、この呼び出しの query の実体（正式名・略称・ID。NFKC・大文字小文字・空白と記号を
除いて比べる）が値に当たる台帳・一覧の行（表計算の 1 行の記録。根拠の `content_kind=record`、または表計算の 1 行の場所）が
あり、その行に略号・区分のような短い値（2 文字以下。数字だけの値は除く）があって、この Run の検索の query にまだ語として
出ていなければ、モデルへの結果にその値と意味を引く次の段の案内（`record_codes`。項目・値・行の実体・根拠と `next_step`。
5 件まで）を足す。案内だけで、呼び出しも回答も止めない。
業務ガイドの照合（#1321・#1322）: 根拠を集めるだけの `rag_retrieve_evidence` は業務ガイド（確かめる条件・分岐・影響範囲）を
見ないため、モデルが業務ガイドを引かずに根拠を集めると、確かめる条件があっても分岐ごとに答えてしまう（#1317 の実環境の
評価）。モデルがその Run でその接続の `rag_lookup_guides`・`rag_search` を呼ばずに `rag_retrieve_evidence` を呼んだら、
Control Plane が同じ接続の `rag_lookup_guides` を 1 回呼ぶ（質問は利用者の質問と根拠を集めた質問。前の Run で問いを
確かめ中なら前の質問も含める。条件は前の Run までに分かった条件と、根拠を集めた呼び出しの `conditions`。検索・回答
プロファイルは根拠を集めた呼び出しのもの。モデルのツールと同じ境界を通し、step に残す〔trace_id は `guide_check_` で
始まる〕。予算・消費には数えない）。最上位の業務ガイドと次の手を、モデルへの根拠の結果に `guide_check` として足す
（`guide_check_note`。判断が `clarify` なら `ask_clarification`、`handoff` なら引き継ぎ、`branch` なら条件ごとに答える、
`answer` なら分かっている条件に当たる場合の手順だけを答え、係るときだけ影響範囲・承認を示す）。照合の結果
（業務ガイド・分かった条件・確かめ中の問い）は支援タスクの状態にも残す。モデルが自分で `rag_lookup_guides` を呼んだときも、同じ次の手を結果に `next_step` として足す（答える判断なら、業務ガイドは資料の根拠ではないので、根拠を集めて確かめてから答えるよう添える）。
回答の最終の検証（#1246・#1277）: `AGENT_FINAL_VALIDATION_ENABLED`（既定 true）が true のとき、RAG の根拠を使った Run の
回答を保存する前に、Control Plane が（モデルではなく）根拠を返した MCP 接続ごとに `rag_validate_answer` を呼ぶ。渡すのは
その Run の質問・回答と、その接続の `rag_search` / `rag_retrieve_evidence` が返した根拠と `rag_read_source` / `rag_read_document`
で読んだ chunk の参照（#1345。多段の質問は段の事実を読んで確かめるため。回答が引用した根拠（`chunk_id` を回答に
書いたもの）を先に、残りは新しい呼び出しから順、重複なし、接続ごとに 30 件まで。#1364。前の段の根拠を上限で
落とさない）と、その接続の最も新しい `rag_search` の `requests`・`gaps`・業務ガイド（`guide`。
無ければ同じ接続の最も新しい `rag_lookup_guides` の最上位。判断が `answer` / `branch` で、検索・回答プロファイルが
分かるときだけ。分かっている条件を `conditions` に入れる）で、RAG の決定的な検査（要求の充足・手順の順序と分岐・
影響範囲・承認。#1276）も動かす。呼び出しはモデルのツールと同じ境界（ポリシーの「拒否」・監査・Run の利用者の
サービストークン・step）を通し、「承認」は待たない。予算には数えない。接続ごとの判定は段落（`answer_quote`）ごとに
まとめ（どれかの接続が矛盾と判定すれば矛盾、そうでなくどれかの接続が裏付ければ裏付けあり）、結果は成果物
（`kind="answer_validation"`・「回答の検証」。接続ごとの結果は `connections`）に残す。valid でなければ、根拠で確かめられ
なかった段落を回答から外し（確かめていない手順を公開しない。handoff §12）、外した段落〔80 文字まで〕と理由、古い版・
見つからない根拠の件数を「確かめられていない点」として足す。決定的な検査の error も同じく扱い、手順・影響範囲・
業務ガイドの版の error はどの段落の手順が誤りかを決められないので本文を載せず、要求の error（答えていない要求を
示していない）は本文を残して不足を示す（warning は出さない）。検証そのもの（接続・呼び出し・応答）が失敗したら、または
接続が検証を提供しなければ、`unvalidated` にして回答は消さずに「この回答は検証できませんでした。」を足す。RAG の根拠を
使っていない Run は、Agent が RAG の根拠のツールを持てば `unvalidated`（`no_rag_evidence`）にして「資料と照らし合わせて
確かめていません」を足し、持たなければ `skipped` で回答はそのまま。ただし利用者への確認の質問だけの回答は、
資料の主張を含まないので `skipped`（`clarification_only`）にして注記しない（#1306）。主張ではない段落（見出し・
出典の行〔「セクション:」「ページ:」などの位置のラベル・根拠の ID の括弧を含む。#1317。要素の定位子（`*Locator*: …`）・〔証拠 ID: …〕・文書名と場所の括弧だけの行〔「**システム変更手順書**（第 2章 申請）」〕も。場所の後に本文が続く行は主張のまま。#1370。規則は RAG と同じで、両方のテストが `platform/contracts/answer-passages/citation-lines.json` の同じ事例で確かめる〕・Markdown の表の区切りと
見出しの行〔#1317。表の本文の行は主張のまま〕・利用者への質問・資料に記載が無いことだけを述べる文と答えられないと
言い切る拒答の文〔#1317〕・「確かめられていない点」の節）は、RAG の判定にかかわらず外さない（#1306。`answer_passages`
が決定的に判定し、成果物の段落に `non_claim` を付ける。迷うもの・数量や操作を抱き合わせた文は主張として扱う）。RAG の
`rag_validate_answer` も、見出し・出典の行・表の区切りと見出し・質問は監査せず `claims` に含めない。段落を外した後に
見出し・出典・表の形だけが残れば、それも消す。利用者に現場のデータ・記録（ログ・設定値・明細など）の確認を求める段落
には、判定とは別に `environment_check` を付ける（#1317。主張のまま確かめて外す。回答の対応の「実データの確認」の印）。
回答の対応（#1305）: Control Plane は最終の検証の後、回答の成果物（`kind="answer"`）の `outcome` に、RAG の回答の記録
（AnswerEnvelope）と同じ語彙の対応（`answered` / `conditional` / `needs_clarification` / `needs_environment_data` /
`needs_human` / `insufficient_evidence`）を、モデルを呼ばずに決めて残す（`answer_outcome`。内容は `value`・決めた手がかりの
`basis`・最後の `rag_search` の対応 `rag_outcome`・不足の印 `signals`）。決め方は上から順に、空の回答 → 拒答、確認の質問
だけの回答 → `needs_clarification`、最終の検証が本文をすべて載せなかった → 拒答、回答が最後の `rag_search` に拠る（その後に
根拠を集め直していない）ならその対応（`needs_environment_data` の後に現場のデータの道具が成功したら `answered` /
`conditional`、確認を求められたが質問だけでなく答えたら `conditional`、`answered` でも回答が不足を示せば `conditional`）、
`rag_search` に拠らず業務ガイドの判断が人への引き継ぎなら `needs_human`、それ以外は Agent が自分で組み立てた回答として
段落で決める（主張の段落〔最終の検証で外した段落と、実データの確認を促す段落〔RAG の判定 `data_confirmation` か、
外していない `environment_check` の段落。#1317〕を除く〕が無ければ、実データの確認を促していれば `needs_environment_data`、
それ以外は拒答。主張があり、不足の印〔資料に記載が無い文・拒答の文・主張に添えた質問・「確かめられていない点」の節・
根拠で確かめられない・矛盾として外した段落〔`unsupported` / `contradicted`。確かめが終わらなかった `unassessed` だけなら
付けない。#1317〕・決定的な検査の error・実データの確認・業務ガイドの条件が分からないまま答えた〔`guide_conditions`。
最後の `rag_search` の後の最も新しい `rag_lookup_guides`〔モデルか Control Plane の照合〕の最上位の業務ガイドの判断が
`clarify` / `branch` なのに、確認の質問だけで答えなかった。#1321・#1322〕〕があれば `conditional`、無ければ `answered`）。決められなければ `outcome` を付けずに回答を保存する。業務支援の評価（#1289 の D）は
これで採点する（`rag/evaluation/business-support/README.md`）。モデルに対応を申告させる（構造化出力）方式は、回答の形が
Agent ごとに変わり、申告と本文の食い違いの扱いも要るため採らない。
回答の下に出典（`rag_evidence` の引用）・使ったツール（step）を畳んで出し、承認待ちはその場で承認・却下する
（`agent.approvals.decide` を持つ利用者だけ）。実行中・承認待ちのあいだは次の質問を送れない（前の回答を履歴に含めるため）。
Run は Agent とゴールだけを受け取る（実行先の選択は無い）。Runtime 画面は組み込み Runtime の状態（SDK の版・既定のモデル・
選べるモデル・実行できるか）と、モデル未設定のときの理由と「システム設定 > モデル」への導線を出す。

## 9. Authentication and RBAC

画面は RAG / NL2SQL と同じ共通認証（`PLATFORM_*` のユーザー・ロール・セッション）でログインする。ロールに付ける
Agent の権限（`AGENT_ROLE_PERMISSIONS`）と対象範囲（`AGENT_ROLE_AGENTS`）は
システムテーブル（`app.system_schema`。#751）が作る。capability は従来の viewer / operator / approver / auditor / admin に対応し、
利用者（Cookie のセッション、local はローカル利用者）から `ActorPolicy` を作って Run・監査・承認・成果物・SSE・WebSocket・
`GET /agents` の絞り込みに流す。Cookie のないリクエストは 401（#750 で header / JWT / 外部 policy の認可を削除した）。
検索・回答プロファイルの判定は RAG が Run の利用者のサービストークンで行うため、Agent は検索・回答プロファイルの対象範囲を持たない（#750）。
詳細は [security-rbac.md](security-rbac.md)。

## 10. Non-goals

- 外部の Agent Runtime（OpenClaw / Hermes / DeerFlow など）への配置と adapter（#754 で削除）
- 独立した planner / workflow engine
- 外部 archive の Marketplace install
