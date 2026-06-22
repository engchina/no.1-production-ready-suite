# no.1-sql-assist Gap Audit

基準: `/u01/workspace/No.1-SQL-Assist`

本表は旧版の主要低層機能を、現在の FastAPI + React 構成へ再マップした吸収状況の正本です。外部 LLM provider、外部 vector DB、旧 Gradio UI は意図的に除外し、LLM/VLM は OCI Enterprise AI、embedding/rerank は OCI GenAI、状態とデータソースは Oracle に集約します。

| 旧版領域 | 旧版参照 | 現在の実装 | UI | 状態 |
|---|---|---|---|---|
| 完整数据库管理 | `utils/management_util.py` の table/view/DDL/comment/drop/general SQL | `/api/nl2sql/db-admin/tables`, `/views`, `/execute`, `/drop-table`, `/import-tabular`, `/tables/{name}/export.xlsx` | Data Tools: Database Admin / Excel 運用 | extended |
| Excel 运维流程 | `samples/*.xlsx`, training/terms/rules import/export | CSV/XLSX import preview/execute、training data XLSX/JSONL export、profile learning material import/export | Data Tools, Learning, Glossary Rules | extended |
| 分类模型训练 | `models/*.joblib`, `*.meta.json`, embedding + LogisticRegression | OCI GenAI 1536 embedding + sklearn LogisticRegression、Oracle state 正本、legacy artifact import、model registry activate/delete | Learning: classifier / model registry | implemented |
| Select AI Profile 低层管理 | `utils/selectai_util.py` profile create/drop/set/list/detail | DB profile list/detail/create/update/drop、attributes JSON import/export、confirmation/audit | Engine Operations: DB profile JSON editor | extended |
| SQL 深度分析/逆生成 | `utils/query_util.py` SQL 分析・逆生成 | `/analyze` deterministic deep fields + optional OCI Enterprise AI structured JSON、`/reverse/deep` fallback付き逆生成 | SQL Analysis | extended |
| DBMS_CLOUD_AI 合成数据 | `DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA` | single object / object_list、profile_name/profile_id、prompt/sample/comment options、operation status/results | Data Tools: DBMS_CLOUD_AI Synthetic Data | extended |
| Select AI Agent 独立操作 | `utils/selectai_agent_util.py` asset/tool/team/conversation | assets list、run-team、run-tool、create conversation、history、privilege check、cleanup | Engine Operations: Agent low-level panel | extended |

## 実行境界

通常の `/api/nl2sql/execute` は従来どおり SELECT/WITH のみです。DB 管理系の DML/DDL/PLSQL/COMMENT、profile drop、comment/annotation apply、synthetic data execute は管理 UI 専用 API に分離し、`execute=true` に加えて `confirmation` が対象名または `ADMIN_EXECUTE` と一致する場合のみ実行します。

## 意図的に除外

- 旧 Gradio UI。
- 外部 LLM provider。
- 外部 vector DB。
- OCI/ADB wallet/start-stop など今回の 7 領域外のインフラ運用。
- `.xls` legacy import。実 fixture 受領後に `xlrd` 導入可否を判断します。

## Blocked-by-env

以下はコード実装済みですが、production live 受入には実環境が必要です。

- OCI GenAI embedding 実 training。
- OCI Enterprise AI direct structured output。
- Oracle DBMS_CLOUD_AI profile create/drop/set/generate。
- Select AI Agent package/tool/team/conversation 実行。
- DBMS_CLOUD_AI synthetic data 実 execute/status/result。
