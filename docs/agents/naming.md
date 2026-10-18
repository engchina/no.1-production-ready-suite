# 設定（`.env`）とデータベース object の命名

> root の [AGENTS.md](../../AGENTS.md) から移した詳細（#2276）。root の規則と同じ強さで守る。規則を変えるときはこのファイルを直し、root には規則の要約と案内だけを書く。
> 設定の変数名の規則と共通の設定の足し方は [backend-standard.md](../../platform/docs/backend-standard.md)「設定（`.env`）の変数名」。

- **3 製品共通の設定は `platform/.env`（雛形 `platform/.env.example`）1 つで、変数名は `PLATFORM_` で始める。** システム設定画面の保存先・共通認証の設定もここで、`model-settings.json` も共有する。3 製品が同じファイルを読み、場所は `PLATFORM_ENV_FILE` で変えられる。
- **製品の `backend/.env` には、その製品だけが使う変数を `RAG_` / `NL2SQL_` / `AGENT_` で置く**（共通の設定を重複させない）。変数名は Settings の属性名から決まる（規則と、共通の設定の足し方は backend-standard.md「設定（`.env`）の変数名」）。
- **表・view・sequence・package・scheduler の job / program・Credential は、共通の仕組みが `PLATFORM_`、製品固有が `RAG_` / `NL2SQL_` / `AGENT_` で始める**（例: NL2SQL のロールの権限・Data Grant は `NL2SQL_`、Select AI の Credential は `NL2SQL_OCI_CRED`）。index・制約は種別を先に付けた形（`PK_` / `FK_` / `CK_` / `UX_` / `IX_` ＋接頭辞。例 `IX_NL2SQL_…`）でもよい（#1712）。
- Oracle が名前を決める object（名前の無い制約の `SYS_C…`・LOB の `SYS_IL…$$` / `SYS_LOB…$$`・identity 列の `ISEQ$$_…`・Oracle Text の `DR$…`・vector 索引の `VECTOR$…`）は、接頭辞の規則の外とする（名前を付けて作る object は規則に従う）。
- 名前を変えるときは旧名との互換を持たない（旧名の環境変数は読まず、テーブルは migration で改名する）。既存環境の更新手順を配備ドキュメントに書く。
- 3 製品は同じ schema を共有するため、4 つの接頭辞で始まる object と、名前に `$` / `#` を含む object（Oracle の内部の object）を業務データとして扱わない。業務の object の一覧・件数（例: NL2SQL の業務プロファイルの対象・管理 SQL）では、SQL の条件で除外する（#1710）。
- ロールの割り当て・復元では製品をまたぐ権限昇格を防ぐ（`pr_system_settings.auth` の `PRODUCT_ROLE_PERMISSION_TABLES`）。製品が権限テーブルを足すときは、この登録と `PLATFORM_ROLES` への `ON DELETE CASCADE` の FK を用意する。
