# AGENTS.md — Production Control Plane for AI Agents

> Claude Code と Codex が参照する正本。ルール変更はこのファイルだけを編集する。

## プロジェクト目標

**Production Control Plane for AI Agents — Define Business Agents once. Execute them anywhere.**

本プロジェクトは Agent Runtime ではない。Business Agent の定義、Skill と Marketplace、
Runtime/Binding、共通 Run・Event・Artifact・Approval・Audit を管理し、OpenClaw / Hermes /
DeerFlow と将来の Runtime を adapter で統合する Control Plane である。

ユーザーが扱う主要概念は次の3つに限定する。

1. **Business Agent** — 業務指示、説明、有効状態、`skill_ids`。
2. **Skill** — AgentSkills 互換の指示本体。MCP/resource は内部依存。
3. **Runtime** — 外部実行基盤。Agent とは `RuntimeBinding` で接続する。

依存方向は `Business Agent → Skill → MCP/resource`。Agent から Plugin、MCP、Tool、
Prompt、Workflow、Template、Runtime 固有設定を直接参照してはならない。

## アーキテクチャ不変条件

- Plugin は Marketplace の**原子的な配布パッケージ**であり実行概念ではない。
- Plugin manifest の正式契約は `skills[] / mcp_servers[] / resources[]`。
- Prompt / Workflow / Template は非実行・版管理 resource。独立 Workflow engine を作らない。
- 新 manifest の `agents[]` は禁止。v1 manifest は Agent を作らず template resource へ変換する。
- Agent は未 Binding でも保存できるが実行できない。
- Run の Binding 解決順は request 明示 → Agent の既定 Binding。無ければ `409`。
- Runtime 間の自動 failover は行わない。監査対象の Binding を Run 中に変更しない。
- Runtime adapter 契約は `probe_capabilities / sync_binding / submit_run / follow_events /
  get_status / cancel / list_artifacts`。
- 未対応操作は成功扱いにせず `409 runtime_capability_unsupported` を返す。
- `legacy-native` は既存 Run/監査/Artifact/Memory export の読取専用。新規 v2 Run を実行しない。
- Runtime secret は値を保存・返却せず、環境変数名 (`*_secret_ref`) だけを保持する。

## Runtime とサービス管理

- 初期 adapter は OpenClaw Gateway WebSocket、Hermes Runs/Responses API、DeerFlow
  LangGraph-compatible API。
- Runtime は公式 Docker image を `image@sha256` で固定する。派生 image と source vendoring は
  行わない。
- Compose profile は `openclaw / hermes / deerflow / dispatcher`。各 Runtime は独立 volume、
  healthcheck、内部 network を持つ。
- service action は静的 allowlist の `pull/start/stop/restart/remove/logs` のみ。
- Docker socket は既定で mount しない。service control は明示的な管理者運用時だけ有効化する。
- 開発時 dispatcher は in-process。本番は Oracle checkpoint の row lock と Run lease を使う
  `runtime-dispatcher` service。新しい queue 製品は追加しない。

## MCP 境界

- 既存 RAG / NL2SQL / external MCP / sandbox tool は Agent に直接公開しない。
- Skill の `mcp_requirements[{server_id, tool_names}]` から閉包を計算し、Binding 固有 token の
  `/api/mcp/{binding_id}` が必要 tool だけを公開する。
- Binding token は個別 env または master secret から HMAC 派生する。token/外部 API key を
  snapshot、API、ログ、Artifact に出さない。
- 既存の schema 検証、policy、masking、監査を MCP 呼出しでも再利用する。

## 技術スタック

- Backend: Python 3.12、FastAPI、Pydantic v2、httpx、uv、Oracle (`python-oracledb`)。
- Frontend: Vite、React Router、TypeScript、Tailwind、shadcn/ui、TanStack Query、Zustand。
- 状態: 開発 memory/file、本番 Oracle 26ai。外部 queue・外部 vector DB は追加しない。
- モデル: 既存 OCI Enterprise AI 設定を Runtime へ渡す。別 LLM provider を Control Plane に
  組み込まない。
- 観測: Prometheus、OpenTelemetry、Langfuse。業務データ原文と secret は trace に送らない。

## 日本語・UI/UX

- UI、エラー、通知、LLM 指示の第一言語は日本語。文言は i18n 経由。
- 日本語フォントは `"Noto Sans JP", "Roboto", system-ui, sans-serif`、本文 14px。
- ナビは「業務 Agent / Skill / Runtime / Run / 承認・監査 / Marketplace」を主要導線とする。
  Plugin、Tools、Planner、Memory を独立ナビに戻さない。
- Agent 編集画面は Skill 選択だけ。実行先は Agent 詳細の Binding panel、Run では Binding
  上書きだけを表示する。
- 空、読込、エラー、degraded、未 Binding、capability 非対応を明示する。
- UI/UX 作業では必ず `ui-ux-pro-max` skill を使い、desktop と 375px、キーボード操作を
  Playwright で確認する。

## デザインシステム / UI（platform が正本）

- **UI に触る変更（`frontend/`）の前に、platform リポジトリの [docs/design-system/ARCHITECTURE.md](../no.1-production-ready-platform/docs/design-system/ARCHITECTURE.md) を読む。** ワークスペースに sibling の `../no.1-production-ready-platform` が無い場合は GitHub の `engchina/no.1-production-ready-platform` の `docs/design-system/` を参照する。
  - トークン値・コンポーネント仕様・意図的な見た目の変更点: 同 `README.md`
  - 実装の参照: 同 `components-reference.md`
  - 共通の禁止事項とレビュー観点: platform の `AGENTS.md`「デザインシステム / UI」節
- **依存の向きは「デザインシステムの決定 → `@engchina/production-ready-ui`（platform の `packages/ui`）→ 本リポジトリ」の一方向。** 本リポジトリでコンポーネントやトークンを新規実装しない。必要になったら platform に `packages/ui` へ入れる Issue を立てる。
- **本リポジトリが持てるのは次だけ。**
  - ナビ構造（nav config）と業務コピー（i18n）
  - データ取得・状態管理・権限
  - ドメイン enum → コンポーネント prop の対応表（例: 状態 → `StatusBadge` の `variant`）
  - 画面固有の業務レイアウト
  - 1製品しか使わない部品は置いてよい。判断基準は「他の2製品がこれを欲しがるか」で、欲しがるなら `packages/ui` に入れる
- **色・型・余白・角丸・影・モーション・フォーカス表示・テーマ（light / dark / auto）は `packages/ui` が持つ。** `frontend/src/globals.css` は `@import "tailwindcss"` → `@import "@engchina/production-ready-ui/styles.css"` → `@source "../node_modules/@engchina/production-ready-ui/dist"` と、画面固有のレイアウトだけにする。`main.tsx` から JS で import すると共有ユーティリティが生成されない。

### 禁止事項

- 生の hex（`#1a73c1` 等）と生の px を書く。色は `--color-*` トークン（`bg-surface` / `text-fg-muted` / `border-border-control` 等のユーティリティ）を使う。旧名（`bg-card` / `text-muted` / `bg-primary` / `var(--primary)` 等）は移行用の互換エイリアスで、新規コードで使わない。
- `globals.css` に色トークンや `.dark { … }` の上書きを定義する。
- `TextField` / `PageHeader` / `Button` / `StatusBadge` などの共有コンポーネントを再実装する。
- `<table>` を手書きする。`DataTable` を使う。
- `<div className="px-8 py-6">` や `style={{ padding: "1.5rem 2rem" }}` のような余白コンテナを手書きする。`PageBody` を使う。
- `ToggleChip` をタブ代わりに使う。タブ＝同じ対象の別の見方に切り替えるのは `Tabs`、チップ＝データの絞り込みは `ToggleChip`。
- `loading` 中にボタンのラベルを「実行中…」等に差し替える。ラベルは変えず、`icon` がスピナーに置き換わる。子要素にアイコンを書かず `icon={Upload}` で渡す。
- 製品ごとのアクセント色を作る。製品は wordmark・ナビ・内容で区別する。
- 絵文字と手描き SVG。アイコンは `lucide-react`（14 / 16 / 20 / 24px のみ）。
- `@engchina/production-ready-ui` の内部パス（`dist/components/**` や `dist/tokens/*.css`）を import したりテストで読んだりする。パッケージのルートと `styles.css` だけを使う。

### 画面の構成

```tsx
<AppShell sidebar={<Sidebar … footer={<SidebarAccountFooter … />} />}>
  <PageHeader title="…" actions={[{ id, kind: "primary", label, icon }]} tabs={<Tabs … />} />
  <PageBody>
    <Section title="…">…</Section>
  </PageBody>
</AppShell>
```

- `PageHeader` の `actions` は配列で渡す（danger → utility → secondary → primary の順に自動で並び、右端が primary になる）。
- `PageHeader` と `PageBody` に `wide` を渡す場合は必ず両方に同じ値を渡す。片方だけだと 1920px でタイトルと本文の左端がずれる。
- 単位の境界: 文字サイズとコントロール高さは px、余白とレイアウト寸法は rem（14px ルート）。

### 既存ルールとの優先順位

- トークン・コンポーネントの見た目と振る舞い（サイズ・variant・アイコン・loading・ヘッダーの並び順・フォーカス・ダークテーマ）は、`ui-ux-pro-max` skill の一般論より platform の `docs/design-system/` を優先する。

### UI 変更の検証

- ライト / ダークの両テーマで確認する。
- 1280px / 1920px の両幅で確認する。1920px では PageHeader のタイトルと本文の左端が揃うこと。
- キーボード操作（最初の Tab で「本文へスキップ」、フォーカスリングの視認性、`Tabs` の ← → / Home / End）を確認する。
- 状態を表す UI は色だけに依存しない（`StatusBadge` / `Banner` / `Toast` はアイコン付き）。
- 意図的な見た目の変更は platform の `docs/design-system/README.md` §7 と照合し、PR の `検証結果` に記載する。
### Agent 固有

- サイドバーのアカウント領域（ユーザー・ロール・ログアウト・テーマ切替）は `SidebarAccountFooter` を `Sidebar` の `footer` に渡して表示する。
- ライト専用だった画面も `data-theme="dark"` でダークテーマに追従するため、色の直書きを残さない。

## API と移行

- Snapshot 正式版は `agent-control-plane.snapshot.v2`。
- v1 `tool_names` は Skill を安全に推定する。変換不能 Agent は `migration_required=true`、無効。
- v1 `command_allowed_prefixes` は最初の Binding policy へ移す。
- v2 `POST /api/runs` の `tool_calls` は `422`。Binding 未設定は
  `409 runtime_binding_required`。
- 移行リリース中だけ `X-Agent-API-Version: 1` を明示した旧 Run に deprecation/sunset header を
  返す。新 UI と新規テストで v1 を使わない。

## セキュリティ

- secret は `.env` / secret store 経由。ハードコード、commit、API 応答への展開を禁止。
- Runtime base URL、service action、Binding/native ref、manifest ID を検証する。
- Plugin install は衝突時に全体失敗し、部分展開しない。参照中 Skill の disable/uninstall は
  `409`。
- RBAC は viewer/operator/approver/auditor/admin を維持する。MCP endpoint は Binding token を
  RBAC の代替にせず、Runtime からの能力呼出し境界として扱う。

## 開発・検証

- 機能変更と同時に pytest / Playwright を追加・更新する。
- 完了前に backend の black/ruff/mypy/pytest、frontend build/Playwright、
  `docker compose config`、secret/socket/digest security check を実行する。
- Runtime 実 image 起動は opt-in integration job。通常 CI は fixture server で adapter の
  再接続、重複 event、timeout、不正応答を検証する。
- UI 作業以外でも既存ユーザー変更を尊重し、無関係な差分を戻さない。

## 主要ディレクトリ

```text
backend/app/features/agent/
  control_plane.py          Runtime / Binding / adapter / service catalog
  runtime_dispatcher.py     claim/lease dispatcher
  runtime.py                共通 Run/Event/Artifact/Audit と legacy history
  skills.py                 Skill registry と MCP/resource 依存
  plugins.py                Marketplace package の原子的 install
  router.py                 REST / SSE / WS / Binding MCP endpoint
frontend/src/
  pages/AgentRuntimePages.tsx
  lib/api.ts, lib/i18n.ts, lib/routes.ts
docker-compose.yml          Control Plane + Runtime profiles
docs/agent-control-plane-design.md
```
