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
