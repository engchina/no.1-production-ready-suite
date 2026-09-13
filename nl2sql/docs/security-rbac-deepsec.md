# アプリケーション認証・RBAC・Deep Data Security 運用ガイド

## 適用範囲

本機能は OCI IAM を使用せず、Oracle に永続化した local application user と role で認証・認可する。
ただし `backend/.env` の `APP_ADMIN_LOGIN_USER_ID=system_admin` / `APP_ADMIN_LOGIN_USER_PASSWORD` に一致する構成管理者は、
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
Ontology query-session 系も同じ境界に従う。`/api/nl2sql/query-sessions`、`generate-sql`、
`confirm-sql` は `nl2sql.query.generate` を要求し、実 DB へ SELECT を発行する
`/api/nl2sql/query-sessions/{session_id}/execute` は `POST /api/nl2sql/execute` と同じく
`nl2sql.sql.execute` を要求する。
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

`APP_ADMIN_LOGIN_USER_ID=system_admin` / `APP_ADMIN_LOGIN_USER_PASSWORD` を `backend/.env` に設定すると、その構成管理者で
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
password 変更画面から変更でき、変更結果は `backend/.env` の `APP_ADMIN_LOGIN_USER_PASSWORD` に書き戻される。
旧キー `APP_ADMIN_PASSWORD` だけを持つ既存 `.env` は読み取り時に fallback として受理されるが、
password 変更を行うと旧キー行は除去され `APP_ADMIN_LOGIN_USER_PASSWORD` へ移行する。
通常 user の password はこれまでどおり DB hash として保存される。ユーザー管理 API/UI は
`system_admin` の大小文字違いを含む DB user 作成を拒否する。

`SYSTEM_ADMIN` role は構成管理者と旧 bootstrap user 専用とする。ユーザー管理 API/UI は、後続で
作成したユーザーへの新規付与・再付与を拒否する。旧版や手動操作で非 bootstrap user に
`SYSTEM_ADMIN` が残っている場合も migration では自動撤去せず、管理者が必要に応じて手動で解除する。

## ユーザー・ロールの物理削除

削除 API は現在表示中の version を `If-Match: "<version>"` で受け取り、前提条件を同一
トランザクション内で再確認する。ユーザーは無効化済みで、ログイン中の操作者自身でも初期
システム管理者でもない場合に限り、セッション、ロール割り当て、ユーザー本体を物理削除する。
履歴や実行ジョブに保存済みの `actor_user_uuid` は監査識別子として保持する。

カスタムロールは、割り当てユーザーがなく、アーカイブ済みで、保存済み Data Grant policy が空の
場合に限り物理削除する。Data Grant が残る場合は、ロールを復元し、Deep Data Security の
`データ権限` で空の Data Grant をプレビュー・適用して Oracle 側の managed grant を清掃した後、
再度アーカイブして削除する。ロール削除 API 自体は Oracle DDL を実行せず、機能権限と業務
プロファイル関連を削除してからロール本体を削除する。組み込みロールは削除できない。

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
次の適用・検証・data-plane query から使用される。`DATA USER 認証` の保存 / `Oracle へ同期` は
`DEEPSEC_DATA_USER` が未作成なら `CREATE END USER IF NOT EXISTS ...`、作成済みなら
`ALTER END USER IF EXISTS ... IDENTIFIED BY ...` で DB 側の password / account unlock / schema association
も同期する。同期に失敗した場合、backend `.env` と runtime 設定は保存前へ戻す。

DeepSec V001 と `DATA USER 認証` の同期を実行する `ORACLE_USER` には少なくとも `CREATE ROLE`、
`CREATE END USER`、`ALTER END USER`、`CREATE DATA ROLE`、`GRANT ANY ROLE` / Data Role grant、`CREATE CONTEXT`、
`CREATE PROCEDURE`、対象 object への `GRANT SELECT`、および DeepSec metadata view 参照権限が必要。
`ORA-01017` が通常ユーザーの SELECT 実行時だけ発生する場合は、application RBAC ではなく
DATA USER password の漂移を疑い、Deep Data Security 画面で password を保存し直すか、
`Oracle へ同期` を再実行する。

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
2. `DATA USER 認証` tab で DATA USER password を保存し、必要に応じて `Oracle へ同期` で DB 側へ反映する。
3. V001 の SQL preview と SHA-256 checksum を確認する。V001.1 は END USER 作成・password 変更を含まない。
4. 各 step は `ADMIN_EXECUTE` 実行確認語を入力して順番に適用する。API は version、step、checksum、confirmation だけを
   受け付け、SQL 本文は受け付けない。
5. 失敗した場合は ledger の完了 step を保持し、原因を解消して失敗 step から再開する。
6. `データ権限` tab で実 table/view/materialized view、許可列、必要な row scope を role ごとに設定し、SQL preview と checksum を確認してから `ADMIN_EXECUTE` で Data Grant を適用する。適用は現在保存済みの role policy への同期であり、新規作成・更新・削除・全削除を反映する。

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

capability は SELECT Data Grant のみを対象にする。行 scope の UI は `ALL` と条件ツリー
(`EXPRESSION`) で指定する。従来の structured filter (`FILTERS`) は互換読み取りする。旧 UI の文字列系 column 値一致 (`COLUMN_EQUALS`) は互換入力として backend に
残すが、画面では AND グループ内の `EQ + 固定値` 条件へ統合する。`FILTERS` は UI/API から列・operator・
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

## 対象識別子の引用規則（Issue #560）

DeepSec のデータ権限は、対象の owner / object と列（`column_names`、行条件の列、関連キー、関連テーブル）を
`app/features/nl2sql/object_identity.py` の `canonical_object_part` と同じ規則の **canonical token** で
検証・保存・照合する。frontend の `formatDbObjectName` / `formatEntitlementTargetName` も同じ規則である。

| 入力 | 保存値（token） | 意味 |
|---|---|---|
| `orders` / `ORDERS` / `"ORDERS"` | `ORDERS` | 引用が不要な名前。従来の保存値と同じ |
| `"Mixed_Case"` | `"Mixed_Case"` | 大文字小文字を保持する引用名。`MIXED_CASE` とは別の object |
| `Mixed_Case`（引用なし） | `MIXED_CASE` | Oracle と同じく大文字として解釈する |
| `DEPT@REMOTE`、`my table`（引用なし） | 拒否 | 非引用識別子にならない名前は推測で引用しない |

- `resource_code` は `target_owner` と `target_object` の token を `.` で単純連結した値に揃える
  （例: `SALES."Mixed_Case"`）。引用が不要な名前では従来の大文字の `OWNER.OBJECT` と同じになる。
- 二重引用符・NUL・制御文字を含む名前、前後に空白がある引用名、引用符を含めて 128 バイトを超える token は拒否する。
  `TARGET_OWNER` / `TARGET_OBJECT` / `SCOPE_COLUMN` 列が `VARCHAR2(128)` のため、引用が必要な名前は
  126 バイトまでとなる。
- Data Grant / GRANT / `SET USE DATA GRANTS ONLY` の SQL には token をそのまま識別子として埋め込む
  （token は内部に `"` を含まない）。PL/SQL の文字列リテラル内（`ALL_OBJECTS` の照合、`EXECUTE IMMEDIATE`）
  では `'` を `''` に escape する。辞書ビュー（`ALL_OBJECTS` / `ALL_TAB_COLUMNS` / `DBA_DATA_GRANTS` /
  `DBA_POLICIES` / `ALL_CONSTRAINTS` / `ALL_DEPENDENCIES`）の照合は、token から引用符を外したカタログ上の名前を
  bind 変数で渡す。
- `GET /api/security/deepsec/target-objects` と詳細 API は、`owner` / `name` をカタログ上の値のまま
  （大文字化せず）返し、`qualified_name` を canonical な修飾名で返す。詳細 API の path は token で指定し
  （`/target-objects/SALES/%22Mixed_Case%22`）、`columns[].column_name` は token で返す。
- 関連条件の候補（外部キー、Ontology edge、既存 Data Grant predicate 内の表参照）も同じ規則で修飾名にする。
  predicate 内の引用されていない表名は大文字、引用された表名は大文字小文字を保つ。
  関連テーブル条件の候補範囲になる業務プロファイルの対象表も、#561 から同じ規則で保存する（次節）。

### 既存データの互換と移行

引用が不要な名前の保存値・`resource_code`・Data Grant 名（`NL2SQL_DG_...` の hash 入力）・checksum は変わらないため、
自動移行はしない。

#560 より前は、対象一覧が owner / object を大文字化して返し、入力チェックも引用符を外して大文字化していた。
そのため `"Mixed_Case"` を選ぶと、同じ owner に大文字の `MIXED_CASE` がある場合だけ詳細取得・適用が成功し、
**大文字の同名表（または同名列）へ権限が付与されていた可能性がある**（同名表が無い場合は適用時に拒否され、誤付与は起きない）。
保存値から利用者の意図は判別できず、別の object へ自動で付け替えると公開範囲が変わるため、自動移行はしない。
次の SQL（Data Grant owner で実行する参考例。実 Oracle では未検証）で大文字小文字だけが異なる object / 列が並存する
ルールを洗い出し、該当ルールは画面で対象を選び直して適用し直すこと。

```sql
-- 保存済みの対象と、大文字小文字だけが異なる object が並存するルール
SELECT e.ROLE_ID, e.ENTITLEMENT_ID, e.TARGET_OWNER, e.TARGET_OBJECT,
       o.OWNER AS CATALOG_OWNER, o.OBJECT_NAME AS CATALOG_OBJECT, o.OBJECT_TYPE
  FROM NL2SQL_APP_DATA_ENTITLEMENTS e
  JOIN ALL_OBJECTS o
    ON UPPER(o.OWNER) = e.TARGET_OWNER
   AND UPPER(o.OBJECT_NAME) = e.TARGET_OBJECT
 WHERE e.TARGET_OBJECT NOT LIKE '"%'
   AND (o.OWNER <> e.TARGET_OWNER OR o.OBJECT_NAME <> e.TARGET_OBJECT)
   AND o.OBJECT_TYPE IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW');

-- 保存済みの許可列と、大文字小文字だけが異なる列が並存するルール
SELECT e.ROLE_ID, e.ENTITLEMENT_ID, e.TARGET_OWNER, e.TARGET_OBJECT,
       j.COLUMN_NAME, c.COLUMN_NAME AS CATALOG_COLUMN
  FROM NL2SQL_APP_DATA_ENTITLEMENTS e
 CROSS APPLY JSON_TABLE(e.COLUMN_NAMES, '$[*]' COLUMNS (COLUMN_NAME VARCHAR2(130) PATH '$')) j
  JOIN ALL_TAB_COLUMNS c
    ON c.OWNER = e.TARGET_OWNER
   AND c.TABLE_NAME = e.TARGET_OBJECT
   AND UPPER(c.COLUMN_NAME) = j.COLUMN_NAME
   AND c.COLUMN_NAME <> j.COLUMN_NAME;
```

### 業務プロファイルの対象表（Issue #561）

業務プロファイルの対象表・ビュー（`allowed_tables` / `allowed_views`）も、保存・照合を上表と同じ規則にそろえた。
`app/features/nl2sql/service.py` の `_resolve_profile_object_name` は、`OWNER.OBJECT` を
`canonical_qualified_name`、owner を省略した名前を `canonical_object_part` で正規化する。

| 経路 | #561 以降の挙動 |
|---|---|
| 保存（`POST` / `PATCH /api/nl2sql/profiles`） | `SALES."Mixed_Case"` はそのまま保存し、`SALES.MIXED_CASE` とは別の対象として両方を保持できる。`"SALES"."MIXED_CASE"` / `sales.mixed_case` は従来どおり `SALES.MIXED_CASE`。非引用識別子にならない名前（`SALES.my table`）と引用符の壊れた名前は `ValueError`（API はエラー応答）で拒否する |
| owner 省略の旧形式 | `"Mixed_Case"` はカタログの引用名の表に解決する。引用なしの `mixed_case` は `MIXED_CASE` として解決し、引用名の表 `Mixed_Case` には解決しない |
| SQL 安全性検査（許可表の照合） | SQL の表参照は sqlglot の引用情報（`SqlTableReference.owner_quoted` / `name_quoted`）を使い、引用なしを大文字、引用ありを書かれたとおりに解釈する。`SALES.MIXED_CASE` だけを許可したとき `SELECT * FROM SALES."Mixed_Case"` を拒否し、逆も拒否する（修正前はどちらも `SALES.MIXED_CASE` として許可していた） |
| SQL 生成の schema context | Profile の対象から schema catalog の詳細を読むとき、大文字小文字だけが異なる表の定義は使わない（#563 で schema catalog の詳細取得自体が大文字小文字を区別するようになった） |
| Select AI の `object_list` | #564 で実 Oracle の挙動を確認し、引用が必要な owner / 表名は `{"owner": "SALES", "name": "\"Mixed_Case\""}` の token で反映する（下記「確認結果」）。#561 の時点では確認前のため `PROFILE_OBJECT_LIST_UNSUPPORTED` で同期を止めていたが、#564 で解除した。引用が不要な名前の `object_list` は従来と同じ |
| オントロジー | 質問補完の代表値も引用規則どおりに Profile の対象へ絞る。node の識別と Profile の view は #563 で引用名に対応した（次節）。AI 構築は引き続き対象外 |
| DeepSec の関連テーブル候補（`GET /api/security/deepsec/relations`） | Profile の object が canonical な修飾名になるため、引用名の表を対象・関連テーブルとして選べる |
| 画面（業務プロファイル・スキーマ参照） | 対象の選択・件数・一括選択・未保存判定のキーを `normalizeDbObjectKey`（`dbObjectIdentity.ts`）にそろえ、引用名を大文字化しない |

#### 確認結果: Select AI の `object_list`・合成データ生成と引用名（Issue #564）

Oracle のドキュメント（`DBMS_CLOUD_AI` の profile 属性 `object_list`）は `{"owner": "SH", "name": "customers"}` の
形式だけを示し、大文字小文字や二重引用符の扱いを規定していない。そこで実 Oracle で次を確認した。

- 環境: Autonomous Database（Oracle AI Database 26ai Enterprise Edition Release 23.26.3.3.0）、2026-09-14 実施。
- 同じ owner に `ZZ_CLAUDE_QV_<時刻>`（列 `ID` / `UPPER_ONLY_COL`）と `"Zz_Claude_Qv_<時刻>"`（列 `"Id"` /
  `"Quoted_Only_Col"`）を作り、`object_list` の表記ごとに Select AI profile を作成して `showprompt` / `showsql` と
  `GENERATE_SYNTHETIC_DATA`（1 行）を実行した。検証用の表・profile は確認後にすべて削除した。

| 渡した表記 | `USER_CLOUD_AI_PROFILE_ATTRIBUTES` の再読込 | `showprompt` の表定義 / `showsql` の FROM | 合成データの生成先 |
|---|---|---|---|
| `{"owner": "ADMIN", "name": "Zz_Claude_Qv_…"}`（引用なし・大文字小文字混在） | 渡したとおり | **大文字の表** `"ADMIN"."ZZ_CLAUDE_QV_…"` | `object_name` / `object_list` とも **大文字の表** |
| `{"owner": "ADMIN", "name": "\"Zz_Claude_Qv_…\""}`（引用あり） | 渡したとおり（引用符付き） | 引用名の表 `"ADMIN"."Zz_Claude_Qv_…"` | `object_name` / `object_list` とも引用名の表 |
| `{"owner": "\"ADMIN\"", "name": "\"Zz_Claude_Qv_…\""}` | 渡したとおり | 引用名の表 | — |
| `{"owner": "ADMIN", "name": "ZZ_CLAUDE_QV_…"}` / `{"owner": "admin", "name": "zz_claude_qv_…"}` | 渡したとおり | 大文字の表 | — |

`DBMS_CLOUD_AI` は `object_list` の owner / name と `GENERATE_SYNTHETIC_DATA` の `owner_name` / `object_name` を
**SQL 識別子として解釈する**（引用なしは大文字化、`"..."` は大文字小文字を保持）。そのため:

- `select_ai_object_list_entry`（`oracle_adapter.py`）は、カタログ上の名前を `format_object_part` の token
  （引用が必要な部分だけ `"..."`）で渡す。引用が不要な名前は従来と同じ値になる。
  Select AI / Select AI Agent の Profile 同期は引用名の表でも通常どおり行い、`PROFILE_OBJECT_LIST_UNSUPPORTED` は廃止した。
- `generate_synthetic_data` も owner / 表名を同じ token で渡す。修正前はカタログ上の名前（`Mixed_Case`）をそのまま
  渡していたため、**引用名の表を対象にすると大文字の同名表に行を生成する**経路があった（現行の受付は必ずプレビュー経由で
  `NL2SQL_SP_…` の一時表を生成対象にするため該当しない。`preview=false` で受け付けた旧履歴の実行と
  `Nl2SqlService.generate_synthetic_data` の直接呼び出しが該当する）。
- 再読込の照合（`_select_ai_object_scope_set`）は、Oracle が返す表記を同じ規則（`parse_object_identity`）で解釈する。

#### 既存 Profile の互換と誤保存の検出

引用が不要な名前の保存値・Select AI の `object_list`・SQL は変わらないため、自動移行はしない。
#561 より前は、画面の選択と保存の両方で引用符を外して大文字化していたため、引用名の表を選ぶと
**大文字の同名表 `SALES.MIXED_CASE` として保存されていた**（同名表が無い場合も `SALES.MIXED_CASE` として保存され、
Oracle 反映時に object が見つからない）。保存値から利用者の意図は判別できず、別の表へ自動で付け替えると
Profile の公開範囲が変わるため、次の SQL（アプリの管理 schema で実行する参考例。実 Oracle では未検証）で
大文字小文字だけが異なる表・ビューを持つ Profile を洗い出し、画面で対象を選び直して保存し直すこと。

```sql
-- Profile の対象（引用なしの OWNER.OBJECT）と、小文字を含む引用名の object の大文字化が一致する Profile
SELECT p.PROFILE_ID, p.NAME, j.OBJECT_KIND, j.OBJECT_KEY,
       o.OWNER AS CATALOG_OWNER, o.OBJECT_NAME AS CATALOG_OBJECT, o.OBJECT_TYPE
  FROM NL2SQL_PROFILES p
 CROSS APPLY (
         SELECT 'TABLE' AS OBJECT_KIND, t.OBJECT_KEY
           FROM JSON_TABLE(p.PAYLOAD_JSON, '$.allowed_tables[*]'
                           COLUMNS (OBJECT_KEY VARCHAR2(512) PATH '$')) t
         UNION ALL
         SELECT 'VIEW', v.OBJECT_KEY
           FROM JSON_TABLE(p.PAYLOAD_JSON, '$.allowed_views[*]'
                           COLUMNS (OBJECT_KEY VARCHAR2(512) PATH '$')) v
       ) j
  JOIN ALL_OBJECTS o
    ON o.OWNER || '.' || UPPER(o.OBJECT_NAME) = j.OBJECT_KEY
   AND o.OBJECT_NAME <> UPPER(o.OBJECT_NAME)
 WHERE INSTR(j.OBJECT_KEY, '"') = 0
   AND o.OBJECT_TYPE IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
 ORDER BY p.PROFILE_ID, j.OBJECT_KEY;
```

該当 Profile では、対象に残っている `SALES.MIXED_CASE` が意図した表か確認し、引用名の表を使う場合は
`SALES."Mixed_Case"` を選び直す。DeepSec のデータ権限の検出は前節の SQL を使う。

### schema catalog・オントロジー・列単位の許可チェック（Issue #563）

#561 の時点で残っていた「大文字化した名前を一意キーにする」経路を、同じ引用規則にそろえた。
内部のキーは **辞書ビュー上の名前（大文字小文字を保持、引用符なし）**、API の入力は **引用規則の token**
（引用なしは大文字、`"Mixed_Case"` は大文字小文字を保持）とし、境界で `normalize_object_part` / `format_object_part`
（`object_match_key` / `catalog_match_key`）で変換する。

| 経路 | #563 以降の挙動 |
|---|---|
| schema refresh（`fetch_schema_manifest` / `fetch_catalog` / 増分 merge / 永続化） | manifest・変更 object・merge・`NL2SQL_SCHEMA_OBJECTS` / `COLUMNS` / `CONSTRAINTS` の key を辞書上の名前にした。修正前は `UPPER` した名前を bind していたため、`Mixed_Case` の列定義を取得できず（同名表があればその定義を保存し）、`MIXED_CASE` と 1 行に潰れていた |
| 詳細 `GET /api/schema/objects/{owner}/{object_name}` | path は引用規則で解釈する（`/SALES/%22Mixed_Case%22` は引用名の表、`/sales/mixed_case` は従来どおり `SALES.MIXED_CASE`）。repository と cache は大文字小文字を区別して完全一致する。引用符が壊れた path は 400 |
| 一覧 `GET /api/schema/objects` / `GET /api/nl2sql/db-admin/objects` | `owner` は引用規則で解釈し、辞書上の owner と完全一致させる。`profile_id` の絞り込みは Profile の対象を辞書上の `(owner, name)` の組にして完全一致させる（Oracle は `JSON_TABLE` の `$.owner` / `$.name`）。keyset cursor は辞書上の名前を保持する（大文字化した cursor では同名表を読み飛ばす・重複する） |
| 詳細の代表値（`fetch_metadata_sample_values`） | owner / object / 列を引用規則で解釈し、辞書ビューの照合と `"..."` 引用に辞書上の名前を使う。修正前は引用符を外して大文字化し、大文字の同名表・同名列の値を取得していた |
| オントロジーの node ID | `stable_physical_id` に渡す名前を `physical_identity_part` にした。大文字化しても変わらない名前（`ORDERS`、`売上`）は **既存の ID・technical_name・schema fingerprint を変えない**。小文字を含む名前だけ `"Mixed_Case"` を ID に含め、`technical_name` も `SALES."Mixed_Case"` にする。metadata / 物理参照の owner・object・column は辞書上の名前を保持する |
| Profile の view・物理 scope の絞り込み・draft scope・質問補完の column policy | 引用規則の照合キーで対応付け、引用名の Profile を引用名の node に対応付ける（#561 の「対象にしない」扱いを解除） |
| オントロジーの AI 構築 | schema context と LLM 出力の参照解決（`_ScopeResolver` / `_SchemaContextLookup`）が名前を大文字化して照合するため、小文字を含む引用名の表・列・関係は **引き続き対象外**（警告）。取り違えを避けるため、view に含まれていても AI 構築の context と参照解決から除く |
| 列単位の許可チェック（`allowed_objects.columns`） | 列名は引用規則の token で照合する。SQL の列参照は sqlglot の引用情報（`SqlColumnReference.owner_quoted` / `table_quoted` / `name_quoted`）で解釈する。`"Amount"` だけを許可したとき `SELECT AMOUNT` を拒否し、`AMOUNT` だけを許可したとき `SELECT "Amount"` を拒否する（修正前はどちらも `AMOUNT` として許可していた）。同名表を JOIN したときの表名修飾（`"Mixed_Case"."Amount"`）も引用規則で解決する |
| 画面 | スキーマ参照の列詳細は token で要求する。業務プロファイルのスキーマ別グループは owner を大文字化せず、`"Sales"` と `SALES` を別グループとして件数・一括選択する（表示は SQL と同じ表記）。オントロジーの物理名表示とグラフの cluster も大文字化しない |

#### 既存データの互換

- 引用が不要な名前（辞書上の名前が大文字化しても変わるものがない名前）は、保存値・キー・SQL・API 応答・オントロジーの ID が従来と同じ。schema / migration の変更なし。
- 修正前に保存された schema catalog の行は、小文字を含む名前も大文字化されている（列定義は大文字の同名表のもの、または取得できていない）。**DB 構造の全件再取得**で manifest の key が辞書上の名前に揃い、引用名の表は新しい行として取得される。大文字化された行は、大文字の同名表が無ければ削除され、あればその表の行として比較・更新される（targeted refresh は対象 object だけを更新する）。
- オントロジーは、再取得後の catalog から作る次の revision で引用名の node が別 node になる。修正前の revision で 1 node に潰れていた同名表の業務定義・mapping は自動では付け替えない（どちらの表を意図したか判別できないため、ID が変わらない大文字の表の node に残る）。
- API の入力: 引用なしの小文字（`/api/schema/objects/sales/orders`、`owner=sales`、列 `amount`）は従来どおり大文字として解釈する。辞書上の小文字の名前をそのまま渡していたクライアント（`owner=Sales`）は、`owner="Sales"` と引用して渡す必要がある。

### 画面表示

表名・列名は SQL と同じ表記（引用が必要な部分だけ `"..."`）で表示する。一覧で `SALES.MIXED_CASE` と
`SALES."Mixed_Case"` は別の候補として並び、選択後の見出し・許可列・プレビュー SQL も同じ表記になる。
入力規則に反する識別子の API エラーは「… は有効な Oracle identifier で指定してください。大文字小文字の混在や
記号を含む名前は "Mixed_Case" のように二重引用符で囲みます。」と返す。

## 条件グループと関連テーブル条件（Issue #447）

行条件は `すべて満たす（AND）` / `いずれかを満たす（OR）` のグループで編集する。
各グループが括弧に相当し、文字要約と読み取り専用 SQL preview で確認できる。
根を含む 3 階層、ルール全体で 20 フィールド条件・3 関連カード、関連キーは 8 組を製品上限とする。
関連カード内の条件グループも階層数に含める。空グループ、空値、未完了の関連は保存・preview・apply
できず、最後の条件を削除しても `ALL` に切り替わらない。既存の型別 operator とログインユーザーIDを継続する。

関連カードは同じ DB の Profile、関連テーブル、等値キー、関連レコードの条件を指定する。
確認済み外部キー（enabled / validated）と公開 revision / view の承認済み Ontology edge を候補にし、
管理者による実在列の手動指定も可能。複合キーを保持し、一つのカードは一つの相関 `EXISTS` に変換する。
同一カードの条件は同一の関連レコードで満たす必要があり、別レコードの部分一致を合成しない。
自関連、多段関連、関連カード内の関連、DB link、任意 SQL、NOT グループ、NOT EXISTS、集約・関数編集、
`WHEN … GRANTED ON` による権限継承、書込み権限は対象外。

Profile は設定候補の範囲のみを定める。Data Grant は物理オブジェクトに対して全 Profile で有効となる。
Profile の変更・削除で既存の DB grant を自動削除しない。保存した Profile 範囲 version と関係の ID/version、
実在列・型・参照権限・既存 grant/view の依存を preview/apply 時に再検証する。変更や検証不能はエラーで止め、
別オブジェクトへの置換、利用者権限の自動拡大、長い条件の複数 grant への自動分割は行わない。

Oracle の predicate は最大 4,000 文字で、参照権限は Data Grant owner、循環は query runtime でも確認される。
アプリ側は認可のガードを含む完全な predicate で文字数を検証する。
[Oracle CREATE DATA GRANT](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/create-data-grant.html)
に従い、固定のユーザー・active role・適用済み entitlement の条件の内側に括弧付きユーザー条件を置く。
OR はこのガードを迂回できない。複数 Data Grant の実効権限は和集合なので、追加した「制限ルール」で
既存許可を狭めることはできない。AND が必要な条件は同じルールへまとめる。
[Oracle About Data Grants](https://docs.oracle.com/en/database/oracle/oracle-database/26/ddscg/data-grants.html)

関連条件は関連テーブルへの読み取り権限を自動付与しない。関連テーブル自身の DeepSec/VPD 設定によって
照合できる行が制限される可能性がある。2026-09-11 の隔離 Oracle 実機検証では、関連表を
`USE DATA GRANTS ONLY` で保護すると、関連表の grant が D3 のみの時は本人分（ID=3）のみ、
D1/D3 に変更すると関連条件に一致する ID=1 も返った。これは検証環境で観測した結果であり、
任意の権限構成の最終可視行を継承する機能としては提供しない。

### API・移行・状態保持

`scope_mode: EXPRESSION` / `scope_expression: {version: 1, root: ...}` を entitlement API に追加した。
ノードは `group`、`condition`、`related_exists` の discriminated union で、SQL 断片は受け付けない。
`GET /api/security/deepsec/scope-profiles` と `GET /api/security/deepsec/relations` が設定候補を返す。
対象一覧の `profile_id` は候補を絞る任意パラメーターで、runtime 認可には追加しない。

デプロイ時は `backend` で `uv run python -m app.cli.app_security_migrate --apply` を実行し、
migration `020_deepsec_scope_expression.sql` の nullable `SCOPE_EXPRESSION` CLOB と mode 制約を適用する。
旧 `FILTERS` / `COLUMN_EQUALS` はそのまま読み、編集・保存したルールのみ新形式にする。
既存 entitlement ID / Data Grant 名 / checksum / apply 状態は未変更時に保持する。
複雑ルールへの旧形式上書きは拒否する。新 UI が明示的に全行へ変更する場合のみ
`scope_expression_version: 1` で対応クライアントであることを示す。

草稿は既存 WorkspaceState により同一ユーザー・DB の同一タブ sessionStorage に期限付きで保持する。
確認語・SQL preview は復元せず、ルール変更や画面復帰で実行同意を解除する。
再取得は草稿を上書きせず、保存時に元の role version を用いて競合を検出する。

### 回帰検証

`test_deepsec_scope_expression.py` は括弧の違い、OR の認可ガード、同一関連行、重複、NULL、複合キー、
旧形式互換・所有フィールド・CLOB 契約、範囲/型/権限/循環/長さの拒否を検証する。
`security-rbac.spec.ts` は desktop と mobile-375 で編集・preview・草稿復元・確認解除・空条件・レイアウトを検証する。
実機の opt-in `test_deepsec_expression_only_isolated_objects` は新規隔離テーブルと grant のみを作り、
実 SELECT の結果、保護された関連表、4KB 超 CLOB、runtime `ORA-52561` を確認し、finally で削除する。

```bash
NL2SQL_RUN_DEEPSEC_INTEGRATION=1 \
NL2SQL_DEEPSEC_INTEGRATION_CONFIRM=I_UNDERSTAND_DEEPSEC_DB_MUTATION \
uv run pytest tests/test_deepsec_real_oracle_integration.py -k expression_only
```

70–80% は常用シナリオの設計目標であり、計測済み業務カバレッジではない。

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

Select AI `DBMS_CLOUD_AI.GENERATE` と Select AI Agent の
`DBMS_CLOUD_AI_AGENT.RUN_TEAM` / `RUN_TOOL` / `CREATE_CONVERSATION` は、非
`system_admin` の SQL 生成でも `system_admin` と同じ Thin + mTLS の通常 Oracle user 接続で実行し、
DeepSec context は付けない。NL2SQL job の生成済み SELECT 実行は、非 `system_admin` では DeepSec
context 付き data connection を使用する。`system_admin`、migration、DeepSec V001 適用、schema
refresh、profile/credential/asset 管理は通常 Oracle user 接続で実行し、DeepSec context は付けない。

## 主要な安全境界

- session token は 256-bit random value、DB には SHA-256 digest だけを保存する。
- session cookie は HttpOnly、CSRF は cookie/header/server digest の三者一致を要求する。
- password は Argon2id で保存し、平文 password、hash、session token、Oracle secret を応答・監査へ出さない。
- Data pool connection は貸出時に application user UUID を context へ設定し、返却前に必ず clear する。
- context 設定・clear に失敗した connection は再利用せず、data operation は control pool へ fallback しない。
- background job は actor UUID を保存し、worker 実行時に user が active であることを再確認する。
