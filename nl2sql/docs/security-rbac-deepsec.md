# アプリケーション認証・RBAC・Deep Data Security 運用ガイド

## 適用範囲

本機能は OCI IAM を使用せず、Oracle に永続化した local application user と role で認証・認可する。
ただし `backend/.env` の `APP_ADMIN_LOGIN_USER_ID=system_admin` / `APP_ADMIN_PASSWORD` に一致する構成管理者は、
認証 table を参照しない `SYSTEM_ADMIN` として扱う。`APP_ADMIN_LOGIN_USER_ID` は `system_admin` 固定・
大小文字区別であり、`System_Admin` / `SYSTEM_ADMIN` などは database user へ fallback しない。
`ORACLE_USER` / `ORACLE_PASSWORD` は database connection 専用であり、application login には使用しない。
アプリケーション機能権限は FastAPI の route manifest で default deny とし、画面表示制御に加えて API 側でも
毎回ユーザー状態、role、permission を再評価する。

RBAC は画面表示用の `menu.*` と、API/データ利用用の capability permission を分ける。たとえば
`menu.profiles` は「業務プロファイル」管理画面へ入る権限であり、SQL 生成画面で profile を選択・利用する
権限ではない。`menu.query` と `menu.sql_to_question` は認証時に `nl2sql.profiles.read` と
`nl2sql.schema.read` を継承し、業務 profile の summary / usage context と schema picker を読める。
一方で full profile detail、作成、更新、削除、import/export、Oracle sync は
`nl2sql.profiles.manage` を要求する。`nl2sql.profiles.manage` は `nl2sql.profiles.read` を継承する。
schema refresh は `nl2sql.schema.refresh`、schema 参照は `nl2sql.schema.read` とし、refresh は read を継承する。

同じ方針で、SQL 生成と SELECT SQL 実行も capability を分ける。`menu.query` は
`nl2sql.query.generate` / `nl2sql.sql.execute` / `nl2sql.feedback.write` を継承し、SQL 生成 API、
直接実行、本人履歴への feedback 登録を利用できる。`menu.direct_sql` は `nl2sql.sql.execute` だけを
継承し、`/api/nl2sql/jobs`、`recommend-profile`、`rewrite`、`similar-history` などの生成補助 API は
利用できない。feedback 管理一覧、admin review、feedback index/config は `nl2sql.feedback.manage` を
要求し、普通ユーザーは自分の履歴にだけ feedback を登録できる。Select AI / Agent の低レベル資産 API は
`nl2sql.select_ai_assets.read` / `refresh` / `manage`、sample data は
`nl2sql.sample_data.manage`、legacy learning material は `nl2sql.learning_material.manage`、
diagnostics は `nl2sql.system_status.read`、persistence recover は `nl2sql.persistence.recover` で制御する。
ただし `GET /api/nl2sql/persistence` は業務画面起動時の readiness gate が利用する粗粒度状態であり、
ログイン済みユーザーなら capability なしで参照できる。

Deep Data Security は共有 DATA USER と classic application context を使用する。これは本システムの
非 IAM 構成向け custom integration であり、Oracle 公式の IAM/database access token を含む local END
USER 認証フローとは区別する。
通常ユーザーの SELECT SQL 実行は DeepSec data plane を通り、Oracle / DeepSec runtime failure は
HTTP 502 の実行エラーとして画面に表示する。これはアプリケーションのメニュー権限不足ではない。
`NL2SQL_DEEPSEC_CTX_PKG` の context クリアロジックを更新した後は、Deep Data Security 画面で
V001.2「アプリケーションコンテキスト」を再適用する。古い package を DB に残したままでは通常ユーザーの
SELECT 実行で `DeepSec context を消去できないため接続を破棄しました。` が継続する。
`CLEAR_APP_USER` は `DBMS_SESSION.CLEAR_CONTEXT` ではなく、trusted package 内で
`DBMS_SESSION.SET_CONTEXT(..., NULL)` により `LOGIN_USER_ID` と legacy `APP_USER_ID` を空に戻す。
`PLS-00905: object ... NL2SQL_DEEPSEC_CTX_PKG is invalid` が出る場合も V001.2 を再適用し、適用結果が
失敗になったときは画面の compile error を修正してから再実行する。
V001 step の Oracle 実行・compile エラーは HTTP 409 として返し、DeepSec plan には `FAILED` と
安全化した error message を保存する。これは再適用・DB 権限修正で解消する運用エラーであり、API の
未処理 500 ではない。
`oracle_data_connection_close_failed` の `DPY-1001` は破棄済み接続を close した副作用であり、
実際の context クリア失敗は backend の `oracle_deepsec_context_clear_failed` warning を確認する。

## 初期 migration と構成管理者

`APP_ADMIN_LOGIN_USER_ID=system_admin` / `APP_ADMIN_PASSWORD` を `backend/.env` に設定すると、その構成管理者で
アプリケーションへログインできる。この `SYSTEM_ADMIN` ログインは `NL2SQL_APP_USERS` /
`NL2SQL_AUTH_SESSIONS` を読まず、認証 table が未作成でも利用できる。通常の application user を追加して
使う場合は、DB 接続後に次を一度実行する。
処理は幂等であり、再実行できる。

```bash
cd backend
uv sync
uv run python -m app.cli.app_security_migrate --apply --skip-bootstrap
```

通常の application user は `NL2SQL_APP_USERS` から照合される。構成管理者の password は application
password 変更画面から変更でき、変更結果は `backend/.env` の `APP_ADMIN_PASSWORD` に書き戻される。
通常 user の password はこれまでどおり DB hash として保存される。ユーザー管理 API/UI は
`system_admin` の大小文字違いを含む DB user 作成を拒否する。

`SYSTEM_ADMIN` role は構成管理者と旧 bootstrap user 専用とする。ユーザー管理 API/UI は、後続で
作成したユーザーへの新規付与・再付与を拒否する。旧版や手動操作で非 bootstrap user に
`SYSTEM_ADMIN` が残っている場合も migration では自動撤去せず、管理者が必要に応じて手動で解除する。

旧版で作成された 8 個の `RAG_*` security table が存在する場合、migration 005 がデータを保持したまま
`NL2SQL_*` へ table、constraint、index を rename し、entitlement resource code も移行する。新規環境は
migration 004 から `NL2SQL_*` object を直接作成する。005 に残る旧 prefix は移行元を識別するためだけの
versioned legacy reference であり、runtime object 名としては使用しない。

本番では少なくとも次を設定する。

```dotenv
APP_AUTH_ENABLED=true
APP_AUTH_COOKIE_SECURE=true
APP_AUTH_IDLE_TIMEOUT_MINUTES=60
APP_AUTH_ABSOLUTE_TIMEOUT_HOURS=12
APP_AUTH_FAILED_LOGIN_LIMIT=5
APP_AUTH_LOCKOUT_MINUTES=15
```

既定では、通常ユーザーの無操作 timeout は 60 分、session の絶対有効期限は 12 時間とする。
業務端末が管理下にあり、無人端末リスクを組織として受容できる低リスク環境でだけ、
deployment 固有の `.env` で `APP_AUTH_IDLE_TIMEOUT_MINUTES=720` を明示して 12 時間の無操作
timeout に拡張できる。これは production 既定値ではない。

`system_admin` 構成管理者は認証 table 未作成時の bootstrap / 運用復旧用 identity であり、
`NL2SQL_AUTH_SESSIONS` を使わない署名 token として絶対有効期限のみを持つ。通常運用は DB に
永続化した application user を使い、`system_admin` は初期設定と復旧用途に限定する。

## DeepSec V001 の前提

DeepSec は python-oracledb Thin mode のみ対応する。`ORACLE_DEEPSEC_ENABLED=true` の場合、
`ORACLE_DRIVER_MODE=thick` は起動時の設定 validation、Oracle 接続検証、DeepSec status / V001 適用で
fail-fast する。DATA USER password は Deep Data Security 画面から保存でき、保存後は API を再起動せずに
次の適用・検証・data-plane query から使用される。

共通設定:

```dotenv
ORACLE_DEEPSEC_ENABLED=true
ORACLE_DEEPSEC_DATA_USER=DEEPSEC_DATA_USER
ORACLE_DEEPSEC_DATA_USER_PASSWORD=<strong-random-secret>
ORACLE_DRIVER_MODE=thin
ORACLE_CLIENT_LIB_DIR=
ORACLE_CONNECTION_SECURITY=wallet_mtls
ORACLE_WALLET_DIR=<thin-mode-wallet-or-config-directory>
ORACLE_WALLET_PASSWORD=<wallet-password-if-required>
```

Thin mTLS の Wallet / config directory には `tnsnames.ora` と `ewallet.pem` を配置する。
`sqlnet.ora` と `cwallet.sso` は Thick 互換の Oracle Net 構成向けであり、DeepSec 有効時の必須条件にはしない。
DeepSec 無効の非標準運用で Thick 専用機能が必要な場合のみ、別 service/process として設計し直す。

本システムは database access token や python-oracledb の `set_end_user_security_context()` を使わず、
共有 `DEEPSEC_DATA_USER` の direct logon と application context / `CLIENT_IDENTIFIER` で実行時 user を
伝播する。ただし DeepSec 全体の driver mode は Thin に統一する。

管理画面の `システム設定 > Deep Data Security` で以下を行う。

1. status の driver mode、前提権限、既存 object 名を確認する。
2. V001 の SQL preview と SHA-256 checksum を確認する。password は placeholder のみ表示される。
3. 各 step は `ADMIN_EXECUTE` 実行確認語を入力して順番に適用する。API は version、step、checksum、confirmation だけを受け付け、SQL 本文は受け付けない。
4. 失敗した場合は ledger の完了 step を保持し、原因を解消して失敗 step から再開する。
5. `データ権限` tab で実 table/view/materialized view、許可列、必要な row scope を role ごとに設定し、SQL preview と checksum を確認してから `ADMIN_EXECUTE` で Data Grant を適用する。適用は現在保存済みの role policy への同期であり、新規作成・更新・削除・全削除を反映する。

Oracle DDL は暗黙 commit を含むため、V001 全体を一括 rollback したようには表示しない。既存の無関係な
END USER、DATA ROLE、context、Data Grant は DROP/上書きしない。

## Data Grant ポリシーの判定

`データ権限` は fake/probe table を作成せず、既存の Oracle table/view/materialized view に対する role-based policy を
`NL2SQL_APP_DATA_ENTITLEMENTS` に保存する。新規 app table は作成しない。UI は対象 object と column を
既存 schema catalog / live metadata から選ばせ、任意 SQL や raw predicate は受け付けない。

Data Grant SQL は backend が固定生成する。`NL2SQL_DEEPSEC_CTX_PKG.SET_APP_USER_UUID` は現在の
内部 application user UUID を検証し、DDS policy evaluator から参照できる `CLIENT_IDENTIFIER` へ設定する。
同時に、ユーザー管理で登録したログインユーザーIDを `NL2SQL_APP_USER_CTX.LOGIN_USER_ID` へ設定する。predicate は
`ORA_END_USER_CONTEXT.CLIENT_IDENTIFIER` で現在の内部 application user UUID を取得し、
`NL2SQL_APP_USER_ROLES` / `NL2SQL_APP_ROLES` /
`NL2SQL_APP_DATA_ENTITLEMENTS` から、その user に割り当てられた active role の policy を解決する。
権限設定そのものは user id 単位ではなく `ROLE_ID` 単位であり、複数 role の policy は加法的に合成される。
行 scope で値ソース「ログインユーザーID」を選んだ場合は、
`SYS_CONTEXT('NL2SQL_APP_USER_CTX', 'LOGIN_USER_ID')` を業務列と比較する。

V1 の capability は SELECT Data Grant のみを対象にする。行 scope の UI は `ALL` と structured filter
(`FILTERS`) で指定する。旧 UI の文字列系 column 値一致 (`COLUMN_EQUALS`) は互換入力として backend に
残すが、画面では `FILTERS` の `EQ + 固定値` 条件へ統合する。`FILTERS` は UI/API から列・operator・
値ソース・値を JSON として受け付け、backend が AND predicate へ固定生成する。`EQ` の文字列列と
NUMBER 列では、値ソースとして固定値またはログインユーザーIDを選べる。ログインユーザーIDは
`ORA_END_USER_CONTEXT.CLIENT_IDENTIFIER` を使い、現在の application user id と対象列を比較する。
NUMBER 列の `EQ` 値は正整数のみ許可し、ログインユーザーIDが正整数へ変換できない場合は
一致なしになる。対応列型は文字列、数値、日付/時刻の主要型と NULL 判定に限定し、任意 SQL や raw predicate
は受け付けない。列 scope は選択した column list から生成する。適用時は対象 object に
`SET USE DATA GRANTS ONLY ... ENABLED` を設定するため、対象 object は管理対象であることを UI と preview
で明示する。この enforcement は Deep Sec users に対する強制であり、通常 DB user / DB role への汎用
VPD ではない。

`Data Grant を適用` は UI の現在 draft をアプリ DB へ保存してから Oracle 側を同期する。backend は
`NL2SQL_DG_%` prefix かつ `NL2SQL_APP_DATA_ROLE` grantee の managed Data Grant を Oracle metadata から
照合し、アプリ DB の現在 policy に存在しない stale grant は DROP する。stale grant の対象 object に
現在 policy が 1 件も残らない場合は、DROP 前に `SET USE DATA GRANTS ONLY ... DISABLED` を実行する。

Data Grant の grantee は標準 DB role ではなく DeepSec の DATA ROLE / END USER を使う。本システムでは
共有 connection pool END USER の `DEEPSEC_DATA_USER` が direct logon し、Data Grant の grantee は
`NL2SQL_APP_DATA_ROLE` とする。V001 で `NL2SQL_APP_DB_ROLE` を `NL2SQL_APP_DATA_ROLE` へ付与し、
さらに `DEEPSEC_DATA_USER` へ `GRANT DATA ROLE NL2SQL_APP_DATA_ROLE` を行うことで、共有 END USER の
direct logon と対象 object 参照に必要な DB role を有効化する。生成 predicate は Oracle 仕様に合わせて
4000 文字以内で検証し、超過時は SQL 実行前に validation error とする。

`Data Grant を検証` は fake row count ではなく、適用済み role policy について metadata を照合する。
具体的には foundation object、保存済み Data Grant 名、対象 owner/object、grantee、Data Grants Only の状態、
対象 object 上の enabled VPD/RLS policy を確認し、不一致は運用エラーとして表示する。DeepSec が正しく
許可していても、旧 VPD/RLS policy が同じ object に残っていると Oracle は追加で行を絞り込むため、
`SQL_ASSIST_VPD_%` のような legacy policy は無効化または削除してから DeepSec を適用する。

`SYSTEM_ADMIN` は application feature permission では将来権限を含む wildcard だが、data entitlement では
wildcard ではない。実データへのアクセス範囲は、他の role と同じく `データ権限` workflow で明示的に
設定・適用する。

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

NL2SQL job の生成済み SELECT 実行、Select AI `DBMS_CLOUD_AI.GENERATE`、Select AI Agent の
`DBMS_CLOUD_AI_AGENT.RUN_TEAM` / `RUN_TOOL` / `CREATE_CONVERSATION` も非 `system_admin` では
DeepSec context 付き data connection を使用する。`system_admin`、migration、DeepSec V001 適用、
schema refresh、profile/credential/asset 管理は同じ Thin + mTLS の通常 Oracle user 接続で実行し、
DeepSec context は付けない。

## 主要な安全境界

- session token は 256-bit random value、DB には SHA-256 digest だけを保存する。
- session cookie は HttpOnly、CSRF は cookie/header/server digest の三者一致を要求する。
- password は Argon2id で保存し、平文 password、hash、session token、Oracle secret を応答・監査へ出さない。
- Data pool connection は貸出時に application user UUID を context へ設定し、返却前に必ず clear する。
- context 設定・clear に失敗した connection は再利用せず、data operation は control pool へ fallback しない。
- background job は actor UUID を保存し、worker 実行時に user が active であることを再確認する。
