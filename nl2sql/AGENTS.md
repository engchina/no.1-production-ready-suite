# AGENTS.md — Production Ready NL2SQL

> NL2SQL（`nl2sql/`）固有のルール。GitHub 運用・Issue / PR 規約・CI・デザインシステム・共通の技術方針（言語・AI / DB の分担・シークレット・LLM 出力の検証・ローカルの検証の範囲・UI 変更の検証）は、root の [../AGENTS.md](../AGENTS.md) を先に適用する。ルールを変えるときはこのファイル（共通ルールは root）を編集する。
> Issue の label は `product:nl2sql`、PR title の scope は `nl2sql`。NL2SQL の docs・コメントの `#N` のうち、2026-09-25 の monorepo 統合より前のものは旧 repo `engchina/no.1-production-ready-nl2sql` の番号。

## プロジェクト概要

> **製品名と固有 namespace は `Production Ready NL2SQL` / `NL2SQL` を正とする。**
> 新規 DB object、cookie、storage key、context、UI の製品ラベルへ汎用 `RAG` prefix を使用しない。
> `GraphRAG`、`RAGAS`、`RAGFlow` など技術・製品の固有名詞はこの置換対象外とする。

Oracle AI Database の業務データに、自然言語で SQL を生成・検証・実行する SQL 専用の NL2SQL の参照実装。特定の業務ドメインに固定しない。

- **AI 活用**: チャット（自然言語による SQL の多輪の生成と実行。SQL 生成の画面と同じく送信のジョブが実行まで行う）、自然言語の質問から SQL を生成して実行する（SQL 生成）、SELECT SQL の直接実行、SQL から質問の生成、実行履歴。
- **データ準備**: 管理 SQL の実行、テーブル・ビュー・データ・コメント・アノテーション・ドメインの管理、用語・同義語、共通ルール、サンプルデータ。
- **改善・運用**: 業務プロファイル（SQL 生成の対象範囲）、オントロジー構築、フィードバック、質問分類モデル、SQL 生成評価。

新機能の設計・比較検討の前に、外部 OSS / 研究プロジェクトの一覧 [docs/reference-rag-projects.md](./docs/reference-rag-projects.md) の該当カテゴリを参照する。優れた点は取り込むが、確定スタック（下記）へ必ず再マッピングし、外部ベクトル DB・別 LLM プロバイダをそのまま導入しない（逸脱するなら理由を添えてユーザーに確認する）。

## 用語

- 利用者が入力する自然言語（SQL 生成・チャット・AI 要件確認の入力など）は **`質問`**（root「言語」）。`クエリ` は SQL・技術の概念（サブクエリなど）にだけ使い、旧称の「検索クエリ」も使わない。`frontend/tests/i18n-key-contract.test.ts` が辞書の文言を検査する。
- 利用者の自然言語の入力とその実行を **「検索」と呼ばない**（#1189）。入力は「質問」、それによる SQL の生成と実行は「SQL を生成して実行」（ボタン）・「SQL の生成と実行」（説明の文）、その結果は「実行結果」と呼ぶ（旧称「NL2SQL 検索」「検索実行」「検索結果」「検索画面」は使わない）。
  - 「検索」のままでよいもの: 一覧の絞り込みの検索欄（`SearchField`・「検索語をクリア」）、選択欄の候補の検索、類似検索・ベクトル検索・埋め込み検索・オントロジーのサーバ検索などの技術の概念、利用者の入力の解析で受け付ける語（`検索条件` のラベルなど）。
  - オントロジーの Markdown の関係の行の `検索利用: 利用可 / 利用不可`（`ontology_build.py`）は変えない。生成した Markdown の hash（`markdown_hash`）で変更を判定するため、文言を変えると既存のすべての業務プロファイルが「変更あり」になり再公開が要る（#1197）。
  - 同じ i18n-key-contract のテストが、SQL 生成の画面の主な文言・旧称の言い回し・AI 要件確認（`ontology_clarification.py`）の文言を検査する。

## 技術スタック（確定。NL2SQL 固有の分）

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
- 配備は root の「配備」（1 台の Compute。NL2SQL の手順は `init_script.sh`）。

## UI/UX

- 日本語 UI のレイアウト（行高・禁則・フォント）を検証する。
- UI/UX の機能追加・修正では、Playwright のテスト（e2e / interaction / 必要に応じた visual check）を追加・更新し、完了前に実行する。主要導線・レスポンシブ・キーボード操作・アクセシビリティの破綻が無いことを確かめる。
- 情報設計・画面構成・ナビ導線・状態遷移・文言設計は、`frontend/src` と `src/lib/i18n` / `src/lib/routes` を正本として整備する（見た目と共有部品は platform の `docs/design-system/`）。
- サイドナビ（折りたたみ可）の構成の正本は `frontend/src/components/layout/nav-config.ts`:
  - **AI 活用** / **データ準備** / **改善・運用**: NL2SQL の業務機能。
  - **セキュリティ設定**: 権限管理（ロールごとの機能権限・業務プロファイル利用権限）/ Deep Data Security。
  - **ユーザーとロール** / **システム設定**（OCI 認証 / アップロード保存先 / モデル / データベース / 外観と証明書）: 3 製品で共通（platform の共有パッケージ）。
  - **運用設定**: システムテーブル管理 / Select AI Credential（データベース設定から分けた）。
- サイドナビのラベルは日本語第一。正式名が長い項目だけ短縮形（`sidebarLabelKey`）で表示し（例: `システムテーブル管理` → `システムテーブル`）、**ページタイトル / `aria-label` は正式名を維持**する（`nav.*` と `nav.*.sidebar` の二段管理）。
- ページネーション・確認ダイアログ・トースト・一括選択（全選択 / 選択件数）は共通の部品を使い、データ取得・通知・ページングは hooks に集約する。
- NL2SQL 固有の画面の差分は [docs/frontend-messaging-spec.md](./docs/frontend-messaging-spec.md) / [docs/frontend-button-spec.md](./docs/frontend-button-spec.md) / [docs/frontend-page-archetypes-spec.md](./docs/frontend-page-archetypes-spec.md) に書く。メッセージは UX 契約の 6 チャネル / 4 トーン / i18n 規約に従う。アクションは共通の `Button` で、類似機能は同じ size・variant にする。

### lint

- `frontend/.oxlintrc.json` は platform の adherence 設定を `extends` で相対パス参照する（`../../platform/…`。ルールとプラグインはコピーしない）。CI の `NL2SQL / Frontend` は `npm run lint`（`oxlint --deny-warnings`）を実行する。
- アプリ固有のセレクタは、platform のプラグインを別名で再公開した `frontend/lint/app-design-system-plugin.mjs` の `nl2sql-design-system/restricted-syntax` に書く（今は無い）。`design-system/restricted-syntax` / `no-restricted-imports` を同じ名前で上書きしない。
- グラフ座標など px が正しい幾何値は `// oxlint-disable-next-line <rule>` と理由で局所的に除外する。

### NL2SQL 固有の部品

- ドメイン固有として残すもの: `WorkflowProgressStrip`、オントロジー / 関係グラフ（配色は `--color-graph-*`。種別 = 塗り、状態 = 線）。
- `--font-mono`（Google Sans Code）は共有の tokens が定義し、書体ファイルは `@fontsource/google-sans-code` で自前ホストする。

## ディレクトリ構成

```
backend/                  FastAPI アプリ（uv の venv。pyproject.toml に ruff / mypy / pytest の設定）
  app/
    main.py               エントリ（pr_backend_core の create_app、ルーター、lifespan）
    settings.py           設定（PLATFORM_* は platform/.env、NL2SQL_* は backend/.env）
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
frontend/                 Vite + React Router + Tailwind v4 + 共有 UI
  src/App.tsx             ルート定義
  src/features/nl2sql/    NL2SQL の画面・状態・ロジック（ontology/ はオントロジーの画面）
  src/features/security/  ログイン・権限管理・Deep Data Security の画面
  src/components/         layout（AppSidebar・nav-config.ts）/ settings / system（DatabaseGate）
  src/lib/                api / routes / i18n / 作業状態・未保存変更のガード
  lint/                   NL2SQL 固有の oxlint プラグイン
  tests/                  ロジックテスト（*.test.ts、node --test）/ e2e（Playwright）
docs/                     NL2SQL 固有の設計・運用・テスト計画・UI の差分 spec
scripts/                  開発・Compute 上の起動 / 更新 / ログのスクリプトと、そのテスト
init_script.sh            NL2SQL の配備の手順（systemd の backend・worker。platform/deploy/suite-init.sh が呼ぶ）
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

- CI の job は `NL2SQL / Backend`・`NL2SQL / Frontend`・`NL2SQL / E2E smoke`。backend は pytest、frontend のロジックは `node --test`（`frontend/tests/*.test.ts`）、UI とユーザー操作は Playwright。
- 時間のかかる処理（DB 構造の再取得・SQL 生成評価・オントロジー構築・合成データ生成）は永続 job にし、API は job を投入して返す。本番では `init_script.sh` が作る systemd の worker（`production-ready-nl2sql-{schema-refresh,quality-evaluation,ontology,synthetic}-worker`）が処理する（[docs/compute-operations.md](./docs/compute-operations.md)）。

## 横断的な保守・セキュリティ契約

3 製品共通の契約（更新 API とデータの所有範囲、認可と状態遷移のサーバー側強制、i18n の変更と E2E の locator、状態保持、未保存変更の離脱ガード）は platform の [UX 契約](../platform/docs/ux-contracts/README.md)（[cross-cutting.md](../platform/docs/ux-contracts/cross-cutting.md) / [workspace-state.md](../platform/docs/ux-contracts/workspace-state.md)）が正本。ここには NL2SQL 固有の規則だけを書く（旧 repo の Issue #168〜#185 の再発防止策）。

### role の権限昇格の防止

- `SYSTEM_ADMIN` 以外が role を更新するとき、追加される実効権限 `expand_permissions(new) - expand_permissions(current)` は actor 自身の実効権限の部分集合でなければならず、違反は `403` で拒否する。暗黙 permission と `grants_all_profile_access` 相当の profile 管理権限も展開後に評価し、自分・他人いずれの role 経由でも権限昇格を許可しない。未保持の既存権限を削除する操作は妨げない。
- role 作成は未割当のため現行どおり許可できるが、user への role 割当では既存の実効権限部分集合 check を必須とする。role/assignment 変更が次 request から再計算される前提で、変更直後の許可・拒否まで API 回帰テストに含める。

### 状態保持の実装範囲

- 保存の namespace・期限・対象画面・検証の spec は [docs/frontend-workspace-state-spec.md](./docs/frontend-workspace-state-spec.md) に書く。SQL・生成 SQL・`ADMIN_EXECUTE` などの確認語は共通契約の「入力」「生成した結果」「確認語」として扱う。

## コーディング規約

1. **LLM / VLM は OCI Enterprise AI 経由のみ**（OCI Generative AI の chat API を使わない）。embedding / rerank は OCI Generative AI（Cohere）、ベクトル検索は Oracle AI Vector Search。
2. **Backend の ASGI event loop 上で同期 I/O を直接実行しない。** 詳細は [docs/backend-concurrency-contract.md](./docs/backend-concurrency-contract.md) を正とする。同期 domain service / Oracle repository / file・CLOB・Excel 処理 / OCI SDK 呼び出しは sync FastAPI route（`def`）、CLI / worker、または `backend/app/api/concurrency.py` の `run_sync_io(...)` 内だけで実行する。`async def` route は `UploadFile`・SSE / WebSocket・async client など本当に `await` が必要な場合に限定する。
