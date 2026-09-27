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

monorepo `no.1-production-ready-suite` の `agent/` で作業します。共有 package は同じ repository の `../platform/` を相対パスで参照します。

```text
no.1-production-ready-suite/
  platform/
  agent/
```

### 設定ファイル（#211）

| ファイル | 内容 | 雛形 |
|---|---|---|
| `../platform/.env` | 3製品共通の設定（`PLATFORM_*`）。OCI 認証・アップロード保存先・モデル・データベース。システム設定画面の保存先 | [`../platform/.env.example`](../platform/.env.example) |
| `backend/.env` | Agent 固有の設定（`AGENT_*`） | [`backend/.env.example`](backend/.env.example) |
| `../platform/model-settings.json` | モデル設定（画面から保存。3製品で共有） | — |

backend は共通 `.env` → `backend/.env` の順に読み、環境変数が最優先です。共通 `.env` の場所は
`PLATFORM_ENV_FILE` で変えられます。接頭辞のない旧名（`ORACLE_DSN` / `LOG_LEVEL` など）は読みません。

```bash
cp ../platform/.env.example ../platform/.env   # 共通（RAG / NL2SQL と同じファイル）
cp backend/.env.example backend/.env

# backend
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8020

# frontend
cd frontend
npm install
BACKEND_URL=http://127.0.0.1:8020 npm run dev
```

frontend の Vite は `BACKEND_URL` を明示したときだけ `/api` を backend へ proxy します（既定の接続先は持ちません）。
`BACKEND_URL` を渡さずに `npm run dev` すると hermetic モードになり、`/api` は proxy されず 404 を返します（起動時に警告を 1 行表示）。
`scripts/start-all.sh` / `scripts/start-frontend.sh` は `BACKEND_URL` を明示して起動します。

既存 helper を使う場合:

```bash
scripts/start-all.sh
scripts/check-all.sh
```

## Docker Compose

Runtime は必要な profile だけ起動します。Docker socket は mount されません。

設定は次の 3 つです（#211）。

- `../platform/.env`（3製品共通の `PLATFORM_*`）: `../platform/` を書き込み可能で `/app/platform` にマウントし、
  `PLATFORM_ENV_FILE=/app/platform/.env` で読みます。システム設定画面の保存先なので env_file では渡しません
  （環境変数は `.env` より優先されるため、画面で保存した値が反映されなくなります）。
- `backend/.env`（Agent 固有の `AGENT_*`）: `env_file` で渡します（無くても起動します）。
- `agent/.env`（`.env.runtime.example` から作る）: compose の `${...}` 補間用です。ここに書いた値は
  `backend/.env` より優先されます。Runtime API の認証値は `AGENT_OPENCLAW_GATEWAY_TOKEN` /
  `AGENT_HERMES_API_SERVER_KEY` / `AGENT_DEER_FLOW_INTERNAL_AUTH_TOKEN` に書き、compose が各 Runtime の
  期待する名前（`OPENCLAW_GATEWAY_TOKEN` / `API_SERVER_KEY` / `DEER_FLOW_INTERNAL_AUTH_TOKEN`）へ渡します。

```bash
cp ../platform/.env.example ../platform/.env
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

## 既存環境の更新手順（#211）

#211 で設定を共通 `.env`（`platform/.env`、`PLATFORM_*`）と Agent の `backend/.env`（`AGENT_*`）に分けました。
旧名は読まないため、既存環境では更新後に 1 回だけ次を行います。

1. backend（systemd の `production-ready-agent-backend` または compose の `control-plane` / `runtime-dispatcher`）を停止する。
2. 移行内容を確認する（書き換えない）。monorepo root で実行します。

   ```bash
   uv run --project platform/packages/backend_core \
       python platform/scripts/migrate_env_to_platform.py --product agent
   ```

3. 内容に問題がなければ `--apply` で書き換える（`<file>.bak-211` を作ります）。共通の変数は `platform/.env` へ移り、
   残りは `AGENT_` 接頭辞になります。

   ```bash
   uv run --project platform/packages/backend_core \
       python platform/scripts/migrate_env_to_platform.py --product agent --apply
   ```

4. スクリプトが扱わないものを手で直す。
   - compose の `agent/.env`: `OPENCLAW_GATEWAY_TOKEN` / `HERMES_API_SERVER_KEY` / `DEER_FLOW_INTERNAL_AUTH_TOKEN` を
     `AGENT_OPENCLAW_GATEWAY_TOKEN` / `AGENT_HERMES_API_SERVER_KEY` / `AGENT_DEER_FLOW_INTERNAL_AUTH_TOKEN` へ改名する。
   - Binding 個別の MCP token（`CONTROL_PLANE_MCP_TOKEN_<BINDING_ID>`）を `AGENT_BINDING_MCP_TOKEN_<BINDING_ID>` へ改名する
     （移行スクリプトは `AGENT_CONTROL_PLANE_MCP_TOKEN_<BINDING_ID>` にするため、その名前からも改名する）。
     Runtime 側で `api_key_env` を固定で設定している場合は、Binding を再同期する。
   - 既存の Runtime 定義（snapshot や Oracle に保存済み）の `auth_secret_ref` が旧名（`OPENCLAW_GATEWAY_TOKEN` など）の
     場合は、Runtime 画面または `PATCH /api/runtimes/{id}` で新名へ変更する。
5. 再配備または再起動する。
   - Resource Manager の stack で配備した instance: 手順 1〜4 を instance 上の
     `/u01/aipoc/no.1-production-ready-suite` で（`git pull` と `agent/backend` の `uv sync --locked --no-dev` の後に）
     行い、`sudo systemctl restart production-ready-agent-backend` を実行する。新しく配備する stack は最初から新構成で作られる。
   - Docker Compose: `docker compose up -d control-plane`（dispatcher を使う場合は `--profile dispatcher` も）で作り直す。

## OCI への配備（Resource Manager）

Compute 1 台 + Autonomous AI Database を OCI Resource Manager で配備する Terraform stack を
`terraform/stack/` に置いています。入力・instance 上の構成・制約は [terraform/README.md](terraform/README.md) を参照してください。

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
