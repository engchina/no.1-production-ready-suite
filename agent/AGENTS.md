# AGENTS.md — Production Control Plane for AI Agents

> Agent（`agent/`）固有のルール。GitHub 運用・Issue / PR 規約・CI・デザインシステム・共通の技術方針（言語・AI / DB の分担・シークレット・UI 変更の検証）は、root の [../AGENTS.md](../AGENTS.md) を先に適用する。ルールを変えるときはこのファイル（共通ルールは root）を編集する。
> Issue の label は `product:agent`、PR title の scope は `agent`。

## プロジェクト目標

**業務・業界の Agent Platform — 業務 Agent を作り、Skill と MCP（RAG・NL2SQL・その他）を安全に使わせる。**

業務 Agent の定義、Skill、実行（組み込み Runtime）、共通の Run・Event・Artifact・Approval・Audit を 1 つの製品で持つ。主要概念と依存方向（`業務 Agent → Skill → ツール（MCP / resource）`）:

1. **業務 Agent** — 業務の指示、説明、有効状態、`skill_ids`、使うモデル（`model_id`。空なら既定のテキストモデル）、データの範囲（`data_scopes`。RAG / NL2SQL の使えるプロファイルと既定。空なら利用者が使えるすべて。#1378）。
2. **Skill** — AgentSkills 互換の指示本体。必要なツール（MCP / Control Plane のツール）を宣言する。

## アーキテクチャ不変条件

- 実行は Control Plane の**組み込み Runtime**（`features/agent/builtin_runtime.py`）。
  - SDK は OpenAI Agents SDK（`openai-agents`。版を固定し、Dependabot の PR で上げる）。
  - モデルは OCI Enterprise AI の Responses API（システム設定 > モデルの接続）。別の LLM provider は組み込まない。
  - SDK の tracing は常に無効にする（業務データを外部へ送らない）。
- 外部の Runtime（OpenClaw / Hermes / DeerFlow など）・Binding・Docker の Runtime は持たない（#754）。
- 指示は「Agent の指示 + 割り当てた Skill の指示」。ツールは Skill の requirement が必要とするものだけを function tool として渡す。`server_id="control-plane"` は `tool_registry` のツール、それ以外は MCP 接続（`rag` / `nl2sql` / 登録した接続）の `tools/list` のツール（名前は `<接続>__<ツール>`）。Skill の指示は MCP のツールを素の名前（`rag_search`）で書いてよく、Run のときにモデルへ渡す名前へ書き直す。モデルが無い名前のツールを呼んでも Run は落とさず、呼ぶ名前をモデルへ返して呼び直させる（読み替えて実行はしない。#1303）。
- ツールの実行は `tool_registry.invoke`（ポリシー・ガードレール・監査・成果物）を必ず通す。
  - ポリシーが「拒否」のツールはモデルに渡さない。
  - 「承認」のツールは SDK の `needs_approval` で中断する。中断した Run は `waiting_approval` にし、SDK の状態を Run に保存する。
  - 承認がすべて決まったら状態を復元して再開する。却下したツールは実行しない。
- 業務 Agent は版を持つ。編集は下書きで、「公開」した版だけが利用者の Run に使われる。「この版に戻す」で前の版を公開し直す。下書きで実行できるのは Agent 管理（admin）だけ。
  - 公開の版は使うスキルの中身（指示・説明・MCP のツール・中身の hash）を固定し、公開中の版の Run は固定した中身で動く（下書きは今のスキル。組み込みのスキルも固定する）。スキルが変わった業務 Agent は「スキルが更新されています」を出し、再公開で取り込む（#1855）。
- Run は Agent を選ぶだけで作れる（Binding の選択は無い）。開発は API のプロセスで実行（`in_process`）し、本番は Oracle checkpoint の row lock と Run lease を使う runtime-dispatcher（`python -m app.features.agent.runtime_dispatcher`）。
- 保存先: Run・業務 Agent と、画面・API で変えた定義（Skill・MCP 接続・ツール権限）は `AGENT_RUNTIME_REPOSITORY_BACKEND` に従い、Oracle では共通の `PLATFORM_ORACLE_*` で `AGENT_RUNTIME_*` / `AGENT_CONTROL_PLANE_ITEMS` に保存する（テーブルはシステムテーブルが作る。アプリは DDL を実行しない）。
  - MCP 接続の秘密は `app.secret_box` で暗号化する。`.env` の宣言は保存しない。
  - 既定の `auto` は、DB の設定がそろっていれば `oracle_checkpoint`、無ければ `memory`（保存しない。再起動で消える）を起動時に選ぶ（`storage_backend`）。保存先の 1 件の Run の不整合では起動を止めず、Run 単位で直す・退避する（`quarantined`）。
  - DB に接続できないあいだも起動し（Run の repository は import 時に作らない）、接続できた時点で再起動せずに読み込む（`storage_bootstrap`。それまで業務の API は 503。docs/agent-control-plane-design.md §5.1.1）。
  - Oracle は Run・業務 Agent を 1 件 = 1 行（`AGENT_RUNTIME_ENTITIES`）で持ち、変えたものだけを書く（#1939。migration 016 が以前の全体の snapshot を移す）。repository の変更は変えた Run・業務 Agent に印を付け（`_mark_run_changed` など。Event を足すと自動）、書き込みは lock を放した後に行う（event loop の上の変更はバックグラウンドで書く）。`router.py` の `async def` の handler は repository を `run_in_threadpool` で呼ぶ（`tests/test_router_threadpool.py`）。
  - 書くプロセスは head（`AGENT_RUNTIME_CHECKPOINTS` の行の版 `VERSION_NO`・`WRITER_ID`）を lock し、前回の同期の後に別のプロセスが書いた行を取り込んでから書く（`runtime_checkpoint_store`。#1916。片方の書き込みがもう片方の更新を消さない。両方が同じ 1 件を変えたときだけ後の値）。
  - テストは `tests/conftest.py` で memory に固定する。保存先の状態は `GET /api/runtime/storage` が返し、「実行環境」の「保存先」のカードと、定義・実行を作る画面の先頭の `NonPersistentStorageNotice`（warning の Banner）が案内する。
- 利用状況・フィードバックの集計: Oracle の構成は Run の事実（`AGENT_RUN_FACTS`。1 Run = 1 行）を SQL で集計する（期間は 365 日まで。書き込みは `run_facts_store` のバックグラウンドの MERGE で、Run を止めない）。memory / file はメモリの Run を集計する（docs/agent-control-plane-design.md §5.2）。
- Agent はスキルだけを持つ。プラグイン（Skill・MCP・resource の配布単位）とマーケットプレイスは作り直さない（#1798・#1803 で削除）。スキルはビルトイン・`AGENT_SKILLS_DIR` の SKILL.md・`AGENT_SKILLS_DEFINITIONS_JSON`・画面で入れ、MCP 接続は直接登録する。
- SKILL.md と同じディレクトリの `references/` の下のテキスト / Markdown は、スキルの参照文書（`references`）として読む（Agent Skills の progressive disclosure。symlink を辿らず、ディレクトリの外を読まず、件数・大きさに上限）。指示には索引だけを入れ、その Run にだけ `skill_reference_read` を渡して必要な部分を読む。読み取りも `tool_registry.invoke` のポリシー・監査を通す。`scripts/` などの同梱のコードは読まず・実行しない。
- スキルは Agent Skills の形（`<name>/SKILL.md` ＋ `references/` の zip）で書き出せる（`GET /api/skills/{skill_id}/export`・画面のスキルの操作・MCP の `agent_skill_export`。`features/agent/skill_export.py`。#1874）。組み込みのスキルは `skills/` と同じ内容、画面のスキルは frontmatter を作り、`skills-ref` の検証を通す。
- Prompt / Workflow / Template は非実行・版管理 resource。独立 Workflow engine を作らない。

## MCP 境界

- **Agent は外部の MCP クライアントに業務 Agent へのチャットを公開する**（`POST /api/mcp`。`features/agent/mcp_server.py`。#1874）。認証は共通の API キー（使える製品に `agent`）だけで、キーを発行した利用者の権限・対象範囲で判定し、キーに業務 Agent の範囲があればさらに絞る（範囲の外は 403）。ツールは `agent_list` / `agent_chat` / `agent_get_run` / `agent_answer_confirmation` / `agent_skill_export`（契約は `platform/contracts/mcp/agent-tools.json`）。
  - MCP から作った Run（`metadata.source=mcp`・キーの ID と名前）は、無人の実行と同じく承認の要るツールを呼ばない（`mcp.approval_required`。#1813 と同じ流れ）。回答前の確認（#1839）は構造化して返し、`agent_answer_confirmation` で答える。
  - Run・会話の見え方は画面と同じ（キーの持ち主が system_admin なら他の利用者の Run を読み、会話を続けられる。Run はキーの持ち主として実行する。#1928）。
  - キーごとの直近 24 時間の Run の数の上限（`PLATFORM_API_KEY_RUNS_PER_DAY`。超えたら 429 相当のツールのエラー）。1 分あたりの呼び出しの回数は共通の認証が数える。
  - RAG は Agent を呼ばない（Agent の MCP を呼ぶのは外部のクライアントだけ）。サービストークンでの Agent の MCP の呼び出しは持たない。
- RAG / NL2SQL / 外部 MCP は「MCP 接続」1 つの仕組みで管理する（旧「外部 RAG」「外部 NL2SQL」「外部 MCP」は持たない）。RAG / NL2SQL は標準の接続 `rag` / `nl2sql`（画面の名前は「ナレッジ検索（RAG）」「データ問い合わせ（NL2SQL）」。認証はサービストークンで、削除・名前・認証方式の変更はできない）。URL は `AGENT_EXTERNAL_RAG_MCP_URL` / `AGENT_EXTERNAL_NL2SQL_MCP_URL` で、配備がこれを与えた接続は URL も変えられない（API は 400、`base_url_locked`）。空の構成（ローカルの開発・その製品を配備しない構成）だけ画面で URL を設定できる。外部の MCP は認証方式（なし / API キー / OAuth client credentials / サービストークン）を選んで追加する。
- MCP 接続のツールは、組み込み Runtime から `tool_registry.invoke`（`definition` / `handler` を渡す）を通してだけ呼ぶ（policy・masking・監査を再利用する）。承認の要否は MCP の `readOnlyHint` とツール権限（`<接続>__<ツール>` の名前）。外部 API key・token を snapshot、API、ログ、Artifact に出さない。
  - 例外は画面の操作の「RAG の図の根拠を開く」（`GET /api/runs/{run_id}/figure-url`）だけで、Run のツール呼び出しではないため `tool_registry` を通さず、**画面を見ている利用者**を `sub`・`purpose=figure_url` にしたサービストークンで RAG の `rag_read_source`（`include_image_url`）を呼び、返った短命の URL を保存しない（`features/agent/rag_figures.py`）。
- サービストークンは呼び出しごとの `issue_service_token`（`sub` = Run の利用者 `RunState.created_by_user_uuid`、なければ `AGENT_MCP_SERVICE_USER_LOGIN_ID` のサービス利用者。`aud` = 接続の audience）。承認後の実行も承認者ではなく Run の利用者で呼ぶ。業務 Agent にデータの範囲があれば、その接続の範囲の ID を claim `profile_ids` に入れ、呼び先でも強制させる（#1379。範囲なしなら付けない）。
- 再試行は、送信前の失敗と 429 / 503 は全メッセージ、502 / 504 は手順と読み取り専用（`readOnlyHint`）のツールだけで、書き込みのツールは呼び先に届いた後の失敗では再送しない。待ちは backoff + jitter（`Retry-After` に従う）、呼び出し全体は接続の timeout に収める（docs/agent-control-plane-design.md §4.1）。

## 技術スタック（Agent 固有の分）

- Backend は httpx と Oracle（`python-oracledb`）、Frontend は共有 UI に加えて shadcn/ui。
- 状態は開発で memory / file、本番で Oracle AI Database。外部 queue・外部 vector DB は追加しない。
- 観測は Prometheus・OpenTelemetry・Langfuse。業務データの原文と secret は trace に送らない。

## UI/UX

- ナビは上に一般の利用者の画面、下に管理者の画面を置く（RAG / NL2SQL と同じ）。**AI 活用**: チャット / 実行履歴 / 承認 → **Agent 構築**: 業務 Agent / スキル / 自動実行 → **改善・運用**: 品質評価 / フィードバック / 利用状況 / 監査ログ → セキュリティ設定 → ユーザーとロール → 運用設定 → システム設定。既定の入口はチャット。
  - Tools を独立ナビに戻さない。ツール権限はナビに出さない。Planner・Memory・Command Policy・Runtime Safety の画面とコマンド実行ツール（`sandbox_command_run`）は作り直さない（#756 で削除）。
  - **運用設定**: システムテーブル（先頭）/ 実行環境（組み込み Runtime の状態）/ MCP 接続。**システム設定**: OCI 認証 / アップロード保存先 / モデル / データベース / 外観と証明書（3 製品で共通。platform の共有パッケージ）。
- 画面・ナビの文言に「Control Plane」「Runtime」のような基盤の用語を使わない（識別子・URL・権限コードは変えない）。用語は Run → 「実行」、Skill → 「スキル」、組み込み Runtime → 「実行環境」（表は docs/frontend-page-archetypes-spec.md §1）。
- チャットは業務利用者の入口で、1 往復が 1 Run。同じ会話（`thread_id`）の前の質問と回答と使ったツールの名前を、組み込み Runtime が 3 製品共通の予算（`PLATFORM_CHAT_HISTORY_*`。backend-standard.md「チャットの会話の履歴を LLM に渡す量」）で未信頼のデータとしてモデルへ渡す。会話は作った利用者（持ち主）だけが読み、続けられる（同じ Agent に限る）。system_admin は全員の会話を見て、本人と同じく続けられる（#1889・#1928）。
- 「AI 活用」（チャット・実行履歴・承認）は、system_admin は全員の Run・会話、それ以外は自分の Run・会話だけ（3 製品共通。ほかの利用者の Run は 404。承認の権限を持つ人も自分が実行した Run の承認だけ。#1889。docs/security-rbac.md §4.1）。
  - 実行履歴の行は実行した人と会話の持ち主の両方に出る（自分の会話で system_admin が実行した Run も出す）。行・詳細・会話の Run に `executed_by_me`・`executor_display_name`・`can_decide`（詳細と会話は `owned_by_me`・`conversation_owner_display_name` も）を返し、`GET /runs?executor=all|mine|others` で絞る。画面は「実行者」の列・「〈名前〉 が実行」・実行者の絞り込み・詳細の「実行者」「会話の持ち主」（#1954）。
  - 承認・再開・Agent 自身の確認への回答は実行した人と system_admin だけ（持ち主でも system_admin が実行した Run は 404）。持ち主の画面は待っている相手（「システム管理者（〈名前〉）の承認・回答を待っています」）を出して操作を出さず、チャットの「自分で質問し直す」で止めて同じ質問を持ち主の権限で送り直せる。system_admin が他の利用者の会話を開くと、入力欄の上に「この会話の持ち主にも、回答と途中の結果が見えます」を出す（#1954）。
  - system_admin は他の利用者のデータに本人と同じすべての操作ができる（続きの質問・停止・回答前の確認・評価・取消・再開・再実行・承認。#1928）。実行するのは操作した system_admin 自身（新しい Run の `created_by_user_uuid`・権限・業務 Agent の範囲・サービストークンの `sub`）で、会話の持ち主（`thread_owner_user_uuid`）は変えない。操作した人を Run・イベント（`cancelled_by` / `decided_by`）・評価（`feedback.user_uuid`）に残し、持ち主以外が送った往復は会話に送った人の表示名（`sender_display_names`）を出す。外部の MCP もキーの持ち主が system_admin なら同じ（キーの業務 Agent の範囲はそのまま）。
- ログインと権限: 共通認証（`AGENT_AUTH_MODE=production`）。製品固有の権限管理は「セキュリティ設定」。メニュー権限は `menu.*`、実データの閲覧・操作は capability（`agent.runs.view` / `agent.runs.operate` / `agent.approvals.decide` / `agent.audit.view` / `agent.admin`）。権限カタログと API の manifest の正本は `backend/app/security/permissions.py`、説明は [docs/security-rbac.md](./docs/security-rbac.md)。
- 業務 Agent の編集画面は指示・スキル・モデル・データの範囲を選ぶ（範囲の強制は組み込み Runtime の tool handler。モデルの指示に頼らない。docs/agent-control-plane-design.md §2）。実行（Run）は業務 Agent と目標だけで作る（実行先の選択は無い）。
- 空、読込、エラー、モデル未設定（組み込み Runtime が実行できない）、承認待ちを明示する。
- 各ページの型（A〜D）の割り当て、離脱ガードの対象画面、作業状態として残す field は [docs/frontend-page-archetypes-spec.md](./docs/frontend-page-archetypes-spec.md) に書く。
- サイドバーのアカウント領域（ユーザー・ロール・ログアウト・テーマ切替）は `SidebarAccountFooter` を `Sidebar` の `footer` に渡す。ダークテーマ（`data-theme="dark"`）に追従するため、色の直書きを残さない。
- lint は `frontend/eslint.config.js` が `../../platform/docs/design-system/adherence.oxlintrc.json` を import する。
- Playwright は `desktop` と `mobile-375`（Pixel 5、375×812、タッチ）の 2 project。spec の中で viewport を回しているテストと、画面幅に関係しない spec は `mobile-375` では実行しない（`playwright.config.ts`）。PR の CI の `mobile-375` は、さらに `@responsive` の tag の test だけ（全件は nightly。直下の AGENTS.md「UI 変更の検証」）。

## API

- Snapshot 正式版は `agent-control-plane.snapshot.v2`。v1 `tool_names` は Skill を安全に推定する。変換不能 Agent は `migration_required=true`、無効。
- `POST /api/runs` は `agent_id` と `goal`（会話の続きは `thread_id`、確認の取り消しの操作は `clarification_cancel`。#1441・#1445）を受け取り、組み込み Runtime で実行する。旧エンジン（v1 の Run・`tool_calls`・planner・Memory・`X-Agent-API-Version: 1`）は持たない。
- Skill は instructions と MCP ツールの許可リスト（`mcp_requirements`）だけを持つ。ToolCall のテンプレート（`tool_calls`）・`POST /api/skills/plan`・`agent_skill_run` は持たない。

## セキュリティ

- manifest ID を検証する。
- 業務 Agent が使う Skill の削除は `409`。
- RBAC は viewer / operator / approver / auditor / admin を維持する。
- 画面のログインは共通認証（Cookie のセッション）。capability は 5 ロールに対応し、利用者（local はローカル利用者）から `ActorPolicy` を作って router の既存の判定に流す。local でもユーザー・ロールは Oracle の `PLATFORM_*`。Agent 独自の header / JWT / 外部 policy の認可（旧 `AGENT_RBAC_*`）は持たない。対象範囲はエージェントだけで、検索・回答プロファイルの判定は RAG に任せる。
- 新しい API は必ず権限 manifest（`app/security/permissions.py`）に登録する（登録外は 403。完全性テストがある）。API は HTTP だけで、push は SSE、操作は REST にする（WebSocket は持たない。#2260）。
- 承認の決定者（`decided_by`）は Cookie の利用者から決め、request の値を使わない。

## 開発・検証

- 機能変更と同時に pytest / Playwright を追加・更新する。
- 完了前に `scripts/check-all.sh`（backend の ruff format / ruff check / mypy / pytest・bandit、frontend の lint / build）を実行する。ローカルの既定は Playwright と pip-audit を省く。関係する spec だけ `SKIP_E2E=0 E2E_ARGS="e2e/<対象>.spec.ts" scripts/check-all.sh`、全部は `FULL=1`。
- CI の job は `Agent / Backend`・`Agent / Frontend`・`Agent / E2E smoke`。smoke に入れる spec は `ci.yml` の `agent-e2e` に書く（ログイン・認証・Run の stream・Runtime の主導線など、壊れると全体に響くもの。合計が CI でおおむね 1 分に収まる量）。
- 組み込み Runtime のテストは SDK の `ScriptedModel`（`agents.testing`）でモデルを台本にし、OCI へは接続しない。実環境の OCI Enterprise AI での確認は手動 / ステージングで行い、PR に分けて書く。
- 既存のユーザーの変更を尊重し、無関係な差分を戻さない。
- **worktree で Agent の backend を主の `.env`（`platform/.env` の `PLATFORM_ORACLE_*`）のまま起動しない**（#1916）。主の dev server（8020）と同じ DB の Run・業務 Agent の保存先（`AGENT_RUNTIME_CHECKPOINTS` / `AGENT_RUNTIME_ENTITIES`）に 2 つ目のプロセスが書く。書き込みは head を lock して別のプロセスの変更を Run・業務 Agent の単位で取り込むので更新は消えないが、起動した側は主の dev server の実行中の Run を「中断した Run」として失敗にし（#1894）、互いのメモリは次に書くまで古いまま。worktree の確認は pytest（保存先は memory・偽の Oracle）と CI の実 Oracle の結合テストで行い、実画面で要るなら主の dev server を使う。どうしても起動するなら、別の DB（別の `PLATFORM_ENV_FILE`）か `AGENT_RUNTIME_REPOSITORY_BACKEND=memory` にする。重なったときはログ `agent_runtime_checkpoint_concurrent_write`（相手の `other_writer_id`）が出る。

## 主要ディレクトリ

```text
backend/app/features/agent/
  builtin_runtime.py        組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI）
  runtime_dispatcher.py     claim/lease dispatcher（本番の別プロセス）
  runtime.py                共通 Run/Event/Artifact/Audit と legacy history
  skills.py                 Skill registry と組み込みのスキル（repo 直下の skills/ を読む。#1816）
  skills_loader.py          SKILL.md と references/ の読み込み
  router.py                 REST / SSE / WS
backend/app/security/       共通認証の上の Agent の権限・対象範囲・権限管理 API
backend/app/system_schema.py               システムテーブル（AGENT_* の DDL・migration・状態）
backend/app/cli/agent_system_schema.py     システムテーブルの status / initialize / recreate の CLI
frontend/src/
  App.tsx                   route（画面は React.lazy で route ごとに読む）
  pages/<領域>/<画面>.tsx    agents / runs / approvals / audit / skills / settings など
  pages/shared/             2 画面以上で使う小さな部品・helper
  lib/api.ts, lib/i18n.ts, lib/routes.ts
docs/agent-control-plane-design.md
```
