# アプリケーション認証・RBAC・Deep Data Security 運用ガイド

## 適用範囲

本機能は OCI IAM を使用せず、Oracle に永続化した local application user と role で認証・認可する。
アプリケーション機能権限は FastAPI の route manifest で default deny とし、画面表示制御に加えて
API 側でも毎回ユーザー状態、role、permission を再評価する。

Deep Data Security は共有 local END USER と classic application context を使用する。これは本システムの
非 IAM 構成向け custom integration であり、Oracle 公式の IAM/database access token を含む local END
USER 認証フローとは区別する。

## 初期 migration と管理者 bootstrap

Oracle 接続設定を `backend/.env` に設定した後、次を一度実行する。処理は幂等であり、再実行できる。

```bash
cd backend
uv sync
uv run python -m app.cli.app_security_migrate --apply
```

`NL2SQL_APP_USERS` が空の場合だけ、`ORACLE_USER` と `ORACLE_PASSWORD` から首個の application user を
作成し、組み込み `SYSTEM_ADMIN` role を付与する。この application password のコピーは初回だけで、
以降は database password と独立する。初回ログイン後は強制パスワード変更が必要になる。

旧版で作成された 8 個の `RAG_*` security table が存在する場合、migration 005 がデータを保持したまま
`NL2SQL_*` へ table、constraint、index を rename し、entitlement resource code も移行する。新規環境は
migration 004 から `NL2SQL_*` object を直接作成する。005 に残る旧 prefix は移行元を識別するためだけの
versioned legacy reference であり、runtime object 名としては使用しない。

本番では少なくとも次を設定する。

```dotenv
APP_AUTH_ENABLED=true
APP_AUTH_COOKIE_SECURE=true
APP_AUTH_IDLE_TIMEOUT_MINUTES=30
APP_AUTH_ABSOLUTE_TIMEOUT_HOURS=12
APP_AUTH_FAILED_LOGIN_LIMIT=5
APP_AUTH_LOCKOUT_MINUTES=15
```

## DeepSec V001 の前提

V001 を適用する前に、API process を選択した driver mode で再起動する。同一 process 内で
Thick/Thin は混在させない。現在の integration は共有 local END USER を password で直接 login し、
classic application context に application user UUID を設定するため、**Thin/Thick の両方に対応する**。

共通設定:

```dotenv
ORACLE_DEEPSEC_ENABLED=true
ORACLE_DEEPSEC_END_USER=NL2SQL_APP_END_USER
ORACLE_DEEPSEC_END_USER_PASSWORD=<strong-random-secret>
```

Thick mode を使う場合:

```dotenv
ORACLE_DRIVER_MODE=thick
ORACLE_CLIENT_LIB_DIR=/u01/aipoc/instantclient_23_26
```

Oracle Client と Wallet / Oracle Net 設定を用意する。現在の設定解決では
`<ORACLE_CLIENT_LIB_DIR>/network/admin` を `tnsnames.ora` 等の配置先として使用する。

Thin mode を使う場合:

```dotenv
ORACLE_DRIVER_MODE=thin
ORACLE_WALLET_DIR=<thin-mode-wallet-or-config-directory>
ORACLE_WALLET_PASSWORD=<wallet-password-if-required>
```

python-oracledb の `create_end_user_security_context()` / `set_end_user_security_context()` または
`end_user_sec_provider` SPI による EndUserSecurityContext payload 伝播は Thin mode 限定である。
本システムはこれらの API/SPI を使用しない。将来 payload integration を追加する場合は、direct logon
との構成境界を分け、payload 側だけ `ORACLE_DRIVER_MODE=thin` を必須とする。

管理画面の `システム設定 > Deep Data Security` で以下を行う。

1. status の driver mode、前提権限、既存 object 名を確認する。
2. V001 の SQL preview と SHA-256 checksum を確認する。password は placeholder のみ表示される。
3. 各 step を確認 dialog から順番に適用する。API は version、step、checksum だけを受け付け、SQL 本文は受け付けない。
4. 失敗した場合は ledger の完了 step を保持し、原因を解消して失敗 step から再開する。
5. Limited user/role に `NL2SQL_DEEPSEC_PROBE` の `ROW_READ` entitlement を設定し、verify を実行する。

Oracle DDL は暗黙 commit を含むため、V001 全体を一括 rollback したようには表示しない。既存の無関係な
END USER、DATA ROLE、context、Data Grant は DROP/上書きしない。

## Data Grant 検証の判定

- context 未設定: probe row は 0 件。
- Limited subject: entitlement scope の row のみ取得でき、未認可の `SENSITIVE_TEXT` は `NULL`、
  `ORA_IS_COLUMN_AUTHORIZED(SENSITIVE_TEXT)` は false。
- Full subject: すべての probe row と sensitive column を取得できる。
- 複数 role: entitlement は加法的に合成される。

`SYSTEM_ADMIN` は application feature permission では将来権限を含む wildcard だが、data entitlement では
wildcard ではない。bootstrap 時に `NL2SQL_DEEPSEC_PROBE/*/FULL` だけを明示付与する。

## SQL 実行画面の安全境界

AI 活用の `/direct-sql`（SELECT SQL を実行）は `search.view` で表示し、実行には `search.execute` を
要求する。`/api/nl2sql/execute` だけを使用し、SELECT/WITH 以外と複数 statement はサーバー側の
SELECT-only guard で拒否する。許可された SQL は、ログイン中 application user UUID を DeepSec
context へ設定した data pool で実行する。

データ準備の `/admin-sql`（管理 SQL を実行）は `settings.database.sql_execute` permission を持つ
管理者向け機能である。単一の SELECT/WITH は `/api/nl2sql/db-admin/execute` から同じ data pool の
`execute_select()` に収束する。非 SELECT または複数 statement は管理 SQL として扱い、
`ADMIN_EXECUTE` 確認語、RBAC、監査を通した上で application DB user の control pool から実行する。
この経路は業務データ参照用の DeepSec data plane ではないため、通常の SELECT 実行と混同しない。
確認不要の判定には通常の SELECT-only guard を再利用し、WITH で始まる更新文も管理 SQL として扱う。

## 主要な安全境界

- session token は 256-bit random value、DB には SHA-256 digest だけを保存する。
- session cookie は HttpOnly、CSRF は cookie/header/server digest の三者一致を要求する。
- password は Argon2id で保存し、平文 password、hash、session token、Oracle secret を応答・監査へ出さない。
- Data pool connection は貸出時に application user UUID を context へ設定し、返却前に必ず clear する。
- context 設定・clear に失敗した connection は再利用せず、data operation は control pool へ fallback しない。
- background job は actor UUID を保存し、worker 実行時に user が active であることを再確認する。
