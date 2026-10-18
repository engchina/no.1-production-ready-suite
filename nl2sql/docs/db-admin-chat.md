# DB 管理チャット（#2264）

「データ準備」の「管理 SQL を実行」の上の「DB 管理チャット」（`/db-admin-chat`）は、DBA の役割の利用者が自然言語の質問から Oracle の SQL（SELECT・DML・DDL・DCL・PL/SQL のすべて）を作り、確かめてから実行する画面。「チャット」（[sql-chat.md](./sql-chat.md)）は業務プロファイルの範囲の SELECT だけ、「管理 SQL を実行」は SQL を手で書く。DB 管理チャットはその間を埋める。

## 確定した仕様

| 項目 | 内容 |
|---|---|
| 権限 | メニュー権限 `menu.db_admin_chat`（「DB 管理チャット」。含む権限は DB 構造の参照・再取得）。既定はどのロールにも付けない（system_admin だけが使える）。ほかのロールへの付与は「自分の持つ権限だけを付与できる」規則（[../AGENTS.md](../AGENTS.md)「role の権限昇格の防止」）で越権を防ぐ。「管理 SQL を実行」の権限（`menu.admin_sql`）では代わりにしない |
| 生成 | OCI Enterprise AI Direct だけ（システム設定 › モデルの既定のテキストモデル）。業務プロファイルで限らず DB 全体が対象 |
| 生成の文脈 | DB 構造の cache から質問（と画面の中の前の質問）に関係する表を最大 12 件選び、列（1 表 40 列まで）を渡す。ほかの表は名前だけ（80 件まで）。Oracle のシステムの schema と製品の表は cache の段階で外れている。DB の管理の質問（版・表領域・セッション・ロック・権限・無効な object など）は、LLM への指示（`backend/app/features/nl2sql/prompts/db_admin_chat/system.txt`）にデータ辞書と動的性能ビュー（`V$VERSION`・`PRODUCT_COMPONENT_VERSION`・`DBA_TABLESPACE_USAGE_METRICS`・`DBA_DATA_FILES`・`DBA_FREE_SPACE`・`V$SESSION`・`V$LOCKED_OBJECT`・`DBA_OBJECTS`・`DBA_ROLE_PRIVS` など）の使い方を書いて答えられるようにする。Autonomous AI Database で見えないビュー（`ORA-00942` / `ORA-01031`）は、エラーの分析で `USER_` / `ALL_` ビューの代わりを示す |
| 接続 | system_admin と同じく、アプリ用の DB ユーザーのネイティブの接続だけ（SELECT も `OracleNl2SqlAdapter.execute_select(admin_control_plane=True)` で `connection()` を使い、DeepSec の DATA USER の pool・業務の実行の pool を使わない） |
| 実行 | 生成した SQL を必ず表示し、自動では実行しない。1 つの読み取り専用の SELECT / WITH…SELECT は「実行」で実行する（確認語は要らない）。それ以外は実行確認語 `ADMIN_EXECUTE`（1 つの対象を消す 1 文の `DROP` / `TRUNCATE` は対象の名前。例 `APP.T1`）を入力したときだけ実行する。確認は 1 回ごと（実行したら消える）で、SQL が変わる（AI のエラー分析で直した SQL を使う）と確認からやり直す |
| 分類 | 生成エンジンに関係なく、実行の前にサーバーで必ず分類する（`_split_sql_statements`・`_admin_statement_type`・`is_select_only`）。生成の応答（`plan`）も実行の前に作り直す（画面の値を信じない） |
| 実行の実装 | 「管理 SQL を実行」の `_execute_db_admin_sql` を `chat=True` で使う（複数の文・PL/SQL・トランザクション・暗黙の commit の警告・DB 構造の再取得は同じ）。違いは、DML だけの複数の文も部分の commit の経路（`data_dml`）へ渡さず、**全部成功したときだけ commit** すること |
| 止める文 | 製品の object（`NL2SQL_` / `PLATFORM_` / `RAG_` / `AGENT_` で始まる名前、`$` / `#` を含む名前）と動的 SQL は「管理 SQL を実行」と同じく止める。例外は読み取り専用の SELECT の動的性能ビュー（`V$` / `GV$`。所有者は無指定・SYS・PUBLIC）だけで、セッション・ロック・版の質問に要るので読ませる（`SYS.OBJ$` などほかの `$` の object は止める）。加えて、方針で止める文を技術の規則としてコードに書く（下の表） |
| 失敗 | 「AI でエラーを分析」で原因と直した SQL を示す（`POST /api/nl2sql/db-admin-chat/analyze-error`。OCI Enterprise AI が無いときは ORA のコードからの分析）。直した SQL も分類し、確認語からやり直す |
| 会話 | 保存しない（画面を離れると消える。常時マウントしない）。同じ画面の中の続きの質問は、前の 6 往復の質問・SQL・実行の結果の要約を文脈に送る |
| 監査 | 表 `NL2SQL_ADMIN_AUDIT_LOG`（migration 023）に、実行した人・SQL の全文・文の種類・確認語の有無・結果（success / partial / failure / blocked）・commit / rollback・影響行数・時間・経路（`db_admin_chat` / `admin_sql` / `data_preparation`）を件数の上限なく残す。SELECT の実行と止めた文も残す。確認語の待ちだけの応答は残さない |
| 公開 | 画面だけの API（Cookie / CSRF）。外部の MCP・Agent・API キーからは使えない。四眼原則（別の人の承認）は持たない |

## 方針で止める文

`backend/app/features/nl2sql/db_admin_chat.py` の `FORBIDDEN_STATEMENT_RULES`。文字列リテラル・引用識別子・コメントの中身を空白にした文に当てる（値の中の語では止めない）。**DB 管理チャットだけに当て、「管理 SQL を実行」には当てない**（手で書く管理の経路として、今までどおり `ALTER SYSTEM KILL SESSION` などを ADMIN_EXECUTE で実行できる。LLM が作る SQL にだけ追加の柵を置く）。

| 規則 | 止める文 |
|---|---|
| `ALTER_SYSTEM` | `ALTER SYSTEM` |
| `ALTER_DATABASE` | `ALTER DATABASE` / `ALTER PLUGGABLE DATABASE`（`DATABASE LINK` は除く） |
| `CREATE_DROP_DATABASE` | `CREATE` / `DROP` の `DATABASE` / `PLUGGABLE DATABASE` |
| `SHUTDOWN_STARTUP` | `SHUTDOWN` / `STARTUP` |
| `USER_ACCOUNT` | `CREATE` / `ALTER` / `DROP USER` |
| `DROP_TABLESPACE` | `DROP TABLESPACE` |
| `AUDIT_POLICY` | `NOAUDIT`、`CREATE` / `ALTER` / `DROP AUDIT POLICY` |
| `SESSION_STATE` | `ALTER SESSION`（接続の状態を変える） |
| `TRANSACTION_CONTROL` | `COMMIT` / `ROLLBACK` / `SAVEPOINT` / `SET TRANSACTION`（commit は実行の仕組みが決める） |
| `ADMIN_PRIVILEGE_GRANT` | `DBA`・`PDB_DBA`・`SYSDBA` などの管理者のロール・権限、`ALL PRIVILEGES`・`ANY PRIVILEGE` の付与と取り消し |
| `DYNAMIC_SQL` | `EXECUTE IMMEDIATE`・`DBMS_SQL`・`OPEN … FOR`（文のどこでも） |
| `EXTERNAL_ACCESS_PACKAGE` | `UTL_HTTP`・`UTL_TCP`・`UTL_SMTP`・`UTL_MAIL`・`UTL_FILE`・`UTL_INADDR`・`DBMS_NETWORK_ACL_ADMIN`・`DBMS_CLOUD`・`DBMS_CREDENTIAL`・`DBMS_JAVA`・`DBMS_SCHEDULER`・`DBMS_JOB`・`DBMS_PIPE`・`DBMS_LDAP`・`HTTPURITYPE`（SELECT の中でも止める。外部への送信・ファイル・ジョブ・資格情報） |

## API

| API | 権限 | 内容 |
|---|---|---|
| `POST /api/nl2sql/db-admin-chat/generate` | `menu.db_admin_chat` | `{question, history}` → `{explanation, plan, context_tables, model, warnings}`。SQL は実行しない。モデルが無いと 409、生成の失敗は 502 |
| `POST /api/nl2sql/db-admin-chat/execute` | `menu.db_admin_chat` | `{sql, confirmation, row_limit}` → 管理 SQL を実行と同じ `DbAdminExecuteData`。確認語が合わなければ `confirmation_required` の文を返し実行しない |
| `POST /api/nl2sql/db-admin-chat/analyze-error` | `menu.db_admin_chat` | `{question, sql, result_text}` → `{analysis, corrected_plan}` |

生成と分析は LLM を同期で呼ぶので、Nginx の長い待ち時間の API（`platform/deploy/suite-nginx.sh` の `SUITE_NL2SQL_LONG_API_PATHS`）に入れる。画面の待ち時間は `API_TIMEOUT_MS.longRunningJob`。

## 画面

「チャット」と同じ部品・同じ文言で描く（2 つの画面で書き写さない。`frontend/src/features/nl2sql/components/ChatScaffold.tsx`）。

- 骨格は共通の `ChatLayout`（会話を保存しないので `history` を渡さず、履歴のパネルと開閉ボタンを出さない）、入力欄は共通の `ChatComposer`（Enter で送信・Shift+Enter で改行・生成中は停止）、送った質問は `ChatPendingTurn`（生成の失敗は質問を残して「再送信」）。
- 回答は `ChatTurn` / `ChatAnswer`。処理の経過は共通の `ChatProgress` で、段階の名前はチャットと同じ定義（生成 → SQL の安全性の確認（サーバーの分類）→ 実行）。
- SQL は `ChatSqlBlock`（状態のバッジ・文の種類・コピー・実行・本文）。SELECT の「実行」は SQL の横（チャットと同じ位置）、確認語の要る SQL は警告（変更すること・DDL は暗黙に commit されて元に戻せないこと）と共通の `ExecutionConfirmationField` の横。止める文は理由の `Banner` を出し、実行の操作を出さない。
- SELECT の結果はチャットと同じ `ChatQueryResult`（共通の `ChatResultTable`。要約・プレビュー・すべての行・CSV）。更新系の結果・失敗は「管理 SQL を実行」と同じ `DbAdminExecutionResult`（文ごとの状態・影響行数・commit / rollback）。DDL の後の DB 構造の同期の案内は結果の直下。

## アプリ用の DB ユーザーの権限

データ辞書・動的性能ビューは `PDB_DBA` の範囲で読む（[../../terraform/README.md](../../terraform/README.md)「アプリ用の DB ユーザー」）。開発の ADB（ADMIN の接続）では `V$VERSION`・`PRODUCT_COMPONENT_VERSION`・`DBA_TABLESPACE_USAGE_METRICS`・`DBA_DATA_FILES`・`DBA_FREE_SPACE`・`V$SESSION`・`DBA_OBJECTS`・`DBA_ROLE_PRIVS`・`DBA_USERS`・`V$LOCKED_OBJECT`・`DBA_SEGMENTS`・`V$PARAMETER` が読めた。アプリ用の DB ユーザー（`ADB_APP_USER`）での確認は検証環境で行う（読めないビューは実行の失敗として分析が代わりのビューを示す）。監査の表は製品の migration（023）が作るので、追加の grant は要らない。
