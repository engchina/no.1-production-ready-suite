# AGENTS.md — Production Ready RAG

> このファイルは **Claude Code と Codex の両方が参照する正本(single source of truth)** です。
> `CLAUDE.md` はこのファイルを `@AGENTS.md` で取り込みます。ルールを変更する際は **必ずこのファイルを編集**してください。

## 開発ワークフロー / GitHub 運用

- **`main` ブランチへ直接 commit / push / 変更しない。** すべての変更は GitHub Issue を先に作成し、Issue に紐づく作業ブランチで行う。
- 作業ブランチ名は既定で `codex/<issue-number>-<short-topic>` とする。既存 ref との衝突などで使用できない場合も、Issue 番号と作業内容が分かる名前を使う。
- 変更後は Pull Request を作成し、関連 Issue、変更内容、検証結果を PR description に明記する。
- merge は CI/checks が成功したことを確認してから行う。必須 CI が存在しない場合は、PR 上で checks 状態を確認し、実行した代替検証を明記してから merge 判断する。
- docs-only の小さな変更や緊急修正も原則として同じ Issue → branch → PR → CI/checks → main merge の流れに従う。例外が必要な場合は、理由を添えてユーザ確認を取る。

### GitHub Issue / Pull Request の記述規約

#### 共通

- Issue / PR のタイトルと本文は**原則として日本語**で記述する。code identifier、API path、file path、command、製品・ライブラリの固有名詞は英語のままでよい。
- タイトルは対象と事象が分かる具体的な文にする。「不具合」「修正」「対応」だけの曖昧なタイトルにしない。
- 本文は Markdown 見出しで構造化し、確認した事実と推測を区別する。未調査・未確定の項目は断定せず「調査中」「未確認」と明記し、判明後に本文を更新する。
- API、関数、設定 key、status code、error message、再現値など、調査・レビュー・回帰テストに必要な具体情報を記載する。secret、token、個人情報、実 credential は記載しない。
- ユーザー向け概念は `ナレッジ構築` / `業務ビュー` / `検索・回答設定` を使い、`pipeline` / `adapter` / `profile` などの工程語は code identifier を指す場合に限る。

#### Issue

- Issue の種別にかかわらず、最低でも `問題`、`症状`、`原因`、`修正方針` の4項目を含める。初回登録時に原因が未確定でも `原因` を省略せず、現時点の仮説または「調査中」と記載する。

```markdown
## 問題

何が問題なのかを記載する。

## 症状

どのような入力や条件で何が起きるのかを、画面/API/状態/error message などの観測事実に基づいて記載する。

## 原因

どの code path、data flow、または設計が原因と考えられるかを記載する。未確定の場合は仮説と未確認事項を区別する。

## 修正方針

どこを、どのような考え方で修正するかを、責務境界と変更しない範囲を含めて記載する。
```

- 可能であれば `影響範囲`、`再現手順`、`関連ファイル`、`必要なテスト` も追加する。必要に応じて `期待動作`、`完了条件`、`補足`、`ログ`、`スクリーンショット`、`代替案`を追加する。
- feature / docs / refactor / investigation Issue では、`問題` に背景や現在の不足、`症状` に現状の制約や具体例、`原因` に設計上の理由または調査対象を記載し、4項目を Issue の性質に合わせて具体化する。
- 3 層モデル(文書レシピ / KB スコープ / Business View)に関わる Issue では、どの層の責務かを明記し、責務越境になっていないかを `修正方針` に記載する。
- 長い log は必要箇所だけを抜粋し、再現に不要な出力を貼らない。
- `完了条件`は「対応する」のような作業表現だけにせず、期待状態と必要な test / lint / build / 手動確認を判定可能な形で列挙する。

#### Pull Request

- PR title は原則として `<type>: <日本語の要約> (#<issue-number>)` とする。`type` は変更内容に合わせて `feat` / `fix` / `docs` / `test` / `refactor` / `chore` 等を使用する。
- PR 本文は原則として次の見出しを使用する。

```markdown
## 関連 Issue

Closes #<issue-number>

## 背景 / 原因

Issue の要点と、この変更が必要な理由を記載する。bug fix では根因を記載する。

## 変更内容

- 変更した責務・挙動を具体的に記載する
- schema / API / UI / data migration / compatibility への影響を記載する

## 検証結果

- `<実行した command>` — pass / fail / skip と件数
- 手動確認または Playwright の対象 flow / viewport / 状態

## 既知の制約・残課題

- 未対応範囲、既知の制約、follow-up Issue を記載する。なければ「なし」と記載する。
```

- `関連 Issue` には、merge で完了する Issue は `Closes #N`、参照のみは `Refs #N` と記載する。複数ある場合はすべて列挙する。
- `変更内容` は commit の羅列ではなく、reviewer が挙動差分と責務境界を判断できる粒度で記載する。変更していない重要範囲や backward compatibility も必要に応じて明記する。
- `検証結果` には実行した正確な command と結果を記載する。失敗・skip・未実行を隠さず、今回の変更によるものか既存問題かを分ける。実行できない test がある場合は理由と代替確認を記載する。backend は `uv run pytest` / `uv run ruff check .` / `uv run mypy .`、frontend は `npm run lint` / `npm run build` / `npm run test` を基本とする。
- UI/UX 変更では、対象 Playwright spec、desktop / 375px viewport、主要導線と重要状態(空/読込/エラー/ブロック)の結果を記載する。見た目を変更した場合は必要に応じて screenshot または visual check の結果を添える。
- OCI / Oracle / LLM を呼ぶ範囲の変更では、CI 上の決定論スタブによる確認と、手動/ステージングでの実サービス確認をそれぞれ区別して記載する。
- docs-only など test 対象外の場合も `検証結果` を省略せず、`git diff --check` 等の実施結果と、コード test を実行しない理由を記載する。
- PR 作成後に追加修正や検証結果の変化があった場合は、コメントだけで済ませず PR 本文を最終状態へ更新してから review / merge する。

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
  - **検索・回答設定**: ファイル準備、文書解析、文書分割、検索インデックス、検索方法、根拠確認、回答スタイル、回答プロンプト、安全チェック、品質評価、GraphRAG、エージェント計画。
  - **システム設定**: OCI 認証、アップロード保存先、モデル、データベース、サービス管理。
- メッセージ機構は [docs/frontend-messaging-spec.md](./docs/frontend-messaging-spec.md) を正本とする。
- ボタン仕様は [docs/frontend-button-spec.md](./docs/frontend-button-spec.md) を正本とする。

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
12. `main` へ直接 commit / push しない。Issue → 作業ブランチ → PR → CI/checks → merge の流れと、Issue / PR の記述規約([開発ワークフロー / GitHub 運用](#開発ワークフロー--github-運用))に従う。
