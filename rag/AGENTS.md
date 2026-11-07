# AGENTS.md — Production Ready RAG

> RAG（`rag/`）固有のルール。共通のルールは root の [../AGENTS.md](../AGENTS.md) を先に適用する。詳細は下の表の文書に書く。
> Issue の label は `product:rag`、PR title の scope は `rag`。

## 作業ごとに読む文書

| 作業 | 読む文書 |
|---|---|
| 文書レシピ・KB・検索・回答プロファイルの設定、取込・検索の工程 | [docs/agents/settings-responsibility.md](./docs/agents/settings-responsibility.md) |
| 画面・ナビ・権限・「AI 活用」のデータの見え方 | [docs/agents/ui.md](./docs/agents/ui.md) |
| コードの置き場所 | [docs/agents/structure.md](./docs/agents/structure.md) |
| 技術スタック・配備・サービス管理画面・実 Oracle のテスト・破壊的な migration・「DocRAG」の置き換え | [docs/agents/development.md](./docs/agents/development.md) |

## プロジェクト概要

文書とナレッジベースを構築し、業務ごとの **Search Answer Profile** から検索・回答する RAG を本番品質で提供する（ingestion・grounding・回答生成・評価・観測・Oracle / OCI への配備）。SQL 専用の自然言語問い合わせは `../nl2sql/` の責務で、`rag/` へ機能・UI・API・設定を混在させない。

## 用語

- ユーザー向けの主概念は **ナレッジ構築** / **検索・回答プロファイル** / **取込・検索・回答設定**。「検索・回答プロファイル」は正式な製品語で、parser / source profile 等の工程の技術語とは区別する（KB membership・文書レシピの責務を移さない）。
- `producer` / `consumer` / `Pipeline` / `Adapter` / `Profile` / `Runtime` / `Backend` などの技術語は、code identifier・コード内部・開発者向けの診断パネルに限る。
- `SearchAnswerProfile` は正式な code / API 名として維持し、ユーザー表示は「検索・回答プロファイル」。
- **「DocRAG」は使わない**（画面・API のメッセージ・docs の地の文・識別子・設定・DB の名前のすべて。旧名は migration と更新手順にだけ残す。[置き換え](./docs/agents/development.md)）。
- 3 層モデル（文書レシピ / KB スコープ / Search Answer Profile）に関わる Issue では、どの層の責務かを明記し、責務越境になっていないかを `修正方針` に書く。

## RAG 設定責務

> **3 層モデル（確定）**: 文書 = 処理レシピを持つ / KB = 純スコープ（コレクション）/ Search Answer Profile → KB。
> KB は「レシピ」と「検索スコープ」を兼任しない。レシピは文書単位、検索・回答設定は Search Answer Profile。

設定・工程に触る前に [docs/agents/settings-responsibility.md](./docs/agents/settings-responsibility.md) を読む。

## 開発コマンド

```bash
# backend
cd backend && uv sync
uv run pytest tests/test_<対象>.py   # 関係するテスト（全件・mypy . ・pip-audit は CI）
uv run ruff format --check . && uv run ruff check . && uv run mypy .
uv run uvicorn app.main:app --reload

# frontend
cd frontend && npm install
npm run lint && npm run build
npx vitest related <変更したファイル>   # または npm run test（全件）
npx playwright test e2e/<対象>.spec.ts  # 関係する spec だけ（smoke・全件は CI と nightly）
npm run dev   # /api は BACKEND_URL を明示したときだけ proxy する（未指定なら 404 の hermetic モード）

# 外部 parser のサービス（services/parsers/<name>。venv は parser ごと）
RAG_SERVICES_MODELS=skip scripts/rag-services.sh sync parser-docling   # torch は lock の CPU 版（#1650）
cd services/parsers/docling && uv run --no-sync --with pytest python -m pytest -q tests
```

CI の job は `RAG / Backend`・`RAG / Frontend`・`RAG / E2E smoke` 等。外部 parser のテストは `RAG / Parser docling` で、その parser と path 依存が変わったときだけ動く（#1512）。

## 解析の出力の版

解析の出力の内容を変える修正（解析サービス・`rag_engine` の解析・`rag_parser_core`）は、同じ PR で `app/rag/ingestion.py` の `EXTRACTION_ARTIFACT_SCHEMA_VERSION` を上げる（上げないと保存済みの段・全体の抽出結果が使い直され、解析からの再処理でも直らない。#4577）。

## テスト

backend は pytest、frontend のロジックは Vitest、UI/UX は Playwright。実 Oracle のテストはテスト専用の schema で流すことを推奨し、データを削除する migration には `destructive_note` を書く（[docs/agents/development.md](./docs/agents/development.md)）。
