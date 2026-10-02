# AGENTS.md — Production Control Plane for AI Agents

> **Agent（`agent/`）固有のルール**です。GitHub 運用・Issue / PR 規約・CI・デザインシステム・共通の技術方針は、monorepo 共通の [../AGENTS.md](../AGENTS.md) を正本として先に適用します。
> Claude Code と Codex が参照します。ルール変更はこのファイル（共通ルールは ../AGENTS.md）を編集します。
> Issue には `product:agent` label を付け、PR title の scope は `agent` にする。

## プロジェクト目標

**業務・業界の Agent Platform — 業務 Agent を作り、Skill と MCP（RAG・NL2SQL・その他）を安全に使わせる。**

業務 Agent の定義、Skill と Marketplace（Plugin）、実行（組み込み Runtime）、共通の Run・Event・Artifact・
Approval・Audit を 1 つの製品で持つ。再設計案（2026-10-02。Agent Platform 再設計案）に沿って段階的に作り変える。

ユーザーが扱う主要概念は次のとおり。

1. **業務 Agent** — 業務の指示、説明、有効状態、`skill_ids`、使うモデル（`model_id`。空なら既定のテキストモデル）。
2. **Skill** — AgentSkills 互換の指示本体。必要なツール（MCP / Control Plane のツール）を宣言する。
3. **Plugin** — Skill と MCP の接続をまとめた配布単位（Marketplace から入れる）。

依存方向は `業務 Agent → Skill → ツール（MCP / resource）`。

## アーキテクチャ不変条件

- 実行は Control Plane の**組み込み Runtime**（`features/agent/builtin_runtime.py`。#754）。
  - SDK は OpenAI Agents SDK（`openai-agents`。版を固定し、Dependabot の PR で上げる）。
  - モデルは OCI Enterprise AI の Responses API（システム設定 > モデルの接続）。別の LLM provider は組み込まない。
  - SDK の tracing は常に無効にする（業務データを外部へ送らない）。
- 外部の Runtime（OpenClaw / Hermes / DeerFlow など）・Binding・Docker の Runtime は持たない（#754 で削除）。
- 指示は「Agent の指示 + 割り当てた Skill の指示」。ツールは Skill の requirement が必要とするものだけを
  function tool として渡す。`server_id="control-plane"` は `tool_registry` のツール、それ以外は MCP 接続
  （`rag` / `nl2sql` / 登録した接続）の `tools/list` のツール（名前は `<接続>__<ツール>`。#757）。
- ツールの実行は `tool_registry.invoke`（ポリシー・ガードレール・監査・成果物）を必ず通す。
  - ポリシーが「拒否」のツールはモデルに渡さない。
  - 「承認」のツールは SDK の `needs_approval` で中断する。中断した Run は `waiting_approval` にし、SDK の状態を Run に保存する。
  - 承認がすべて決まったら状態を復元して再開する。却下したツールは実行しない。
- Run は Agent を選ぶだけで作れる（Binding の選択は無い）。開発は API のプロセスで実行（`in_process`）し、
  本番は Oracle checkpoint の row lock と Run lease を使う runtime-dispatcher（`python -m app.features.agent.runtime_dispatcher`）。
- Plugin は Marketplace の**原子的な配布パッケージ**であり実行概念ではない。正式契約は `skills[] / mcp_servers[] / resources[]`。
- Prompt / Workflow / Template は非実行・版管理 resource。独立 Workflow engine を作らない。

## MCP 境界

- RAG / NL2SQL / 外部 MCP は「MCP 接続」1 つの仕組みで管理する（#757。旧「外部 RAG」「外部 NL2SQL」「外部 MCP」は削除）。
  RAG / NL2SQL は組み込みの接続 `rag` / `nl2sql`（認証はサービストークン、削除できない）で、URL の初期値は
  `AGENT_EXTERNAL_RAG_MCP_URL` / `AGENT_EXTERNAL_NL2SQL_MCP_URL`。外部の MCP は認証方式（なし / API キー /
  OAuth client credentials / サービストークン）を選んで追加する。
- MCP 接続のツールは、組み込み Runtime から `tool_registry.invoke`（`definition` / `handler` を渡す）を通してだけ呼ぶ
  （policy・masking・監査を再利用する）。承認の要否は MCP の `readOnlyHint` とツール権限（`<接続>__<ツール>` の名前）。
  外部 API key・token を snapshot、API、ログ、Artifact に出さない。
- サービストークンは呼び出しごとの `issue_service_token`（`sub` = Run の利用者 `RunState.created_by_user_uuid`、なければ
  `AGENT_MCP_SERVICE_USER_LOGIN_ID` のサービス利用者。`aud` = 接続の audience）。承認後の実行も承認者ではなく Run の利用者で呼ぶ。
  `tools/call` は 502 / 504・timeout で再試行しない（LLM を使うツールがある）。詳細は docs/agent-control-plane-design.md §4.1。

## 技術スタック

- Backend: Python 3.12、FastAPI、Pydantic v2、httpx、uv、Oracle (`python-oracledb`)。
- Frontend: Vite、React Router、TypeScript、Tailwind、shadcn/ui、TanStack Query、Zustand。
- 状態: 開発 memory/file、本番 Oracle AI Database。外部 queue・外部 vector DB は追加しない。
- 実行: OpenAI Agents SDK（組み込み Runtime）。モデルは OCI Enterprise AI（Responses API）。別 LLM provider を
  組み込まない。
- 観測: Prometheus、OpenTelemetry、Langfuse。業務データ原文と secret は trace に送らない。

## 日本語・UI/UX

- UI、エラー、通知、LLM 指示の第一言語は日本語。文言は i18n 経由。
- 日本語フォントは `"Noto Sans JP", "Roboto", system-ui, sans-serif`、本文 14px。
- ナビは「業務 Agent / Skill / Runtime / Run / 承認・監査 / Marketplace」を主要導線とする。
  Plugin、Tools を独立ナビに戻さない（旧エンジンの Planner・Memory は #756 で削除した）。
- 設定は2セクションに分ける。**運用設定**：システムテーブル（先頭。RAG / NL2SQL と同じ。#751）/ Agent 接続設定 / MCP 接続（#757）/
  Control Plane バックアップ（Agent 固有）。**システム設定**：OCI 認証 / アップロード保存先 / モデル /
  データベース / 外観（3製品で共通。画面と API は platform の共有パッケージ）。
  ツール権限はナビに出さない（Control Plane 化で外した方針を維持）。Command Policy・Runtime Safety の画面と
  コマンド実行ツール（`sandbox_command_run`）は #756 で削除した。
- ログインと権限（#215）: 共通認証（`AGENT_AUTH_MODE=production`）。製品固有の権限管理は「セキュリティ設定」
  （権限管理）。並びは 3 製品で同じ「セキュリティ設定 → ユーザーとロール → 運用設定 → システム設定」（#658）。
  メニュー権限は `menu.*`、実データの閲覧・操作は capability（`agent.runs.view` / `agent.runs.operate` /
  `agent.approvals.decide` / `agent.audit.view` / `agent.admin`）。権限カタログと API の manifest の正本は
  `backend/app/security/permissions.py`、説明は [docs/security-rbac.md](./docs/security-rbac.md)。
- Agent 編集画面は指示・Skill・モデルを選ぶ。Run は Agent とゴールだけで作る（実行先の選択は無い。#754）。
- 空、読込、エラー、モデル未設定（組み込み Runtime が実行できない）、承認待ちを明示する。
- 画面の振る舞い（メッセージ機構・ボタンの役割と配置・ページの型・状態保持・横断的な保守契約）は
  platform の [UX 契約](../platform/docs/ux-contracts/README.md) を正本とする。
- 各ページの型（A〜D）の割り当て、離脱ガードの対象画面、作業状態として残す field は
  [docs/frontend-page-archetypes-spec.md](./docs/frontend-page-archetypes-spec.md) に書く。
- UI/UX 作業では必ず `ui-ux-pro-max` skill を使い、desktop と 375px、キーボード操作を
  Playwright で確認する。

## デザインシステム / UI

- 共通ルール（platform が正本・禁止事項・画面の構成・lint・UI 変更の検証）は [../AGENTS.md](../AGENTS.md)「デザインシステム / UI」に従う。lint は `frontend/eslint.config.js` が `../../platform/docs/design-system/adherence.oxlintrc.json` を import する。

### Agent 固有

- サイドバーのアカウント領域（ユーザー・ロール・ログアウト・テーマ切替）は `SidebarAccountFooter` を `Sidebar` の `footer` に渡して表示する。
- ライト専用だった画面も `data-theme="dark"` でダークテーマに追従するため、色の直書きを残さない。

## API と移行

- Snapshot 正式版は `agent-control-plane.snapshot.v2`。
- v1 `tool_names` は Skill を安全に推定する。変換不能 Agent は `migration_required=true`、無効。
- `POST /api/runs` は `agent_id` と `goal` だけを受け取り、組み込み Runtime で実行する（Binding は無い。#754）。
  旧エンジン（v1 の Run・`tool_calls`・planner・Memory・`X-Agent-API-Version: 1`）は #756 で削除した。
- Skill は instructions と MCP ツールの許可リスト（`mcp_requirements`）だけを持つ。ToolCall のテンプレート
  （`tool_calls`）と `POST /api/skills/plan`・`agent_skill_run` は #756 で削除した。

## セキュリティ

- secret は `.env` / secret store 経由。ハードコード、commit、API 応答への展開を禁止。
- manifest ID を検証する。
- Plugin install は衝突時に全体失敗し、部分展開しない。参照中 Skill の disable/uninstall は
  `409`。
- RBAC は viewer/operator/approver/auditor/admin を維持する。
- 画面のログインは共通認証（Cookie のセッション）。capability は従来の 5 ロールに対応し、利用者（local はローカル利用者）から
  `ActorPolicy` を作って router の既存の判定に流す。local でもユーザー・ロールは Oracle の `PLATFORM_*`（RAG / NL2SQL と同じ）。
  Agent 独自の header / JWT / 外部 policy の認可（旧 `AGENT_RBAC_*`）は持たない。対象範囲はエージェントだけで、業務ビューの判定は
  RAG に任せる（#750）。新しい API は必ず権限 manifest（`app/security/permissions.py`）に
  登録する（登録外は 403。完全性テストがある）。WebSocket は handler の中で Cookie と `Origin` を検証する。
- 承認の決定者（`decided_by`）は Cookie の利用者から決め、request の値を使わない。

## 開発・検証

- 機能変更と同時に pytest / Playwright を追加・更新する。
- 完了前に `scripts/check-all.sh`（backend の ruff format/ruff check/mypy/pytest・bandit、
  frontend の lint/build）を実行する。
  - `check-all.sh` のローカルの既定は Playwright e2e と pip-audit を省く（#339）。関係する spec だけ
    `SKIP_E2E=0 E2E_ARGS="e2e/<対象>.spec.ts" scripts/check-all.sh` で実行する。全部を実行するときは `FULL=1`。
  - PR の CI は `Agent / Backend`・`Agent / Frontend`・`Agent / E2E smoke`（約 1 分の smoke）の 3 job。Playwright の
    全件は `e2e-nightly.yml`、pip-audit の全件は `dependency-audit-nightly.yml` が毎晩実行する。PR の `検証結果` には
    ローカルで実行した command と、CI の job 結果を分けて書く。
  - smoke に入れる spec は `ci.yml` の `agent-e2e` に書く（ログイン・認証・Run の stream・Runtime の主導線など、壊れると
    全体に響くもの。合計が CI でおおむね 1 分に収まる量）。
- 組み込み Runtime のテストは SDK の `ScriptedModel`（`agents.testing`）でモデルを台本にし、OCI へは接続しない。
  実環境の OCI Enterprise AI での確認は手動 / ステージングで行い、PR に分けて書く。
- UI 作業以外でも既存ユーザー変更を尊重し、無関係な差分を戻さない。

## 主要ディレクトリ

```text
backend/app/features/agent/
  builtin_runtime.py        組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI。#754）
  runtime_dispatcher.py     claim/lease dispatcher（本番の別プロセス）
  runtime.py                共通 Run/Event/Artifact/Audit と legacy history
  skills.py                 Skill registry と MCP/resource 依存
  plugins.py                Marketplace package の原子的 install
  router.py                 REST / SSE / WS
backend/app/security/       共通認証の上の Agent の権限・対象範囲・権限管理 API（#215）
backend/app/system_schema.py               システムテーブル（AGENT_* の DDL・migration・状態。#751）
backend/app/cli/agent_system_schema.py     システムテーブルの status / initialize / recreate の CLI
frontend/src/
  pages/AgentRuntimePages.tsx
  lib/api.ts, lib/i18n.ts, lib/routes.ts
docs/agent-control-plane-design.md
```
