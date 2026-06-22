# Production Ready Agent Runtime

生产级 Agent Runtime / Agent コンソールの参照実装です。業務 RAG と業務 NL2SQL は本プロジェクト内に持たず、外部 REST ツールとして接続します。本リポジトリは Run / Step / Event / ToolCall / ToolResult / Approval / Artifact / Memory を中心に、権限、監査、SSE、UI、検証パイプラインを提供します。

## 現在の実装範囲

- Agent Runtime: in-process repository、Oracle/file checkpoint、append-only event log、SSE、cancel / resume / replay、snapshot export/import。
- Auto Planner Loop v1: `POST /api/runs` 只给 `goal` 且不传 `tool_calls` 时，Runtime 会自动选择 Skill 并生成 ToolCall；每个 tool step 成功后还会用执行摘要继续判断下一步。`planner_mode=off` 可关闭自动规划。
- OCI Responses Planner: `AGENT_PLANNER_PROVIDER=oci_responses` で OCI OpenAI-compatible Responses API `/responses` を呼び、同じ `PlannerDecision` schema に正規化。`oci_agent` provider は OCI Generative AI Agents 実践用の予約インターフェースとして保持し、現時点では既定で heuristic planner へ fallback。
- Tool Registry v2: Pydantic schema、権限レベル、side effects、timeout、retry、audit metadata。
- Skill Registry v1: `agent_skill_run` が内蔵 skill を標準 `ToolCall` 計画へ展開し、Runtime が RAG / NL2SQL / MCP / command を通常 step として実行。
- Approval gate: read-only は既定で自動実行、sensitive / write / deny / ask policy は承認または拒否。
- External tools:
  - `external_rag_search`: 外部業務 RAG の `/search` を呼び、answer / contexts / citations を標準化。
  - `external_nl2sql_query`: 外部 NL2SQL の `/query` を呼び、SQL / columns / rows / lineage / warnings を監査用に保持。
  - `external_mcp_list_tools` / `external_mcp_call`: 外部 MCP JSON-RPC gateway の discovery / tool call。
- Safety: SQL は本サービス内で実行しない。tool output の prompt injection、PII、token、secret、非 read-only SQL を検出・マスク・警告。
- Frontend console: Runs、Run detail、Approvals、Audit、Tools、Agents、Memory、External RAG/NL2SQL settings、Tool policy、Runtime safety。
- Observability: `/metrics` Prometheus metrics、`/api/observability/status`、CI quality/security/e2e checks。

詳しい設計は [docs/agent-runtime-design.md](docs/agent-runtime-design.md) を参照してください。

## リポジトリ配置

このテンプレートは共有 package を sibling repo として参照します。

```text
<workspace>/
  no.1-production-ready-agent/
  no.1-production-ready-platform/
```

`backend/pyproject.toml` と `frontend/package.json` は `../no.1-production-ready-platform` 配下の共有 package を path dependency として使います。

## ローカル起動

```bash
scripts/start-all.sh
```

既定の URL:

- Backend OpenAPI: `http://localhost:8020/docs`
- Frontend: `http://localhost:3002`
- Metrics: `http://localhost:8020/metrics`

個別起動:

```bash
scripts/start-backend.sh
scripts/start-frontend.sh
```

## 標準チェック

```bash
scripts/check-all.sh
```

既定で以下を実行します。

- backend: `black --check .`, `ruff check .`, `mypy .`, `pytest -q`, validation evidence dry-run, `bandit -r app --skip B608`, `pip-audit`
- frontend: `npm run build`, `npm run test:e2e`

必要に応じて一部を省略できます。

```bash
SKIP_AUDIT=1 scripts/check-all.sh
SKIP_E2E=1 scripts/check-all.sh
SKIP_BACKEND=1 scripts/check-all.sh
SKIP_FRONTEND=1 scripts/check-all.sh
SKIP_VALIDATION_EVIDENCE=1 scripts/check-all.sh
```

`pip-audit` は脆弱性 DB へアクセスするため、ネットワークが無い環境では `SKIP_AUDIT=1` を使ってください。
`bandit` の B608 は Oracle 内部 repository の validated schema object 名に対する誤検知を避けるため標準チェックでは skip します。利用者値は bind parameter のままで、業務 NL2SQL の SQL は本プロジェクト内で実行しません。

本番向けの Oracle / IdP / MCP / sandbox 実環境検証は手動 GitHub Actions
`Production Validation Evidence` か
[docs/agent-runtime-production-validation.md](docs/agent-runtime-production-validation.md) の preflight / collector / validator を使います。
ローカルや self-hosted runner 上では `scripts/validate-production-evidence.sh` を使うと同じ流れを一括実行できます。
必要な secret 名は [docs/agent-runtime-production-validation.env.example](docs/agent-runtime-production-validation.env.example) を参照してください。

## 主要 API

| Method | Path | 用途 |
|---|---|---|
| `POST` | `/api/runs` | Run 作成 |
| `GET` | `/api/runs` | Run 一覧 |
| `GET` | `/api/runs/{id}` | Run 詳細 |
| `GET` | `/api/runs/{id}/events` | SSE event log |
| `WS` | `/api/runs/{id}/events/ws` | WebSocket event stream / heartbeat / command ack channel |
| `POST` | `/api/runs/{id}/cancel` | Run cancel |
| `POST` | `/api/runs/{id}/resume` | Run resume |
| `POST` | `/api/runs/{id}/replay` | Run replay |
| `GET` | `/api/runs/{id}/audit` | Tool audit records |
| `GET` | `/api/runs/{id}/artifacts` | Run artifacts |
| `GET` | `/api/audit/tool-calls` | Tool call audit records with filters |
| `GET` | `/api/audit/tool-calls.csv` | Tool call audit CSV export |
| `GET` | `/api/runtime/snapshot` | Runtime snapshot export |
| `POST` | `/api/runtime/snapshot/import` | Runtime snapshot dry-run validation / confirmed import |
| `POST` | `/api/approvals/{id}/decision` | 承認 / 却下 |
| `GET` | `/api/tools` | Tool Registry v2 |
| `GET` | `/api/skills` | Agent Skill Registry |
| `POST` | `/api/skills/plan` | Skill を ToolCall 計画へ展開 |
| `GET` | `/api/tools/external-mcp` | 外部 MCP gateway tools/list discovery |
| `POST` | `/api/tools/invoke` | 単発 tool invocation |
| `GET/PATCH` | `/api/settings/external-rag` | 外部 RAG 設定 |
| `GET/PATCH` | `/api/settings/external-nl2sql` | 外部 NL2SQL 設定 |
| `GET/PATCH` | `/api/settings/external-mcp` | 外部 MCP gateway 設定 |
| `GET/PATCH` | `/api/settings/planner` | Auto planner / OCI Responses planner 設定 |
| `GET/PATCH` | `/api/settings/tool-policy` | Tool policy |
| `GET/PATCH` | `/api/settings/runtime-safety` | 実行安全上限 |
| `GET/POST/PATCH` | `/api/agents` | Agent profiles |
| `GET/POST` | `/api/memory/search` | Agent memory search |
| `GET` | `/api/observability/status` | 観測設定状態 |
| `GET` | `/api/observability/events` | Sanitized trace event buffer |

## 外部サービス設定

```bash
AGENT_EXTERNAL_RAG_BASE_URL=http://rag-service.local
AGENT_EXTERNAL_RAG_API_KEY=...
AGENT_EXTERNAL_RAG_TIMEOUT_SECONDS=10
AGENT_EXTERNAL_RAG_MAX_RETRIES=3

AGENT_EXTERNAL_NL2SQL_BASE_URL=http://nl2sql-service.local
AGENT_EXTERNAL_NL2SQL_API_KEY=...
AGENT_EXTERNAL_NL2SQL_TIMEOUT_SECONDS=15
AGENT_EXTERNAL_NL2SQL_DEFAULT_LIMIT=100
AGENT_EXTERNAL_NL2SQL_MAX_RETRIES=3

AGENT_EXTERNAL_MCP_BASE_URL=http://mcp-gateway.local/jsonrpc
AGENT_EXTERNAL_MCP_API_KEY=...
AGENT_EXTERNAL_MCP_TIMEOUT_SECONDS=10
AGENT_EXTERNAL_MCP_MAX_RETRIES=3

AGENT_PLANNER_PROVIDER=heuristic # heuristic / oci_responses / oci_agent
AGENT_PLANNER_OCI_RESPONSES_BASE_URL=https://inference.generativeai.${OCI_REGION}.oci.oraclecloud.com/openai/v1
AGENT_PLANNER_OCI_RESPONSES_API_KEY=...
AGENT_PLANNER_OCI_RESPONSES_MODEL=...
AGENT_PLANNER_OCI_RESPONSES_PROJECT=...
AGENT_PLANNER_OCI_AGENT_ENDPOINT=...
AGENT_PLANNER_OCI_AGENT_API_KEY=...
AGENT_PLANNER_TIMEOUT_SECONDS=8
AGENT_PLANNER_MAX_RETRIES=3
AGENT_PLANNER_FALLBACK_TO_HEURISTIC=true
AGENT_PLANNER_ALLOWED_TOOL_NAMES=agent_skill_run
AGENT_PLANNER_ALLOW_COMMAND_GENERATION=false

AGENT_PERMISSION_DEFAULT_MODE=approval
AGENT_MEMORY_ENABLED=true
AGENT_RUNTIME_REPOSITORY_BACKEND=memory # memory / file / oracle_checkpoint / oracle_normalized
AGENT_RUNTIME_SNAPSHOT_PATH=/tmp/agent-runtime-snapshot.json
AGENT_RUNTIME_ORACLE_DSN=
AGENT_RUNTIME_ORACLE_USER=
AGENT_RUNTIME_ORACLE_PASSWORD=
AGENT_RUNTIME_ORACLE_TABLE=AGENT_RUNTIME_CHECKPOINTS
AGENT_RUNTIME_ORACLE_CHECKPOINT_KEY=default
AGENT_RUNTIME_ORACLE_CREATE_SCHEMA=true
AGENT_RUNTIME_ORACLE_PROJECTION_PREFIX=AGENT_RUNTIME
AGENT_RBAC_ENABLED=false
AGENT_RBAC_ACTOR_HEADER=x-agent-actor
AGENT_RBAC_ROLES_HEADER=x-agent-roles
AGENT_RBAC_BUSINESS_VIEWS_HEADER=x-agent-business-views
AGENT_MAX_TOOL_CALLS_PER_RUN=20
AGENT_MAX_PENDING_APPROVALS_PER_RUN=5

AGENT_METRICS_ENABLED=true
AGENT_TRACE_EVENTS_ENABLED=true
AGENT_TRACE_EVENTS_BUFFER_SIZE=500
AGENT_TRACE_EXPORTER_URL=
AGENT_TRACE_EXPORTER_API_KEY=
AGENT_TRACE_EXPORTER_TIMEOUT_SECONDS=2
AGENT_LANGFUSE_HOST=
AGENT_LANGFUSE_PUBLIC_KEY=
AGENT_LANGFUSE_SECRET_KEY=
AGENT_OPENTELEMETRY_ENDPOINT=

AGENT_COMMAND_TOOLS_ENABLED=false
AGENT_COMMAND_WORKSPACE_ROOT=.
AGENT_COMMAND_ALLOWED_PREFIXES=
AGENT_COMMAND_DEFAULT_TIMEOUT_SECONDS=10
AGENT_COMMAND_MAX_TIMEOUT_SECONDS=30
AGENT_COMMAND_OUTPUT_LIMIT_BYTES=20000
```

`AGENT_RUNTIME_REPOSITORY_BACKEND=oracle_normalized` では checkpoint に加えて正規化
projection tables を更新し、`/api/audit/tool-calls` と CSV export は projection-backed query
を優先します。memory / file backend は従来どおり runtime state を走査します。

`AGENT_TRACE_EXPORTER_URL` を設定すると、runtime は sanitized `TraceEvent` を webhook へ
best-effort POST します。tool output / RAG answer / NL2SQL rows などの業務データ原文は送信しません。

## セキュリティ境界

- 本サービスは業務 DB に直接接続しません。
- NL2SQL が返す SQL は監査・説明用であり、本サービスでは実行しません。
- 業務 RAG / NL2SQL の認証情報は環境変数または設定 API の runtime store で扱い、ログや artifact に secret を残さない設計です。
- `AGENT_RBAC_ENABLED=true` の場合、`X-Agent-Roles` で `operator` / `approver` / `auditor` / `admin` を受け取り、高リスク API を保護します。`X-Agent-Business-Views` は run / tool request の `business_view_id` アクセスを制限します。本番では前段の認証基盤で署名済み identity header に変換してください。
- `sandbox_command_run` は既定で無効です。有効化しても shell は使わず、workspace root 内の cwd と allowlist prefix に一致する argv のみ実行します。
- Agent profile の `command_allowed_prefixes` は sandbox command の prefix を Agent 単位で狭めるための設定です。global command tool disabled を上書きして有効化するものではありません。
- 外部 tool output は system prompt / developer instruction として扱いません。
- 漏洩版や非公開コードは設計入力として使用しません。公開公式ドキュメントと公開研究から原則だけを再マッピングします。
