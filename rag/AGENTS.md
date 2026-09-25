# AGENTS.md — Production Ready RAG

> **RAG（`rag/`）固有のルール**です。GitHub 運用・Issue / PR 規約・CI・デザインシステム・共通の技術方針は、monorepo 共通の [../AGENTS.md](../AGENTS.md) を正本として先に適用します。
> Claude Code と Codex の両方が参照します。`CLAUDE.md` はこのファイルを `@AGENTS.md` で取り込みます。ルールを変更する際は **必ずこのファイル（共通ルールは ../AGENTS.md）を編集**してください。

## GitHub 運用・Issue / PR 規約（RAG 固有の追加分）

- 共通ルールは [../AGENTS.md](../AGENTS.md)「開発ワークフロー / GitHub 運用」に従う。Issue には `product:rag` label を付け、PR title の scope は `rag` にする。
- ユーザー向け概念は `ナレッジ構築` / `業務ビュー` / `検索・回答設定` を使い、`pipeline` / `adapter` / `profile` などの工程語は code identifier を指す場合に限る。
- 3 層モデル(文書レシピ / KB スコープ / Business View)に関わる Issue では、どの層の責務かを明記し、責務越境になっていないかを `修正方針` に記載する。
- PR の `検証結果` は、backend は `uv run pytest` / `uv run ruff check .` / `uv run mypy .`、frontend は `npm run lint` / `npm run build` / `npm run test` を基本とする。

## プロジェクト概要

**A production-ready RAG reference implementation for enterprise knowledge search, document ingestion, grounding, answer generation, evaluation, observability, and deployment on Oracle / OCI.**

本プロジェクトは、文書とナレッジベースを構築し、業務ごとの **Business View** から検索・回答する RAG システムを本番品質で提供することを目標とする。SQL 専用の自然言語問い合わせプロダクトは同じ monorepo の `../nl2sql/` の責務であり、`rag/` へ機能・UI・設定を混在させない。

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
- インフラ: Docker Compose(開発)。OCI Resource Manager の Terraform stack(`terraform/stack/`、Compute 1 台 + ADB + Docker Compose、CPU parser のみ)で配備する。OKE / Container Instances は規模が決まってから検討する(#136)。

## UI/UX 開発ルール

- **UI/UX に関する作業(設計・実装・レビュー・改善)は必ず `ui-ux-pro-max` skill を使う。**
- デザインは日本語 UI 前提で検証する。本文は日本語第一フォントスタック `"Noto Sans JP", "Roboto", system-ui, sans-serif`、本文ベース `14px` を基本とする。
- SaaS / 業務ツールとして、静かで読み取りやすい情報密度、安定したナビゲーション、明確なフォーム状態を優先する。
- UI/UX 変更ごとに Playwright で実画面を確認し、desktop と 375px 幅を最低限検証する。
- ナビゲーションは折りたたみ可能なサイドナビを正とし、主要セクションは以下とする。
  - **ナレッジ構築**: ダッシュボード、文書アップロード、文書インデックス、ナレッジベース。
  - **業務ビュー**: RAG 検索、業務ビュー、品質評価。
  - **検索・回答設定**: ファイル準備、文書解析、文書分割、検索インデックス、検索方法、根拠確認、回答スタイル、回答プロンプト、安全チェック、品質評価、GraphRAG、エージェント計画。
  - **運用設定**: HuggingFace 設定、サービス管理（RAG 固有の運用項目）。
  - **システム設定**: OCI 認証、アップロード保存先、モデル、データベース、外観（3製品で共通。画面と API は platform の共有パッケージ）。
- 画面の振る舞い（メッセージ機構・ボタンの役割と配置・ページの型・状態保持・横断的な保守契約）は platform の [UX 契約](../platform/docs/ux-contracts/README.md) を正本とする。RAG 固有の差分は [docs/frontend-messaging-spec.md](./docs/frontend-messaging-spec.md)（文書詳細の失敗表示）、[docs/frontend-workspace-state-spec.md](./docs/frontend-workspace-state-spec.md)（離脱ガードと作業状態の保持の対象・保存 key）、[docs/frontend-page-archetypes-spec.md](./docs/frontend-page-archetypes-spec.md)（各ページのページの型 A〜D と、対象の操作の `RowActionMenu` / `ObjectActionBar` への割り当て・例外）に書く。
- ボタンの大きさ・スタイル・アイコン・loading・ヘッダーの並び順は platform の `docs/design-system/` を正本とし、画面内の配置と命名は [UX 契約 buttons.md](../platform/docs/ux-contracts/buttons.md) に従う。

## デザインシステム / UI

- 共通ルール（platform が正本・禁止事項・画面の構成・lint・UI 変更の検証）は [../AGENTS.md](../AGENTS.md)「デザインシステム / UI」に従う。lint は `frontend/eslint.config.mjs` が `../../platform/docs/design-system/adherence.oxlintrc.json` を import する。
- 本ディレクトリの `docs/frontend-messaging-spec.md` は、デザインシステムと UX 契約が規定しない範囲でのみ有効とする。

### RAG 固有

- 移行時に `ToggleChip` をタブとして使っている箇所（ビューの切替）を `Tabs` に置き換え、絞り込みの箇所だけ `ToggleChip` に残す。
- `--font-mono`（ID・ログ・SQL の等幅書体）は platform の共有 tokens が定義する。書体ファイルは `frontend/src/fonts.css` で `@fontsource/google-sans-code` を自前ホストする。

## RAG 設定責務

> **3 層モデル(確定)**: 文書 = 処理レシピを持つ / KB = 純スコープ(コレクション)/ Business View → KB。
> KB は「レシピ」と「検索スコープ」を兼任しない。レシピは文書単位、検索・回答設定は Business View。

### 文書(Document)= レシピ

文書は中身に加えて **1〜3 件の独立した処理レシピ(preprocess / parser / chunking)** を自身のプロパティとして持つ。各レシピは設定、ジョブ、成果物、エラー、工程状態を個別に保持する。

- ファイル準備(preprocess)。
- 文書解析(parser / OCR engine)。
- 文書分割(chunking strategy、chunk size、overlap、parent-child)。
- 索引構築(vector index build、GraphRAG、navigation summary、asset summary、field extraction)。
- 品質 gate(解析品質、chunk 品質、公開前チェック)。

レシピの既定値は **global(「検索・回答設定」配下の ファイル準備 / 文書解析 / 文書分割 など)** から解決し、各レシピが選んだ値で上書きする。**KB からは解決しない**。embedding / HNSW は単一固定。同じ文書のジョブは直列実行し、異なる文書のジョブは並行実行できる。

### ナレッジベース(KB)= コレクション(純スコープ)

KB は **どの文書を検索対象にするか(membership)だけ**を持つ純スコープ。**レシピ(preprocess / parser / chunking)も検索・回答設定も持たない**。

- 文書 membership(N:N。1 文書が複数 KB に所属可、1 KB が複数文書を束ねる)。
- 名称 / 説明 / スコープ。

文書の KB 出し入れは `rag_document_knowledge_bases` の行 add/delete **のみ**で、chunk へ波及しない(再プラン/materialize/GC を起こさない)。KB UI から preprocess/parser/chunking・検索方法・根拠確認・回答スタイル・安全チェック・品質評価を出さない。KB の legacy adapter/query config は読み取りのみ許容し、runtime では使わず、次回保存で再保存しない。

### Business View

Business View は **検索・回答に使う設定だけ**を持つ。

- 参照 KB scope。
- 回答プロンプト(system prompt / default language)。
- 検索方法、根拠確認、回答スタイル、安全チェック、品質評価。
- feedback 集計。

設定責務は次の通りとし、業務ビューには Sidebar 全項目を複製しない。

| 設定群 | グローバル既定 | 文書レシピ上書き | Business View 上書き |
|---|---|---|---|
| ファイル準備 / 文書解析 / 文書分割 / GraphRAG | 可 | 可 | 不可 |
| 検索インデックス | 可 | 不可 | 不可 |
| 検索方法 / 根拠確認 / 回答スタイル / 回答プロンプト / 安全チェック / 品質評価 | 可 | 不可 | 可 |
| エージェント計画 | 可 | 不可 | 不可 |

GraphRAG の構築深度は文書レシピ、検索時の利用は Business View の「検索方法」にあるグラフ拡張で選ぶ。共有 Oracle 索引の設定は Business View へ保存しない。

検索時の解決順は **request 明示 > Published Business View > global defaults**。KB の legacy query override は使わない。

### 複数レシピ融合(精度向上)

同じ文書の検索精度を上げたい場合はレシピを最大3件まで追加し、それぞれを独立して materialize する。**各レシピの直近成功した active chunk_set はすべて検索対象**とし、既存の hybrid RRF + rerank + source-span 重複除去で融合する。主レシピ、候補、配信中、昇格の概念は持たず、`single / routed` は runtime で使わない。設定変更後の再処理中や再処理失敗時も、直前の active chunk_set を検索対象として維持する。KB membership を変えてもレシピ集合や chunk_set は変わらない。

レシピ追加・削除は親文書行をロックして **最少1件・最大3件**を保証する。活動中ジョブのあるレシピは編集・削除できない。成功時だけ新 chunk_set を active に原子切替し、失敗時は他レシピと旧 active 出力を変更しない。

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
npm run dev   # /api は BACKEND_URL を明示したときだけ proxy する（未指定なら 404 の hermetic モード）
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
12. `main` へ直接 commit / push しない。Issue → 作業ブランチ → PR → CI/checks → merge の流れと、Issue / PR の記述規約（[../AGENTS.md](../AGENTS.md)「開発ワークフロー / GitHub 運用」）に従う。
