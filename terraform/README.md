# Production Ready Suite — OCI Resource Manager stack

`terraform/stack/` は、3製品（RAG / NL2SQL / Agent Control Plane）を OCI Resource Manager から配備する **1つだけの** Terraform stack です（#217）。
以前は製品ごとに `rag/` `nl2sql/` `agent/` の下に stack がありましたが、この stack に統合しました。

作成・設定するもの:

- Oracle Autonomous AI Database を **1つ**（新規作成、または既存 ADB の選択）と、その Wallet。選んだ製品すべてで共有する
- OCI Compute を **1台だけ**（Oracle Linux 9。ログインのユーザーは `opc`。#1316・#1611）。選んだ製品（`deploy_rag` / `deploy_nl2sql` / `deploy_agent`）をすべてこの 1 台に入れる
- Compute の cloud-init。suite monorepo を1回 clone し、[`platform/deploy/suite-init.sh`](../platform/deploy/suite-init.sh) が
  選んだ製品の `init_script.sh`（[`rag/`](../rag/init_script.sh) / [`nl2sql/`](../nl2sql/init_script.sh) / [`agent/`](../agent/init_script.sh)）を順に呼び、
  Nginx の site（製品の path の prefix と HTTPS）を 1 つだけ書く
- 3製品で共通のサービス間 token の署名鍵（`random_password`。共通 `.env` の `PLATFORM_SERVICE_TOKEN_SECRET`。#233）

```text
              ┌──────────────────────────────┐
              │ Autonomous AI Database（1つ） │  PLATFORM_* / RAG_* / NL2SQL_* / AGENT_* の table
              └──────────────┬───────────────┘
                             │ Wallet（1つ）
  ┌──────────────────────────┴───────────────────────────────────────────────┐
  │ Compute（1台。PRODUCTION_READY_SUITE）                                    │
  │   Nginx :443（HTTPS。:80 は開けない）/ HTTPS が off なら :80                 │
  │     /rag/    → frontend の dist、/rag/api/    → 127.0.0.1:8000（RAG）       │
  │     /nl2sql/ → frontend の dist、/nl2sql/api/ → 127.0.0.1:8010（NL2SQL）    │
  │     /agent/  → frontend の dist、/agent/api/  → 127.0.0.1:8020（Agent）     │
  │     /        → /agent/（302）、/platform/ca.crt → 自作の Root CA の証明書     │
  │   systemd: 各製品の backend・worker、RAG の前処理 / parser（127.0.0.1:18010〜） │
  └──────────────────────────────────────────────────────────────────────────┘
```

## 対応リージョン

- **stack は `ap-tokyo-1`（東京）と `ap-osaka-1`（大阪）だけをサポートします（#660）。** Compute の image（`instance_image_source_id`）は、この 2 つのリージョンの Oracle Linux 9 の platform image（`Oracle-Linux-9.8-2026.09.18-0`）の OCID に固定しています（#1611）。
  - `ap-tokyo-1`: `ocid1.image.oc1.ap-tokyo-1.aaaaaaaawxmm5rja45shcpoactn4avvfv2sty55ap5jnaxqmpzbgtdc7qcsa`
  - `ap-osaka-1`（既定）: `ocid1.image.oc1.ap-osaka-1.aaaaaaaabdp5adn6fy2h63s5iizgqv77wz4fqrjzosackszdo2gn5rn436ha`
  - image を新しくするときは、`schema.yaml`・`variables.tf`・`terraform/scripts/verify_stack_contract.py`（`ORACLE_LINUX_IMAGES`）を同じ PR でそろえます。
  `us-chicago-1` など他のリージョンで stack を作ると、`region` の validation で plan が失敗します。
- 共通 `.env` の `PLATFORM_ORACLE_ADB_REGION` には stack のリージョンが入ります。システム設定 > データベース の「Autonomous Database 管理」のリージョンの選択肢も同じ 2 つです。

## 配備する製品

Resource Manager の画面では製品を選ばず、RAG / NL2SQL / Agent Control Plane の 3 製品をすべて 1 台の Compute に配備します（#1562。Suite はセットで配備する）。
「配備する製品」のグループと `deploy_rag` / `deploy_nl2sql` / `deploy_agent` は画面に出しません（`visible: false`。既定はすべて `true`）。

- 変数は残しています。Terraform の CLI で一部の製品を外すときは `-var deploy_<製品>=false` を渡します（最低 1 つ。1 つも選ばないと plan が precondition で失敗します）。
  外した製品の入力グループ（`RAG` / `NL2SQL Deep Data Security`）は画面に出ません。
- `app_admin_login_user_password`（構成管理者 `system_admin`、3 製品で共通）は必須です。
  NL2SQL で Deep Data Security を有効にする場合は `nl2sql_oracle_deepsec_data_user_password` も必要です。
- 製品の選択は Compute の cloud-init に入るため、選択を変えて apply すると **Compute は作り直されます**（OCI provider は `user_data` の変更で instance を置き換える）。データの正本は ADB で、
  Compute の中のローカルの保存（アップロードの原本・`platform/.env` を画面で変えた値・HTTPS の CA）は失われます。
- 配備が終わると、`/var/log/suite-init.log` に入口の URL（`suite:`。`/` は Agent Control Plane へ転送。#1561）、製品ごとの URL、CA 証明書の URL を出します。
  その後に配備全体の確認の結果（`check:` の行）を出します（下の「配備が正常かの確認」）。

## Autonomous AI Database（全製品で共有）

- **対応バージョン**: 3製品とも Oracle AI Database 26ai 以降が前提です（`VECTOR` 型と Oracle AI Vector Search を使うため）。`adb_db_version` は既定の `26ai` のまま使ってください。
  NL2SQL の SQL ドメインと annotation は旧 Oracle Database 23ai で入った機能で、それより前のバージョンでは NL2SQL がその機能を使いません。
  リポジトリの文書・画面・コメントでは製品名にバージョンを入れず（#564）、対応バージョンはここだけに書きます。
- `adb_deployment_mode` で「新規 Autonomous AI Database の作成」か「既存の Autonomous AI Database を選択」を選びます。
  - 新規: 既定の DB 名は `SUITEADB`、workload は `LH`（Lakehouse。#1558）、ECPU は `2`。
    3製品を載せる場合は、負荷に合わせて ECPU 数とストレージを上げてください。
  - 既存: ADB の OCID と、全製品の `PLATFORM_ORACLE_USER` / `PLATFORM_ORACLE_PASSWORD` に書く値を入力します。`PLATFORM_ORACLE_DSN` は空なら `<db_name>_high` です。
    stack は既存 ADB から Wallet を生成するだけで、ネットワーク・アクセス・mTLS・ACL は変更しません。
    ADMIN 以外のユーザーで接続するときは、下の「[既存の ADB で ADMIN 以外のユーザーを使うとき](#既存の-adb-で-admin-以外のユーザーを使うとき1648)」の権限が要ります。
- 新規 ADB の場合、全製品が `ADMIN` で接続します。製品のテーブルは `RAG_` / `NL2SQL_` / `AGENT_`、3製品で共有するユーザー・ロール・セッションのテーブルは `PLATFORM_` の接頭辞で分かれているため、同じスキーマでも衝突しません（#212）。
- ネットワーク・アクセス（既定はプライベート・エンドポイント）の画面構成は Autonomous AI Database の作成画面に合わせています。
  ACL（許可された IP および VCN）を使う場合は、Compute の subnet（または VCN）を許可してください。
  プライベート・エンドポイントの場合は、Compute の subnet から ADB へ TCP `1522` で到達できる必要があります。
- DB の password / user / DSN に single quote は使えません（共通 `.env` では値を single quote で囲むため）。
- ADB の DDL は Terraform にも cloud-init にも持ちません。各製品の `init_script.sh` がアプリの CLI（冪等）で作成・更新します。
  作成・更新に失敗しても backend は起動します（画面の システムテーブル から直せる）が、配備の最後の確認が `check: <製品> schema NG` で失敗します（[配備が正常かの確認](#配備が正常かの確認1642)）。

### 既存の ADB で ADMIN 以外のユーザーを使うとき（#1648）

- **3 製品の専用の DB ユーザー（schema）を作ることを推奨します。** 3 製品は同じ schema に `PLATFORM_` / `RAG_` / `NL2SQL_` / `AGENT_` の表・索引・sequence・package を作ります。
  業務データや別のアプリケーション（旧版の単独の製品を含む）と同じ schema（とくに `ADMIN`）に入れると、同じ名前の表と衝突し、
  NL2SQL の業務プロファイル・管理 SQL の対象にも 3 製品以外の object が並びます。
- 初期化は、旧名の表（統合の前の `RAG_APP_*` / `NL2SQL_APP_*` など）を改名・削除しません（NL2SQL はその名前を log に出して残します）。
  RAG / Agent の初期化は、台帳（`RAG_SCHEMA_MIGRATIONS` / `AGENT_SCHEMA_MIGRATIONS`）に記録の無い schema に残る廃止した表を、
  承認（`--allow-destructive`、画面では削除の確認）が無ければ削除せずに止めます。
- 権限（ADMIN で付与します。`<user>` は `PLATFORM_ORACLE_USER`）。CI の実 Oracle の結合テスト（`.github/scripts/oracle-free-ci.sh`）と同じ構成です。

  ```sql
  CREATE USER <user> IDENTIFIED BY "<password>" DEFAULT TABLESPACE DATA QUOTA UNLIMITED ON DATA;
  -- 表・索引・sequence・view・procedure（NL2SQL の package）・SQL ドメインの作成
  GRANT DB_DEVELOPER_ROLE TO <user>;
  GRANT CREATE SESSION TO <user>;
  -- RAG: ベクトル索引の構築の値（NEIGHBORS / EFCONSTRUCTION）を読む
  GRANT SELECT ON SYS.V_$VECTOR_GRAPH_INDEX TO <user>;
  ```

  - `DB_DEVELOPER_ROLE` を使わない場合は、`CREATE TABLE`・`CREATE SEQUENCE`・`CREATE VIEW`・`CREATE PROCEDURE` と、RAG の全文検索（Oracle Text の索引・lexer・stoplist）の `CTXAPP`（または `EXECUTE ON CTXSYS.CTX_DDL`）を付与します。
  - 表領域の quota が無いと `ORA-01950`、権限が無いと `ORA-01031`（Oracle Text は `PLS-00201`）で初期化が失敗し、配備の最後の確認が NG になります。
  - NL2SQL が SQL を生成・実行する業務データが別の schema にあるときは、その表・view の `SELECT` を付与します。
  - NL2SQL の機能ごとに使うとき: フィードバックのベクトルの再構築は `EXECUTE ON DBMS_LOCK`、Select AI は `EXECUTE ON DBMS_CLOUD_AI` と `EXECUTE ON DBMS_CLOUD`、
    合成データの生成は `SELECT ON SYS.DBA_LOAD_OPERATIONS` と `SELECT ON SYS.V_$SESSION`。
  - NL2SQL の Deep Data Security（`NL2SQL_ORACLE_DEEPSEC_ENABLED=true`）: `CREATE ROLE`・`CREATE END USER`・`ALTER END USER`・`CREATE DATA ROLE`・
    `GRANT ANY ROLE`・`CREATE CONTEXT`・`CREATE PROCEDURE` と、DeepSec の metadata（`DBA_END_USERS`・`DBA_DATA_ROLES`・`DBA_ROLES`・`DBA_CONTEXT`・
    `DBA_DATA_GRANTS`・`DBA_TAB_PRIVS`・`DBA_POLICIES`）の参照。詳しくは [nl2sql/docs/security-rbac-deepsec.md](../nl2sql/docs/security-rbac-deepsec.md)「DeepSec V001 の前提」。

## 製品ごとの入力と instance 上の構成

AI の設定（OCI 認証、OCI Enterprise AI、埋め込み / リランクなど）は stack では受け取りません。起動後に各アプリのシステム設定で行います。
stack は secret を cloud-init に埋め込んで `backend/.env` と共通 `platform/.env` を作るため、Resource Manager の stack・job 履歴・state は機密として扱ってください。

### 設定ファイル（#211）

Compute には `platform/.env` を 1 つと、製品ごとの `backend/.env` を置きます。cloud-init が `/u01/aipoc/props/`
（`platform.env`・`<製品>.backend.env`）に書き、各製品の `init_script.sh` がリポジトリへ配置します（0600）。

| ファイル | 内容 | 接頭辞 |
|---|---|---|
| `platform/.env` | 3製品共通の設定（ADB 接続・OCI の region / compartment・アップロード保存先・構成管理者 `PLATFORM_ADMIN_*`・サービス間 token の署名鍵 `PLATFORM_SERVICE_TOKEN_SECRET`・`PLATFORM_AUTH_COOKIE_SECURE`（`https_enabled` と同じ値）・ログインの Cookie 名 `PLATFORM_AUTH_SESSION_COOKIE_NAME` / `PLATFORM_AUTH_CSRF_COOKIE_NAME`）。1 台の Compute では 3 製品が同じファイルを読み書きする（システム設定画面の保存先）。既にあれば上書きしない | `PLATFORM_` |
| `<製品>/backend/.env` | その製品だけの設定 | `RAG_` / `NL2SQL_` / `AGENT_` |

`model-settings.json` は `platform/.env` と同じ場所の 1 つを 3 製品で共有し、アップロードの原本などのデータは製品ごとの
`/u01/data/production-ready-<製品>`（各製品の Settings の既定値）に置きます。3 製品のプロセスは同じユーザー `opc` で動きます
（共通の `platform/.env`・Wallet・`~/.oci/config` を画面から読み書きするため。#1316）。

変数名の規則と既存環境の移行は [AGENTS.md](../AGENTS.md) の「設定（`.env`）とデータベース object の命名」と各製品の配備ドキュメントを参照してください。

### RAG（`deploy_rag`）

RAG は NL2SQL / Agent と同じく、Docker を使わずネイティブ（uv の venv + systemd + Nginx）で動かします（#286）。
文書の前処理と解析は、依存が大きく異なるためサービスごとに uv の venv を分け、それぞれを systemd の unit にします。
詳細と既存環境（Docker Compose で配備した Compute）からの移行は [rag/docs/deployment.md](../rag/docs/deployment.md) を参照してください。

- ログイン: 共通認証（`RAG_AUTH_MODE=production`。#214）。最初は構成管理者 `system_admin`（`app_admin_login_user_password`）でログインし、
  「ユーザーとロール」でユーザーとロールを作り、「セキュリティ設定 > 権限管理」でロールごとのメニュー・検索・回答プロファイル・ナレッジベースを設定します。
  Cookie を HTTPS 限定にするか（`PLATFORM_AUTH_COOKIE_SECURE`）は `https_enabled` から決まります。`RAG_AUDIT_CONTEXT_HASH_SALT` は instance 上で生成します。
- 前処理 7 つと CPU の parser を配備します（出力 `rag_services`）。GPU の parser（ASR・MinerU / Dots.OCR）は含めず、
  MinerU / Dots.OCR は起動後に「検索・回答設定 > 文書解析」で外部 API として指定します。

  | service | 既定 | 入力 |
  |---|---|---|
  | `parser-docling` | 常に配備 | 既定の解析エンジン（`RAG_PARSER_ADAPTER_BACKEND=docling`。PDF と画像。#286） |
  | `parser-unstructured` | 配備する（#1559。文書解析の設定でも有効にする: `RAG_PARSER_UNSTRUCTURED_ENABLED`） | `rag_enable_parser_unstructured`（テキスト・HTML・Office・メールなど Docling が扱えない形式を、処理レシピで Unstructured を選んで取り込む場合） |
  | `parser-oci-genai-vision` / `parser-oci-document-understanding` | 配備しない | `rag_enable_oci_cloud_parsers` |

  Docling を配備するかを選ぶ入力（以前の `rag_enable_parser_docling`）は廃止しました（Docling は既定の解析エンジンのため常に配備します）。
  Marker の parser（`parser-marker`）と入力 `rag_enable_parser_marker` は削除しました（#270）。

- 前処理 / parser の起動 / 停止は「運用設定 > サービス管理」画面から行えます（`RAG_SERVICE_CONTROL_ENABLED=true`）。backend の実行ユーザーには、
  配備した前処理 / parser の unit の `systemctl enable --now / disable --now / restart` と `journalctl -u <unit>` を
  sudoers（`/etc/sudoers.d/production-ready-rag-services`）で許可します。利用者が最後に操作した起動 / 停止の状態は、再起動・再配備でも保たれます
  （初めて配備する unit は起動します）。
- RAG も NL2SQL / Agent と同じユーザー `opc` で動きます（#1316・#1611）。

| 項目 | 値 |
|---|---|
| backend | `production-ready-rag-backend.service`（`127.0.0.1:8000`）と `production-ready-rag-ingestion-worker.service`。Nginx が `/rag/` で `frontend/dist` を配信し `/rag/api/` を proxy |
| 前処理 / parser | `production-ready-rag-<service>.service`（`rag/services/*/*/.venv`、`127.0.0.1:18010〜18028`） |
| 実行ユーザー | `opc`（1 台の Compute。#1316・#1611） |
| 設定 | `/u01/aipoc/no.1-production-ready-suite/rag/backend/.env` と `platform/.env`（`0600`） |
| ログ | `/var/log/rag-init.log`、`sudo journalctl -u production-ready-rag-backend.service -f` |

```bash
sudo tail -f /var/log/rag-init.log
systemctl list-units --all 'production-ready-rag-*'
# RAG の system schema の初期化（冪等）を手動で再実行する
cd /u01/aipoc/no.1-production-ready-suite/rag/backend
sudo -u opc .venv/bin/python -m app.rag.system_schema_cli initialize
sudo systemctl restart production-ready-rag-backend.service
```

### NL2SQL（`deploy_nl2sql`）

- ログイン: 構成管理者 `SYSTEM_ADMIN`（ユーザー ID は `system_admin` 固定、RAG と共通）。`app_admin_login_user_password`（12〜30 文字、`admin` と `"` を含まない）。
- Deep Data Security: `nl2sql_oracle_deepsec_enabled`（既定 `true`）。有効な場合は `nl2sql_oracle_deepsec_data_user_password` が必要です。
- Nginx + systemd に直接配備します（Docker は使いません）。backend は `127.0.0.1:8010`（ローカルの開発と同じ。#1316 で 8000 から変えた）。
- instance 上の構成・更新手順・障害対応は [nl2sql/docs/compute-operations.md](../nl2sql/docs/compute-operations.md) を参照してください。

### Agent Control Plane（`deploy_agent`）

- ログイン: 共通認証（`AGENT_AUTH_MODE=production`。#215）。最初は構成管理者 `system_admin`（`app_admin_login_user_password`。RAG / NL2SQL と共通）で
  ログインし、「ユーザーとロール」でユーザーとロールを作り、「セキュリティ設定 > 権限管理」でロールごとのメニュー・実行 / 承認 / 監査 / 管理の権限・
  エージェントを設定します（検索・回答プロファイルは RAG の権限管理）。`init_script.sh` が `python -m app.cli.agent_system_schema --initialize` で認証・権限のテーブルを作ります。
  Nginx の Basic 認証は廃止しました。
  Cookie を HTTPS 限定にするか（`PLATFORM_AUTH_COOKIE_SECURE`）は `https_enabled` から決まります。
  Cookie のないリクエストは 401 です（header / JWT の認可は #750 で削除。[agent/docs/security-rbac.md](../agent/docs/security-rbac.md)）。
- RAG / NL2SQL との連携（#233）: 同じ stack で RAG / NL2SQL も配備すると、Agent の `backend/.env` に
  `AGENT_EXTERNAL_RAG_MCP_URL` / `AGENT_EXTERNAL_NL2SQL_MCP_URL`（同じ Compute の backend を直接呼ぶ `http://127.0.0.1:8000/api/mcp` /
  `http://127.0.0.1:8010/api/mcp`。Nginx を通さない。#1316）を書きます。
  これは Agent の「MCP 接続」の `rag` / `nl2sql` の初期値です（#757）。配備しなかった製品の URL は空で、後から画面の「MCP 接続」で設定できます。Agent は Run を作った利用者として
  呼び、認証は共通 `.env` の `PLATFORM_SERVICE_TOKEN_SECRET`（stack が生成して全 Compute に同じ値を配る）で署名した短命の token です。
  あわせて `AGENT_EXTERNAL_RAG_PUBLIC_URL=/rag` を書きます。Agent の画面で RAG の図の根拠を開く短命の URL（#1311）を、
  同じ origin の `/rag` の下（Nginx が `/rag/api/` を RAG の backend へ渡す）に付け替えます（#1316）。
- Agent の実行は Control Plane の組み込み Runtime（OpenAI Agents SDK と OCI Enterprise AI の Responses API。#754）。外部の Runtime と Docker は使いません。
  モデルは「システム設定 > モデル」の OCI Enterprise AI の接続と既定のテキストモデルです。
- Runtime 状態（Run・業務 Agent）と画面で変えた定義（Skill・プラグイン・MCP 接続・ツール権限）は Oracle に保存します（`oracle_checkpoint`。接続は共通の `PLATFORM_ORACLE_*`、table は `init_script.sh` の `agent_system_schema --initialize` が作成。#764）。旧 `AGENT_RUNTIME_ORACLE_*` は書きません。gunicorn は 1 worker、dispatcher は `in_process` に固定します。

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
cd /u01/aipoc/no.1-production-ready-suite/agent/backend && sudo -u opc /usr/local/bin/uv run python -m app.cli.agent_system_schema --initialize
```

既存の Agent の instance（Basic 認証で配備したもの）を更新する手順は [agent/docs/security-rbac.md §8](../agent/docs/security-rbac.md#8-既存環境の更新手順215) を参照してください。

### Compute・Nginx・HTTPS（3 製品で共通。#1316）

- Compute は 1 台です。既定は **4 OCPU / 24 GB / boot volume 200 GB**。RAG の CPU の parser（Docling）が最も多く使うので、
  文書の取込が多いときは OCPU とメモリを上げてください。
- Nginx は 1 つの site（`/etc/nginx/conf.d/production-ready-suite.conf`。`platform/deploy/suite-nginx.sh` が生成）で、製品を path の prefix で分けます。
  `/etc/nginx/nginx.conf` は、package の既定の server（port 80）を外したもの（`platform/templates/nginx/nginx.conf`）に置き換えます。

  | path | 内容 |
  |---|---|
  | `/rag/` `/nl2sql/` `/agent/` | 各製品の frontend（`FRONTEND_BASE_PATH=/<製品>/` で build した dist）。画面の URL は `index.html` に返す |
  | `/<製品>/api/...` | 製品の prefix だけを外して各 backend（`127.0.0.1:8000` / `8010` / `8020`）へ。SSE・MCP・ログインの上限（#1173）も今までと同じ。待ち時間は既定 600 秒（SSE / WebSocket は heartbeat）、LLM を同期で呼ぶ API は RAG 660 秒・NL2SQL 2460 秒・Agent の `tools/invoke` 960 秒。本文の上限は各製品の `<製品>_MAX_UPLOAD_BYTES` + 10 MiB（既定は RAG・NL2SQL 210M、Agent 110M）。値の根拠は `platform/deploy/suite-nginx.sh`（#1597） |
  | `/<製品>/health` | backend の `/api/health` |
  | `/` | `/agent/` へ 302（Agent を配備しないときは、最初に配備した製品へ） |
  | `/platform/ca.crt` | 自作の Root CA の証明書（HTTPS が on のときだけ）。`/platform/` は platform（共通基盤）が配る共有の内容のために予約し、ほかの path は 404 |

- **HTTPS（`https_enabled`、既定 on）**: Nginx が `https_port`（既定 443）だけで TLS 1.2 / 1.3 を受けます。`http_port`（既定 80）は
  firewalld でも開けず、Nginx も listen しません（`http://` から https への転送もしません。#1638）。HSTS は付けません（IP の証明書のため）。
  off にすると `http_port` の HTTP だけで配信します。フォームには on なら `https_port`、off なら `http_port` が出ます。
  以前の配備（`http_port` も開けて https へ転送していた版）は、`suite-restart.sh` / `suite-update.sh` の再配備で firewalld に残った
  `http_port` を閉じます（`firewall-cmd --permanent --remove-port=<http_port>/tcp`。`/var/log/suite-init.log` に出ます）。
  既定でない port は output の URL に付きます（例 `https://<IP>:8443/rag/`）。
- **使えない port**: SSH（22）・よく使われる service（25 / 53 / 111 / 1521 / 1522 / 3306 / 5432 / 6379 / 9090）・Compute の中で製品が使う port
  （backend の 8000 / 8010 / 8020、RAG の前処理 / parser の 18000〜18099）と、1〜65535 の外。一覧は `terraform/stack/locals.tf` の
  `reserved_ports` / `reserved_port_ranges`（apply の precondition）と `platform/deploy/suite-nginx.sh` の `SUITE_RESERVED_PORTS` /
  `SUITE_RESERVED_PORT_RANGES`（手で配備したときも先に失敗する）が同じ値を持ち、`verify_stack_contract.py` が照合します。
  - 証明書は初回の起動で Compute の中で作ります（`platform/deploy/suite-tls.sh`）。発行者などは固定で、stack の入力にはしません。

    | | subject | 鍵・有効期間 |
    |---|---|---|
    | Root CA | `C=JP, ST=Tokyo, L=Minato-ku, O=Oracle, OU=Production Ready Suite, CN=Production Ready Root CA` | RSA 3072・SHA-256・3650 日 |
    | サーバー | `C=JP, ST=Tokyo, L=Minato-ku, O=Oracle, OU=Production Ready Suite, CN=<公開 IP>`、SAN = 公開 IP + private IP | RSA 3072・SHA-256・397 日 |

  - CA の秘密鍵は `/u01/aipoc/ssl/ca.key`（root の 0600）で、Compute の外へは出しません。利用者には CA の証明書（`/platform/ca.crt`）だけを配ります。
    各製品の「システム設定 > 外観と証明書」の「HTTPS の証明書」から、ダウンロードと端末への取り込み方を確認できます。
  - 公開 IP は OCI の instance metadata に無いため、Compute に公開 IP があるときだけ外部のサービス（既存の No.1-RAG / No.1-SQL-Assist と同じ `http://whatismyip.akamai.com/`、取れなければ `https://checkip.amazonaws.com`）に
    送信元の IP を問い合わせます。分からないときは private IP だけの証明書を作り、`/var/log/suite-init.log` に警告を出します。
    公開 IP を指定して作り直す: `sudo bash /u01/aipoc/no.1-production-ready-suite/platform/deploy/suite-tls.sh renew --public-ip <公開 IP>`
  - サーバー証明書は、期限の 30 日前から timer（`production-ready-suite-tls-renew.timer`、毎日）が作り直して Nginx を reload します。CA は作り直しません。
    状態は `sudo bash .../platform/deploy/suite-tls.sh status` で確認します。
- **subnet の security list または NSG**（stack の外で管理。stack は ingress の規則・NSG を作りません。#259）で、利用者の端末からの
  TCP `https_port`（HTTPS が off なら `http_port`）だけを許可してください（HTTPS が on のとき `http_port` は Compute で使いません）。
  Compute の OS の firewall（firewalld）は suite-init.sh が選んだ port を開けます（`firewall-cmd --permanent --add-port=<port>/tcp`）。製品間の通信（Agent → RAG / NL2SQL の MCP）は同じ Compute の
  127.0.0.1 なので、security list は要りません。
- ログインのセッションは 3 製品で共通です（#1565。下の「ログインのセッションの共有（#1565）」）。`/`（Agent、無ければ最初の製品）を含め、
  どの製品の `/login` でログインしても全製品を使えます。
- Wallet は `/u01/aipoc/wallet`、ログは `/var/log/cloud-init-custom.log`（bootstrap）・`/var/log/suite-init.log`（全体）・`/var/log/<製品>-init.log`。
- apply 後の output に、`application_url`（`/`）、製品ごとの URL（`rag_application_url` など。配備しなかった製品は表示されない）、
  `ca_certificate_url`（HTTPS のとき）、`ssh_to_instance` が出ます。

#### Compute の OS（Oracle Linux 9。#1611）

| 項目 | Oracle Linux 9 の Compute |
|---|---|
| ログイン・実行ユーザー | `opc`（`ssh opc@<IP>`。3 製品のプロセスと repository の所有者。sudo は OCI の image の設定のまま無制限） |
| package | `dnf`。BaseOS / AppStream だけを使い、EPEL・CodeReady Builder は有効にしない。Nginx は module `nginx:1.24`、Node.js は module `nodejs:24`（入らなければ公式の tarball を SHASUMS256 で確かめて `/usr/local/lib/nodejs`）。Python は今までどおり uv の `uv python install 3.12` |
| Nginx | site は `/etc/nginx/conf.d/production-ready-suite.conf`。worker のユーザーは `nginx`、ログは `/var/log/nginx/production-ready-<製品>-*.log` |
| firewall | `firewalld`。HTTPS が on なら `https_port` だけ、off なら `http_port` だけを `--permanent` で開ける（on のときに `http_port` が開いていれば閉じる。#1638） |
| SELinux | enforcing のまま（無効にしない）。`suite-init.sh` の `configure_selinux` が次を設定する |
| RAG の前処理 / parser の OS の依存 | LibreOffice（`libreoffice-core` / `-writer` / `-impress` / `-calc`、AppStream の 7.1）・`google-noto-sans-cjk-ttc-fonts` / `google-noto-serif-cjk-ttc-fonts`・`poppler-utils`・`tesseract` / `tesseract-langpack-eng` / `tesseract-langpack-jpn` |

SELinux の設定（`semanage` は `policycoreutils-python-utils`）:

- `semanage fcontext -a -t usr_t '/u01/aipoc/no.1-production-ready-suite(/.*)?'` と `restorecon -R`: systemd（`init_t`）は `/u01` の既定の
  `default_t` のファイルを実行も読みもできないため、repository を `usr_t` にする（RAG の unit の `.venv/bin/gunicorn` と `EnvironmentFile`）。
  新しく作るファイル（venv・`node_modules`・`git pull` の分）は親の `usr_t` を受け継ぐ。
- `semanage fcontext -a -t httpd_sys_content_t '<repository>/<製品>/frontend/dist(/.*)?'`: Nginx が配信する dist。build の後に `restorecon -R`。
- `semanage fcontext -a -t cert_t '/u01/aipoc/ssl(/.*)?'`: Nginx が読む証明書と鍵（`/platform/ca.crt` も）。
- `setsebool -P httpd_can_network_connect 1`: Nginx から `127.0.0.1` の各製品の backend への proxy。
- `restorecon -v /usr/local/bin/uv /usr/local/bin/uvx`（各製品の `init_script.sh` の `install_uv`。#1642）: uv の installer は一時の場所から
  移すため `bin_t` にならず、systemd が NL2SQL / Agent の unit の `ExecStart=/usr/local/bin/uv` を実行できない（`status=203/EXEC`）。
  suite-v0.1.3 までの Compute は、`sudo restorecon -v /usr/local/bin/uv /usr/local/bin/uvx` の後、systemd の自動の再起動で起動する。
- 公開する port（HTTPS が on なら `https_port`、off なら `http_port`）が 80 / 443 以外なら `semanage port -a -t http_port_t -p tcp <port>`。
- Ubuntu で配備した Compute は更新しません。Oracle Linux 9 にするには stack を apply して Compute を作り直してください（データの正本は ADB。
  Compute の中だけにあるものは下の「製品ごとの Compute の stack からの更新」と同じく引き継がれません）。

#### 製品ごとの Compute の stack からの更新（#1316）

- 製品ごとの Compute（`oci_core_instance.product` / `oci_core_instance.agent`）は、apply で削除され、1 台の Compute（`oci_core_instance.suite`）が作られます。
  データの正本は ADB なので、DB のデータ（ユーザー・ロール・各製品のテーブル）はそのまま使えます。
- Compute の中だけにあるもの（アップロードの原本などのローカルの保存、画面で変えた `platform/.env` / `model-settings.json` の値、`~/.oci/config`）は
  引き継がれません。必要なら apply の前に退避し、新しい Compute で各製品の「システム設定」から設定し直してください（data migration の仕組みは持ちません）。
- 入力の変更: `application_port`・`<製品>_instance_*`・`rag_app_auth_cookie_secure` / `nl2sql_app_auth_cookie_secure` / `agent_app_auth_cookie_secure` は
  廃止し、`instance_display_name`・`instance_flex_shape_ocpus` / `_memory`・`instance_boot_volume_size`・`https_enabled`・`http_port`・`https_port` を足しました。
- 製品の `init_script.sh` を単独で実行する配備（製品ごとの Nginx の site）はやめました（3 製品はこの suite でセットで配備します）。
- URL が `http://<製品ごとの IP>/` から `https://<IP>/<製品>/` に変わります。ブックマークや、外部から Agent の MCP（`/agent/api/mcp`）を呼ぶ設定を更新してください。

### 配備した後の更新と再起動（#1566・#1577）

| スクリプト（`platform/deploy/`） | 実行 | 内容 |
|---|---|---|
| `suite-pull.sh` | opc か `sudo` | repository の所有者（opc）で `origin/main` を取得し、`main` を付け替える（clone は depth 1）。追跡ファイルに変更があれば中止し、未追跡のファイルは保つ。取得だけで、build・再起動はしない |
| `suite-restart.sh` | `sudo` | 既定: 今のコードから build して再配備する（`suite-init.sh` の再実行。依存の同期・共有 UI と各製品の frontend の build・DB の初期化と migration・systemd・Nginx・再起動）。最後に配備全体を確かめる（下の「配備が正常かの確認」）。`--no-build` は build せずに、有効（enabled）な `production-ready-*` の service と Nginx だけを再起動する。`--status` は再起動せず、確認だけを 1 回行う |
| `suite-update.sh` | `sudo` | `suite-pull.sh` で取得し、取得した新しい `suite-restart.sh`（既定）を実行する |

- `suite-init.sh` は props（Resource Manager の入力）から、各製品の `backend/.env` を作り直す。
  - そのため `suite-restart.sh` は、build の前に今の `backend/.env` を `/u01/aipoc/props/<製品>.backend.env` へ写す（画面で保存した値を保つ）。
  - 元の props は `/u01/aipoc/recovery/<時刻>/` に退避する。
  - `platform/.env`・Wallet・HTTPS の CA は、`suite-init.sh` がそのまま保つ。
- 2 つのスクリプトは lock を共有し、同時の実行を防ぐ。log は `/var/log/suite-ops.log`（再配備の詳細は `/var/log/suite-init.log`）。
- cloud-init は `git` と `tmux` を入れる。
- 製品の `init_script.sh` は backend の `/api/health` を 3 秒ごとに最大 300 秒待ち（`SUITE_BACKEND_HEALTH_TIMEOUT_SECONDS`）、15 秒ごとに経過・状態・
  再起動の回数を出す。service が `failed` になるか再起動を 3 回繰り返したら、すぐに status と journal を出して失敗にする（#1642）。
- OS の再起動では、enabled の unit（`WantedBy=multi-user.target`）と Nginx が自動で起動する。
  - enabled の unit は、各製品の backend・worker と、起動にした RAG の前処理 / parser。
  - ADB は Compute の外にあるため、止まっていれば OCI のコンソールから起動する。

### 配備が正常かの確認（#1642）

`suite-init.sh` の最後（`suite-restart.sh` の最後と `--status` も同じ。`platform/deploy/suite-health.sh` の `suite_check_deployment`）に、次を確かめる。

- 有効な `production-ready-*` の service（証明書の更新の oneshot を除く）がすべて `active` で、再起動を繰り返していないか
- 配備した製品の backend の `/api/health`（`127.0.0.1:<port>`）と、Nginx を通した `/<製品>/api/health`（HTTPS が on なら `https://127.0.0.1:<https_port>`）
- Nginx が `active` か
- 各製品の system schema が ready か（#1648。製品の schema の CLI の `check`。DDL は実行しない）。`init_script.sh` は表の作成・更新に
  失敗しても backend を起動する（画面から直せる）ため、health だけでは OK に見える。NG なら CLI の要約（状態・不足の object・
  前回の失敗の ORA コード）、製品の初期化の log（`/var/log/<製品>-init.log`）の最後の ORA コード、権限の不足の手がかりを出す

結果は製品ごとに 1 行で出す。1 つでも NG なら、NG の service の journal の末尾を出し、0 以外で終わる。

```
[suite-init] check: rag backend OK / services 4/4 active / nginx OK
[suite-init] check: nl2sql backend OK / services 5/5 active / nginx OK
[suite-init] check: agent backend OK / services 1/1 active / nginx OK
[suite-init] check: nginx.service active（https://127.0.0.1:443）
[suite-init] check: rag schema OK（status=ready）
[suite-init] check: nl2sql schema OK（status=ready）
[suite-init] check: agent schema OK（status=ready）
[suite-init] check: OK（すべての service が active で、各製品の health が backend と Nginx の両方で応答し、system schema が ready です）。
```

schema の CLI（backend の directory で `sudo -u opc .venv/bin/python <下の引数>`。ready なら 0）:
RAG `-m app.rag.system_schema_cli check`・NL2SQL `-m app.cli.nl2sql_system_schema --check`（`app_security_migrate` の表も見る）・
Agent `-m app.cli.agent_system_schema --check`。

- 配備の直後は `sudo tail -n 20 /var/log/suite-init.log` の最後の `check:` の行を見る。
- 後から確かめるときは `sudo /u01/aipoc/no.1-production-ready-suite/platform/deploy/suite-restart.sh --status`（再起動しない）。

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
   # RAG
   cd /u01/aipoc/no.1-production-ready-suite/rag/backend
   sudo -u opc .venv/bin/python -m app.rag.system_schema_cli initialize
   # NL2SQL
   cd /u01/aipoc/no.1-production-ready-suite/nl2sql/backend
   sudo -u opc /usr/local/bin/uv run python -m app.cli.app_security_migrate --apply --skip-bootstrap
   # Agent
   cd /u01/aipoc/no.1-production-ready-suite/agent/backend
   sudo -u opc /usr/local/bin/uv run python -m app.cli.agent_system_schema --initialize
   ```

3. Nginx のログインの API の上限は、`platform/deploy/suite-nginx.sh` の site（`/<製品>/api/auth/login`）に入っています。
4. 確認: backend のログに `auth_login_throttle_shared_store_unavailable` が出ないこと。存在しないログイン ID で 1 回ログインに失敗すると、
   `SELECT COUNT(*) FROM PLATFORM_LOGIN_ATTEMPTS` が 2 増えること（窓を過ぎると、次の失敗のときに消える）。

### ログインのセッションの共有（#1565）

1 台の Compute の 3 製品（`/rag/`・`/nl2sql/`・`/agent/`）は同じ origin なので、ログインの Cookie を共有し、1 回のログインで全製品を使えます。

- Cookie: セッション `pr_session`（HttpOnly）と CSRF `pr_csrf`（`Path=/`・`SameSite=Lax`、HTTPS なら `Secure`）。名前は共通 `.env` の
  `PLATFORM_AUTH_SESSION_COOKIE_NAME` / `PLATFORM_AUTH_CSRF_COOKIE_NAME`（変えるなら frontend の `CSRF_COOKIE_NAME`
  （`@production-ready/system-settings`）も同じにする）。製品ごとの `RAG_` / `NL2SQL_` / `AGENT_APP_AUTH_*_COOKIE_NAME` は読みません。
- セッションは共通のテーブル `PLATFORM_AUTH_SESSIONS` にあり、どの製品の backend でも検証できます。構成管理者 `system_admin` の token
  （DB の行を持たない署名付きの token）も、接頭辞 `platform-system-admin-v1` と署名の鍵（共通の `PLATFORM_ADMIN_LOGIN_*` から導く）が 3 製品で同じです。
- ログインし直すと、ブラウザが持っていた古いセッションは失効します。ログアウトは共有のセッションを失効させ、開いているほかの製品・タブは
  `BroadcastChannel` で知らせを受けてログイン画面へ移ります（受けなくても次の API の 401 で移る）。
- 権限はこれまでどおり製品ごとです（ロールに権限の無い製品は 403 の画面）。

既存環境の更新:

1. コードを更新します。`platform/.env` に次を足します（無くても既定値は同じ）。製品の `backend/.env` の
   `RAG_APP_AUTH_SESSION_COOKIE_NAME` / `RAG_APP_AUTH_CSRF_COOKIE_NAME`・`NL2SQL_APP_AUTH_*_COOKIE_NAME`・`AGENT_APP_AUTH_*_COOKIE_NAME` は
   もう読まないので消します。

   ```bash
   PLATFORM_AUTH_SESSION_COOKIE_NAME=pr_session
   PLATFORM_AUTH_CSRF_COOKIE_NAME=pr_csrf
   ```

2. 3 製品の frontend を build し直し（`@production-ready/system-settings` の CSRF の Cookie 名が変わる）、backend を再起動します
   （「配備した後の更新と再起動（#1566）」の `suite-update.sh` で行える。`backend/.env` に残った旧名の行は読まれないだけで害はない）。
3. 利用者は一度ログインし直します。古い Cookie（`rag_session` など）は読まれず、期限（`PLATFORM_AUTH_ABSOLUTE_TIMEOUT_HOURS`）で
   ブラウザから消えます。古い Cookie のセッションの行は、期限が過ぎると使えなくなります。構成管理者の古い token（`rag-system-admin-v1` など）も使えません。

## パッケージと検証

monorepo root から実行します。

```bash
python terraform/scripts/package_stack.py
python terraform/scripts/verify_stack_contract.py terraform/dist/production-ready-suite-terraform-stack.zip
```

出力は `terraform/dist/production-ready-suite-terraform-stack.zip` です。この zip を Resource Manager へ upload して stack を作成します。
`verify_stack_contract.py` は、フォーム（`schema.yaml`）と Terraform 変数の一致、製品選択と 1 台の Compute・HTTPS の入力の契約、製品ごとの `backend/.env` の key が各製品の Settings にあること、
backend の port（RAG 8000・NL2SQL 8010・Agent 8020）が init_script.sh・Nginx・MCP の URL で一致し衝突しないこと、証明書の固定の subject、
RAG の前処理 / parser（`rag/scripts/rag-systemd.sh` の unit の定義と uv.lock があること）、各製品の `init_script.sh` の配備契約
（Docker を入れないこと、RAG の sudoers・状態の保持を含む）と、Dockerfile・compose が無いこと（#356 / #754）を検証します。

CI（`.github/workflows/ci.yml` の `Suite / Deploy`。中身は `.github/workflows/suite-deploy.yml` で、release の workflow も再利用する。#1592）は、
`terraform fmt` / `terraform validate`（Terraform 1.5.7）と上の2つに加え、
`platform/deploy/tests/`（suite-init の振る舞い、openssl で証明書の subject・SAN・拡張・有効期間、隔離した Nginx で prefix・SSE・MCP・ログインの上限・
`/` → `/agent/`・HTTPS on / off）と、各製品の `init_script.sh` と配備・更新のスクリプトのテスト（`<製品>/scripts/tests/`）を 1 つの job で実行します。
製品の frontend・共有 UI・Markdown だけの変更では動きません。実テナンシーへの配備確認は手動で行います。

## release

`.github/workflows/terraform-release.yml` が `suite-v*` の tag（例: `suite-v0.1.0`）から release を作り、
`production-ready-suite-terraform-stack.zip` と `.sha256` を公開し、その release を GitHub の Latest にします。次のボタンは最新の release を配備します。

[![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-suite/releases/latest/download/production-ready-suite-terraform-stack.zip)

root の [README.md](../README.md)「OCI へのデプロイ」のボタンも `releases/latest` を指すので、release のたびに書き換えません。版ごとの変更は release のノートに書きます。

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
| NL2SQL の `adb_workload` の既定 `LH` | `LH`（#1558。統合の当初は `OLTP` だった） |
