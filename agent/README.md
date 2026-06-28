# Production Control Plane for AI Agents

**Define Business Agents once. Execute them anywhere.**

OpenClaw、Hermes、DeerFlow などの Runtime と競合せず、それらを統合・管理する AI Agent
Control Plane の参照実装です。Business Agent は Skill だけを選択し、MCP や resource の詳細、
Runtime 固有設定は Control Plane が Binding 同期時に解決します。

```text
Marketplace → 配布 package → Skill Registry
                                  │
Business Agent ───────────────→ Skill → MCP / resource
       │
       └─ Runtime Binding → OpenClaw / Hermes / DeerFlow
```

## 主要機能

- Business Agent: 業務指示、説明、有効状態、`skill_ids`。
- Skill: AgentSkills 互換指示、`mcp_requirements`、`resource_ids`。
- Marketplace: Skill / MCP / prompt・workflow・template resource の原子的 install。
- Runtime/Binding: Agent 定義と実行先を分離し、Agent ごとに既定 Binding は最大1件。
- Common Run: 状態、Event、cancel、Artifact、Approval、Audit を Runtime 間で正規化。
- Runtime adapter: OpenClaw Gateway WS、Hermes Runs API、DeerFlow LangGraph API。
- Binding MCP: 既存 RAG/NL2SQL tool を選択 Skill の閉包だけに限定して公開。
- Dispatcher: 開発は in-process、本番は Oracle row lock + claim/lease worker。
- Docker service management: 固定 digest、profile、healthcheck、volume、静的操作 allowlist。
- Snapshot v2: Runtime/Binding を含む Control Plane backup。v1 snapshot/manifest を移行。

設計詳細は [docs/agent-control-plane-design.md](docs/agent-control-plane-design.md) を参照してください。

## ローカル開発

workspace には共有 package の sibling repo が必要です。

```text
<workspace>/
  no.1-production-ready-agent/
  no.1-production-ready-platform/
```

```bash
# backend
cd backend
uv sync
uv run uvicorn app.main:app --reload

# frontend
cd frontend
npm install
npm run dev
```

既存 helper を使う場合:

```bash
scripts/start-all.sh
scripts/check-all.sh
```

## Docker Compose

Runtime は必要な profile だけ起動します。Docker socket は mount されません。

```bash
cp .env.runtime.example .env
docker compose up -d control-plane
docker compose --profile openclaw up -d runtime-openclaw
docker compose --profile hermes up -d runtime-hermes
docker compose --profile deerflow up -d runtime-deerflow
```

本番 dispatcher を使う場合は Oracle 設定を入れ、Control Plane と worker の
`AGENT_RUNTIME_DISPATCH_MODE=external` を有効にします。

```bash
docker compose --profile dispatcher up -d control-plane runtime-dispatcher
```

公式 Runtime image は `docker-compose.yml` で `@sha256` 固定しています。更新時は公式 release と
manifest を検証して digest を明示更新してください。

## 主要 API

| Method | Path | 用途 |
|---|---|---|
| `GET/POST/PATCH` | `/api/runtimes` | Runtime 定義 |
| `GET` | `/api/runtimes/{id}/status` | capability/status probe |
| `POST` | `/api/runtimes/services/{id}/{action}` | allowlist 済み service action |
| `GET` | `/api/runtimes/services/{id}/logs` | service log |
| `GET/POST/PATCH/DELETE` | `/api/runtime-bindings` | Agent と Runtime の Binding |
| `POST` | `/api/runtime-bindings/{id}/sync` | Skill/MCP 閉包を Runtime へ同期 |
| `GET/POST/PATCH` | `/api/agents` | Business Agent |
| `GET/POST/PATCH` | `/api/skills` | Skill registry |
| `GET/POST` | `/api/plugins` | Marketplace 配布 package（内部契約名） |
| `POST` | `/api/runs` | Binding を固定して Run 作成 |
| `GET` | `/api/runs/{id}/events` | SSE event |
| `WS` | `/api/runs/{id}/events/ws` | WebSocket event |
| `POST` | `/api/runs/{id}/cancel` | capability 対応時だけ cancel |
| `GET` | `/api/runs/{id}/artifacts` | normalized Artifact |
| `POST` | `/api/mcp/{binding_id}` | Binding 固有 MCP endpoint |
| `GET/POST` | `/api/runtime/snapshot` | Snapshot v2 export/import |

`POST /api/runs` は `agent_id` と任意の `runtime_binding_id` を受け取ります。Binding が解決できない
場合は `409 runtime_binding_required`、旧 `tool_calls` は `422` です。移行期間の旧テスト/API は
`X-Agent-API-Version: 1` を明示した場合だけ動作し、deprecation/sunset header を返します。

## セキュリティ境界

- Runtime secret は環境変数値ではなく env 名で参照し、API/snapshot/log に値を出しません。
- Binding MCP は Binding 固有 token と Skill allowlist の両方を検証します。
- Plugin install は ID 衝突時に全体を失敗させます。参照中 Skill は削除できません。
- Runtime の capability が無い操作は `409` で fail closed します。
- Runtime 自動 failover は行いません。
- Control Plane に別 LLM provider、外部 vector DB、新規 queue 製品を追加しません。

## 検証

```bash
cd backend
uv run black --check .
uv run ruff check .
uv run mypy .
uv run pytest -q

cd ../frontend
npm run build
npm run test:e2e

cd ..
docker compose config
docker compose --profile openclaw --profile hermes --profile deerflow config
```
