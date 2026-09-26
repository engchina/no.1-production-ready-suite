# Production Ready RAG — OCI Resource Manager stack

`rag/terraform/stack/` は、Production Ready RAG を OCI Resource Manager から配備する Terraform stack です（#136）。
NL2SQL / Agent の stack と同じ「Compute 1 台 + ADB + cloud-init」の形です。以前の skeleton（`rag/infra/terraform/`）はこの stack に置き換えました。

作成・設定するもの:

- Oracle Autonomous AI Database 26ai（新規作成、または既存 ADB の選択）と、その Wallet
- OCI Compute 1 台（Ubuntu、既定 `VM.Standard.E5.Flex` 4 OCPU / 32 GB、boot volume 200 GB）
- cloud-init の bootstrap。suite monorepo を clone し、[`rag/init_script.sh`](../init_script.sh) で Docker Compose と Nginx を構成する

## 配備方式（なぜ Docker Compose か）

RAG は NL2SQL / Agent と違い、文書の前処理と解析を**独立したマイクロサービス**（`services/preprocess/*`、`services/parsers/*`）で動かします。
parser ごとに依存（LibreOffice、tesseract、Docling のモデルなど）が大きく異なり、同じ Python 環境に同居できないものもあります
（marker と unstructured は pillow 系で共存できない）。そのため、Compute に直接インストールせず、既存の
[`rag/docker-compose.yml`](../docker-compose.yml) をそのまま使います（README の「まとめて（Docker）」と同じ経路）。

- Docker Engine と compose / buildx plugin は Docker 公式の apt repository から入れます。
- `rag/docker-compose.yml` は変更せず、OCI 用の差分だけを `/u01/aipoc/rag-deploy/docker-compose.oci.yml`（init script が生成）で重ねます。
  - backend の公開を `127.0.0.1:8000` に限定する（既定の `0.0.0.0:8000` を `!override` で置き換える）
  - ADB の Wallet（`/u01/aipoc/wallet`）を backend と ingestion-worker に mount する
  - reboot 後も戻るよう `restart: unless-stopped` を付ける
- **frontend は compose の `frontend` service を使いません。** `frontend/Dockerfile` は build context が `rag/frontend` だけで、
  `file:../../platform/packages/ui` を解決できないためです。代わりに `node:22-slim` の container で platform と frontend を build し、
  host の Nginx が `frontend/dist` を配信して `/api/` を backend へ proxy します（SSE のため proxy buffering は無効）。
- 起動は systemd の `production-ready-rag.service`（`rag-compose up -d --no-build <service…>`）が行います。

OKE / Container Instances への配備は、利用者の規模が決まってから検討します（#136）。

## 文書解析（parser）

CPU の parser だけを配備します。

| service | 既定 | 入力 |
|---|---|---|
| `parser-unstructured` | 常に起動 | 既定の解析方式（`RAG_PARSER_ADAPTER_BACKEND=unstructured`） |
| `parser-docling` | 起動 | `enable_parser_docling` |
| `parser-marker` | 起動しない | `enable_parser_marker`（CPU とメモリを多く使う） |
| `parser-oci-genai-vision` / `parser-oci-document-understanding` | 起動しない | `enable_oci_cloud_parsers`（OCI を呼ぶ薄い proxy。GPU 不要） |

前処理（`preprocess-*` の 7 service）と、サービス管理画面の操作対象である `pipeline-generation` / `pipeline-retrieval` も起動します。
他の `pipeline-*` service は起動しません（backend は pipeline service に届かない場合、backend 内の同じ処理に縮退します）。

**GPU の parser（MinerU / Dots.OCR / GLM-OCR / Unlimited-OCR）はこの stack に含めません。** RAG はこれらを外部 API として扱うため、
GPU の環境を別に用意し、起動後に「検索・回答設定 > 文書解析」で API host を指定します。compose の `gpu` profile（ASR）も起動しません。
将来、GPU shape の Compute を任意で追加する場合は、`variables.tf` と `compute.tf` のコメントの位置に変数とリソースを足します。

## パッケージと検証

monorepo root から実行します。

```bash
python rag/scripts/package_terraform_stack.py
python rag/scripts/verify_terraform_stack_contract.py rag/dist/production-ready-rag-terraform-stack.zip
bash rag/scripts/tests/init-script-deployment.test.sh
```

出力は `rag/dist/production-ready-rag-terraform-stack.zip` です。この zip を Resource Manager へ upload して stack を作成します。
release は製品共通の `.github/workflows/terraform-release.yml` が `rag-v*` の tag から作ります（#94）。最初の release（`rag-v0.1.0`）を作った後は、次のボタンで配備できます（tag を固定して参照します。`releases/latest` は別製品の release を指しうるため使いません）。

[![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-suite/releases/download/rag-v0.1.0/production-ready-rag-terraform-stack.zip)

CI（`.github/workflows/ci.yml` の `RAG / Terraform`）は、`terraform fmt` / `terraform validate`（Terraform 1.5.7）、
zip の契約検証、`scripts/tests/init-script-deployment.test.sh` を実行します。実テナンシーへの配備確認は手動で行います。

## 入力

- 配備先: region、compartment、availability domain、VCN、Compute subnet、SSH 公開鍵
- アプリケーションへのアクセス: RAG の backend は Cookie session の login（`RAG_AUTH_MODE=production`）で UI と API を保護します。
  - `app_login_user`（既定 `rag_admin`）→ `RAG_AUTH_USERNAME`、`app_login_password`（必須、12〜64 文字、英大文字・小文字・数字を含む）→ `RAG_AUTH_PASSWORD`
  - `app_auth_cookie_secure` → `RAG_AUTH_COOKIE_SECURE`。HTTP のまま使う間は `false`（既定）。HTTPS の終端を前に置いたら `true` にします。
  - `RAG_AUTH_SESSION_SECRET` と `RAG_AUDIT_CONTEXT_HASH_SALT` は instance 上で `openssl rand` で1回だけ生成します（Terraform の state には残りません）。
- 文書解析: `enable_parser_docling` / `enable_parser_marker` / `enable_oci_cloud_parsers`（上の表）
- Autonomous AI Database: 画面構成とネットワーク・アクセス（既定はプライベート・エンドポイント）は NL2SQL / Agent の stack と同じです。
  既定の DB 名は `RAGADB`、workload は `OLTP`（取込 job・chunk・vector を継続して書き込むため）です。

AI の設定（OCI 認証、OCI Enterprise AI、埋め込み / リランク、アップロード保存先）は stack では受け取りません。起動後にアプリケーションのシステム設定で行います。
入力は RAG 固有の設定（ログイン等。`RAG_*`）を `rag/backend/.env`、3製品共通の設定（DB 接続・OCI region / compartment・アップロード保存先。`PLATFORM_*`）を共通 `.env`（`platform/.env`）に分けて書きます（#211）。
compose の env_file は `$` を展開するため、入力の値は single quote で囲んで書きます。そのため、password と DSN に single quote は使えません。
stack は secret を cloud-init に埋め込んで `backend/.env` と `platform/.env` を作るため、Resource Manager の stack・job 履歴・state は機密として扱ってください。
`platform/.env` はシステム設定画面の保存先でもあるため、init script は既にあれば上書きしません（再実行で画面の保存値を消さない）。

## ADB の schema（DDL）

Oracle 26ai の table / vector index / Oracle Text / audit table の DDL は Terraform にも init script にも埋め込みません。

- 配備時は init script が、アプリの CLI（冪等）で RAG の system schema を作成・更新します。
  ```bash
  sudo rag-compose run --rm --no-deps -T backend uv run --no-sync python -m app.rag.system_schema_cli initialize
  ```
  失敗しても backend は起動します。ADB に届くようになったら上の command を再実行するか、画面の
  「システム設定 > データベース > RAG システムテーブル」から初期化します。
- 変更前に DDL をレビューしたい場合は、backend の `uv run python -m app.rag.oracle_schema --output ../artifacts/oracle-schema.sql --manifest-output ../artifacts/oracle-schema.manifest.json` で生成した artifact を確認し、SQLcl または管理された migration 手順で適用します。

## instance 上の構成

| 項目 | 値 |
|---|---|
| 公開 port | `application_port`（既定 `80`）。host の Nginx が `frontend/dist` を配信し、`/api/` を backend へ proxy |
| backend | compose の `backend`（gunicorn + UvicornWorker、`127.0.0.1:8000` だけに公開） |
| 取込 | compose の `ingestion-worker`（取込 queue を消費する専用 worker） |
| compose | `rag-compose`（`/usr/local/bin/rag-compose`。project 名 `production-ready-rag`）と `production-ready-rag.service` |
| 設定 | RAG 固有: `/u01/aipoc/no.1-production-ready-suite/rag/backend/.env`（compose の env_file、`0600`）<br>3製品共通: `/u01/aipoc/no.1-production-ready-suite/platform/.env`（backend / ingestion-worker に `platform/` を mount し `PLATFORM_ENV_FILE` で読み書き。container の appuser が所有、`0600`。`model-settings.json` も同じディレクトリ） |
| Wallet | `/u01/aipoc/wallet`（container の appuser が所有、`0700` / file は `0600`）。Thin mode + Wallet(mTLS) で接続（`PLATFORM_ORACLE_CLIENT_LIB_DIR` は空） |
| データ | compose の named volume（`backend-local-storage`: 原本、`oci-config`: OCI 認証） |
| ログ | `/var/log/cloud-init-custom.log`、`/var/log/rag-init.log`、`sudo rag-compose logs -f backend` |

- `RAG_ENVIRONMENT` / `PLATFORM_ENV_FILE` / `PLATFORM_LOCAL_STORAGE_DIR` / `PLATFORM_OCI_CONFIG_FILE` と各 service の URL は `docker-compose.yml` の `environment` が正本です（`.env` には書きません）。
- 既存の instance を #211 以降の版へ更新する手順は [docs/deployment.md](../docs/deployment.md) の「既存環境の更新手順（#211）」を参照してください。
- サービス管理画面の起動/停止（`RAG_SERVICE_CONTROL_ENABLED`）は無効のままです（backend に docker socket を渡さない）。
- `/api/ready` は OCI / AI の設定が揃うまで `503` を返すため、compose の healthcheck は設定前は unhealthy になります。init script は `/api/health` で起動を確認します。

## トラブルシューティング

```bash
sudo tail -f /var/log/rag-init.log
sudo systemctl status production-ready-rag
sudo rag-compose ps
sudo rag-compose logs --tail 200 backend ingestion-worker
curl -i http://127.0.0.1:8000/api/health
curl -i http://127.0.0.1/health

# RAG の system schema の初期化（冪等）を手動で再実行する
sudo rag-compose run --rm --no-deps -T backend uv run --no-sync python -m app.rag.system_schema_cli initialize
sudo systemctl restart production-ready-rag
```

ADB がプライベート・エンドポイントの場合、Compute の subnet から ADB へ TCP `1522` で到達できる必要があります。
初回の配備は Docker image の build（Docling のモデルを含む）に時間がかかります。
