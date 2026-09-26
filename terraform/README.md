# Production Ready Suite — OCI Resource Manager stack

`terraform/stack/` は、3製品（RAG / NL2SQL / Agent Control Plane）を OCI Resource Manager から配備する **1つだけの** Terraform stack です（#217）。
以前は製品ごとに `rag/` `nl2sql/` `agent/` の下に stack がありましたが、この stack に統合しました。

作成・設定するもの:

- Oracle Autonomous AI Database 26ai を **1つ**（新規作成、または既存 ADB の選択）と、その Wallet。選んだ製品すべてで共有する
- 選んだ製品ごとに OCI Compute を **1台ずつ**（Ubuntu。shape / image / subnet / SSH 鍵は共通、OCPU / メモリ / boot volume は製品ごと）
- 各 Compute の cloud-init。suite monorepo を1回 clone し、その製品の `init_script.sh`（[`rag/`](../rag/init_script.sh) / [`nl2sql/`](../nl2sql/init_script.sh) / [`agent/`](../agent/init_script.sh)）で配備する

```text
                 ┌──────────────────────────────┐
                 │ Autonomous AI Database（1つ） │  RAG_* / NL2SQL_* / AGENT_* の table
                 └──────────────┬───────────────┘
                 Wallet（1つ）を全 Compute に配る
        ┌───────────────────────┼────────────────────────┐
  deploy_rag              deploy_nl2sql              deploy_agent
  RAG_INSTANCE            NL2SQL_INSTANCE            AGENT_INSTANCE
  Docker Compose + Nginx  systemd + Nginx            systemd + Nginx（Basic 認証）
```

## 配備する製品の選択

Resource Manager の「配備する製品」で、`deploy_rag` / `deploy_nl2sql` / `deploy_agent` を選びます（複数選択可、既定はすべて）。

- **最低1つは選んでください。** 1つも選ばないと plan が precondition で失敗します。
- 選んだ製品の入力グループ（`RAG` / `NL2SQL` / `NL2SQL Deep Data Security` / `Agent Control Plane`）だけがフォームに表示されます。
- 選んだ製品のパスワード（`rag_app_login_password` / `nl2sql_app_admin_login_user_password` / `agent_app_basic_auth_password`）は必須です。
  選ばなかった製品のパスワードは空で構いません。
- 後から製品を追加・削除するときは、同じ stack で選択を変えて apply します。選択を外した製品の Compute は削除され、ADB と他製品の Compute はそのまま残ります。

## Autonomous AI Database（全製品で共有）

- `adb_deployment_mode` で「新規 Autonomous AI Database の作成」か「既存の Autonomous AI Database を選択」を選びます。
  - 新規: 既定の DB 名は `SUITEADB`、workload は `OLTP`（RAG の取込・Agent の Runtime checkpoint が継続して書き込むため）、ECPU は `2`。
    3製品を載せる場合は、負荷に合わせて ECPU 数とストレージを上げてください。
  - 既存: ADB の OCID と、全製品の `ORACLE_USER` / `ORACLE_PASSWORD` に書く値を入力します。`ORACLE_DSN` は空なら `<db_name>_high` です。
    stack は既存 ADB から Wallet を生成するだけで、ネットワーク・アクセス・mTLS・ACL は変更しません。
- 新規 ADB の場合、全製品が `ADMIN` で接続します。製品のテーブルは `RAG_` / `NL2SQL_` / `AGENT_` の接頭辞で分かれているため、同じスキーマでも衝突しません。
- ネットワーク・アクセス（既定はプライベート・エンドポイント）の画面構成は Autonomous AI Database の作成画面に合わせています。
  **Compute は全製品で同じ subnet に置きます。** ACL（許可された IP および VCN）を使う場合は、その subnet（または VCN）を許可してください。
  プライベート・エンドポイントの場合は、Compute の subnet から ADB へ TCP `1522` で到達できる必要があります。
- DB の password / user / DSN に single quote は使えません（RAG の `backend/.env` は値を single quote で囲むため、全製品で同じ規則にしています）。
- ADB の DDL は Terraform にも cloud-init にも持ちません。各製品の `init_script.sh` がアプリの CLI（冪等）で作成・更新します。

## 製品ごとの入力と instance 上の構成

AI の設定（OCI 認証、OCI Enterprise AI、埋め込み / リランクなど）は stack では受け取りません。起動後に各アプリのシステム設定で行います。
stack は secret を cloud-init に埋め込んで `backend/.env` を作るため、Resource Manager の stack・job 履歴・state は機密として扱ってください。

### RAG（`deploy_rag`）

RAG は文書の前処理と解析を独立したマイクロサービスで動かすため（parser ごとに依存が大きく異なり、同じ Python 環境に同居できないものもある）、
Compute に直接インストールせず [`rag/docker-compose.yml`](../rag/docker-compose.yml) を使います。

- ログイン: backend の Cookie session login（`AUTH_MODE=production`）。`rag_app_login_user`（既定 `rag_admin`）→ `AUTH_USERNAME`、
  `rag_app_login_password`（12〜64 文字、英大文字・小文字・数字を含む）→ `AUTH_PASSWORD`。
  `rag_app_auth_cookie_secure` は HTTPS の終端を前に置いたら `true` にします。`AUTH_SESSION_SECRET` と `AUDIT_CONTEXT_HASH_SALT` は instance 上で生成します。
- 文書解析は CPU の parser だけを配備します。GPU の parser（MinerU / Dots.OCR / GLM-OCR / Unlimited-OCR）は含めず、
  起動後に「検索・回答設定 > 文書解析」で外部 API として指定します。

  | service | 既定 | 入力 |
  |---|---|---|
  | `parser-unstructured` | 常に起動 | 既定の解析方式（`RAG_PARSER_ADAPTER_BACKEND=unstructured`） |
  | `parser-docling` | 起動 | `rag_enable_parser_docling` |
  | `parser-marker` | 起動しない | `rag_enable_parser_marker`（CPU とメモリを多く使う） |
  | `parser-oci-genai-vision` / `parser-oci-document-understanding` | 起動しない | `rag_enable_oci_cloud_parsers` |

- Compute の既定は 4 OCPU / 32 GB / boot volume 200 GB（parser のモデルと Docker image が大きいため）。

| 項目 | 値 |
|---|---|
| backend | compose の `backend`（`127.0.0.1:8000` だけに公開）と `ingestion-worker`。host の Nginx が `frontend/dist` を配信し `/api/` を proxy |
| compose | `rag-compose`（project 名 `production-ready-rag`）と `production-ready-rag.service` |
| 設定 | `/u01/aipoc/no.1-production-ready-suite/rag/backend/.env`（compose の env_file、`0600`） |
| ログ | `/var/log/rag-init.log`、`sudo rag-compose logs -f backend` |

```bash
sudo tail -f /var/log/rag-init.log
sudo rag-compose ps
# RAG の system schema の初期化（冪等）を手動で再実行する
sudo rag-compose run --rm --no-deps -T backend uv run --no-sync python -m app.rag.system_schema_cli initialize
sudo systemctl restart production-ready-rag
```

### NL2SQL（`deploy_nl2sql`）

- ログイン: 組み込みの `SYSTEM_ADMIN`（ユーザー ID は `system_admin` 固定）。`nl2sql_app_admin_login_user_password`（12〜30 文字、`admin` と `"` を含まない）。
- Deep Data Security: `nl2sql_oracle_deepsec_enabled`（既定 `true`）。有効な場合は `nl2sql_oracle_deepsec_data_user_password` が必要です。
- Nginx + systemd に直接配備します（Docker は使いません）。Compute の既定は 2 OCPU / 16 GB / 100 GB。
- instance 上の構成・更新手順・障害対応は [nl2sql/docs/compute-operations.md](../nl2sql/docs/compute-operations.md) を参照してください。

### Agent Control Plane（`deploy_agent`）

- Agent には組み込みの login が無いため、Nginx の **HTTP Basic 認証**で UI と API 全体を保護します。
  `agent_app_basic_auth_user`（既定 `agent_admin`）と `agent_app_basic_auth_password`（12〜64 文字、`"` `'` `:` を含まない）。
  instance には SHA-512 crypt の hash だけを保存します。`/health` と Binding MCP（`/api/mcp/`）は対象外です。
- Runtime 連携（任意）: `agent_control_plane_public_base_url`（空なら `http://<Compute の private IP>[:port]/api`）、
  `agent_control_plane_mcp_token_secret`（32 文字以上。空なら Binding MCP は fail closed）。
- Agent Runtime（OpenClaw / Hermes / DeerFlow）はこの stack では配備しません。起動後に Runtime 画面から登録します。
- Runtime 状態は Oracle に保存します（`oracle_checkpoint`、table は backend が起動時に作成）。gunicorn は 1 worker、dispatcher は `in_process` に固定します。
- Compute の既定は 2 OCPU / 16 GB / 100 GB。

| 項目 | 値 |
|---|---|
| backend | `production-ready-agent-backend.service`（`127.0.0.1:8020`、1 worker） |
| 設定 | `/u01/aipoc/no.1-production-ready-suite/agent/backend/.env` |
| データ | `/u01/data/production-ready-agent`（model 設定、Binding、Artifact） |
| ログ | `/var/log/agent-init.log`、`journalctl -u production-ready-agent-backend` |

```bash
sudo tail -f /var/log/agent-init.log
sudo journalctl -u production-ready-agent-backend -f
curl -i http://127.0.0.1:8020/api/health
```

### 全 Compute で共通

- 公開 port は `application_port`（既定 `80`）。製品ごとに別の Compute なので port は衝突しません。
- Wallet は `/u01/aipoc/wallet`、cloud-init のログは `/var/log/cloud-init-custom.log`。
- apply 後の output に、製品ごとの URL（`rag_application_url` など）と SSH command が出ます。配備しなかった製品の output は表示されません。
- 製品間の HTTP 連携（Agent から RAG / NL2SQL を呼ぶなど）は、別 Compute の private IP を指定します。必要な port を VCN のセキュリティ・ルールで開けてください。

## パッケージと検証

monorepo root から実行します。

```bash
python terraform/scripts/package_stack.py
python terraform/scripts/verify_stack_contract.py terraform/dist/production-ready-suite-terraform-stack.zip
```

出力は `terraform/dist/production-ready-suite-terraform-stack.zip` です。この zip を Resource Manager へ upload して stack を作成します。
`verify_stack_contract.py` は、フォーム（`schema.yaml`）と Terraform 変数の一致、製品選択の契約、製品ごとの `backend/.env` の key が各製品の Settings にあること、
RAG の compose service、各製品の `init_script.sh` の配備契約を検証します。

CI（`.github/workflows/ci.yml` の `Suite / Terraform`）は、`terraform fmt` / `terraform validate`（Terraform 1.5.7）と上の2つを実行します。
各製品の `init_script.sh` のテスト（`<製品>/scripts/tests/init-script-deployment.test.sh`）は製品ごとの job が実行します。実テナンシーへの配備確認は手動で行います。

## release

`.github/workflows/terraform-release.yml` が `suite-v*` の tag（例: `suite-v0.1.0`）から release を作り、
`production-ready-suite-terraform-stack.zip` と `.sha256` を公開します。最初の release（`suite-v0.1.0`）を作った後は、次のボタンで配備できます
（tag を固定して参照します）。

[![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-suite/releases/download/suite-v0.1.0/production-ready-suite-terraform-stack.zip)

製品ごとの release（`rag-v*` / `nl2sql-v*` / `agent-v*`）は作りません。既に公開済みの release（例: `nl2sql-v0.1.32`）はそのまま残ります。

## 製品ごとの stack からの移行

既存の製品ごとの stack で作った環境は、そのまま動き続けます。統合 stack へ移る場合は、新しい stack を作って「既存の Autonomous AI Database を選択」で
今の ADB を指定し、配備する製品を選んで apply します（Compute は新しく作られます）。新しい Compute で動作を確認してから、古い stack の Compute を destroy してください。
古い stack が ADB を作っていた場合、その stack を destroy すると ADB も削除されます。

入力の名前は、製品固有のものに製品名の接頭辞を付けました。

| 旧（製品ごとの stack） | 新（統合 stack） |
|---|---|
| RAG の `app_login_user` / `app_login_password` / `app_auth_cookie_secure` | `rag_app_login_user` / `rag_app_login_password` / `rag_app_auth_cookie_secure` |
| RAG の `enable_parser_docling` / `enable_parser_marker` / `enable_oci_cloud_parsers` | `rag_enable_parser_*` / `rag_enable_oci_cloud_parsers` |
| NL2SQL の `app_admin_login_user_password` / `oracle_deepsec_enabled` / `oracle_deepsec_data_user_password` | `nl2sql_app_admin_login_user_password` / `nl2sql_oracle_deepsec_enabled` / `nl2sql_oracle_deepsec_data_user_password` |
| NL2SQL の `app_environment` / `app_auth_cookie_secure` / `app_admin_login_user_id` | `nl2sql_app_environment` / `nl2sql_app_auth_cookie_secure` / `nl2sql_app_admin_login_user_id` |
| Agent の `app_basic_auth_user` / `app_basic_auth_password` | `agent_app_basic_auth_user` / `agent_app_basic_auth_password` |
| `instance_display_name` / `instance_flex_shape_ocpus` / `instance_flex_shape_memory` / `instance_boot_volume_size` | `<製品>_instance_display_name` / `<製品>_instance_flex_shape_ocpus` / `<製品>_instance_flex_shape_memory` / `<製品>_instance_boot_volume_size` |
| `adb_name` の既定 `RAGADB` / `NL2SQLADB` / `AGENTADB` | `SUITEADB` |
| NL2SQL の `adb_workload` の既定 `LH` | `OLTP`（3製品で共有するため） |
