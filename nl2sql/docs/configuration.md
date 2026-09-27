# NL2SQL 設定ソース

設定は 2 つの `.env` に分けて置く（#211）。

| ファイル | 雛形（設定カタログ） | 置くもの | 変数名 |
|---|---|---|---|
| 共通 `.env`（`platform/.env`） | `platform/.env.example` | 3製品共通の設定。OCI 認証・Oracle 26ai・モデル・アップロード保存先・構成管理者・認証ポリシー | `PLATFORM_*` |
| 製品 `.env`（`nl2sql/backend/.env`） | `nl2sql/backend/.env.example` | NL2SQL だけが使う設定。アプリ・認証の有効 / 無効と Cookie 名・Deep Data Security・NL2SQL runtime・Select AI など | `NL2SQL_*` |

- backend は共通 `.env` → 製品 `.env` の順に読む。同じ変数が環境変数にあれば環境変数が優先される。
- 共通 `.env` の場所は環境変数 `PLATFORM_ENV_FILE` で変えられる（既定は monorepo の `platform/.env`）。
  コンテナでは `PLATFORM_ENV_FILE` を明示し、共通 `.env` を含むディレクトリを書き込み可能でマウントする
  （`env_file` で注入すると画面で保存した値より環境変数が優先され、保存が反映されない）。
- **旧名（`ORACLE_DSN` / `OCI_REGION` / `APP_ADMIN_*` / `LOG_LEVEL` / `DEBUG` / `OCI_PROFILE` など接頭辞のない名前）は読まない。**
  既存環境は下の「既存環境の更新手順（#211）」で移す。
- 各環境の `.env` は secret、デプロイ差分、明示的なセキュリティ境界だけを保持し、カタログの全項目を複製する必要はない。

## 画面から保存する値の書き込み先

| 画面 / 操作 | 書き込み先 |
|---|---|
| システム設定（OCI 認証・アップロード保存先・データベース・ADB・モデルの API key） | 共通 `.env`（`PLATFORM_*`） |
| モデルの非 secret 設定 | `PLATFORM_MODEL_SETTINGS_FILE`（既定 `model-settings.json`。相対パスは共通 `.env` の場所から解決し、3製品で共有する） |
| 構成管理者のパスワード変更 | 共通 `.env` の `PLATFORM_ADMIN_LOGIN_USER_PASSWORD` |
| Deep Data Security の DATA USER パスワード | 製品 `.env` の `NL2SQL_ORACLE_DEEPSEC_*` |
| Select AI Credential の作成 | 製品 `.env` の `NL2SQL_SELECT_AI_CREDENTIAL_NAME` / `NL2SQL_SELECT_AI_REGION` |

## 優先順位と secret

1. 設定 UI が `PLATFORM_MODEL_SETTINGS_FILE` へ保存した非 secret のモデル設定。
2. 環境変数。
3. 共通 `.env` / 製品 `.env`。
4. `Settings` の既定値（`.env.example` と同じ値）。

secret は例外とし、`PLATFORM_OCI_ENTERPRISE_AI_API_KEY` は共通 `.env`（または環境変数）だけから読み込む。
旧 v1 `model-settings.json` の key は環境 key がない場合に限り一時的に読み込める。
次回のモデル設定保存で `.env` へ移し、secret を含まない v2 JSON へ更新する。
API 応答は `has_api_key`、`secret_source`、`legacy_secret_detected` だけを返す。

OCI SDK の profile は `PLATFORM_OCI_CONFIG_PROFILE` だけで決める（空なら `DEFAULT`）。旧 `OCI_PROFILE` は読まない。

## 設定の監査

デプロイ前に read-only audit を実行する。

```bash
cd backend
.venv/bin/python -m app.cli.config_audit
```

このコマンドは共通 `.env`（`PLATFORM_ENV_FILE`）・製品 `.env`・両方の雛形・`model-settings.json` を検査し、
stable JSON を返す。設定値は出力しない。終了コードが 0 以外の場合は、カタログ、本機環境、
セキュリティ組み合わせ、ファイル権限、モデル設定のいずれかを修正する。
製品 `.env` に残った `PLATFORM_*` や旧名は `ENV_ACTUAL_UNKNOWN_KEYS`、共通 `.env` に置いた製品の変数は
`PLATFORM_ENV_ACTUAL_UNKNOWN_KEYS` として検出する。

非 local 環境は起動時に `NL2SQL_DEBUG=false` を必須とする。認証を有効にする場合は
`PLATFORM_AUTH_COOKIE_SECURE=true` も必須とする。

## 既存環境の更新手順（#211）

旧名は読まないため、#211 を含む版へ更新するときは `backend/.env` の共通設定を共通 `.env` へ移す。
OCI Compute（Terraform で配備した環境）での手順を示す。パスは `<suite>` = `/u01/aipoc/no.1-production-ready-suite`。

1. backend と worker を停止する。

   ```bash
   sudo systemctl stop production-ready-nl2sql-schema-refresh-worker.service \
     production-ready-nl2sql-synthetic-worker.service \
     production-ready-nl2sql-quality-evaluation-worker.service \
     production-ready-nl2sql-ontology-worker.service
   sudo systemctl stop production-ready-nl2sql-backend.service
   ```

2. ソースを更新し（`git pull` など）、移行内容を確認する。ファイルは変更しない。

   ```bash
   cd <suite>
   uv run --project platform/packages/backend_core \
     python platform/scripts/migrate_env_to_platform.py --product nl2sql
   ```

3. 内容を確認したら適用する。共通の変数は `platform/.env` へ `PLATFORM_*` で移り、製品の変数は
   `nl2sql/backend/.env` に `NL2SQL_*` で残る。書き換え前のファイルは `<file>.bak-211` に保存される。

   ```bash
   uv run --project platform/packages/backend_core \
     python platform/scripts/migrate_env_to_platform.py --product nl2sql --apply
   ```

   - 旧 `APP_ADMIN_USERNAME` / `APP_ADMIN_PASSWORD`（`APP_ADMIN_LOGIN_USER_*` より前の旧名）と `OCI_PROFILE` は移さない。
     使っていた場合は `platform/.env` に `PLATFORM_ADMIN_LOGIN_USER_ID=system_admin` /
     `PLATFORM_ADMIN_LOGIN_USER_PASSWORD`、`PLATFORM_OCI_CONFIG_PROFILE` を手で設定する。
   - `model-settings.json` は共通 `.env` の場所になければコピーされる（`PLATFORM_MODEL_SETTINGS_FILE` を
     絶対パスで指定している場合はそのファイルを使い続ける）。

4. `platform/.env` を backend の実行ユーザー所有・`0600` にする。

   ```bash
   sudo chown ubuntu:ubuntu <suite>/platform/.env && sudo chmod 0600 <suite>/platform/.env
   ```

5. `nl2sql/scripts/update-after-pull.sh` を実行して再配備・再起動する（`platform/.env` の存在・権限と
   `PLATFORM_ORACLE_WALLET_DIR` を確認し、recovery snapshot に `platform.env` も保存する）。
6. `cd <suite>/nl2sql/backend && .venv/bin/python -m app.cli.config_audit` が `"ok":true` になることを確認する。

- 新規の Resource Manager 適用では、cloud-init が `/u01/aipoc/props/platform.env` を `platform/.env`
  （`0600`）へ、`/u01/aipoc/props/backend.env` を `nl2sql/backend/.env` へ配置するため、この手順は不要。
- Docker Compose では手順 2〜3 のあと `docker compose up -d --build` で再起動する。共通 `.env` は
  `PLATFORM_ENV_DIR`（既定 `../platform`）のディレクトリをマウントして読む。
