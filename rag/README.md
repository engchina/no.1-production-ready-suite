# Production Ready RAG

A production-ready RAG reference implementation covering data ingestion, chunking, indexing, hybrid retrieval, reranking, evaluation, observability, guardrails, and deployment best practices.

特定業務ドメインに固定せず、RAG システムを本番品質で構築するための参照実装として、ドキュメント取込から検索・回答生成・運用品質までを一貫して扱う。

> プロジェクトの開発ルール（技術スタック・規約）は [AGENTS.md](./AGENTS.md) が正本です（Claude Code / Codex 共通）。

## 技術スタック

| レイヤー | 採用 |
|---|---|
| LLM / VLM | **OCI Enterprise AI**（OCI Generative AI の chat API は使わない） |
| 埋め込み / リランク | **OCI Generative AI**（Cohere Embed v4 = 1536次元 / Rerank v4 fast） |
| ベクトル検索 / DB | **Oracle 26ai** AI Vector Search（`VECTOR(1536, FLOAT32)`）+ Oracle Text |
| バックエンド | Python 3.12 + **FastAPI** + Pydantic v2 + uv |
| フロントエンド | **Vite + React Router** + TypeScript + Tailwind v4 + shadcn/ui + TanStack Query + Zustand |
| ストレージ | OCI Object Storage |

UI/UX は日本語第一の業務アプリとして、情報設計・画面構成・状態モデル・タイポグラフィを本リポジトリ内で管理する。実装技術は AGENTS.md の確定スタックを正とする。

## クイックスタート

```bash
# バックエンド
cd backend
uv sync
cp .env.example .env        # OCI / Oracle の接続情報を設定
uv run uvicorn app.main:app --reload    # http://localhost:8000/docs

# フロントエンド（別ターミナル）
cd frontend
npm ci
cp .env.example .env.local
npm run dev                 # http://localhost:3000

# まとめて（Docker）
docker compose up --build
```

詳細は [backend/README.md](./backend/README.md) / [frontend/README.md](./frontend/README.md) を参照。

## 実装済みの参照フロー

- `POST /api/documents/upload`: 原本を Object Storage 境界へ保存し、SHA-256 / サイズ / 重複元を記録してドキュメント行を作成。
- `POST /api/documents/{id}/ingest`: OCI Enterprise AI 境界で OCR/構造化要素抽出し、ページ・章節・表・リスト感知 chunking、embedding、Oracle 26ai 境界への索引まで実行。
- `GET /api/dashboard/summary`: 文書状態、索引済み件数、検索可能チャンク数、最近の活動、readiness をまとめて返却。
- `POST /api/search`: hybrid/vector/keyword 検索、rerank、citation-grounded 回答、trace ID、guardrail warning を返却。
- `POST /api/search/select-ai`: Oracle Select AI profile を使い、自然言語から SQL (`showsql`) または明示的な SQL 実行結果 (`runsql`) を取得。
- `POST /api/evaluation/run`: golden set による precision@k、recall@k、MRR、回答キーワード命中率、groundedness pass rate、case 単位の失敗理由分布を算出。
- `POST /api/evaluation/compare`: 同じ golden set で複数の検索設定を比較し、ranking metric に基づく best experiment を返却。
- `/metrics`: Prometheus metrics を公開。

`evaluation/golden-set.example.json` は評価 API のテンプレートです。実データ投入後に `evaluation/golden-set.json` へコピーして document id と期待キーワードを調整し、CI / staging gate で使います。

Backend は常に OCI Enterprise AI、OCI Generative AI、Oracle 26ai を前提に動作します。開発・staging・本番のいずれも OCI / Oracle 接続情報を `.env` または設定画面から注入してください。

## ドキュメント

- [RAG アーキテクチャ](./docs/rag-architecture.md)
- [評価・観測性・ガードレール](./docs/evaluation-observability-guardrails.md)
- [デプロイメント](./docs/deployment.md)
- [参考 RAG プロジェクト](./docs/reference-rag-projects.md)

## CI

`.github/workflows/ci.yml` で backend / frontend / Docker Compose の品質門を固定している。Pull Request と `main` への push で、backend の format・lint・type check・test・security/dependency audit、frontend の lint・type check・dependency audit・build、`docker compose config` を実行する。
