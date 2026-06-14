# AGENTS.md — Production Ready RAG

> このファイルは **Claude Code と Codex の両方が参照する正本(single source of truth)** です。
> `CLAUDE.md` はこのファイルを `@AGENTS.md` で取り込みます。ルールを変更する際は **必ずこのファイルを編集**してください。

## プロジェクト概要

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
- **レイアウト/UI 構造**(情報設計・画面構成・ナビ導線・状態遷移・文言設計)は、本プロジェクト内の `frontend/src` と `src/lib/i18n` / `src/lib/routes` を正本として継続的に整備する。
- **技術選定は本 AGENTS.md の確定スタックを正とする。** フロントエンドのフレームワーク・ライブラリ・パターンは Vite + React Router + TypeScript + Tailwind + shadcn/ui + TanStack Query + Zustand を採用する。

**ナビゲーション/画面構成**:
- 折りたたみ可能な**サイドナビ**。セクション: 「データ取込」(ダッシュボード/アップロード/文書インデックス)・「RAG」(検索)・「設定」(OCI 認証/モデル/Object Storage/DB/プロンプト)。
- レイアウト構成要素: header / footer / breadcrumb / sideTabBar / tabs。
- 主要画面: ダッシュボード(主要機能ハブ + メトリクスカード + RAG フロー + 最近のアクティビティ + システム情報)、アップロード、文書インデックス、RAG 検索、各種設定、**文書プレビュー作業領域(DocumentPreviewWorkspace)**。

**状態モデル / UX パターン**:
- ファイル状態: `UPLOADED → INGESTING → INDEXED`(+ `ERROR`)を **StatusBadge** で可視化。人手の帳票項目修正・登録確認ゲートは設けず、RAG 取込成功時点で検索対象にする。
- ページネーション、確認ダイアログ、トースト通知、一括選択(全選択/選択件数表示)を共通コンポーネント化。
- **メッセージ機構(通知・成功/エラー・フォーム検証・確認ダイアログ・空/読込/エラー状態)は [docs/frontend-messaging-spec.md](./docs/frontend-messaging-spec.md) を正本とする。** 関連 UI を新規実装・改修するときは必ず同 spec の 6 チャネル / 4 トーン / i18n 規約に従うこと。
- **ボタン(大きさ・スタイル・配置・命名)は [docs/frontend-button-spec.md](./docs/frontend-button-spec.md) を正本とする。** アクションは共通 `<Button>` を使い、size(sm/md/lg)・variant(primary/secondary/ghost/danger)・配置・aria-label/文言キー規則を揃える。類似機能は同じ size・variant にすること。
- データ取得・通知・ページングは hooks に集約する。状態管理は TanStack Query + Zustand を使う。

**タイポグラフィ/デザイン原則**:
- **日本語第一フォントスタック**: `"Noto Sans JP", "Roboto", system-ui, sans-serif`。本文ベース `font-size: 14px`。
- 落ち着いた業務系トーンを shadcn/ui のテーマで再現する。
- 文言は日本語(i18n 経由)で管理する。

## ディレクトリ構成

```
backend/                  FastAPI アプリ
  app/
    main.py               エントリ（CORS, ルーター, lifespan）
    config.py             設定（pydantic-settings）
    logging_config.py     JSON 構造化ログ
    api/routes/           health / dashboard / documents / search / evaluation
    clients/              oci_enterprise_ai(LLM/VLM) / oci_genai(embed,rerank)
                          / oracle(26ai) / object_storage
    rag/                  chunking / ingestion / pipeline
    schemas/              common / document / search
  tests/                  pytest
  pyproject.toml          uv 管理、ruff/black/mypy/pytest 設定
frontend/                 Vite + React Router + Tailwind v4 + shadcn/ui
  src/main.tsx            Vite エントリ
  src/App.tsx             React Router ルート定義
  src/globals.css         Tailwind v4 / shadcn/ui theme tokens
  src/components/         layout/Sidebar, StatusBadge, PageHeader, providers
  src/lib/                routes / i18n(ja) / utils
docker-compose.yml        backend + frontend
```

## 検証済みコマンド（雛形は両方ビルド通過）

```bash
# backend
cd backend && uv sync && uv run pytest && uv run ruff check .
uv run uvicorn app.main:app --reload   # http://localhost:8000/docs

# frontend
cd frontend && npm install && npm run build
npm run dev                            # http://localhost:3000
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
npm run dev
npm run lint && npm run build
```

## テスト/検証方針

- 開発時は実装と同時に対応するテストコードを追加・更新する。バックエンドは pytest、フロントエンドのロジックは Vitest、UI/UX とユーザー操作は Playwright を基本とする。
- 変更後は該当範囲の lint・型チェック・テストを実行し、完了報告に実行結果を明記する。実行できない場合は理由と代替確認を明記する。
- UI/UX に関わるすべての機能は、Playwright でブラウザ表示を確認し、少なくとも主要ユーザーフロー、モバイル幅(例: 375px)、デスクトップ幅、重要な空/読込/エラー状態を検証する。

## コーディング規約・重要ルール

1. **LLM/VLM 呼び出しは OCI Enterprise AI 経由のみ**。OCI Generative AI の chat API を LLM/VLM に使わない。
2. **embedding/rerank は OCI Generative AI(Cohere)経由**。
3. **ベクトル検索は Oracle 26ai AI Vector Search**。外部ベクトル DB を導入しない。
4. シークレット(OCI 認証・DB 接続)は `.env` 経由。**ハードコード禁止**、コミットしない。
5. LLM 出力は Pydantic スキーマで検証してから DB 保存する。
6. UI 作業は `ui-ux-pro-max` skill を使用。
7. 機能開発では、実装と同時にテストコードを追加・更新する。
8. 変更後は該当範囲の lint・型チェック・テストを実行してから完了とする。
9. UI/UX に関わる変更は Playwright で画面確認とテストを実施してから完了とする。
10. このスタックから外れる提案(別 LLM プロバイダ、別 DB 等)をする場合は、必ず理由を添えてユーザに確認する。
