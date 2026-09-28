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
- Runtime service management: 第三者の Runtime イメージ（固定 digest）の profile・healthcheck・volume・静的操作 allowlist（`docker-compose.yml`）。
- Snapshot v2: Runtime/Binding を含む Control Plane backup。v1 snapshot/manifest を移行。
- ログインと権限: RAG / NL2SQL と同じ共通認証（`AGENT_AUTH_MODE=production`）。ロールごとの権限と、
  エージェント・業務ビュー単位の対象範囲（「Agent セキュリティ > 権限管理」）。詳細は
  [docs/security-rbac.md](docs/security-rbac.md)。

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
| `../platform/.env` | 3製品共通の設定（`PLATFORM_*`）。OCI 認証・アップロード保存先・モデル・データベース・構成管理者（`PLATFORM_ADMIN_*`）と認証ポリシー（`PLATFORM_AUTH_*`）。システム設定画面の保存先 | [`../platform/.env.example`](../platform/.env.example) |
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

ローカル開発の既定は `AGENT_AUTH_MODE=local`（全権限のローカル利用者。ログイン不要）です。ログインを確認するときは
`AGENT_AUTH_MODE=production` にし、共通 `.env` の `PLATFORM_ORACLE_*` と `PLATFORM_ADMIN_LOGIN_USER_PASSWORD` を設定して
`cd backend && uv run python -m app.cli.agent_security_migrate` でテーブルを作ります（[docs/security-rbac.md](docs/security-rbac.md)）。

既存 helper を使う場合:

```bash
scripts/start-all.sh
scripts/check-all.sh                                        # ローカルの既定は e2e と pip-audit を省く（#339）
SKIP_E2E=0 E2E_ARGS="e2e/auth-login.spec.ts" scripts/check-all.sh   # 関係する spec だけ e2e も実行
FULL=1 scripts/check-all.sh                                 # CI と同じく全部を実行
```

## Runtime（第三者の構築済みイメージ）と dispatcher

Control Plane（backend・frontend）と runtime-dispatcher は自前のコードなので、Docker を使わずネイティブで動かします
（開発は上の `uv run` / `scripts/start-all.sh`、本番は systemd。#286 / #356）。Docker を使うのは、
第三者が構築済みのイメージを配る Agent Runtime（OpenClaw・Hermes・DeerFlow）だけです。`docker-compose.yml` は
この 3 つだけを持ち、イメージは公式 registry の `@sha256` で固定しています（派生イメージは作りません）。
必要な profile だけ起動します。Docker socket は mount されません。

- `agent/.env`（`.env.runtime.example` から作る）: compose の `${...}` 補間用です。Runtime API の認証値を
  `AGENT_OPENCLAW_GATEWAY_TOKEN` / `AGENT_HERMES_API_SERVER_KEY` / `AGENT_DEER_FLOW_INTERNAL_AUTH_TOKEN` に書き、
  compose が各 Runtime の期待する名前（`OPENCLAW_GATEWAY_TOKEN` / `API_SERVER_KEY` / `DEER_FLOW_INTERNAL_AUTH_TOKEN`）へ
  渡します。Control Plane の backend にも同じ名前・同じ値を設定します（Runtime 定義の `auth_secret_ref` が参照します）。
- Control Plane が Binding ごとに書き出す Skill / MCP（`AGENT_RUNTIME_BINDINGS_DIR`、開発の既定は
  `backend/.agent-runtime-bindings`）を、各 Runtime へ読み取り専用で渡します。場所を変えたときは `agent/.env` の
  `AGENT_RUNTIME_BINDINGS_HOST_DIR` も合わせます。
- Runtime から Control Plane の Binding MCP を呼ぶ場合は、backend の `AGENT_CONTROL_PLANE_PUBLIC_BASE_URL` を
  `http://host.docker.internal:8020/api` にします（compose が `host.docker.internal` を host へ向けます。
  `scripts/start-backend.sh` は既定で `0.0.0.0:8020` で listen します）。

```bash
cp .env.runtime.example .env
docker compose --profile openclaw up -d runtime-openclaw
docker compose --profile hermes up -d runtime-hermes
docker compose --profile deerflow up -d runtime-deerflow
```

本番 dispatcher を使う場合は Oracle 設定を入れ、backend の `AGENT_RUNTIME_DISPATCH_MODE=external` を有効にして、
dispatcher を別のプロセスで起動します（`backend/.env` と共通 `.env` を読みます）。

```bash
cd backend
AGENT_RUNTIME_DISPATCH_MODE=external uv run python -m app.features.agent.runtime_dispatcher
```

公式 Runtime image は `docker-compose.yml` で `@sha256` 固定しています。更新時は公式 release と
manifest を検証して digest を明示更新してください。

### Control Plane を Docker Compose で動かしていた環境の移行（#356）

#356 で、Control Plane の Dockerfile と compose の `control-plane` / `runtime-dispatcher` を削除しました。
compose の project 名（`production-ready-agent-control-plane`）は変えていないので、Runtime の volume はそのまま使えます。

1. 以前のコンテナを止めて消します（`agent/` で実行。Runtime の volume は残ります）。

   ```bash
   for service in control-plane runtime-dispatcher; do
     docker ps -aq --filter label=com.docker.compose.project=production-ready-agent-control-plane \
       --filter "label=com.docker.compose.service=${service}" | xargs -r docker rm -f
   done
   ```

2. 以前の `agent/.env` に書いていた Control Plane の設定（`AGENT_AUTH_MODE` / `AGENT_RUNTIME_*` /
   `AGENT_CONTROL_PLANE_*` など）は `backend/.env` へ移します（`agent/.env` は Runtime の補間だけに使います）。
3. Control Plane の状態を named volume（`production-ready-agent-control-plane_control-plane-state`）に置いていた場合、
   Binding の書き出し（`bindings/`）は次の同期で作り直されます。`AGENT_RUNTIME_REPOSITORY_BACKEND=file` などで
   volume に保存していた状態を引き継ぐときは、中身を backend の保存先へ写してから volume を消します。
4. backend を `uv run`（開発）または systemd の `production-ready-agent-backend`（Resource Manager の stack）で起動し、
   初回は `cd backend && uv run python -m app.cli.agent_security_migrate` でテーブルを作ります。

## 既存環境の更新手順（#211）

#211 で設定を共通 `.env`（`platform/.env`、`PLATFORM_*`）と Agent の `backend/.env`（`AGENT_*`）に分けました。
旧名は読まないため、既存環境では更新後に 1 回だけ次を行います。

1. backend（systemd の `production-ready-agent-backend`、または開発の `uv run`）と、使っている場合は runtime-dispatcher を停止する。
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
   - 開発: backend（と dispatcher）を `uv run` で起動し直す。

## 既存環境の更新手順（#215）

#215 で Agent も共通認証のログインと権限管理になり、Resource Manager の stack の Nginx Basic 認証を廃止しました。
共通 `.env` の `PLATFORM_ADMIN_*` の確認、`AGENT_AUTH_MODE=production`、`agent_security_migrate` の実行、
Nginx の Basic 認証の削除の手順は [docs/security-rbac.md §8](docs/security-rbac.md#8-既存環境の更新手順215) を参照してください。

## OCI への配備（Resource Manager）

3製品共通の Terraform stack（monorepo root の `terraform/stack/`）で配備します。Autonomous AI Database は RAG / NL2SQL と共有し、
Agent Control Plane は専用の Compute 1 台に配備します。ログインは構成管理者 `system_admin`（`app_admin_login_user_password`。RAG / NL2SQL と共通）と、
ユーザー管理で作る DB ユーザーです。「配備する製品」で Agent Control Plane を選び、入力・instance 上の構成・制約は
[terraform/README.md](../terraform/README.md) を参照してください。

## 主要 API

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/health` / `/api/ready` | 稼働確認・readiness（ログイン不要） |
| `GET` | `/api/ready/database` | 画面の DB ゲートが使う DB の状態（3製品共通の判定と契約。常に 200。ログイン不要。#325）。`ok` / `not_configured` / `unreachable` を返す（Agent はシステムテーブルの確認をまだ持たないため `setup_required` は返さない）。ローカル認証（`AGENT_AUTH_MODE=local`）は共通 DB を使わないため、接続を試さず `ok`（`detail=local_auth`）を返し、画面のゲートは出ない。画面はシステム設定の 5 画面以外で、DB が使えるまで本文を案内に替える |
| `POST` | `/api/auth/login` / `/api/auth/logout` / `/api/auth/password/change` | ログイン・ログアウト・パスワード変更（共通認証） |
| `GET` | `/api/auth/me` | ログイン中の利用者（実効権限・`allowed_agent_ids` / `allowed_business_view_ids`） |
| `GET/POST/PATCH/DELETE` | `/api/security/users*` / `/api/security/roles*` | ユーザー管理・ロール管理（3製品共通） |
| `GET` | `/api/security/permissions` / `/api/security/access-targets` | 権限カタログ・権限管理で選べるエージェントと業務ビュー |
| `PUT` | `/api/security/roles/{role_id}/access` | ロールの Agent 権限と対象範囲 |
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

- production（`AGENT_AUTH_MODE=production`）は全 API を既定拒否の権限 manifest で守ります。Cookie のないリクエストは
  `AGENT_RBAC_ENABLED=true` かつ信頼できる identity（HMAC 署名 header・JWT・外部 policy）があるときだけその identity で判定し、それ以外は 401 です（`X-Agent-Roles` の自己申告は信じません）（[docs/security-rbac.md](docs/security-rbac.md)）。
- WebSocket は Cookie のセッションで `Origin` と `Host` の一致を必須にします。承認の決定者はログイン中の利用者です。
- Runtime secret は環境変数値ではなく env 名で参照し、API/snapshot/log に値を出しません。
- Binding MCP は Binding 固有 token と Skill allowlist の両方を検証します。
- Plugin install は ID 衝突時に全体を失敗させます。参照中 Skill は削除できません。
- Runtime の capability が無い操作は `409` で fail closed します。
- Runtime 自動 failover は行いません。
- Control Plane に別 LLM provider、外部 vector DB、新規 queue 製品を追加しません。

## 検証

```bash
cd backend
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run pytest -q

cd ../frontend
npm run build
npm run test:e2e

cd ..
docker compose --profile openclaw --profile hermes --profile deerflow config --quiet   # 第三者 Runtime の compose
```
