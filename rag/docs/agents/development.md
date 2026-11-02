# RAG の技術スタック・配備・テスト

> rag/AGENTS.md の詳細。rag/AGENTS.md の規則と同じ強さで守る。AI / DB の分担は root の AGENTS.md「共通の技術方針」が正本。

## 技術スタック（RAG 固有の分）

| 用途 | 採用 | 制約 |
|---|---|---|
| 回答生成・構造化抽出・クエリ計画 | OCI Enterprise AI | モデル設定は環境変数 / 設定 API 経由 |
| 埋め込み / リランク | OCI Generative AI（Cohere Embed v4 / Rerank v4 fast） | 埋め込みの次元数を Oracle AI Vector Search とそろえる |
| 画像・文書理解 | Enterprise AI Vision（OCI Document Understanding は準備中〔ロードマップ〕。#3374） | OCR・ページ解析・VLM 入力 |

- データ: Oracle Autonomous AI Database / Oracle AI Vector Search（チャンク・引用・文書構造・評価結果・監査ログ・ベクトル検索の正本）と OCI Object Storage（原本・変換済み artifact・レビュー済み文書・評価 artifact）。
- SDK は `oci` / `python-oracledb`。通信は REST + SSE。
- 観測は Langfuse + Prometheus + OpenTelemetry。品質は pytest / pytest-cov / ruff / mypy / bandit / pip-audit / Vitest / Playwright。

## 配備とサービス管理画面

- backend・取込 worker・前処理・parser はサービスごとの uv の venv（`uv sync --locked --no-dev --python 3.12`）と systemd の unit で動かす。unit の定義は `scripts/rag-systemd.sh`（本番の `init_script.sh` と開発の `scripts/rag-services.sh` が共通で使う）。Compute には root の [docs/agents/deploy.md](../../../docs/agents/deploy.md) のとおり他の製品と同じ 1 台に入る（CPU parser のみ）。Dockerfile・compose は作り直さない。開発の起動は backend が `uv run uvicorn`、前処理 / parser が `scripts/rag-services.sh`、frontend が `npm run dev`（docs/deployment.md「ローカル開発」）。
- **サービス管理画面**は systemd の unit を操作する（起動 = `enable --now`、停止 = `disable --now` で最後に操作した状態を保つ）。backend が実行してよいのは、sudoers で許可した allowlist の unit の `systemctl` / `journalctl` だけ（argv 固定・shell を通さない。`app/services/systemd.py`）。前処理 / parser を足すときは、catalog・`scripts/rag-systemd.sh` の `RAG_MICROSERVICES`・URL 設定の既定値（`127.0.0.1:<port>`）をそろえる（テストで照合する）。

## 実 Oracle のテストと破壊的な migration

- **実 Oracle のテスト（`oracle_db` の fixture）は、共有の開発 DB ではなくテスト専用の schema（DB ユーザー）で流すことを推奨する（#619）。** fixture はテストの開始時に未適用の migration を当て、テストが作った行を消すため、共有の DB では並行作業のチェックアウトの migration が手元のデータに当たる。接続先は共通 `.env` の `PLATFORM_ORACLE_*` を読むので、テスト専用のユーザーを書いた別のファイルを `PLATFORM_ENV_FILE=<ファイル> uv run pytest` で渡す。データを削除する migration（テーブルの DROP・行の DELETE）が未適用なら、fixture は何も当てずに実 Oracle のテストを skip し、理由を出す（適用は書き出しの後に `system_schema_cli initialize --allow-destructive` で行う。docs/deployment.md「既存環境の更新手順の共通の注意」）。
- データを削除する migration を足すときは、`oracle_schema.py` の `OracleSchemaSection` に `destructive_note`（削除されるデータと、前にする書き出し）を書く。付け忘れは `tests/test_system_schema_manager.py` が SQL から検出する。

## 「DocRAG」の置き換え

RAG の標準の用語・名前で呼ぶ（回答フロー・親子階層（small-to-big）・回答の記録・回答生成のプロンプト・質問の拡張・回答の生成方式（CRAG / 標準 RAG）。package は `rag_engine`、回答は `app/rag/answer_engine.py`、分割方式は `small_to_big`、回答の設定は `RAG_QUERY_STRATEGY` など）。旧名は migration（保存値・表の改名）と docs/deployment.md の更新手順にだけ残す。
