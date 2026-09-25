# AGENTS.md — Production Ready NL2SQL

> **NL2SQL（`nl2sql/`）固有のルール**です。GitHub 運用・Issue / PR 規約・CI・デザインシステム・共通の技術方針は、monorepo 共通の [../AGENTS.md](../AGENTS.md) を正本として先に適用します。
> Claude Code と Codex の両方が参照します。`CLAUDE.md` はこのファイルを `@AGENTS.md` で取り込みます。ルールを変更する際は **必ずこのファイル（共通ルールは ../AGENTS.md）を編集**してください。

## GitHub 運用・Issue / PR 規約（NL2SQL 固有の追加分）

- 共通ルールは [../AGENTS.md](../AGENTS.md)「開発ワークフロー / GitHub 運用」に従う。Issue には `product:nl2sql` label を付け、PR title の scope は `nl2sql` にする。
- 本ファイル内の `#N` 形式の Issue / PR 番号のうち、2026-09-25 の monorepo 統合（suite#71）より前のものは旧 repo `engchina/no.1-production-ready-nl2sql` の番号を指す。

## プロジェクト概要

> **製品名と固有 namespace は `Production Ready NL2SQL` / `NL2SQL` を正とする。**
> 新規 DB object、cookie、storage key、context、UI の製品ラベルへ汎用 `RAG` prefix を使用しない。
> `GraphRAG`、`RAGAS`、`RAGFlow` など技術・製品の固有名詞はこの置換対象外とする。

**A production-ready RAG reference implementation covering data ingestion, chunking, indexing, hybrid retrieval, reranking, evaluation, observability, guardrails, and deployment best practices.**

本プロジェクトは特定業務ドメインに固定せず、RAG システムを本番品質で構築するための参照実装を目標とする。
パイプライン: ドキュメント取込 → OCR/構造化 → チャンク分割 → 埋め込み → ベクトル索引 → ハイブリッド検索 → リランク → LLM 回答生成 → 評価・観測・ガードレール・デプロイ。

### 参考プロジェクトカタログ

機能設計・実装方針の調査源として、外部 OSS / 研究プロジェクトの一覧を **[docs/reference-rag-projects.md](./docs/reference-rag-projects.md)** に整理している。
新機能の設計・比較検討の前に該当カテゴリ(プロダクト基盤 / GraphRAG / マルチモーダル / Agentic / 解析・取込 / 評価)を参照すること。
**各プロジェクトの優れた点は取り込むが、確定スタック(下記)へ必ず再マッピングし、外部ベクトル DB・別 LLM プロバイダをそのまま導入しない**(逸脱時は §コーディング規約 8 に従い理由を添えて要確認)。

## 言語・ローカライズ方針

- **システムの主要言語は日本語**。UI 文言・エラーメッセージ・通知・LLM への指示/出力はすべて日本語を前提とする。
- 国際化は最初から考慮する(ハードコードせず i18n 経由)。ただし第一言語は日本語。
- SQL 生成画面の自然言語入力は **`クエリ`** を正規名称とする。前に「検索」を付けた旧称は使用しない。
- コード内のコメント/ドキュメントは日本語で可。識別子・型名は英語。

## 技術スタック(確定)

### AI/ML 層
| 用途 | 採用 | 重要な制約 |
|---|---|---|
| LLM(構造化抽出・回答生成) | **OCI Enterprise AI** | ⚠️ **OCI Generative AI の chat 推論 API は使わない**。Enterprise AI を使用 |
| VLM(OCR・画像解析) | **OCI Enterprise AI** | 同上。chat エンドポイントではなく Enterprise AI |
| 埋め込み(embedding) | **OCI Generative AI**(Cohere Embed v4) | 多言語(日本語可)・**1536 次元** |
| リランク(rerank) | **OCI Generative AI**(Cohere Rerank v4 fast) | 検索精度の要 |

> LLM/VLM と embedding/rerank で **使用サービスが異なる**点に注意。実装は両者を別クライアントとして抽象化する。

### データ層(Oracle 集約)
- **Oracle 26ai** — AI Vector Search でベクトル検索を DB 内に一体化。**外部ベクトル DB(pgvector/Qdrant 等)は提案・導入しない。** ベクトル列は埋め込みに合わせ **`VECTOR(1536, FLOAT32)`**。
- **OCI Object Storage** — 原本ファイル保管(処理状態別バケット)。

### バックエンド
- **Python 3.12 + FastAPI**(ASGI、非同期)。
- **Pydantic v2** — LLM 構造化出力のスキーマ定義。
- SDK: **oci** / **python-oracledb**。
- サーバ: Uvicorn(+ Gunicorn for 本番)。
- 依存管理: **uv**。

### フロントエンド
- **Vite + React Router + TypeScript**。
- **Tailwind CSS + shadcn/ui**。
- 通信: REST + **SSE/WebSocket**(回答ストリーミング)。
- 状態管理: TanStack Query + Zustand。

### 横断
- 観測性: **Langfuse**(LLM トレース/コスト)+ Prometheus + OpenTelemetry。
- 品質: pytest / pytest-cov / ruff / black / mypy / bandit / pip-audit / Vitest / Playwright。
- インフラ: Docker Compose(開発)→ OKE / Container Instances(本番)、Terraform(OCI Resource Manager)。

## UI/UX 開発ルール

- **UI/UX に関する作業(設計・実装・レビュー・改善)は必ず `ui-ux-pro-max` skill を使う。** 画面・コンポーネント・スタイル・配色・タイポグラフィ・アクセシビリティはこの skill の知見に従うこと。
- デザインは日本語 UI 前提でレイアウト(行高・禁則・フォント)を検証する。
- UI/UX に関わる機能追加・修正は、**必ず Playwright で実画面を表示して確認・テストする**。主要導線、レスポンシブ表示、キーボード操作、アクセシビリティ上の破綻がないことを確認する。
- UI/UX 変更ごとに Playwright テスト(e2e / interaction / 必要に応じた visual check)を追加・更新し、完了前に実行する。

### UI/UX 構造

**基本原則:**
- **レイアウト/UI 構造**(情報設計・画面構成・ナビ導線・状態遷移・文言設計)は、本プロジェクト内の `frontend/src` と `src/lib/i18n` / `src/lib/routes` を正本として継続的に整備する。**見た目(トークン)と共有コンポーネントは platform の `docs/design-system/` を正本とする**(「デザインシステム / UI」節)。
- **技術選定は本 AGENTS.md の確定スタックを正とする。** フロントエンドのフレームワーク・ライブラリ・パターンは Vite + React Router + TypeScript + Tailwind + shadcn/ui + TanStack Query + Zustand を採用する。

**ナビゲーション/画面構成**:
- 折りたたみ可能な**サイドナビ**。構成の正本は `frontend/src/components/layout/nav-config.ts`:
  - **AI 活用** / **データ準備** / **改善・運用** / **セキュリティ管理**: NL2SQL の業務機能。
  - **運用設定**: システムテーブル管理（NL2SQL 固有の運用項目）。
  - **システム設定**: OCI 認証 / アップロード保存先 / モデル / データベース / 外観（3製品で共通。画面と API は platform の共有パッケージ）。
- サイドナビのラベルは**日本語第一**とし、パイプライン各段階は `解析 (Parser)` のように「日本語+英語正式名」併記の短縮形(`sidebarLabelKey`)で表示する。一方**ページタイトル/`aria-label` は AGENTS.md 準拠の正式名(例: `Parser アダプター`)を維持**する(`nav.*` と `nav.*.sidebar` の二段管理。新アダプター追加時も同様にする)。
- レイアウト構成要素: header / footer / breadcrumb / sideTabBar / tabs。
- 主要画面: ダッシュボード(主要機能ハブ + メトリクスカード + RAG フロー + 最近のアクティビティ + システム情報)、アップロード、文書インデックス、RAG 検索、各種設定、**文書プレビュー作業領域(DocumentPreviewWorkspace)**。

**状態モデル / UX パターン**:
- ファイル状態: `UPLOADED → INGESTING(parse/抽出) → REVIEW(プレビュー確認待ち) → INDEXING → INDEXED`(+ `ERROR`)を **StatusBadge** で可視化する。**ファイル処理は 2 段階(parse → 人がプレビュー確認 → index)を方針とする。** parse/抽出の完了後はいったん `REVIEW` で停止し、`DocumentPreviewWorkspace` で抽出結果を人手で確認・承認(必要なら帳票項目を修正)してから後段の chunk/embed/index を実行する。**人手のプレビュー確認・承認ゲートを通過した文書のみ検索対象にする。** 抽出 artifact は再利用し、確認・承認は index 実行前の必須ゲートとする。
- ページネーション、確認ダイアログ、トースト通知、一括選択(全選択/選択件数表示)を共通コンポーネント化。
- **画面の振る舞い(メッセージ機構・ボタンの役割と配置・ページの型・状態保持)は platform の [UX 契約](../platform/docs/ux-contracts/README.md) を正本とする。** NL2SQL 固有の差分は [docs/frontend-messaging-spec.md](./docs/frontend-messaging-spec.md) / [docs/frontend-button-spec.md](./docs/frontend-button-spec.md) / [docs/frontend-page-archetypes-spec.md](./docs/frontend-page-archetypes-spec.md) に書く。関連 UI を新規実装・改修するときは 6 チャネル / 4 トーン / i18n 規約に従うこと。
- **ボタンの大きさ・スタイル・アイコン・loading・ヘッダーの並び順は platform の `docs/design-system/` を正本とし、画面内の配置と命名は [UX 契約 buttons.md](../platform/docs/ux-contracts/buttons.md) に従う。** アクションは共通 `<Button>` を使い、size(sm/md/lg)・variant(primary/secondary/ghost/danger)・配置・aria-label/文言キー規則を揃える。類似機能は同じ size・variant にすること。
- データ取得・通知・ページングは hooks に集約する。状態管理は TanStack Query + Zustand を使う。

**タイポグラフィ/デザイン原則**:
- **日本語第一フォントスタック**: `"Noto Sans JP", "Roboto", system-ui, sans-serif`。本文ベース `font-size: 14px`。
- 落ち着いた業務系トーンを `@engchina/production-ready-ui` のトークンで再現する(本リポジトリで色トークンを定義しない)。
- 文言は日本語(i18n 経由)で管理する。

## デザインシステム / UI

- 共通ルール（platform が正本・禁止事項・画面の構成・lint・UI 変更の検証）は [../AGENTS.md](../AGENTS.md)「デザインシステム / UI」に従う。以下は NL2SQL 固有の追加分。

### lint

- `frontend/.oxlintrc.json` は platform の `docs/design-system/adherence.oxlintrc.json` を `extends` で相対パス参照し（`../../platform/…`）、生の hex / inline style の生の px / 書体 / 型・角丸の任意値 / 旧トークン名 / 内部パス直 import / loading 中のラベル差し替えを `src/**` で検出する。ルールと JS プラグイン（`design-system-plugin.mjs`）は **NL2SQL にコピーしない**（正本は platform。変更は platform に Issue を立てる）。monorepo の `platform/` が同じ階層にあるため、CI の `NL2SQL / Frontend` job はそのまま `npm run lint`（`oxlint --deny-warnings`）を実行できる。
- アプリ固有のルールを `.oxlintrc.json` に足す場合は、`design-system/restricted-syntax` / `no-restricted-imports` を同じ名前で上書きしない（adherence のセレクタが消える）。アプリ固有のセレクタは、platform のプラグインを別名で再公開した `frontend/lint/app-design-system-plugin.mjs` の `nl2sql-design-system/restricted-syntax` に書く（現在は手書き `<table>` の禁止、#530。RAG / Agent の置き換え後に adherence へ移す）。
- 誤検知や正当な例外（グラフ座標など px が正しい幾何値）は `// oxlint-disable-next-line <rule>` に理由コメントを添えて局所的に除外する。ルール自体を緩める必要がある場合は platform の adherence 設定に Issue を立てる。

### NL2SQL 固有

- **移行で削除するもの**（`packages/ui` に昇格済み。完了したらこの箇条を削除する）
  - `frontend/src/components/ui/button.css` → `Button`
  - `frontend/src/components/ui/status-badge.tsx` → `StatusBadge`
  - `frontend/src/components/PageHeader.tsx` → `PageHeader`
  - `frontend/src/globals.css` の `:root` / `.dark` トークン定義と `.dark .bg-muted\/NN` 上書き → トークンと `light-dark()`
  - 設定画面ごとの独自 `TextField` → `TextField`
  - `frontend/src/components/layout/AppSidebar.tsx` のフッター独自実装 → `SidebarAccountFooter`
- **残すもの（ドメイン固有）**
  - `WorkflowProgressStrip`
  - オントロジー / 関係グラフ。配色は `--color-graph-*`（種別 = 塗り、状態 = 線）
- `--font-mono`（Google Sans Code）は本リポジトリが `@fontsource` で自前ホストして定義する。

## ディレクトリ構成

```
backend/                  FastAPI アプリ
  app/
    main.py               エントリ（CORS, ルーター, lifespan）
    config.py             設定（pydantic-settings）
    logging_config.py     JSON 構造化ログ
    api/routes/           health / dashboard / documents / search / evaluation
    clients/              oci_enterprise_ai(LLM/VLM) / oci_genai(embed,rerank)
                          / oracle(26ai) / object_storage / parser_service(HTTP委譲)
    rag/                  chunking / ingestion / pipeline
    schemas/              common / document / search
  tests/                  pytest
  pyproject.toml          uv 管理、ruff/black/mypy/pytest 設定
packages/
  rag_parser_core/        backend と parser サービスが共有する parser 契約 package
                          (extraction/source schema・routing・registry remap・
                           ParseResponse・FastAPI app factory)。依存は pydantic +
                           charset-normalizer のみ(重い parser 依存・oci/oracle は持たない)
services/parsers/         外部 parser の独立 FastAPI マイクロサービス(各々独自依存/Dockerfile)
  docling/ marker/        CPU(既定起動)
  unstructured/
  mineru/ dots_ocr/       GPU(CUDA、docker compose --profile gpu で opt-in)
  glm_ocr/                GPU(HuggingFace zai-org/GLM-OCR を transformers でロード)
frontend/                 Vite + React Router + Tailwind v4 + shadcn/ui
  src/main.tsx            Vite エントリ
  src/App.tsx             React Router ルート定義
  src/globals.css         Tailwind v4 / shadcn/ui theme tokens
  src/components/         layout/Sidebar, StatusBadge, PageHeader, providers
  src/lib/                routes / i18n(ja) / utils
docker-compose.yml        backend + ingestion-worker + parser サービス群 + frontend
```

## Parser マイクロサービス(重要)

外部 parser(docling / marker / unstructured / mineru / dots_ocr / glm_ocr)は **backend と同居
させず、それぞれ独立した FastAPI マイクロサービス**(`services/parsers/<name>`)で動かす。各サービスは
独自 Dockerfile・独自依存で **独立して upgrade でき、相互・backend に非干渉**(torch 等の共有
依存衝突を回避)。

- 出力契約は共有 package `rag_parser_core` の `StructuredExtraction`(`POST /parse`)で統一し、
  remap 忠実度をネットワーク越しに維持する。readiness は各サービスの `GET /health`。
- backend は取込時に `app.clients.parser_service.ParserServiceClient` で HTTP 委譲する
  (`parse_with_registry(..., external_adapter_runner=...)`)。**サービス未達/timeout 時は warning
  を付けて local / Enterprise AI VLM へ安全に fallback**(`*_adapter_service_unreachable`)。
- 選択は従来どおり `RAG_PARSER_ADAPTER_BACKEND` と `RAG_PARSER_<name>_ENABLED`。サービス URL は
  `RAG_PARSER_<name>_SERVICE_URL`、timeout は `RAG_PARSER_SERVICE_TIMEOUT_SECONDS`。
- **mineru / dots_ocr / glm_ocr は GPU(CUDA)で実 OCR を行う実 parser**(従来の「実 OCR は
  Enterprise AI VLM へ再マップ」方針からの逸脱。ユーザ明示要望による)。GPU 必須のため compose は
  `--profile gpu` で opt-in、CI は GPU 非搭載のため remap 層を fixture でテストし実 GPU は手動検証。
  **glm_ocr は専用 pip package を持たず HuggingFace `zai-org/GLM-OCR` を transformers でロード**する
  (`GLM_OCR_MODEL_ID` で上書き可)。別 LLM provider・外部ベクトル DB は導入しない確定スタックは不変。
- 確定スタックは不変: embedding/rerank=OCI GenAI、回答/構造化 LLM・通常 VLM=Enterprise AI、
  ベクトル DB=Oracle 26ai。**外部ベクトル DB・別 LLM provider は導入しない。**
- monorepo の path 依存(`rag-parser-core`)を使うため、依存追加時は **`uv lock` の再生成が必要**
  (Docker は build context = リポジトリ root)。

### service 系 parser backend(OCI クラウドサービス直呼び)

外部 parser マイクロサービス(上記)とは別に、**OCI クラウドサービスを backend から直接呼ぶ
service 系 backend** を `RAG_PARSER_ADAPTER_BACKEND` で明示選択できる。core(`rag_parser_core`)は
決定論・非 network を保つため実行せず sentinel(`extraction=None`)を返し、実呼び出しは backend の
ingestion が担う(`SERVICE_ADAPTER_BACKENDS`)。

- **`enterprise_ai_vlm`**: OCI Enterprise AI VLM を **fallback ではなく明示選択**する。選択時は
  ローカル/外部 adapter を飛ばして直接 VLM 抽出する(設定は既存 Enterprise AI を再利用、追加 env 不要)。
- **`oci_document_understanding`**: **別 OCI サービス OCI Document Understanding(`oci.ai_document`)**
  の**非同期 processor job** で日本語 OCR/表抽出する。入出力は Object Storage 経由
  (`app.clients.oci_document_understanding.OciDocumentUnderstandingClient`)。**未設定/SDK 失敗/job
  失敗/timeout 時は `None` を返し、既存のローカル/Enterprise AI VLM フローへ安全に縮退**。
  これは確定スタックに無い **追加 OCI サービス**(LLM/VLM=Enterprise AI、OCR は Enterprise AI VLM 再
  マップという従来方針からの拡張)であり、**ユーザ明示要望による**。別 LLM provider・外部ベクトル DB は
  導入しない。設定は `OCI_DOCUMENT_UNDERSTANDING_*`(compartment/namespace/bucket/prefix/language/
  poll/timeout)。空欄は汎用 `OCI_COMPARTMENT_ID` / `OBJECT_STORAGE_*` を使う。
- 設定 API `GET/PATCH /api/settings/parser-adapters` は両 backend を選択値として受理し、GET 応答の
  `service_backends[]` で選択状態と設定可用性(`configured` / `warning_code`)を返す。

## 検証済みコマンド（雛形は両方ビルド通過）

```bash
# backend
cd backend && uv sync && uv run pytest && uv run ruff check .
uv run uvicorn app.main:app --reload   # http://localhost:8000/docs

# frontend
cd frontend && npm install && npm run build
npm run dev                            # http://localhost:3000（/api は BACKEND_URL 明示時のみ proxy。未指定なら 404 の hermetic）
```

## 開発コマンド(scaffolding 後)

```bash
# backend
uv sync                      # 依存解決
uv run uvicorn app.main:app --reload
uv run pytest                # テスト
uv run ruff check . && uv run mypy .   # lint/型

# frontend
cd frontend && npm install
BACKEND_URL=http://127.0.0.1:8010 npm run dev   # BACKEND_URL 未指定なら /api は proxy せず 404（hermetic。起動時に警告）
npm run lint && npm run build
```

取込 API は HTTP リクエスト内で Docling/OCR/embedding/indexing を実行しない。`POST /api/documents/{id}/ingest` と `/ingestion-jobs` は永続 job を投入して即時に返し、既定のローカル開発では in-process dispatcher が `python -m app.rag.ingestion_job_runner <job_id>` subprocess へ job 本体を隔離する。Docker Compose / 本番では `ingestion-worker` service がキューを消費し、API service は job 投入と閲覧系 API に専念する。

## 横断的な保守・セキュリティ契約

3製品共通の契約（更新 API とデータの所有範囲、認可と状態遷移のサーバー側強制、i18n の変更と E2E の locator、ページ遷移・再読込のときの状態保持、未保存変更の離脱ガード）は platform の [UX 契約](../platform/docs/ux-contracts/README.md)（[cross-cutting.md](../platform/docs/ux-contracts/cross-cutting.md) / [workspace-state.md](../platform/docs/ux-contracts/workspace-state.md)）を正本とする。本節には NL2SQL 固有の規則だけを書く。

> 本節は [Issue #168](https://github.com/engchina/no.1-production-ready-nl2sql/issues/168) / [PR #178](https://github.com/engchina/no.1-production-ready-nl2sql/pull/178)、[Issue #169](https://github.com/engchina/no.1-production-ready-nl2sql/issues/169) / [PR #179](https://github.com/engchina/no.1-production-ready-nl2sql/pull/179)、[Issue #170](https://github.com/engchina/no.1-production-ready-nl2sql/issues/170) / [PR #180](https://github.com/engchina/no.1-production-ready-nl2sql/pull/180)、[Issue #181](https://github.com/engchina/no.1-production-ready-nl2sql/issues/181) / [PR #182](https://github.com/engchina/no.1-production-ready-nl2sql/pull/182)、[Issue #183](https://github.com/engchina/no.1-production-ready-nl2sql/issues/183) / [PR #189](https://github.com/engchina/no.1-production-ready-nl2sql/pull/189)、[Issue #184](https://github.com/engchina/no.1-production-ready-nl2sql/issues/184)、[Issue #185](https://github.com/engchina/no.1-production-ready-nl2sql/issues/185) で得た再発防止策を、実装時に検証可能な契約としてまとめたものである。

### role の権限昇格の防止

- `SYSTEM_ADMIN` 以外が role を更新するとき、追加される実効権限 `expand_permissions(new) - expand_permissions(current)` は actor 自身の実効権限の部分集合でなければならず、違反は `403` で拒否する。暗黙 permission と `grants_all_profile_access` 相当の profile 管理権限も展開後に評価し、自分・他人いずれの role 経由でも権限昇格を許可しない。未保持の既存権限を削除する操作は妨げない。
- role 作成は未割当のため現行どおり許可できるが、user への role 割当では既存の実効権限部分集合 check を必須とする。role/assignment 変更が次 request から再計算される前提で、変更直後の許可・拒否まで API 回帰テストに含める。

### 状態保持の実装範囲（Issue #298）

- 保存の namespace・期限・対象画面・検証の spec は [docs/frontend-workspace-state-spec.md](./docs/frontend-workspace-state-spec.md) に書く。SQL・生成 SQL・`ADMIN_EXECUTE` などの確認語は共通契約の「入力」「生成した結果」「確認語」として扱う。

## テスト/検証方針

- 開発時は実装と同時に対応するテストコードを追加・更新する。バックエンドは pytest、フロントエンドのロジックは Vitest、UI/UX とユーザー操作は Playwright を基本とする。
- 変更後は該当範囲の lint・型チェック・テストを実行し、完了報告に実行結果を明記する。実行できない場合は理由と代替確認を明記する。
- UI/UX に関わるすべての機能は、Playwright でブラウザ表示を確認し、少なくとも主要ユーザーフロー、モバイル幅(例: 375px)、デスクトップ幅、重要な空/読込/エラー状態を検証する。

## コーディング規約・重要ルール

1. **LLM/VLM 呼び出しは OCI Enterprise AI 経由のみ**。OCI Generative AI の chat API を LLM/VLM に使わない。
2. **embedding/rerank は OCI Generative AI(Cohere)経由**。
3. **ベクトル検索は Oracle 26ai AI Vector Search**。外部ベクトル DB を導入しない。
4. **chunks 段階の分割は Chunking アダプター(`rag_chunking_strategy`)で手動選択**する。業界の代表的 chunking 手法(structure_aware / recursive_character / sentence_window / hierarchical_parent_child / markdown_heading / page_level)を外部依存なし・決定論で `StructuredExtraction` へ再マップする。`Parser アダプター` と対の概念で、設定 API `GET/PATCH /api/settings/chunking` と専用設定画面から切り替える。新戦略を追加するときも外部ベクトル DB / 別 LLM provider を導入しない。
5. **検索段階は Retrieval アダプター(`rag_retrieval_strategy`)、検索後処理は Grounding アダプター(`rag_post_retrieval_pipeline`)で手動選択**する。検索戦略は hybrid_rrf(既定)/ vector / keyword / graph_augmented / select_ai_structured / business_context_strict / corrective_multi_query。検索後処理は custom(既定・既存 `rag_context_*` フラグを尊重)/ lean / verified_context / context_enrich / compact / full_governed。gap-stop・corrective retrieval・business-fit 加重などの決定論的手法は preset から有効化し、設定 API `GET/PATCH /api/settings/retrieval` `…/grounding` と専用設定画面で切り替える。既定 preset は現行挙動と一致させる。
6. **回答生成は Generation アダプター(`rag_generation_profile`)、安全は Guardrail アダプター(`rag_guardrail_policy`)で手動選択**する。回答生成は grounded_concise(既定・現行 system prompt)/ detailed_cited / strict_extractive / structured_json / bilingual_ja_en で、OCI Enterprise AI へ渡す system prompt 変種を決定論で束ねる(追加 LLM 呼び出しなし)。安全は standard(既定・現行)/ strict / lenient / regulated で、prompt injection・PII マスク・groundedness 閾値の厳格度を束ねる。設定 API `GET/PATCH /api/settings/generation` `…/guardrail` と専用設定画面で切り替え、既定 preset は現行挙動と一致させる。外部 LLM provider / 外部安全 SaaS は導入しない。
7. **索引/検索精度は Vector Index アダプター(`rag_vector_index_profile`)で手動選択**する。balanced(既定・現行 `ORACLE_VECTOR_TARGET_ACCURACY` を使用)/ accurate(98)/ fast(85)で検索時 target accuracy を runtime 即時に切り替える。推奨 HNSW ビルドパラメータ(neighbors/efconstruction/distance)は設定画面の参考表示に留め、適用には索引再作成が必要(`requires_reprovision`)。設定 API `GET/PATCH /api/settings/vector-index` と専用設定画面で切替し、既定 balanced は現行挙動と一致させる。版管理された schema DDL artifact は自動変更しない。
8. **評価の閾値スイートは Evaluation アダプター(`rag_evaluation_suite`)で手動選択**する。request_only(既定・プリセット閾値なし=現行挙動)/ retrieval_focused / balanced / strict_ci / ragas_like を CI gate 用の名前付き閾値として束ねる。解決順は request の明示 thresholds > request の suite > 設定 `rag_evaluation_suite`。設定 API `GET/PATCH /api/settings/evaluation-suite` と専用設定画面で切替し、`/api/evaluation` 応答へ `evaluation_suite` を残す。外部評価 SaaS / LLM-as-judge の追加呼び出しは導入しない(決定論指標のみ)。
9. **知識グラフ構築は GraphRAG アダプター(`rag_graph_profile`)、検索前のクエリ計画は Agentic アダプター(`rag_agentic_profile`)で手動選択**する。GraphRAG は取込側の構築深度を off(既定・KG 非構築=現行挙動)/ entities(entities+relationships のみ)/ full(claims+community summary まで)で束ね、検索側 routing は Retrieval の `graph_augmented` が担う(両者は合成)。legacy `RAG_GRAPH_ENABLED=true` は full 相当として後方互換を保つ。Agentic は off(既定・LLM 計画なし=現行挙動)/ query_rewrite / decompose / multi_hop で OCI Enterprise AI による書き換え・sub-question 分解・multi-hop を行い、既存のマルチクエリ RRF 融合へ注入する(off 以外は追加 LLM 呼び出しが発生、multi_hop は上限 1 hop で corrective retrieval と排他)。設定 API `GET/PATCH /api/settings/graph` `…/agentic` と専用設定画面で切替し、既定はいずれも現行挙動と一致させる。外部グラフ DB / 別 LLM provider は導入しない。
10. シークレット(OCI 認証・DB 接続)は `.env` 経由。**ハードコード禁止**、コミットしない。
11. LLM 出力は Pydantic スキーマで検証してから DB 保存する。
12. UI 作業は `ui-ux-pro-max` skill を使用。
13. 機能開発では、実装と同時にテストコードを追加・更新する。
14. 変更後は該当範囲の lint・型チェック・テストを実行してから完了とする。
15. UI/UX に関わる変更は Playwright で画面確認とテストを実施してから完了とする。
16. このスタックから外れる提案(別 LLM プロバイダ、別 DB 等)をする場合は、必ず理由を添えてユーザに確認する。
17. **Backend の ASGI event loop 上で同期 I/O を直接実行しない。** 詳細は [docs/backend-concurrency-contract.md](./docs/backend-concurrency-contract.md) を正とする。同期 domain service / Oracle repository / file・CLOB・Excel 処理 / OCI SDK 呼び出しは sync FastAPI route(`def`)、CLI/worker、または `backend/app/api/concurrency.py` の `run_sync_io(...)` 内だけで実行する。`async def` route は `UploadFile`・SSE/WebSocket・async client など本当に `await` が必要な場合に限定する。
18. **parse 前の原本変換は前処理アダプター(`rag_preprocess_profile`)で手動選択**する。passthrough(既定・変換なし=現行挙動)/ text_normalize(文字コード・Unicode・空白の正規化、in-process)/ office_to_pdf(LibreOffice)/ pdf_to_page_images(PDF をページ画像 PDF へラスタライズ)/ csv_to_json(CSV をヘッダ列キーのレコード配列 JSON へ変換、engchina/No.1 系 csv2json の再マップ)/ excel_to_json(Excel `.xls`/`.xlsx` をシート単位のレコード配列 JSON へ変換、openpyxl + xlrd)。**原本は必ず保全し、変換物(正規化原本)から原本へ追跡できる派生系譜(`SourceDerivation`)を残す**(溯源)。サービス必須の変換(office_to_pdf / pdf_to_page_images / csv_to_json / excel_to_json)は **`services/parsers/<name>` と同じく 1 変換 = 1 独立マイクロサービス**(`services/preprocess/<name>`、各々独自依存・独自 Dockerfile で独立 upgrade/スケール)へ HTTP 委譲し、profile ごとに専用 URL(`RAG_PREPROCESS_<PROFILE>_SERVICE_URL`)を引く。未達/失敗/無効時は warning を付けて passthrough へ安全に縮退する。設定 API `GET/PATCH /api/settings/preprocess` と専用設定画面(パイプラインの Parser の前)で切替し、KB 単位上書きは取込時スナップショット。**本番は各サービスを Docker イメージ化し OKE / Container Instances へ独立デプロイ**(build context = リポジトリ root)。外部 LLM provider / 外部ベクトル DB は導入しない。`fixed_size` 固定長 chunking を Chunking アダプターに追加し、KB 単位で chunk_size/overlap を固定設定できる。
19. **質問の「業務(利用者)視点」は業務アシスタント(Business View, `rag_business_view`)で束ねる**。KB が「文書をどう加工して索引するか(作る側視点)」を司るのに対し、業務アシスタントは「どの **KB 群(多対多)** を、どんな **query 方針**(Retrieval/Grounding/Generation/Guardrail/Vector Index/Evaluation の上書き、KB の `KnowledgeBaseQueryConfig` を再利用)・**persona**(system prompt/既定言語)で束ねて回答するか(利用する側視点)」を司る別レイヤー。検索 API は `business_view_id` を受けると参照 KB 群を検索対象へ展開し、業務アシスタント 1 枚の query 設定・persona を適用する(**複数 KB の query 設定競合をここで解消**。persona は `rag_generation_system_prompt_override` で Generation profile より優先注入)。取込系(Preprocess/Parser/Chunking/Vector Index build)は物理索引方法なので業務アシスタントでは触らず KB 側のまま。解決順は **request 明示 > 業務アシスタント >(単一 KB 指定時のみ)KB > グローバル既定**。永続化は `rag_business_views.view_config JSON`(参照 KB ids も同梱、link table 無しで DDL 最小)。設定 API `GET/POST/PATCH /api/business-views` `…/{id}/archive` と RAG セクションの専用管理画面/RAG 検索のビュー選択で切替する。**アクセス制御(ビュー単位の利用者制限)は現スタックに認証/RBAC が無いため別途設計**。外部 LLM provider / 外部ベクトル DB は導入しない。
