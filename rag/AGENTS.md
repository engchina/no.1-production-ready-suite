# AGENTS.md — Production Ready NL2SQL

> このファイルは **Claude Code と Codex の両方が参照する正本(single source of truth)** です。
> `CLAUDE.md` はこのファイルを `@AGENTS.md` で取り込みます。ルールを変更する際は **必ずこのファイルを編集**してください。

## プロジェクト概要

**A production-ready NL2SQL (Natural Language to SQL) reference implementation covering schema ingestion, schema linking, knowledge/few-shot grounding, SQL generation, guardrails, self-correction, agentic planning, result rendering, evaluation, observability, and deployment best practices — centered on Oracle Select AI / Select AI Agent.**

本プロジェクトは特定業務ドメインに固定せず、自然言語からの SQL 生成(NL2SQL)システムを本番品質で構築するための参照実装を目標とする。
パイプライン: スキーマ取込 → 注釈/用語集整備 → Select AI プロビジョニング → (意味キャッシュ照会)→ ルーティング(profile/複雑度)→ 曖昧性確認 → スキーマリンク → 知識/例示グラウンディング → **SQL 生成(Select AI / Select AI Agent)** → ガードレール検証(read-only 物理強制 / object 制限 / EXPLAIN / 意味検証)→ 人手プレビュー確認 → 実行 → 結果整形(narrate/可視化)→ 評価・観測。

中核は **Oracle Autonomous Database の Select AI / Select AI Agent**(`DBMS_CLOUD_AI` / `DBMS_CLOUD_AI_AGENT`)で、NL→SQL の生成・実行・説明・会話を **DB 内**で行う。アプリ側は Select AI 資産(credential/profile/tool/agent/task/team)の冪等プロビジョニング、ガードレール、ヒューマンレビュー、評価、観測を担う。

### 参考プロジェクトカタログ

機能設計・実装方針の調査源として、外部 OSS / 研究プロジェクト / Oracle ネイティブ機能 / 自社参照実装の一覧を **[docs/reference-nl2sql-projects.md](./docs/reference-nl2sql-projects.md)** に整理している。
新機能の設計・比較検討の前に該当カテゴリ(自社参照実装 / NL2SQL プロダクト / マルチエージェント研究 / Oracle ネイティブ / 評価ベンチ / パイプラインアダプター対応表)を参照すること。
特に **engchina/No.1-SQL-Assist** と **engchina/no.1-denpyo-toroku-kun** の Select AI / Select AI Agent 連携が中核設計源。
**各プロジェクトの優れた点は取り込むが、確定スタック(下記)へ必ず再マッピングし、外部ベクトル DB・別 LLM プロバイダ・別 NL2SQL SaaS をそのまま導入しない**(逸脱時は §コーディング規約 に従い理由を添えて要確認)。

## 言語・ローカライズ方針

- **システムの主要言語は日本語**。UI 文言・エラーメッセージ・通知・LLM への指示/出力・SQL 説明(narrate)はすべて日本語を前提とする(Select AI の `task` instruction も日本語応答を指定)。
- 国際化は最初から考慮する(ハードコードせず i18n 経由)。ただし第一言語は日本語。
- コード内のコメント/ドキュメントは日本語で可。識別子・型名は英語。

## 技術スタック(確定)

### AI/ML 層
| 用途 | 採用 | 重要な制約 |
|---|---|---|
| **NL→SQL 生成・実行・説明・会話** | **Oracle Select AI / Select AI Agent**(`DBMS_CLOUD_AI` / `DBMS_CLOUD_AI_AGENT`) | NL2SQL の**中核エンジン**。`SELECT AI showsql/runsql/narrate/explainsql/chat` と `RUN_TEAM`。DB 内で実行 |
| **Select AI が使う LLM(モデルの頭脳)** | **OCI Enterprise AI**(Select AI profile から `oci_endpoint_id` で参照) | ⚠️ profile は `provider=oci` のまま、**`oci_endpoint_id` で Enterprise AI / 専用エンドポイントを指す**。**OCI Generative AI の汎用 chat 推論 API を NL2SQL 生成に直接は使わない**(Select AI 経由でのみ) |
| **アプリ側 LLM**(スキーマ注釈生成・用語抽出・結果説明 fallback・Agentic 計画) | **OCI Enterprise AI** | Select AI を介さないアプリ側の構造化抽出・推論。chat エンドポイントではなく Enterprise AI |
| 埋め込み(embedding) | **OCI Generative AI**(Cohere Embed v4) | 例示 NL-SQL ペア / 用語集の意味検索。多言語(日本語可)・**1536 次元** |
| リランク(rerank) | **OCI Generative AI**(Cohere Rerank v4 fast) | スキーマリンク・例示検索の精度の要 |

> NL2SQL 生成は **Select AI(DB 内)**、その背後のモデルは **Enterprise AI**(`oci_endpoint_id` 経由)、embedding/rerank は **OCI GenAI Cohere** と **使用サービスが異なる**点に注意。実装は別クライアントとして抽象化する。

### データ層(Oracle 集約)
- **Oracle Autonomous AI Database(Select AI 対応)** — `DBMS_CLOUD_AI(_AGENT)` で NL2SQL を DB 内に一体化。会話履歴は `USER_CLOUD_AI_CONVERSATION_PROMPTS` 等。
- **Oracle 26ai AI Vector Search** — 例示 NL-SQL ペア・用語集・スキーマ doc の意味検索(Knowledge/Few-shot アダプター)。**外部ベクトル DB(pgvector/Qdrant 等)は提案・導入しない。** ベクトル列は **`VECTOR(1536, FLOAT32)`**。
- **OCI Object Storage** — スキーマ doc・例示 CSV・学習 artifact の保管。

### バックエンド
- **Python 3.12 + FastAPI**(ASGI、非同期)。**共有 backend core(`pr_backend_core` / production-ready-backend-core)** を土台に NL2SQL サービスとして構築。
- **Pydantic v2** — LLM/Select AI 構造化出力(生成 SQL・説明・候補)のスキーマ定義と検証。
- SDK: **oci** / **python-oracledb**。
- サーバ: Uvicorn(+ Gunicorn for 本番)。
- 依存管理: **uv**。

### フロントエンド
- **Vite + React Router + TypeScript**。**共有 UI(`@engchina/production-ready-ui`)** を採用し RAG / NL2SQL / Agent で UI を統一。
- **Tailwind CSS + shadcn/ui**。
- 通信: REST + **SSE/WebSocket**(SQL 生成・実行・narrate のストリーミング)。
- 状態管理: TanStack Query + Zustand。

### 横断
- 観測性: **Langfuse**(LLM/Select AI トレース・コスト・SQL 生成成否)+ Prometheus + OpenTelemetry。
- 品質: pytest / pytest-cov / ruff / black / mypy / bandit / pip-audit / Vitest / Playwright。
- インフラ: Docker Compose(開発)→ OKE / Container Instances(本番)、Terraform(OCI Resource Manager)。

## UI/UX 開発ルール

- **UI/UX に関する作業(設計・実装・レビュー・改善)は必ず `ui-ux-pro-max` skill を使う。** 画面・コンポーネント・スタイル・配色・タイポグラフィ・アクセシビリティはこの skill の知見に従うこと。
- デザインは日本語 UI 前提でレイアウト(行高・禁則・フォント)を検証する。SQL/結果テーブルは等幅・横スクロール・桁揃えを考慮する。
- UI/UX に関わる機能追加・修正は、**必ず Playwright で実画面を表示して確認・テストする**。主要導線(NL 入力→SQL プレビュー→確認→実行→結果)、レスポンシブ表示、キーボード操作、アクセシビリティ上の破綻がないことを確認する。
- UI/UX 変更ごとに Playwright テスト(e2e / interaction / 必要に応じた visual check)を追加・更新し、完了前に実行する。

### UI/UX 構造

**基本原則:**
- **レイアウト/UI 構造**(情報設計・画面構成・ナビ導線・状態遷移・文言設計)は、本プロジェクト内の `frontend/src` と `src/lib/i18n` / `src/lib/routes` を正本として継続的に整備する。
- **技術選定は本 AGENTS.md の確定スタックを正とする。** フロントエンドは Vite + React Router + TypeScript + Tailwind + shadcn/ui + TanStack Query + Zustand + 共有 UI package を採用する。

**ナビゲーション/画面構成**:
- 折りたたみ可能な**サイドナビ**。4 セクション構成:
  - **データ基盤**: ダッシュボード/データソース接続/スキーマカタログ(テーブル・ビュー・列・制約)/注釈・用語集管理/Select AI プロビジョニング状態。
  - **NL2SQL**: NL2SQL コンソール(対話)/業務アシスタント (Assistant)/クエリ履歴/SQL 学習/NL2SQL 評価。
  - **NL2SQL パイプライン**: パイプライン各段階を切り替えるアダプター群を**パイプライン順**に並べる — スキーマ取込(Schema Source)→ スキーマリンク(Schema Linking)→ 知識/例示(Knowledge)→ ルーティング(Router)→ 曖昧性解決(Clarify)→ 生成(Generation)→ ガードレール(Guardrail)→ 自己修正(Self-Correction)→ エージェント計画(Agentic)→ キャッシュ(Cache)→ 結果整形(Result)→ 評価(Evaluation)。**これらは「設定」ではなくパイプライン挙動の切替であるため独立セクションに置く**(インフラ設定と混在させない)。
  - **システム設定**: OCI 認証/データベース接続(ADB wallet)/モデル(Select AI profile・`oci_endpoint_id`)/認証情報(Select AI credential)/Object Storage。
- サイドナビのラベルは**日本語第一**とし、パイプライン各段階は `スキーマリンク (Schema Linking)` のように「日本語+英語正式名」併記の短縮形(`sidebarLabelKey`)で表示する。一方**ページタイトル/`aria-label` は正式名(例: `Schema Linking アダプター`)を維持**する(`nav.*` と `nav.*.sidebar` の二段管理。新アダプター追加時も同様にする)。
- レイアウト構成要素: header / footer / breadcrumb / sideTabBar / tabs。
- 主要画面: ダッシュボード(主要機能ハブ + メトリクスカード + NL2SQL フロー + 最近のクエリ + システム情報)、データソース接続、スキーマカタログ、NL2SQL コンソール、各種設定、**クエリ作業領域(QueryWorkspace: NL 入力 / 生成 SQL プレビュー / 実行結果 / narrate / 候補・修正履歴)**。

**状態モデル / UX パターン**:
- **NL2SQL は 2 段階(生成 → 人がプレビュー確認 → 実行)を方針とする。** Select AI は既定で **`showsql` で SQL を生成・提示し、`QueryWorkspace` で人が SQL を確認・(必要なら手修正)・承認してから `runsql` で実行**する。**read-only を既定**とし、破壊的 SQL(DDL/DML)はガードレールでブロックする。
- クエリ状態: `DRAFT(NL 入力)→ GENERATED(SQL 生成済)→ REVIEW(確認待ち)→ EXECUTED(実行済)`(+ `ERROR` / `BLOCKED`)を **StatusBadge** で可視化する。
- Select AI 資産状態: `REGISTERED(スキーマ登録)→ ANNOTATED(注釈/用語集付与)→ PROVISIONED(profile/team ready)→ ERROR`。`config_hash` で drift を検知し、変更時のみ再プロビジョニングする。
- ページネーション、確認ダイアログ、トースト通知、一括選択(全選択/選択件数表示)を共通コンポーネント化。
- **メッセージ機構(通知・成功/エラー・フォーム検証・確認ダイアログ・空/読込/エラー状態)は [docs/frontend-messaging-spec.md](./docs/frontend-messaging-spec.md) を正本とする。** 関連 UI を新規実装・改修するときは必ず同 spec の 6 チャネル / 4 トーン / i18n 規約に従うこと。
- **ボタン(大きさ・スタイル・配置・命名)は [docs/frontend-button-spec.md](./docs/frontend-button-spec.md) を正本とする。** アクションは共通 `<Button>` を使い、size(sm/md/lg)・variant(primary/secondary/ghost/danger)・配置・aria-label/文言キー規則を揃える。SQL 実行など破壊的になりうる操作は `danger` 系 + 確認ダイアログを徹底する。
- データ取得・通知・ページングは hooks に集約する。状態管理は TanStack Query + Zustand を使う。

**タイポグラフィ/デザイン原則**:
- **日本語第一フォントスタック**: `"Noto Sans JP", "Roboto", system-ui, sans-serif`。本文ベース `font-size: 14px`。SQL/結果は等幅(`"JetBrains Mono", monospace` 等)。
- 落ち着いた業務系トーンを shadcn/ui のテーマで再現する。
- 文言は日本語(i18n 経由)で管理する。

## ディレクトリ構成

```
backend/                  FastAPI アプリ（共有 pr_backend_core 上に構築）
  app/
    main.py               エントリ（CORS, ルーター, lifespan）
    config.py             設定（pydantic-settings）
    logging_config.py     JSON 構造化ログ
    api/routes/           health / dashboard / schema / nl2sql / history / evaluation / settings
    clients/              oci_enterprise_ai(LLM) / oci_genai(embed,rerank)
                          / oracle(ADB/26ai) / select_ai(DBMS_CLOUD_AI(_AGENT) 呼出) / object_storage
    nl2sql/               schema_ingest / schema_linking / knowledge / router / clarify
                          / generation / guardrail / correction / agentic / cache
                          / result / evaluation / pipeline
    select_ai/            provisioning(credential/profile/tool/agent/task/team の冪等管理・config_hash)
    schemas/              common / schema_catalog / query / select_ai
  tests/                  pytest
  pyproject.toml          uv 管理、ruff/black/mypy/pytest 設定
packages/
  (共有 platform 由来) production-ready-backend-core(pr_backend_core)を依存採用
frontend/                 Vite + React Router + Tailwind v4 + shadcn/ui（共有 @engchina/production-ready-ui 採用）
  src/main.tsx            Vite エントリ
  src/App.tsx             React Router ルート定義
  src/globals.css         Tailwind v4 / shadcn/ui theme tokens
  src/components/         layout/Sidebar, StatusBadge, PageHeader, QueryWorkspace, providers
  src/lib/                routes / i18n(ja) / utils
docker-compose.yml        backend + frontend（+ 必要に応じ評価 worker）
```

## Select AI プロビジョニング(重要)

NL2SQL の中核は Oracle Autonomous Database の Select AI / Select AI Agent。アプリは **データソース/業務ドメイン単位**で
Select AI 資産を **冪等にプロビジョニング**する(参照実装 denpyo の設計を踏襲)。

- 資産は `credential → profile → tool(SQL) → agent → task → team` の順に構築し、**決定論ハッシュ命名**
  (prefix + config fingerprint の SHA1 先頭桁、Oracle 識別子 30 byte 上限に丸め)で衝突なく drop+create する。
- **profile attributes**: `provider=oci` / `credential_name` / `model` / `region` / `oci_compartment_id` /
  `object_list`(許可テーブル/ビュー群) / `enforce_object_list` / `annotations` / `comments` / `constraints` /
  `embedding_model`(OCI Cohere) / **`oci_endpoint_id`(Enterprise AI / 専用エンドポイント)** / `max_tokens` / `oci_apiformat`。
- **tool** = `{tool_type: SQL, tool_params:{profile_name}}`、**agent** = `{profile_name, role}`、
  **task** = `{instruction(日本語応答を明示), tools:[tool], enable_human_tool:false}`、
  **team** = `{agents:[{name, task}], process:sequential}`。
- **会話**は `DBMS_CLOUD_AI.CREATE_CONVERSATION` で `conversation_id` を発行してマルチターン文脈を保持し、
  履歴は `USER_CLOUD_AI_CONVERSATION_PROMPTS` 等から参照する。
- **drift 検知**: データソースの `config_hash` / `ready` フラグ / `synced_at` / `last_error` を永続し、
  **設定変更時のみ**再プロビジョニングする(毎回 drop+create しない)。
- **region/model fallback**(モデルが当該 region 非対応なら代替へ)と **rate limit 制御**(slot 予約 + backoff)を備える。
- **未プロビジョニング/権限不足/SDK 失敗時**は warning を付けて安全に縮退(エラー提示・実行ブロック)し、
  必要なら **アプリ側 Enterprise AI オーケストレーション(`app_enterprise_ai` 生成バックエンド)** へ fallback する。
- 権限前提: 接続ユーザに `DBMS_CLOUD_AI` / `DBMS_CLOUD_AI_AGENT` の EXECUTE 権限と OCI credential が必要
  (sql-assist の権限チェックを起動時診断に取り込む)。
- 確定スタックは不変: NL2SQL 生成=Select AI(モデル頭脳=Enterprise AI via `oci_endpoint_id`)、
  embedding/rerank=OCI GenAI Cohere、DB/ベクトル=Oracle ADB/26ai。**外部ベクトル DB・別 LLM provider は導入しない。**

### Select AI 呼び出しの明示パターン(参照実装準拠)

- **単段生成(`select_ai`)**: profile を選んでから生成する。
  `BEGIN DBMS_CLOUD_AI.SET_PROFILE(profile_name => :name); END;` の後
  `SELECT DBMS_CLOUD_AI.GENERATE(prompt => :q, profile_name => :name, action => :a) FROM DUAL`
  (`action` ∈ `showsql` / `runsql` / `narrate` / `explainsql` / `chat`)。既定は `showsql`(プレビュー)→ 人手承認 → `runsql`。
- **多段生成(`select_ai_agent`)**: `DBMS_CLOUD_AI_AGENT.RUN_TEAM(team_name => :tm, prompt => :q)`。
  会話は `DBMS_CLOUD_AI.CREATE_CONVERSATION` で `conversation_id` を発行し継続。応答 JSON は `reply`/`content` を抽出。
- **分類器ベースの自動 profile ルーティング(Router)**: 複数ドメイン profile があるとき、質問を OCI Cohere
  `embed-v4.0` で埋め込み、学習済の軽量分類器(`LogisticRegression`、joblib 永続、**追加 LLM なし・決定論**)で
  domain を予測 → `domain→profile` マップ → `SET_PROFILE` で自動切替する。複雑度判定で `select_ai` ↔ `select_ai_agent`
  も振り分ける(コスト最適化)。学習データ・モデルは版管理し、CI では決定論的にスタブする。
- **profile の二重永続**: DB(`CREATE_PROFILE`)に加え、`object_list` / schema text / context DDL を JSON ミラーへ
  スナップショットし、few-shot 文脈・オフライン点検・drift 比較に再利用する。
- **合成データ生成**: Select AI で表ごとにサンプル行を生成し、評価ゴールデン / デモ / few-shot bootstrap に使う
  (本番データに触れずに検証)。生成物は本番データと明確に分離する。
- **意味キャッシュ(Cache)**: `NL→SQL` / `NL→結果` / `SQL→結果` を Oracle 26ai ベクトル検索(Cohere 埋め込み)で
  類似照会し、頻出・類似質問の再生成/再実行を回避する。鮮度要件で TTL/失効を制御。

## 検証済みコマンド

```bash
# backend
cd backend && uv sync && uv run pytest && uv run ruff check .
uv run uvicorn app.main:app --reload   # http://localhost:8000/docs

# frontend
cd frontend && npm install && npm run build
npm run dev                            # http://localhost:3000
```

## 開発コマンド

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

NL2SQL の生成・実行は HTTP リクエスト内で長時間ブロックしない方針とする。`showsql`(SQL 生成)は同期で返してよいが、
`RUN_TEAM`(Agentic 多段)や大規模実行・評価ジョブは永続 job 化して即時に返し、worker / dispatcher で実行する。
**実行(`runsql`)は必ず人手プレビュー確認・承認ゲートを通過したクエリのみ**を対象とし、read-only を既定とする。

## テスト/検証方針

- 開発時は実装と同時に対応するテストコードを追加・更新する。バックエンドは pytest、フロントエンドのロジックは Vitest、UI/UX とユーザー操作は Playwright を基本とする。
- **Select AI(`DBMS_CLOUD_AI(_AGENT)`)を呼ぶ層は、CI では決定論スタブ/録画応答でテスト**し(実 ADB 依存を切る)、実 Select AI は手動/ステージング検証とする。プロビジョニングの命名・attributes・drift 検知・ガードレールの SQL 判定は決定論ユニットテストで担保する。
- 変更後は該当範囲の lint・型チェック・テストを実行し、完了報告に実行結果を明記する。実行できない場合は理由と代替確認を明記する。
- UI/UX に関わるすべての機能は、Playwright でブラウザ表示を確認し、少なくとも主要ユーザーフロー(NL→SQL→確認→実行→結果)、モバイル幅(例: 375px)、デスクトップ幅、重要な空/読込/エラー/ブロック状態を検証する。

## コーディング規約・重要ルール

1. **NL→SQL 生成は Oracle Select AI / Select AI Agent 経由を中核とする**。`DBMS_CLOUD_AI` / `DBMS_CLOUD_AI_AGENT` を使い、独自に別 LLM へ NL2SQL を丸投げしない(アプリ側生成は `app_enterprise_ai` バックエンドとして明示選択時のみ)。
2. **Select AI が使うモデルは OCI Enterprise AI**(profile の `oci_endpoint_id` で指す)。**OCI Generative AI の汎用 chat 推論 API を NL2SQL 生成に直接使わない**(Select AI 経由でのみ利用)。
3. **embedding/rerank は OCI Generative AI(Cohere)経由**(例示検索・スキーマリンク)。
4. **例示/用語集/スキーマ doc のベクトル検索は Oracle 26ai AI Vector Search**。外部ベクトル DB を導入しない。
5. **スキーマ取込は Schema Source アダプター(`nl2sql_schema_source`)で手動選択**する。`full`(既定・全許可テーブル)/ `curated`(明示選択)/ `sampled`(**M-Schema 風**: 列ごとに distinct 値例 ≤3・各 ≤50 字 + 列説明 + 制約を付与し、低カーディナリティ列は**値/セル値索引**を作って候補値リンクに使う)で `object_list` と annotations/comments/constraints の取込方針を束ねる。設定 API `GET/PATCH /api/settings/schema-source` と専用画面で切替し、既定は現行挙動と一致させる。
6. **スキーマリンクは Schema Linking アダプター(`nl2sql_schema_linking`)で手動選択**する。`enforce_all`(既定・`enforce_object_list=true`)/ `curated` / `auto_prune`(関連テーブル/列のみ抽出。**スキーマを意味ユニットに分解した Oracle 26ai ベクトル多段リトリーバル**で大規模スキーマでも絞る、LinkAlign/CHESS/E-SQL 風・決定論)で profile へ渡すスキーマ範囲を束ねる。設定 API `GET/PATCH /api/settings/schema-linking`。
7. **知識/例示グラウンディングは Knowledge アダプター(`nl2sql_knowledge_profile`)で手動選択**する。`off`(既定)/ `glossary`(用語集注入)/ `few_shot`(類似 NL-SQL 例を Oracle 26ai ベクトル検索で注入、Vanna/sql-assist 風)/ `rag_trained`(Select AI RAG profile 併用)。例示 SQL を「複製優先」で固定する強い prompt 規約に従う。設定 API `GET/PATCH /api/settings/knowledge`。
8. **ルーティングは Router アダプター(`nl2sql_router_profile`)で手動選択**する。`off`(既定)/ `classifier`(質問を OCI Cohere 埋め込み + 学習済 `LogisticRegression` で domain 予測 → `domain→profile` マップ → `SET_PROFILE` で profile 自動選択、**追加 LLM なし・決定論**、sql-assist 由来)/ `complexity_aware`(複雑度で `select_ai` 単段 ↔ `select_ai_agent` 多段を振り分けコスト最適化、EllieSQL 風)。学習データ/モデルは版管理、CI は決定論スタブ。設定 API `GET/PATCH /api/settings/nl2sql/router`。
9. **曖昧性解決は Clarify アダプター(`nl2sql_clarify_policy`)で手動選択**する。`off`(既定)/ `detect`(集計粒度・期間・同名列などの曖昧さを taxonomy で検出し警告)/ `interactive`(確認質問で意図を確定してから生成、human-in-the-loop、AmbiSQL 風)。2 段ゲート哲学と整合。設定 API `GET/PATCH /api/settings/clarify`。
10. **SQL 生成は Generation アダプター(`nl2sql_generation_backend` + `nl2sql_generation_action`)で手動選択**する。バックエンドは `select_ai_agent`(既定・`RUN_TEAM`)/ `select_ai`(`SET_PROFILE` → `DBMS_CLOUD_AI.GENERATE`)/ `app_enterprise_ai`(アプリ側オーケストレーション)。アクションは `showsql→runsql 2段`(既定)/ `runsql` / `narrate` / `explainsql` / `chat`。候補生成(self-consistency)はオプション。設定 API `GET/PATCH /api/settings/generation`。
11. **安全は Guardrail アダプター(`nl2sql_guardrail_policy`)で手動選択**する。`read_only`(既定・**専用低権限 DB ロール/セッションで物理的に SELECT のみ強制**し、prompt だけに頼らない。DDL/DML ブロック)/ `strict`(object allowlist + row limit + `EXPLAIN PLAN` 検証 + PII マスク + **`semantic_verify`**=生成 SQL を `explainsql`/`narrate` で NL へ逆翻訳し元質問と突合して誤 JOIN/誤列を検出、GBV-SQL 風)/ `sandboxed`(専用ロール/スキーマで実行)。SQL injection・破壊的文・全件スキャンの抑止を決定論で束ねる。**実行前の人手承認ゲートは必須**。設定 API `GET/PATCH /api/settings/nl2sql/guardrail`(RAG の `/guardrail` と衝突回避のため `nl2sql/` 配下)。
12. **自己修正は Self-Correction アダプター(`nl2sql_correction_profile`)で手動選択**する。`off`(既定)/ `retry_on_error`(実行エラー文言を Select AI に戻して再生成)/ `verified`(execute→検証→修正ループ + 逆翻訳突合、SQLFixAgent/CSC-SQL/GBV-SQL 風、上限回数あり)。設定 API `GET/PATCH /api/settings/correction`。
13. **エージェント計画は Agentic アダプター(`nl2sql_agentic_profile`)で手動選択**する。`off`(既定・単段)/ `decompose`(sub-question 分解、MAC-SQL 風)/ `multi_hop`(複数ステップ、上限あり)。Select AI Agent の team(`process:sequential`)とアプリ側 Enterprise AI で実装し、追加 LLM 呼び出しが発生する点を明示する。設定 API `GET/PATCH /api/settings/agentic`。
14. **キャッシュは Cache アダプター(`nl2sql_cache_policy`)で手動選択**する。`off`(既定)/ `nl_sql`(NL→SQL)/ `nl_result`(NL→結果)/ `sql_result`(SQL→結果)を **Oracle 26ai ベクトル検索(Cohere 埋め込み)で類似照会**し、頻出・類似質問の再生成/再実行を回避する(レイテンシ/コスト削減)。鮮度要件で TTL/失効を制御。設定 API `GET/PATCH /api/settings/nl2sql/cache`。
15. **結果整形は Result アダプター(`nl2sql_result_profile`)で手動選択**する。`table`(既定)/ `narrate`(Select AI `narrate` で日本語要約)/ `chart`(可視化)/ `bilingual_ja_en`。設定 API `GET/PATCH /api/settings/result`。
16. **評価は Evaluation アダプター(`nl2sql_evaluation_suite`)で手動選択**する。`request_only`(既定・閾値なし)/ `execution_focused` / `balanced` / `strict_ci` / `bird_like` を CI gate 用の名前付き閾値(execution_accuracy / exact_match / 実行成功率 / レイテンシ / 推定コスト)として束ねる。**決定論指標のみ**。外部評価 SaaS / LLM-as-judge の追加呼び出しは導入しない。Select AI 合成データを評価ゴールデン生成に使ってよい(本番データと分離)。設定 API `GET/PATCH /api/settings/evaluation-suite`。
17. **質問の「業務(利用者)視点」は業務アシスタント(`nl2sql_business_view`)で束ねる**。データソース/Select AI profile が「物理スキーマをどう索引するか(作る側)」を司るのに対し、業務アシスタントは「どの **データソース群(多対多)** を、どんな **query 方針**(Schema Linking/Knowledge/Router/Generation/Guardrail/Result/Evaluation の上書き)・**persona**(system prompt/既定言語)で束ねて回答するか(利用する側)」を司る別レイヤー。解決順は **request 明示 > 業務アシスタント >(単一データソース指定時のみ)データソース > グローバル既定**。設定 API `GET/POST/PATCH /api/business-views`。**アクセス制御(ビュー単位の利用者制限)は現スタックに認証/RBAC が無いため別途設計**。
18. Select AI 資産(credential/profile/tool/agent/task/team)は **冪等プロビジョニング + `config_hash` drift 検知**で管理し、毎回 drop+create しない。命名は決定論ハッシュ(30 byte 上限)を守る。
19. シークレット(OCI 認証・DB 接続・ADB wallet・Select AI credential)は `.env` 経由。**ハードコード禁止**、コミットしない。
20. Select AI / LLM 出力(生成 SQL・候補・説明)は **Pydantic スキーマで検証**してから保存・実行する。**実行は必ずガードレール検証 + 人手承認の後**に行う(read-only 既定)。
21. UI 作業は `ui-ux-pro-max` skill を使用。
22. 機能開発では、実装と同時にテストコードを追加・更新する(Select AI 層は決定論スタブ)。
23. 変更後は該当範囲の lint・型チェック・テストを実行してから完了とする。
24. UI/UX に関わる変更は Playwright で画面確認とテストを実施してから完了とする。
25. **このスタックから外れる提案(別 LLM プロバイダ、別ベクトル DB、別 NL2SQL SaaS 等)をする場合は、必ず理由を添えてユーザに確認する。**
