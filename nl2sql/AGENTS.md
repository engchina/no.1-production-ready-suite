# AGENTS.md — Production Ready NL2SQL

> **NL2SQL（`nl2sql/`）固有のルール**です。GitHub 運用・Issue / PR 規約・CI・デザインシステム・共通の技術方針は、monorepo 共通の [../AGENTS.md](../AGENTS.md) を正本として先に適用します。
> Claude Code と Codex の両方がこのファイルを直接読みます（`CLAUDE.md` は置かない。#1263）。ルールを変更する際は **必ずこのファイル（共通ルールは ../AGENTS.md）を編集**してください。

## GitHub 運用・Issue / PR 規約（NL2SQL 固有の追加分）

- 共通ルールは [../AGENTS.md](../AGENTS.md)「開発ワークフロー / GitHub 運用」に従う。Issue には `product:nl2sql` label を付け、PR title の scope は `nl2sql` にする。
- 本ファイル内の `#N` 形式の Issue / PR 番号のうち、2026-09-25 の monorepo 統合（suite#71）より前のものは旧 repo `engchina/no.1-production-ready-nl2sql` の番号を指す。

## プロジェクト概要

> **製品名と固有 namespace は `Production Ready NL2SQL` / `NL2SQL` を正とする。**
> 新規 DB object、cookie、storage key、context、UI の製品ラベルへ汎用 `RAG` prefix を使用しない。
> `GraphRAG`、`RAGAS`、`RAGFlow` など技術・製品の固有名詞はこの置換対象外とする。

Oracle AI Database の業務データに、自然言語で SQL を生成・検証・実行する SQL 専用の NL2SQL の参照実装。特定の業務ドメインに固定しない。

- **AI 活用**: チャット（自然言語による SQL の多輪の生成と実行。SQL 生成の画面と同じく送信のジョブが実行まで行う。#1176）、自然言語の質問から SQL を生成して実行する（SQL 生成）、SELECT SQL の直接実行、SQL から質問の生成、実行履歴。
- **データ準備**: 管理 SQL の実行、テーブル・ビュー・データ・コメント・アノテーション・ドメインの管理、用語・同義語、共通ルール、サンプルデータ。
- **改善・運用**: 業務プロファイル（SQL 生成の対象範囲）、オントロジー構築、フィードバック、質問分類モデル、SQL 生成評価。

### 参考プロジェクトカタログ

機能設計・実装方針の調査源として、外部 OSS / 研究プロジェクトの一覧を **[docs/reference-rag-projects.md](./docs/reference-rag-projects.md)** に整理している。
新機能の設計・比較検討の前に該当カテゴリ(Product Foundation / GraphRAG・Business Graph / Vector Search / Agentic・Query Planning / Evaluation・Guardrails)を参照すること。
**各プロジェクトの優れた点は取り込むが、確定スタック(下記)へ必ず再マッピングし、外部ベクトル DB・別 LLM プロバイダをそのまま導入しない**(逸脱時は §コーディング規約 10 に従い理由を添えて要確認)。

## 言語・ローカライズ方針

- **システムの主要言語は日本語**。UI 文言・エラーメッセージ・通知・LLM への指示/出力はすべて日本語を前提とする。
- 国際化は最初から考慮する(ハードコードせず i18n 経由)。ただし第一言語は日本語。
- 利用者が入力する自然言語（SQL 生成・チャット・AI 要件確認の入力など）は **`質問`** と呼ぶ（3 製品で統一。root の [AGENTS.md](../AGENTS.md) の「言語」、#1183）。`クエリ` は SQL・技術の概念（サブクエリなど）にだけ使い、旧称の「検索クエリ」も使わない。`nl2sql/frontend/tests/i18n-key-contract.test.ts` が辞書の文言を検査する。
- 利用者の自然言語の入力とその実行を **「検索」と呼ばない**（#1189）。入力は「質問」、それによる SQL の生成と実行は「SQL を生成して実行」（ボタン）・「SQL の生成と実行」（説明の文）、その結果は「実行結果」と呼ぶ（旧称「NL2SQL 検索」「検索実行」「検索結果」「検索画面」は使わない）。一覧の絞り込みの検索欄（`SearchField`・「検索語をクリア」）、選択欄の候補の検索、類似検索・ベクトル検索・埋め込み検索・オントロジーのサーバ検索などの技術の概念、利用者の入力の解析で受け付ける語（`検索条件` のラベルなど）は「検索」のままでよい。オントロジーの Markdown の関係の行の `検索利用: 利用可 / 利用不可`（`ontology_build.py`）も変えない。生成した Markdown の hash を保存済みの下書き・公開版と比べて変更を判定する（`markdown_hash`）ため、文言を変えると既存のすべての業務プロファイルが「変更あり」になり再公開が要る（#1197）。同じ i18n-key-contract のテストが、SQL 生成の画面の主な文言・旧称の言い回し・AI 要件確認（`ontology_clarification.py`）の文言を検査する。
- コード内のコメント/ドキュメントは日本語で可。識別子・型名は英語。

## 技術スタック(確定)

### AI/ML 層
| 用途 | 採用 | 重要な制約 |
|---|---|---|
| LLM(SQL 生成・構造化出力) | **OCI Enterprise AI** | ⚠️ **OCI Generative AI の chat 推論 API は使わない**。Enterprise AI を使用 |
| VLM(オントロジー構築の資料の PDF ページの読み取り) | **OCI Enterprise AI** | 同上。chat エンドポイントではなく Enterprise AI |
| 埋め込み(embedding) | **OCI Generative AI**(Cohere Embed v4) | オントロジーのノードの埋め込み。多言語(日本語可)・**1536 次元** |
| リランク(rerank) | **OCI Generative AI**(Cohere Rerank v4 fast) | 3製品共通のモデル設定。NL2SQL ではモデル設定の接続テストだけが呼ぶ |

> LLM/VLM と embedding/rerank で **使用サービスが異なる**点に注意。実装は両者を別クライアント(`app/clients/oci_enterprise_ai.py` / `app/clients/oci_genai.py`)として抽象化する。

### データ層(Oracle 集約)
- **Oracle AI Database** — 業務データ・NL2SQL の状態(業務プロファイル・オントロジー・履歴・job 等)の正本。Oracle AI Vector Search でベクトル検索を DB 内に一体化。**外部ベクトル DB(pgvector/Qdrant 等)は提案・導入しない。** ベクトル列は埋め込みに合わせ **`VECTOR(1536, FLOAT32)`**。
- **アップロード保存先** — local / OCI Object Storage を共通のシステム設定で選ぶ。

### バックエンド
- **Python 3.12 + FastAPI**(ASGI、非同期)。
- **Pydantic v2** — LLM 構造化出力のスキーマ定義。
- SDK: **oci** / **python-oracledb**。SQL の解析は **sqlglot**、オントロジーは **rdflib** / **pyshacl**。
- サーバ: Uvicorn(+ Gunicorn for 本番)。
- 依存管理: **uv**。

### フロントエンド
- **Vite + React Router + TypeScript**。
- **Tailwind CSS v4 + `@engchina/production-ready-ui`**(共有コンポーネント)。関係グラフは `@xyflow/react`。
- 通信: REST(時間のかかる処理は永続 job のポーリング)。
- 状態管理: TanStack Query + Zustand。

### 横断
- 観測性: 構造化ログ + Prometheus(`prometheus-client`)。
- 品質: pytest / pytest-cov / ruff（lint・整形）/ mypy / bandit / pip-audit / `node --test`(ロジックテスト)/ Playwright。
- インフラ: 自前のコードは Docker を使わずネイティブで動かす(開発は `uv run` / `npm run dev`、本番は Compute 上の systemd + Nginx。Dockerfile・compose は持たない。#286 / #356)。配備は Terraform(OCI Resource Manager。monorepo root の統合 stack `terraform/stack/`、#217)と `init_script.sh`。

## UI/UX 開発ルール

- **UI/UX に関する作業(設計・実装・レビュー・改善)は必ず `ui-ux-pro-max` skill を使う。** 画面・コンポーネント・スタイル・配色・タイポグラフィ・アクセシビリティはこの skill の知見に従うこと。
- デザインは日本語 UI 前提でレイアウト(行高・禁則・フォント)を検証する。
- UI/UX に関わる機能追加・修正は、**必ず Playwright で実画面を表示して確認・テストする**。主要導線、レスポンシブ表示、キーボード操作、アクセシビリティ上の破綻がないことを確認する。
- UI/UX 変更ごとに Playwright テスト(e2e / interaction / 必要に応じた visual check)を追加・更新し、完了前に実行する。

### UI/UX 構造

**基本原則:**
- **レイアウト/UI 構造**(情報設計・画面構成・ナビ導線・状態遷移・文言設計)は、本プロジェクト内の `frontend/src` と `src/lib/i18n` / `src/lib/routes` を正本として継続的に整備する。**見た目(トークン)と共有コンポーネントは platform の `docs/design-system/` を正本とする**(「デザインシステム / UI」節)。
- **技術選定は本 AGENTS.md の確定スタックを正とする。** フロントエンドのフレームワーク・ライブラリ・パターンは Vite + React Router + TypeScript + Tailwind + `@engchina/production-ready-ui` + TanStack Query + Zustand を採用する。

**ナビゲーション/画面構成**:
- 折りたたみ可能な**サイドナビ**。構成の正本は `frontend/src/components/layout/nav-config.ts`:
  - **AI 活用** / **データ準備** / **改善・運用**: NL2SQL の業務機能。
  - **セキュリティ設定**: 権限管理（ロールごとの機能権限・業務プロファイル利用権限）/ Deep Data Security（NL2SQL 固有。#206。セクション名は 3 製品で同じ。#658）。
  - **ユーザーとロール**: ユーザー管理 / ロール管理（3製品で共通。画面と API 契約は platform の共有パッケージ。#206）。
  - **運用設定**: システムテーブル管理 / Select AI Credential（NL2SQL 固有の運用項目。Select AI Credential はデータベース設定から分けた。#658）。
  - **システム設定**: OCI 認証 / アップロード保存先 / モデル / データベース / 外観（3製品で共通。画面と API は platform の共有パッケージ）。
- サイドナビのラベルは**日本語第一**とする。正式名が長い項目だけ短縮形(`sidebarLabelKey`)で表示し(例: `システムテーブル管理` → `システムテーブル`)、**ページタイトル/`aria-label` は正式名を維持**する(`nav.*` と `nav.*.sidebar` の二段管理)。
- 画面の構成は共有の `AppShell` / `PageHeader` / `PageBody` を使う([../AGENTS.md](../AGENTS.md)「画面の構成」)。

**状態モデル / UX パターン**:
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
- アプリ固有のルールを `.oxlintrc.json` に足す場合は、`design-system/restricted-syntax` / `no-restricted-imports` を同じ名前で上書きしない（adherence のセレクタが消える）。アプリ固有のセレクタは、platform のプラグインを別名で再公開した `frontend/lint/app-design-system-plugin.mjs` の `nl2sql-design-system/restricted-syntax` に書く（現在は無い。手書き `<table>` の禁止（#530）は 3 製品で同じ規則として platform の adherence へ移した。#800）。
- 誤検知や正当な例外（グラフ座標など px が正しい幾何値）は `// oxlint-disable-next-line <rule>` に理由コメントを添えて局所的に除外する。ルール自体を緩める必要がある場合は platform の adherence 設定に Issue を立てる。

### NL2SQL 固有

- **残すもの（ドメイン固有）**
  - `WorkflowProgressStrip`
  - オントロジー / 関係グラフ。配色は `--color-graph-*`（種別 = 塗り、状態 = 線）
- `--font-mono`（Google Sans Code）は platform の共有 tokens が定義する。書体ファイルは本リポジトリが `@fontsource/google-sans-code` で自前ホストする。

## ディレクトリ構成

```
backend/                  FastAPI アプリ（uv の venv。pyproject.toml に ruff / mypy / pytest の設定）
  app/
    main.py               エントリ（pr_backend_core の create_app、ルーター、lifespan）
    settings.py           設定（PLATFORM_* は platform/.env、NL2SQL_* は backend/.env。#211）
    api/                  ルーターの集約・health / readiness・problem 応答・run_sync_io（concurrency.py）
    features/nl2sql/      NL2SQL の業務機能（SQL 生成・DB 管理・業務プロファイル・オントロジー・
                          フィードバック・評価・合成データ・job と worker）
    features/schema/      DB 構造の API
    features/settings/    システム設定の API（platform の pr_system_settings の共通 API ＋
                          Select AI の資格情報・システムテーブル管理）
    features/mcp/         他製品（Agent）から呼ぶ MCP（POST /api/mcp）
    security/             権限管理・Deep Data Security・認可の判定
    clients/              oci_enterprise_ai（LLM/VLM）/ oci_genai（embed・rerank）/ oracle
    cli/                  migration・system schema・各 worker のエントリ
    schemas/              設定 API のスキーマ
  migrations/             Oracle の DDL（番号順）
  scripts/                実 Oracle / Select AI への手動の接続確認
  tests/                  pytest
frontend/                 Vite + React Router + Tailwind v4 + @engchina/production-ready-ui
  src/App.tsx             ルート定義
  src/features/nl2sql/    NL2SQL の画面・状態・ロジック（ontology/ はオントロジーの画面）
  src/features/security/  ログイン・権限管理・Deep Data Security の画面
  src/components/         layout（AppSidebar・nav-config.ts）/ settings / system（DatabaseGate）
  src/lib/                api / routes / i18n / 作業状態・未保存変更のガード
  lint/                   NL2SQL 固有の oxlint プラグイン
  tests/                  ロジックテスト（*.test.ts、node --test）/ e2e（Playwright）
docs/                     NL2SQL 固有の設計・運用・テスト計画・UI の差分 spec
scripts/                  開発・Compute 上の起動 / 更新 / ログのスクリプトと、そのテスト
init_script.sh            Compute への配備（systemd の backend・worker と Nginx）
```

## 開発コマンド

```bash
# backend（http://localhost:8010/api/health）
cd backend && uv sync
uv run uvicorn app.main:app --reload --port 8010   # または ../scripts/start-backend.sh
uv run pytest tests/test_<対象>.py                  # 関係するテスト（全件は CI）
uv run ruff format --check . && uv run ruff check . && uv run mypy .   # 整形/lint/型

# frontend（http://localhost:3001）
cd frontend && npm install
BACKEND_URL=http://127.0.0.1:8010 npm run dev   # BACKEND_URL 未指定なら /api は proxy せず 404（hermetic。起動時に警告）
npm run lint && npm run build
node --import jiti/register --test tests/<対象>.test.ts   # ロジックテスト
npx playwright test tests/e2e/<対象>.spec.ts               # e2e（関係する spec だけ）
```

時間のかかる処理（DB 構造の再取得・SQL 生成評価・オントロジー構築・合成データ生成）は永続 job にし、API は job を投入して返す。本番では `init_script.sh` が作る systemd の worker（`production-ready-nl2sql-{schema-refresh,quality-evaluation,ontology,synthetic}-worker`）が処理する（[docs/compute-operations.md](./docs/compute-operations.md)）。

## 横断的な保守・セキュリティ契約

3製品共通の契約（更新 API とデータの所有範囲、認可と状態遷移のサーバー側強制、i18n の変更と E2E の locator、ページ遷移・再読込のときの状態保持、未保存変更の離脱ガード）は platform の [UX 契約](../platform/docs/ux-contracts/README.md)（[cross-cutting.md](../platform/docs/ux-contracts/cross-cutting.md) / [workspace-state.md](../platform/docs/ux-contracts/workspace-state.md)）を正本とする。本節には NL2SQL 固有の規則だけを書く。

> 本節は [Issue #168](https://github.com/engchina/no.1-production-ready-nl2sql/issues/168) / [PR #178](https://github.com/engchina/no.1-production-ready-nl2sql/pull/178)、[Issue #169](https://github.com/engchina/no.1-production-ready-nl2sql/issues/169) / [PR #179](https://github.com/engchina/no.1-production-ready-nl2sql/pull/179)、[Issue #170](https://github.com/engchina/no.1-production-ready-nl2sql/issues/170) / [PR #180](https://github.com/engchina/no.1-production-ready-nl2sql/pull/180)、[Issue #181](https://github.com/engchina/no.1-production-ready-nl2sql/issues/181) / [PR #182](https://github.com/engchina/no.1-production-ready-nl2sql/pull/182)、[Issue #183](https://github.com/engchina/no.1-production-ready-nl2sql/issues/183) / [PR #189](https://github.com/engchina/no.1-production-ready-nl2sql/pull/189)、[Issue #184](https://github.com/engchina/no.1-production-ready-nl2sql/issues/184)、[Issue #185](https://github.com/engchina/no.1-production-ready-nl2sql/issues/185) で得た再発防止策を、実装時に検証可能な契約としてまとめたものである。

### role の権限昇格の防止

- `SYSTEM_ADMIN` 以外が role を更新するとき、追加される実効権限 `expand_permissions(new) - expand_permissions(current)` は actor 自身の実効権限の部分集合でなければならず、違反は `403` で拒否する。暗黙 permission と `grants_all_profile_access` 相当の profile 管理権限も展開後に評価し、自分・他人いずれの role 経由でも権限昇格を許可しない。未保持の既存権限を削除する操作は妨げない。
- role 作成は未割当のため現行どおり許可できるが、user への role 割当では既存の実効権限部分集合 check を必須とする。role/assignment 変更が次 request から再計算される前提で、変更直後の許可・拒否まで API 回帰テストに含める。

### 状態保持の実装範囲（Issue #298）

- 保存の namespace・期限・対象画面・検証の spec は [docs/frontend-workspace-state-spec.md](./docs/frontend-workspace-state-spec.md) に書く。SQL・生成 SQL・`ADMIN_EXECUTE` などの確認語は共通契約の「入力」「生成した結果」「確認語」として扱う。

## テスト/検証方針

- 開発時は実装と同時に対応するテストコードを追加・更新する。バックエンドは pytest、フロントエンドのロジックは `node --test`（`frontend/tests/*.test.ts`）、UI/UX とユーザー操作は Playwright を基本とする。
- 変更後は該当範囲の lint・型チェック・テストを実行し、完了報告に実行結果を明記する。実行できない場合は理由と代替確認を明記する。
- ローカルは変更範囲の検査にする（#339）。backend は関係するテストファイル（`uv run pytest tests/test_<対象>.py`）と `uv run pytest --lf -x`（全件は約 2 分かかる）、frontend は `npm run lint` / `npm run build` と関係するロジックテスト（`node --import jiti/register --test tests/<対象>.test.ts`）、e2e は関係する spec だけ（`npx playwright test tests/e2e/<対象>.spec.ts`。全件は 8 shard で 10 分超）。backend の全テスト・`mypy .`・`pip-audit`、Playwright の smoke / 全件は CI（`NL2SQL / Backend`・`NL2SQL / Frontend`・`NL2SQL / E2E smoke`）と nightly に任せ、PR の `検証結果` にその job 結果を引用してよい。
- UI/UX に関わるすべての機能は、Playwright でブラウザ表示を確認し、少なくとも主要ユーザーフロー、モバイル幅(例: 375px)、デスクトップ幅、重要な空/読込/エラー状態を検証する。

## コーディング規約・重要ルール

1. **LLM/VLM 呼び出しは OCI Enterprise AI 経由のみ**。OCI Generative AI の chat API を LLM/VLM に使わない。
2. **embedding/rerank は OCI Generative AI(Cohere)経由**。
3. **ベクトル検索は Oracle AI Vector Search**。外部ベクトル DB を導入しない。
4. シークレット(OCI 認証・DB 接続)は `.env` 経由。**ハードコード禁止**、コミットしない。
5. LLM 出力は Pydantic スキーマで検証してから DB 保存する。
6. UI 作業は `ui-ux-pro-max` skill を使用。
7. 機能開発では、実装と同時にテストコードを追加・更新する。
8. 変更後は該当範囲の lint・型チェック・テストを実行してから完了とする。
9. UI/UX に関わる変更は Playwright で画面確認とテストを実施してから完了とする。
10. このスタックから外れる提案(別 LLM プロバイダ、別 DB 等)をする場合は、必ず理由を添えてユーザに確認する。
11. **Backend の ASGI event loop 上で同期 I/O を直接実行しない。** 詳細は [docs/backend-concurrency-contract.md](./docs/backend-concurrency-contract.md) を正とする。同期 domain service / Oracle repository / file・CLOB・Excel 処理 / OCI SDK 呼び出しは sync FastAPI route(`def`)、CLI/worker、または `backend/app/api/concurrency.py` の `run_sync_io(...)` 内だけで実行する。`async def` route は `UploadFile`・SSE/WebSocket・async client など本当に `await` が必要な場合に限定する。
