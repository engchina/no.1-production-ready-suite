# AGENTS.md — Production Ready RAG

> このファイルは **Claude Code と Codex の両方が参照する正本(single source of truth)** です。
> `CLAUDE.md` はこのファイルを `@AGENTS.md` で取り込みます。ルールを変更する際は **必ずこのファイルを編集**してください。

## プロジェクト概要

**A production-ready RAG reference implementation for enterprise knowledge search, document ingestion, grounding, answer generation, evaluation, observability, and deployment on Oracle / OCI.**

本プロジェクトは、文書とナレッジベースを構築し、業務ごとの **Business View** から検索・回答する RAG システムを本番品質で提供することを目標とする。SQL 専用の自然言語問い合わせプロダクトは sibling repo `../no.1-production-ready-nl2sql` の責務であり、この repo へ機能・UI・設定を混在させない。

RAG の製品語は **ナレッジ構築**、**業務ビュー**、**検索・回答設定** を優先する。`producer / consumer / pipeline / adapter / profile` などの工程語は、コード内部または開発者向け診断に限定する。

## 言語・ローカライズ方針

- **システムの主要言語は日本語**。UI 文言・エラーメッセージ・通知・LLM への指示/出力・回答説明は日本語を前提とする。
- 国際化は最初から考慮する。ユーザー向け文言はハードコードせず i18n 経由で管理する。
- コード内のコメント/ドキュメントは日本語で可。識別子・型名は英語。

## 技術スタック(確定)

### AI / ML 層

| 用途 | 採用 | 重要な制約 |
|---|---|---|
| 回答生成・構造化抽出・クエリ計画 | OCI Enterprise AI | アプリ側 LLM の主経路。モデル設定は環境変数/設定 API 経由 |
| 埋め込み | OCI Generative AI Cohere Embed v4 | 多言語(日本語可)。Oracle Vector Search と次元数を一致させる |
| リランク | OCI Generative AI Cohere Rerank v4 fast | 検索精度改善のための rerank |
| 画像/文書理解 | OCI Document Understanding / Enterprise AI Vision | OCR・ページ解析・VLM 入力に使う |

### データ層

- **Oracle Autonomous Database / Oracle 26ai AI Vector Search** — チャンク、引用、文書構造、評価結果、監査ログ、ベクトル検索の正本。
- **OCI Object Storage** — 原本、変換済み artifact、レビュー済み文書、評価 artifact の保管。
- **外部ベクトル DB は導入しない**。必要な意味検索は Oracle 26ai AI Vector Search に集約する。

### バックエンド

- **Python 3.12 + FastAPI**。共有 backend core(`pr_backend_core` / production-ready-backend-core)を土台にする。
- **Pydantic v2**。LLM 出力・設定・API payload はスキーマで検証する。
- SDK: **oci** / **python-oracledb**。
- 依存管理: **uv**。

### フロントエンド

- **Vite + React Router + TypeScript**。
- **Tailwind CSS + shadcn/ui** と共有 UI package `@engchina/production-ready-ui`。
- 通信: REST + SSE/WebSocket。
- 状態管理: TanStack Query + Zustand。

### 横断

- 観測性: Langfuse + Prometheus + OpenTelemetry。
- 品質: pytest / pytest-cov / ruff / black / mypy / bandit / pip-audit / Vitest / Playwright。
- インフラ: Docker Compose(開発) → OKE / Container Instances(本番)、Terraform(OCI Resource Manager)。

## UI/UX 開発ルール

- **UI/UX に関する作業(設計・実装・レビュー・改善)は必ず `ui-ux-pro-max` skill を使う。**
- デザインは日本語 UI 前提で検証する。本文は日本語第一フォントスタック `"Noto Sans JP", "Roboto", system-ui, sans-serif`、本文ベース `14px` を基本とする。
- SaaS / 業務ツールとして、静かで読み取りやすい情報密度、安定したナビゲーション、明確なフォーム状態を優先する。
- UI/UX 変更ごとに Playwright で実画面を確認し、desktop と 375px 幅を最低限検証する。
- ナビゲーションは折りたたみ可能なサイドナビを正とし、主要セクションは以下とする。
  - **ナレッジ構築**: ダッシュボード、文書アップロード、文書インデックス、ナレッジベース。
  - **業務ビュー**: RAG 検索、業務ビュー、品質評価。
  - **検索・回答設定**: ファイル準備、文書解析、文書分割、検索インデックス、検索方法、根拠確認、回答スタイル、安全チェック、品質評価、GraphRAG、エージェント計画。
  - **システム設定**: OCI 認証、アップロード保存先、モデル、データベース、サービス管理。
- メッセージ機構は [docs/frontend-messaging-spec.md](./docs/frontend-messaging-spec.md) を正本とする。
- ボタン仕様は [docs/frontend-button-spec.md](./docs/frontend-button-spec.md) を正本とする。

## RAG 設定責務

### ナレッジベース(KB)

KB は **知識を作る設定だけ**を持つ。

- ファイル準備(preprocess)。
- 文書解析(parser / OCR engine)。
- 文書分割(chunking strategy、chunk size、overlap、parent-child)。
- 索引構築(vector index build、GraphRAG、navigation summary、asset summary、field extraction)。
- 品質 gate(解析品質、chunk 品質、公開前チェック)。
- 公開 snapshot(published config hash、published counts、published_at)。

KB UI から検索方法、根拠確認、回答スタイル、安全チェック、品質評価を出さない。legacy query config は読み取りのみ許容し、runtime では使わず、次回保存で再保存しない。

### Business View

Business View は **検索・回答に使う設定だけ**を持つ。

- 参照 KB scope。
- persona / system prompt / default language。
- 検索方法、根拠確認、回答スタイル、安全チェック、品質評価。
- serving mode、feedback 集計。

検索時の解決順は **request 明示 > Published Business View > global defaults**。KB の legacy query override は使わない。

### 多 KB / Build Config 差異

同じ文書が複数 KB に属し、chunking / index build 設定が異なる場合は、設定ハッシュ別の chunk set を materialize する。preprocess / parser のように再抽出が必要な差異は、V1 では警告または再取込要求として明示し、誤って既存抽出を silently reuse しない。

GraphRAG、navigation summary、asset summary、field extraction が planning のみで実 materialize 未完の場合は、UI/API diagnostics にその状態を表示する。

## ディレクトリ構成

```text
backend/                  FastAPI アプリ
  app/
    main.py               エントリ(CORS, ルーター, lifespan)
    config.py             設定(pydantic-settings)
    api/routes/           health / dashboard / documents / search / knowledge_bases /
                          business_views / evaluation / settings / services
    clients/              OCI / Oracle / Object Storage clients
    rag/                  ingestion / parsing / chunking / retrieval / grounding /
                          generation / guardrail / evaluation / business view
    schemas/              common / search / knowledge_base / business_view / settings
  tests/                  pytest
frontend/                 Vite + React Router + TypeScript
  src/App.tsx             React Router ルート定義
  src/components/         layout / search / knowledge-bases / business-views / settings
  src/lib/                api / queries / routes / i18n / utils
services/                 parser / preprocess / retrieval / generation などのローカル実行単位
```

## 開発コマンド

```bash
# backend
cd backend && uv sync
uv run pytest
uv run ruff check .
uv run mypy .
uv run uvicorn app.main:app --reload

# frontend
cd frontend && npm install
npm run lint
npm run build
npm run test
npm run dev
```

## テスト/検証方針

- 実装と同時に対応するテストを追加・更新する。backend は pytest、frontend ロジックは Vitest、UI/UX は Playwright。
- OCI / Oracle / LLM を呼ぶ層は CI では決定論スタブ/録画応答でテストする。実サービス検証は手動/ステージングとする。
- 変更後は該当範囲の lint・型チェック・テストを実行し、完了報告に実行結果を明記する。
- UI/UX 変更は Playwright で desktop と mobile 幅を確認する。空/読込/エラー/ブロック状態も必要に応じて確認する。

## コーディング規約・重要ルール

1. RAG の検索・回答は Oracle 26ai Vector Search、OCI Enterprise AI、OCI GenAI embedding/rerank を中心に構成する。
2. 外部ベクトル DB、別 LLM provider、別 RAG SaaS を導入しない。逸脱が必要な場合は理由を添えてユーザ確認する。
3. シークレット(OCI 認証・DB 接続・ADB wallet 等)は `.env` 経由。ハードコード禁止、コミットしない。
4. LLM 出力は Pydantic スキーマで検証してから保存・利用する。
5. ユーザー向け UI では `ナレッジ構築`、`業務ビュー`、`検索・回答設定` を主概念にする。
6. `Pipeline / Adapter / Profile / Runtime / Backend` などの技術語は、コード内部または高度な診断パネルに限定する。
7. `BusinessView` は正式な code/API 名として維持する。ユーザー表示は `業務ビュー` を使う。
8. KB query legacy config は runtime で無視し、保存時に新規保存しない。
9. 機能開発では、既存パターン・既存 API・既存 UI コンポーネントを優先する。
10. UI 作業は `ui-ux-pro-max` skill を使用する。
11. 変更後は該当 lint・型チェック・テストを実行してから完了する。
