# Production Ready Agent Control Plane — OCI Resource Manager stack

`agent/terraform/stack/` は、Agent Control Plane を OCI Resource Manager から配備する Terraform stack です（#136）。
NL2SQL の stack（`nl2sql/terraform/stack/`）と同じ「Compute 1 台 + ADB + cloud-init」の構成です。

作成・設定するもの:

- Oracle Autonomous AI Database 26ai（新規作成、または既存 ADB の選択）と、その Wallet
- OCI Compute 1 台（Ubuntu、既定 `VM.Standard.E5.Flex` 2 OCPU / 16 GB）
- cloud-init の bootstrap。suite monorepo を clone し、[`agent/init_script.sh`](../init_script.sh) で
  Nginx + systemd に直接配備する（Docker は使わない）

Agent Runtime（OpenClaw / Hermes / DeerFlow）はこの stack では配備しません。起動後に Runtime 画面から登録します。

## パッケージと検証

monorepo root から実行します。

```bash
python agent/scripts/package_terraform_stack.py
python agent/scripts/verify_terraform_stack_contract.py agent/dist/production-ready-agent-terraform-stack.zip
```

出力は `agent/dist/production-ready-agent-terraform-stack.zip` です。この zip を Resource Manager へ upload して stack を作成します。
release は製品共通の `.github/workflows/terraform-release.yml` が `agent-v*` の tag から作ります（#94）。最初の release（`agent-v0.1.0`）を作った後は、次のボタンで配備できます（tag を固定して参照します。`releases/latest` は別製品の release を指しうるため使いません）。

[![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-suite/releases/download/agent-v0.1.0/production-ready-agent-terraform-stack.zip)

CI（`.github/workflows/ci.yml` の `Agent / Terraform`）は、`terraform fmt` / `terraform validate`（Terraform 1.5.7）、
zip の契約検証、`scripts/tests/init-script-deployment.test.sh` を実行します。実テナンシーへの配備確認は手動で行います。

## 入力

- 配備先: region、compartment、availability domain、VCN、Compute subnet、SSH 公開鍵
- アプリケーションへのアクセス: Agent には組み込みの login が無いため、Nginx の **HTTP Basic 認証**で UI と API 全体を保護します。
  - `app_basic_auth_user`（既定 `agent_admin`）と `app_basic_auth_password`（必須、12〜64 文字、英大文字・小文字・数字を含む）
  - instance には SHA-512 crypt の hash だけを `/etc/nginx/production-ready-agent.htpasswd` に保存します
  - `/health` と Binding MCP endpoint（`/api/mcp/`）は Basic 認証の対象外です（MCP は Binding 固有 token で認証し、token が無ければ `503`）
- Agent Runtime 連携（任意）
  - `agent_control_plane_public_base_url` → `AGENT_CONTROL_PLANE_PUBLIC_BASE_URL`。空なら `http://<Compute の private IP>[:port]/api`
  - `agent_control_plane_mcp_token_secret` → `AGENT_CONTROL_PLANE_MCP_TOKEN_SECRET`（32 文字以上）。空なら Binding MCP は fail closed
- Autonomous AI Database: 画面構成とネットワーク・アクセス（既定はプライベート・エンドポイント）は NL2SQL の stack と同じです。
  既定の DB 名は `AGENTADB`、workload は `OLTP`（Runtime checkpoint と Run lease の保存が主用途のため）です。

AI の設定（OCI 認証、OCI Enterprise AI など）は stack では受け取りません。起動後にアプリケーションのシステム設定で行います。
stack は secret を cloud-init に埋め込んで `platform/.env`（3製品共通の `PLATFORM_*`）と `backend/.env`（Agent 固有の `AGENT_*`）を作るため（#211）、
Resource Manager の stack・job 履歴・state は機密として扱ってください。

## instance 上の構成

| 項目 | 値 |
|---|---|
| 公開 port | `application_port`（既定 `80`）。Nginx が `frontend/dist` を配信し、`/api/` を backend へ proxy |
| backend | `production-ready-agent-backend.service`（gunicorn + UvicornWorker、`127.0.0.1:8020`、1 worker） |
| 設定 | 共通 `/u01/aipoc/no.1-production-ready-suite/platform/.env`（`PLATFORM_*`: Oracle 接続・OCI・モデル・アップロード保存先）と Agent `/u01/aipoc/no.1-production-ready-suite/agent/backend/.env`（`AGENT_*`）。どちらも `ubuntu:ubuntu 0600` |
| Wallet | `/u01/aipoc/wallet`（`ubuntu:ubuntu 0700`、file は `0600`） |
| データ | `/u01/data/production-ready-agent`（model 設定 `model-settings.json`（`PLATFORM_MODEL_SETTINGS_FILE`）、Binding、Artifact） |
| ログ | `/var/log/cloud-init-custom.log`、`/var/log/agent-init.log`、`journalctl -u production-ready-agent-backend` |

- Runtime 状態は Oracle に保存します（`AGENT_RUNTIME_REPOSITORY_BACKEND=oracle_checkpoint`）。
  table は backend が起動時に作成します（`AGENT_RUNTIME_ORACLE_CREATE_SCHEMA=true`）。stack と init script は DDL を持ちません。
- 接続は python-oracledb Thin mode + Wallet(mTLS) です（`AGENT_RUNTIME_ORACLE_WALLET_DIR` / `AGENT_RUNTIME_ORACLE_WALLET_PASSWORD`）。
- dispatcher は `in_process`、gunicorn は 1 worker に固定します。checkpoint repository は process 内に状態を持つため、
  複数 worker や外部 dispatcher（`runtime-dispatcher`）を同時に動かすと状態がずれます。
- backend は起動時に Oracle へ接続します。ADB に届かない場合 backend は起動せず、systemd が再試行します。

cloud-init は `/u01/aipoc/props/platform.env` と `/u01/aipoc/props/backend.env`（root `0600`）を書き、
`init_script.sh` がそれぞれ `platform/.env` と `agent/backend/.env` へ置きます。
既存 instance を #211 の構成へ更新する手順は [../README.md](../README.md) の「既存環境の更新手順（#211）」を参照してください。

## トラブルシューティング

```bash
sudo tail -f /var/log/agent-init.log
sudo systemctl status production-ready-agent-backend
sudo journalctl -u production-ready-agent-backend -f
curl -i http://127.0.0.1:8020/api/health
curl -i http://127.0.0.1/health

# Oracle repository の初期化（冪等）を手動で再実行する
cd /u01/aipoc/no.1-production-ready-suite/agent/backend
sudo -u ubuntu /usr/local/bin/uv run python -c 'import app.features.agent.runtime'
sudo systemctl restart production-ready-agent-backend
```

ADB がプライベート・エンドポイントの場合、Compute の subnet から ADB へ TCP `1522` で到達できる必要があります。
