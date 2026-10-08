# Production Ready Suite — OCI Resource Manager stack

`terraform/stack/` は、3製品（RAG / NL2SQL / Agent Control Plane）を OCI Resource Manager から配備する **1つだけの** Terraform stack です（#217）。
以前は製品ごとに `rag/` `nl2sql/` `agent/` の下に stack がありましたが、この stack に統合しました。

作成・設定するもの:

- Oracle Autonomous AI Database を **1つ**（新規作成、または既存 ADB の選択）と、その Wallet。選んだ製品すべてで共有する
- 選んだ製品ごとに OCI Compute を **1台ずつ**（Ubuntu。shape / image / subnet / SSH 鍵は共通、OCPU / メモリ / boot volume は製品ごと）
- 各 Compute の cloud-init。suite monorepo を1回 clone し、その製品の `init_script.sh`（[`rag/`](../rag/init_script.sh) / [`nl2sql/`](../nl2sql/init_script.sh) / [`agent/`](../agent/init_script.sh)）で配備する
- 3製品で共通のサービス間 token の署名鍵（`random_password`。共通 `.env` の `PLATFORM_SERVICE_TOKEN_SECRET`。#233）

```text
                 ┌──────────────────────────────┐
                 │ Autonomous AI Database（1つ） │  RAG_* / NL2SQL_* / AGENT_* の table
                 └──────────────┬───────────────┘
                 Wallet（1つ）を全 Compute に配る
        ┌───────────────────────┼────────────────────────┐
  deploy_rag              deploy_nl2sql              deploy_agent
  RAG_INSTANCE            NL2SQL_INSTANCE            AGENT_INSTANCE
  systemd + Nginx         systemd + Nginx            systemd + Nginx
```

## 対応リージョン

- **stack は `ap-tokyo-1`（東京）と `ap-osaka-1`（大阪）だけをサポートします（#660）。** Compute の image（`instance_image_source_id`）は、この 2 つのリージョンの Ubuntu image だけを選択肢にしています。
  `us-chicago-1` など他のリージョンで stack を作ると、`region` の validation で plan が失敗します。
- 共通 `.env` の `PLATFORM_ORACLE_ADB_REGION` には stack のリージョンが入ります。システム設定 > データベース の「Autonomous Database 管理」のリージョンの選択肢も同じ 2 つです。

## 配備する製品の選択

Resource Manager の「配備する製品」で、`deploy_rag` / `deploy_nl2sql` / `deploy_agent` を選びます（複数選択可、既定はすべて）。

- **最低1つは選んでください。** 1つも選ばないと plan が precondition で失敗します。
- 選んだ製品の入力グループ（`RAG` / `NL2SQL` / `NL2SQL Deep Data Security` / `Agent Control Plane`）だけがフォームに表示されます。
- どの製品を選んでも `app_admin_login_user_password`（構成管理者 `system_admin`、3製品で共通）は必須です。
  NL2SQL で Deep Data Security を有効にする場合は `nl2sql_oracle_deepsec_data_user_password` も必要です。
- 後から製品を追加・削除するときは、同じ stack で選択を変えて apply します。選択を外した製品の Compute は削除され、ADB と他製品の Compute はそのまま残ります。

## Autonomous AI Database（全製品で共有）

- **対応バージョン**: 3製品とも Oracle AI Database 26ai 以降が前提です（`VECTOR` 型と Oracle AI Vector Search を使うため）。`adb_db_version` は既定の `26ai` のまま使ってください。
  NL2SQL の SQL ドメインと annotation は旧 Oracle Database 23ai で入った機能で、それより前のバージョンでは NL2SQL がその機能を使いません。
  リポジトリの文書・画面・コメントでは製品名にバージョンを入れず（#564）、対応バージョンはここだけに書きます。
- `adb_deployment_mode` で「新規 Autonomous AI Database の作成」か「既存の Autonomous AI Database を選択」を選びます。
  - 新規: 既定の DB 名は `SUITEADB`、workload は `OLTP`（RAG の取込・Agent の Runtime checkpoint が継続して書き込むため）、ECPU は `2`。
    3製品を載せる場合は、負荷に合わせて ECPU 数とストレージを上げてください。
  - 既存: ADB の OCID と、全製品の `PLATFORM_ORACLE_USER` / `PLATFORM_ORACLE_PASSWORD` に書く値を入力します。`PLATFORM_ORACLE_DSN` は空なら `<db_name>_high` です。
    stack は既存 ADB から Wallet を生成するだけで、ネットワーク・アクセス・mTLS・ACL は変更しません。
- 新規 ADB の場合、全製品が `ADMIN` で接続します。製品のテーブルは `RAG_` / `NL2SQL_` / `AGENT_`、3製品で共有するユーザー・ロール・セッションのテーブルは `PLATFORM_` の接頭辞で分かれているため、同じスキーマでも衝突しません（#212）。
- ネットワーク・アクセス（既定はプライベート・エンドポイント）の画面構成は Autonomous AI Database の作成画面に合わせています。
  **Compute は全製品で同じ subnet に置きます。** ACL（許可された IP および VCN）を使う場合は、その subnet（または VCN）を許可してください。
  プライベート・エンドポイントの場合は、Compute の subnet から ADB へ TCP `1522` で到達できる必要があります。
- DB の password / user / DSN に single quote は使えません（共通 `.env` では値を single quote で囲むため）。
- ADB の DDL は Terraform にも cloud-init にも持ちません。各製品の `init_script.sh` がアプリの CLI（冪等）で作成・更新します。

## 製品ごとの入力と instance 上の構成

AI の設定（OCI 認証、OCI Enterprise AI、埋め込み / リランクなど）は stack では受け取りません。起動後に各アプリのシステム設定で行います。
stack は secret を cloud-init に埋め込んで `backend/.env` と共通 `platform/.env` を作るため、Resource Manager の stack・job 履歴・state は機密として扱ってください。

### 設定ファイル（#211）

各 Compute には2つの `.env` を置きます。どちらも cloud-init が `/u01/aipoc/props/` に書き、各製品の `init_script.sh` がリポジトリへ配置します（0600）。

| ファイル | 内容 | 接頭辞 |
|---|---|---|
| `platform/.env` | 3製品共通の設定（ADB 接続・OCI の region / compartment・アップロード保存先・モデル設定の場所・構成管理者 `PLATFORM_ADMIN_*`・サービス間 token の署名鍵 `PLATFORM_SERVICE_TOKEN_SECRET`・製品ごとの `PLATFORM_AUTH_COOKIE_SECURE`）。システム設定画面の保存先でもあるため、既にあれば上書きしない | `PLATFORM_` |
| `<製品>/backend/.env` | その製品だけの設定 | `RAG_` / `NL2SQL_` / `AGENT_` |

変数名の規則と既存環境の移行は [AGENTS.md](../AGENTS.md) の「設定（`.env`）とデータベース object の命名」と各製品の配備ドキュメントを参照してください。

### RAG（`deploy_rag`）

RAG は NL2SQL / Agent と同じく、Docker を使わずネイティブ（uv の venv + systemd + Nginx）で動かします（#286）。
文書の前処理と解析は、依存が大きく異なるためサービスごとに uv の venv を分け、それぞれを systemd の unit にします。
詳細と既存環境（Docker Compose で配備した Compute）からの移行は [rag/docs/deployment.md](../rag/docs/deployment.md) を参照してください。

- ログイン: 共通認証（`RAG_AUTH_MODE=production`。#214）。最初は構成管理者 `system_admin`（`app_admin_login_user_password`）でログインし、
  「ユーザーとロール」でユーザーとロールを作り、「セキュリティ設定 > 権限管理」でロールごとのメニュー・検索・回答プロファイル・ナレッジベースを設定します。
  `rag_app_auth_cookie_secure`（→ `PLATFORM_AUTH_COOKIE_SECURE`）は HTTPS の終端を前に置いたら `true` にします。`RAG_AUDIT_CONTEXT_HASH_SALT` は instance 上で生成します。
- 前処理 7 つと CPU の parser を配備します（出力 `rag_services`）。GPU の parser（ASR・MinerU / Dots.OCR）は含めず、
  MinerU / Dots.OCR は起動後に「検索・回答設定 > 文書解析」で外部 API として指定します。

  | service | 既定 | 入力 |
  |---|---|---|
  | `parser-docling` | 常に配備 | 既定の解析エンジン（`RAG_PARSER_ADAPTER_BACKEND=docling`。PDF と画像。#286） |
  | `parser-unstructured` | 配備しない | `rag_enable_parser_unstructured`（テキスト・HTML・Office・メールなど Docling が扱えない形式を、処理レシピで Unstructured を選んで取り込む場合） |
  | `parser-oci-genai-vision` / `parser-oci-document-understanding` | 配備しない | `rag_enable_oci_cloud_parsers` |

  Docling を配備するかを選ぶ入力（以前の `rag_enable_parser_docling`）は廃止しました（Docling は既定の解析エンジンのため常に配備します）。
  Marker の parser（`parser-marker`）と入力 `rag_enable_parser_marker` は削除しました（#270）。

- 前処理 / parser の起動 / 停止は「運用設定 > サービス管理」画面から行えます（`RAG_SERVICE_CONTROL_ENABLED=true`）。backend の実行ユーザー
  `ragsvc` には、配備した前処理 / parser の unit の `systemctl enable --now / disable --now / restart` と `journalctl -u <unit>` だけを
  sudoers（`/etc/sudoers.d/production-ready-rag-services`）で許可します。利用者が最後に操作した起動 / 停止の状態は、再起動・再配備でも保たれます
  （初めて配備する unit は起動します）。
- Compute の既定は 4 OCPU / 32 GB / boot volume 200 GB（parser の venv とモデルが大きいため）。

| 項目 | 値 |
|---|---|
| backend | `production-ready-rag-backend.service`（`127.0.0.1:8000`）と `production-ready-rag-ingestion-worker.service`。host の Nginx が `frontend/dist` を配信し `/api/` を proxy |
| 前処理 / parser | `production-ready-rag-<service>.service`（`rag/services/*/*/.venv`、`127.0.0.1:18010〜18028`） |
| 実行ユーザー | `ragsvc`（home `/var/lib/production-ready-rag`）。リポジトリの所有者は `ubuntu` |
| 設定 | `/u01/aipoc/no.1-production-ready-suite/rag/backend/.env` と `platform/.env`（`ragsvc` だけが読む `0600`） |
| ログ | `/var/log/rag-init.log`、`sudo journalctl -u production-ready-rag-backend.service -f` |

```bash
sudo tail -f /var/log/rag-init.log
systemctl list-units --all 'production-ready-rag-*'
# RAG の system schema の初期化（冪等）を手動で再実行する
cd /u01/aipoc/no.1-production-ready-suite/rag/backend
sudo -u ragsvc HOME=/var/lib/production-ready-rag .venv/bin/python -m app.rag.system_schema_cli initialize
sudo systemctl restart production-ready-rag-backend.service
```

### NL2SQL（`deploy_nl2sql`）

- ログイン: 構成管理者 `SYSTEM_ADMIN`（ユーザー ID は `system_admin` 固定、RAG と共通）。`app_admin_login_user_password`（12〜30 文字、`admin` と `"` を含まない）。
- Deep Data Security: `nl2sql_oracle_deepsec_enabled`（既定 `true`）。有効な場合は `nl2sql_oracle_deepsec_data_user_password` が必要です。
- Nginx + systemd に直接配備します（Docker は使いません）。Compute の既定は 2 OCPU / 16 GB / 100 GB。
- instance 上の構成・更新手順・障害対応は [nl2sql/docs/compute-operations.md](../nl2sql/docs/compute-operations.md) を参照してください。

### Agent Control Plane（`deploy_agent`）

- ログイン: 共通認証（`AGENT_AUTH_MODE=production`。#215）。最初は構成管理者 `system_admin`（`app_admin_login_user_password`。RAG / NL2SQL と共通）で
  ログインし、「ユーザーとロール」でユーザーとロールを作り、「セキュリティ設定 > 権限管理」でロールごとのメニュー・実行 / 承認 / 監査 / 管理の権限・
  エージェントを設定します（検索・回答プロファイルは RAG の権限管理）。`init_script.sh` が `python -m app.cli.agent_system_schema --initialize` で認証・権限のテーブルを作ります。
  Nginx の Basic 認証は廃止しました。
  `agent_app_auth_cookie_secure`（非表示の入力。既定 `false` → `PLATFORM_AUTH_COOKIE_SECURE`）は HTTPS の終端を前に置いたら `true` にします。
  Cookie のないリクエストは 401 です（header / JWT の認可は #750 で削除。[agent/docs/security-rbac.md](../agent/docs/security-rbac.md)）。
- RAG / NL2SQL との連携（#233）: 同じ stack で RAG / NL2SQL も配備すると、Agent の `backend/.env` に
  `AGENT_EXTERNAL_RAG_MCP_URL` / `AGENT_EXTERNAL_NL2SQL_MCP_URL`（`http://<その製品の Compute の private IP>[:port]/api/mcp`）を書きます。
  これは Agent の「MCP 接続」の `rag` / `nl2sql` の初期値です（#757）。配備しなかった製品の URL は空で、後から画面の「MCP 接続」で設定できます。Agent は Run を作った利用者として
  呼び、認証は共通 `.env` の `PLATFORM_SERVICE_TOKEN_SECRET`（stack が生成して全 Compute に同じ値を配る）で署名した短命の token です。
  Agent の Compute は RAG / NL2SQL の Compute の後に作ります（Terraform の resource は `oci_core_instance.agent`）。
  あわせて `AGENT_EXTERNAL_RAG_PUBLIC_URL`（`http://<RAG の Compute の public IP（無い subnet では private IP）>[:port]`）を書きます。
  Agent の画面で RAG の図の根拠を開く短命の URL（#1311）を、利用者のブラウザから届くこの起点に付け替えます。
  既存の stack では Agent の `backend/.env` が変わるため Agent の Compute が置き換えになります。置き換えたくない場合は、
  Agent の `backend/.env` に同じ行を手で追記して backend を再起動します（空のままなら MCP の URL の host で開きます）。
- Agent の実行は Control Plane の組み込み Runtime（OpenAI Agents SDK と OCI Enterprise AI の Responses API。#754）。外部の Runtime と Docker は使いません。
  モデルは「システム設定 > モデル」の OCI Enterprise AI の接続と既定のテキストモデルです。
- Runtime 状態（Run・業務 Agent）と画面で変えた定義（Skill・プラグイン・MCP 接続・ツール権限）は Oracle に保存します（`oracle_checkpoint`。接続は共通の `PLATFORM_ORACLE_*`、table は `init_script.sh` の `agent_system_schema --initialize` が作成。#764）。旧 `AGENT_RUNTIME_ORACLE_*` は書きません。gunicorn は 1 worker、dispatcher は `in_process` に固定します。
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
# システムテーブル（認証・権限）の作成・更新（冪等）を手動で再実行する。画面の 運用設定 > システムテーブル でもできる
cd /u01/aipoc/no.1-production-ready-suite/agent/backend && sudo -u ubuntu /usr/local/bin/uv run python -m app.cli.agent_system_schema --initialize
```

既存の Agent の instance（Basic 認証で配備したもの）を更新する手順は [agent/docs/security-rbac.md §8](../agent/docs/security-rbac.md#8-既存環境の更新手順215) を参照してください。

### 全 Compute で共通

- 公開 port は `application_port`（既定 `80`）。製品ごとに別の Compute なので port は衝突しません。
- Wallet は `/u01/aipoc/wallet`、cloud-init のログは `/var/log/cloud-init-custom.log`。
- apply 後の output に、製品ごとの URL（`rag_application_url` など）と SSH command が出ます。配備しなかった製品の output は表示されません。
- 製品間の HTTP 連携（Agent から RAG / NL2SQL の MCP を呼ぶ）は、別 Compute の private IP の `application_port` を使います。
  stack は NSG を作りません（#259）。subnet の security list（stack の外で管理）で、同じ subnet の CIDR から `application_port` の TCP を
  許可してください（Agent と RAG / NL2SQL を一緒に配備する場合）。
- 既存の stack を更新するとき（#233）: 共通 `.env` に `PLATFORM_SERVICE_TOKEN_SECRET` が加わり、Agent の `backend/.env` に MCP の URL が
  加わるため、全製品の Compute の `user_data`（cloud-init）が変わります。OCI provider は `user_data` の変更で Compute を
  **置き換え（replace）** ます（`oci_core_instance` の CustomizeDiff。Agent の resource の移動は `moved` で扱うが、置き換え自体は避けられない）。
  - 作り直してよい環境: そのまま apply します（データの正本は ADB。Compute 上のローカル保存のファイルは失われます）。
  - 作り直したくない環境: stack を apply せず、各 Compute で手動で追記します。各 Compute の `platform/.env` に同じ
    `PLATFORM_SERVICE_TOKEN_SECRET`（`openssl rand -base64 48` などで作った1つの値）を、Agent の `backend/.env` に
    `AGENT_EXTERNAL_RAG_MCP_URL` / `AGENT_EXTERNAL_NL2SQL_MCP_URL`（`http://<private IP>[:port]/api/mcp`）を追記し、backend を再起動します。
    Agent から RAG / NL2SQL の `application_port` へ通信できない場合は、subnet の security list で同じ subnet からの TCP を許可します。
  - どちらの場合も、apply 前に Resource Manager の plan で置き換えになる resource を確認してください。
  旧名の `AGENT_EXTERNAL_RAG_BASE_URL` / `AGENT_EXTERNAL_RAG_API_KEY`（NL2SQL も同じ）は読まれないので削除してください。

### ログインの試行の回数の共有（#1173）

ログインの試行の回数の制限（#1087。`PLATFORM_AUTH_LOGIN_*`）は、失敗の記録を共通のテーブル `PLATFORM_LOGIN_ATTEMPTS` に置き、
3 製品・gunicorn の全 worker・再起動をまたいで同じ回数を数えます（構成管理者 `system_admin` のパスワードを、製品・worker の数だけ
多く試せないようにする）。

- テーブル: `KEY_HASH`（`VARCHAR2(64)`）・`ATTEMPT_ID`（`VARCHAR2(36)`）・`ATTEMPTED_AT`（`TIMESTAMP`、DB の UTC）。主キー `PK_PLATFORM_LOGIN_ATTEMPTS`
  （`KEY_HASH, ATTEMPT_ID`）と index `IX_PLATFORM_LOGIN_ATTEMPTS_AT`（`ATTEMPTED_AT`）。ログイン ID と送信元 IP は保存せず、共通 `.env` の
  `PLATFORM_SERVICE_TOKEN_SECRET` から導いた鍵の HMAC-SHA256 だけを保存します（テーブルを読めても利用者の ID・IP は分からない）。
  `PLATFORM_SERVICE_TOKEN_SECRET` が空（32 文字未満）のときは DB に記録せず、従来どおりプロセス内だけで数えます（起動時にログ
  `auth_login_throttle_shared_store_disabled`）。
- 1 回の失敗で 2 行（ログイン ID＋送信元 IP・送信元 IP）を足します。窓（`PLATFORM_AUTH_LOGIN_ATTEMPT_WINDOW_MINUTES`）を過ぎた行は、
  失敗を記録するついでに 1 回 1,000 行まで消すので、テーブルには窓の中の失敗の分しか残りません。成功した試行・429 で拒否した試行は残しません。
- DB が使えない（接続できない・テーブルが無い）ときは、プロセス内の記録に自動で切り替えて制限を続け、ログ
  `auth_login_throttle_shared_store_unavailable`（`error_code`・`hint` 付き）を出します。30 秒ごとに DB を試し直し、戻れば DB で数えます
  （停止中にプロセス内で数えた失敗も、窓の中なら合わせて数える）。ログインは止まりません（構成管理者は DB の停止中もログインできる）。
- テーブルは各製品のシステムテーブルの初期化（RAG `app.rag.system_schema_cli initialize`・NL2SQL `app.cli.app_security_migrate --apply`・
  Agent `app.cli.agent_system_schema --initialize`。どれも `init_script.sh` が実行する）が、`PLATFORM_USERS` などと一緒に冪等に作ります。
- Nginx: 3 製品の Nginx（`init_script.sh` が書く）は、`/api/auth/login` だけに送信元 IP ごとの緩い上限（1 秒あたり 1 回、burst 30。
  zone は `/etc/nginx/conf.d/production-ready-login-rate-limit.conf`）を掛けます。回数の制限の正本は backend で、Nginx の上限は 1 つの送信元からの
  大量の要求（429 の連打を含む）を照合・DB の前で止めるためのものです。超えたときは backend と同じ形の JSON（`error_code`
  `SECURITY_RATE_LIMITED`）と `Retry-After: 60` の 429 を返します。backend の 429 はそのまま返します。Compute の前にロードバランサーなどの
  proxy を置く場合は、Nginx が利用者の IP を見られるように（`real_ip` など）してください（見られないと、全員が 1 つの送信元として数えられる）。

既存環境の更新（3 製品は同じ schema を共有するので、テーブルの作成はどれか 1 つの製品で行えばよい）:

1. 各 Compute のコードを更新し、backend を再起動します。テーブルを作るまでは、プロセス内で数えてログに上の警告が出ます。
2. テーブルを作ります（冪等。既存の `PLATFORM_*` は変えません）。

   ```bash
   # RAG の Compute
   cd /u01/aipoc/no.1-production-ready-suite/rag/backend
   sudo -u ragsvc HOME=/var/lib/production-ready-rag .venv/bin/python -m app.rag.system_schema_cli initialize
   # NL2SQL の Compute
   cd /u01/aipoc/no.1-production-ready-suite/nl2sql/backend
   sudo -u ubuntu /usr/local/bin/uv run python -m app.cli.app_security_migrate --apply --skip-bootstrap
   # Agent の Compute
   cd /u01/aipoc/no.1-production-ready-suite/agent/backend
   sudo -u ubuntu /usr/local/bin/uv run python -m app.cli.agent_system_schema --initialize
   ```

3. Nginx のログインの API の上限は、各製品の `init_script.sh` の再実行で入ります。手で入れる場合は、`platform/templates/nginx/login-rate-limit.conf`
   を `/etc/nginx/conf.d/production-ready-login-rate-limit.conf` に置き、`init_script.sh` の `configure_nginx` と同じ
   `location = /api/auth/login` と `location @pr_login_rate_limited` を site に足して、`sudo nginx -t && sudo systemctl reload nginx` を実行します。
4. 確認: backend のログに `auth_login_throttle_shared_store_unavailable` が出ないこと。存在しないログイン ID で 1 回ログインに失敗すると、
   `SELECT COUNT(*) FROM PLATFORM_LOGIN_ATTEMPTS` が 2 増えること（窓を過ぎると、次の失敗のときに消える）。

## パッケージと検証

monorepo root から実行します。

```bash
python terraform/scripts/package_stack.py
python terraform/scripts/verify_stack_contract.py terraform/dist/production-ready-suite-terraform-stack.zip
```

出力は `terraform/dist/production-ready-suite-terraform-stack.zip` です。この zip を Resource Manager へ upload して stack を作成します。
`verify_stack_contract.py` は、フォーム（`schema.yaml`）と Terraform 変数の一致、製品選択の契約、製品ごとの `backend/.env` の key が各製品の Settings にあること、
RAG の前処理 / parser（`rag/scripts/rag-systemd.sh` の unit の定義と uv.lock があること）、各製品の `init_script.sh` の配備契約
（Docker を入れないこと、RAG の sudoers・状態の保持を含む）と、Dockerfile・compose が無いこと（#356 / #754）を検証します。

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
| RAG の `app_login_user` / `app_login_password` / `app_auth_cookie_secure` | 廃止（ログインは構成管理者 `app_admin_login_user_password` と DB ユーザー。#214） / 廃止 / `rag_app_auth_cookie_secure` |
| RAG の `enable_parser_docling` / `enable_parser_marker` / `enable_oci_cloud_parsers` | 廃止（Docling は常に配備。#286） / 廃止（Marker は削除。#270） / `rag_enable_oci_cloud_parsers` |
| NL2SQL の `app_admin_login_user_password` / `oracle_deepsec_enabled` / `oracle_deepsec_data_user_password` | `app_admin_login_user_password`（RAG と共通） / `nl2sql_oracle_deepsec_enabled` / `nl2sql_oracle_deepsec_data_user_password` |
| NL2SQL の `app_environment` / `app_auth_cookie_secure` / `app_admin_login_user_id` | `nl2sql_app_environment` / `nl2sql_app_auth_cookie_secure` / `app_admin_login_user_id`（RAG と共通） |
| Agent の `app_basic_auth_user` / `app_basic_auth_password` | 廃止（ログインは構成管理者 `app_admin_login_user_password` と DB ユーザー。#215） |
| 統合 stack の `agent_app_basic_auth_user` / `agent_app_basic_auth_password`（output の `agent_basic_auth_user`） | 廃止（`app_admin_login_user_password` を Agent にも使う。#215）。Cookie を HTTPS 限定にする `agent_app_auth_cookie_secure` を新設 |
| `instance_display_name` / `instance_flex_shape_ocpus` / `instance_flex_shape_memory` / `instance_boot_volume_size` | `<製品>_instance_display_name` / `<製品>_instance_flex_shape_ocpus` / `<製品>_instance_flex_shape_memory` / `<製品>_instance_boot_volume_size` |
| `adb_name` の既定 `RAGADB` / `NL2SQLADB` / `AGENTADB` | `SUITEADB` |
| NL2SQL の `adb_workload` の既定 `LH` | `OLTP`（3製品で共有するため） |
