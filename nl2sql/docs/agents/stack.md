# NL2SQL の技術スタック

> nl2sql/AGENTS.md の詳細。nl2sql/AGENTS.md の規則と同じ強さで守る。AI / DB の分担は root の AGENTS.md「共通の技術方針」が正本。

| 用途 | 採用 | 重要な制約 |
|---|---|---|
| LLM（SQL 生成・構造化出力） | **OCI Enterprise AI** | ⚠️ **OCI Generative AI の chat 推論 API は LLM / VLM に使わない** |
| VLM（オントロジー構築の資料の PDF ページの読み取り） | **OCI Enterprise AI** | 同上 |
| 埋め込み | **OCI Generative AI**（Cohere Embed v4） | オントロジーのノードの埋め込み。多言語・**1536 次元** |
| リランク | **OCI Generative AI**（Cohere Rerank v4 fast） | 3 製品共通のモデル設定。NL2SQL ではモデル設定の接続テストだけが呼ぶ |

- LLM / VLM と embedding / rerank は別クライアント（`app/clients/oci_enterprise_ai.py` / `app/clients/oci_genai.py`）に抽象化する。
- **Oracle AI Database** が業務データと NL2SQL の状態（業務プロファイル・オントロジー・履歴・job 等）の正本。ベクトル列は **`VECTOR(1536, FLOAT32)`**。アップロード保存先は local / OCI Object Storage を共通のシステム設定で選ぶ。
- Backend: SDK は `oci` / `python-oracledb`、SQL の解析は **sqlglot**、オントロジーは **rdflib** / **pyshacl**、サーバは Uvicorn（本番は + Gunicorn）。
- Frontend: Tailwind CSS v4 と共有 UI、関係グラフは `@xyflow/react`、通信は REST（時間のかかる処理は永続 job のポーリング）。
- 観測は構造化ログ + Prometheus（`prometheus-client`）。品質は pytest / pytest-cov / ruff / mypy / bandit / pip-audit / `node --test`（ロジックテスト）/ Playwright。
- 配備は root の [docs/agents/deploy.md](../../../docs/agents/deploy.md)（1 台の Compute。NL2SQL の手順は `init_script.sh`）。
