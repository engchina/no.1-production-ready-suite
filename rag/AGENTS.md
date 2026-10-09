# AGENTS.md — Production Ready RAG

> RAG（`rag/`）固有のルール。GitHub 運用・Issue / PR 規約・CI・デザインシステム・共通の技術方針（言語・AI / DB の分担・シークレット・LLM 出力の検証・ローカルの検証の範囲）は、root の [../AGENTS.md](../AGENTS.md) を先に適用する。ルールを変えるときはこのファイル（共通ルールは root）を編集する。
> Issue の label は `product:rag`、PR title の scope は `rag`。

## プロジェクト概要

文書とナレッジベースを構築し、業務ごとの **Search Answer Profile** から検索・回答する RAG を本番品質で提供する（ingestion・grounding・回答生成・評価・観測・Oracle / OCI への配備）。SQL 専用の自然言語問い合わせは `../nl2sql/` の責務で、`rag/` へ機能・UI・API・設定を混在させない。

## 用語

- ユーザー向けの主概念は **ナレッジ構築** / **検索・回答プロファイル** / **検索・回答設定**。「検索・回答プロファイル」は正式な製品語で、parser / source profile 等の工程の技術語とは区別する（KB membership・文書レシピの責務を移さない）。
- `producer` / `consumer` / `Pipeline` / `Adapter` / `Profile` / `Runtime` / `Backend` などの技術語は、code identifier・コード内部・開発者向けの診断パネルに限る。
- `SearchAnswerProfile` は正式な code / API 名として維持し、ユーザー表示は「検索・回答プロファイル」。
- **「DocRAG」は使わない。** rag_poc から移したときの名前で、画面・API のメッセージ・docs の地の文にも、コードの識別子・設定（env）・API・DB の名前にも使わない。RAG の標準の用語・名前で呼ぶ（回答フロー・親子階層（small-to-big）・回答の記録・回答生成のプロンプト・質問の拡張・回答の生成方式（CRAG / 標準 RAG）。package は `rag_engine`、回答は `app/rag/answer_engine.py`、分割方式は `small_to_big`、回答の設定は `RAG_QUERY_STRATEGY` など）。旧名は migration（保存値・表の改名）と docs/deployment.md の更新手順にだけ残す。
- 3 層モデル（文書レシピ / KB スコープ / Search Answer Profile）に関わる Issue では、どの層の責務かを明記し、責務越境になっていないかを `修正方針` に書く。

## 技術スタック（RAG 固有の分）

| 用途 | 採用 | 制約 |
|---|---|---|
| 回答生成・構造化抽出・クエリ計画 | OCI Enterprise AI | モデル設定は環境変数 / 設定 API 経由 |
| 埋め込み / リランク | OCI Generative AI（Cohere Embed v4 / Rerank v4 fast） | 埋め込みの次元数を Oracle AI Vector Search とそろえる |
| 画像・文書理解 | OCI Document Understanding / Enterprise AI Vision | OCR・ページ解析・VLM 入力 |

- データ: Oracle Autonomous AI Database / Oracle AI Vector Search（チャンク・引用・文書構造・評価結果・監査ログ・ベクトル検索の正本）と OCI Object Storage（原本・変換済み artifact・レビュー済み文書・評価 artifact）。
- SDK は `oci` / `python-oracledb`。Frontend は共有 UI に加えて shadcn/ui、通信は REST + SSE / WebSocket。
- 観測は Langfuse + Prometheus + OpenTelemetry。品質は pytest / pytest-cov / ruff / mypy / bandit / pip-audit / Vitest / Playwright。
- **配備**: backend・取込 worker・前処理・parser はサービスごとの uv の venv（`uv sync --locked --no-dev --python 3.12`）と systemd の unit で動かす。unit の定義は `scripts/rag-systemd.sh`（本番の `init_script.sh` と開発の `scripts/rag-services.sh` が共通で使う）。Compute には root の「配備」のとおり他の製品と同じ 1 台に入る（CPU parser のみ）。Dockerfile・compose は作り直さない。開発の起動は backend が `uv run uvicorn`、前処理 / parser が `scripts/rag-services.sh`、frontend が `npm run dev`（docs/deployment.md「ローカル開発」）。
- **サービス管理画面**は systemd の unit を操作する（起動 = `enable --now`、停止 = `disable --now` で最後に操作した状態を保つ）。backend が実行してよいのは、sudoers で許可した allowlist の unit の `systemctl` / `journalctl` だけ（argv 固定・shell を通さない。`app/services/systemd.py`）。前処理 / parser を足すときは、catalog・`scripts/rag-systemd.sh` の `RAG_MICROSERVICES`・URL 設定の既定値（`127.0.0.1:<port>`）をそろえる（テストで照合する）。

## UI/UX

- 静かで読み取りやすい情報密度、安定したナビゲーション、明確なフォームの状態を優先する（SaaS / 業務ツール）。
- サイドナビ（折りたたみ可）の主なセクション:
  - **ナレッジ構築**: 文書アップロード、文書インデックス、ナレッジベース、検索・回答プロファイル。
  - **AI 活用**: チャット、RAG 検索。
  - **検索・回答設定**: ファイル準備、文書解析、文書分割、検索インデックス、関係情報の構築、検索方法、回答プロンプト、安全チェック、評価の基準。
  - **改善・運用**: 品質評価、フィードバック。
  - **セキュリティ設定**: 権限管理（ロールごとのメニュー権限・検索・回答プロファイル・ナレッジベース）。
  - **ユーザーとロール** / **システム設定**（OCI 認証、アップロード保存先、モデル、データベース、外観と証明書）: 3 製品で共通（platform の共有パッケージ）。
  - **運用設定**: システムテーブル管理（先頭）、HuggingFace 設定、サービス管理。サービス管理の工程の並び・名前・説明は、検索・回答設定のナビの項目と各設定画面の説明から作る（`frontend/src/components/settings/service-stages.ts`）。
- ナビ・ルート・ページ内の操作は権限（`backend/app/security/permissions.py` のコード）で出し分ける。API は backend の manifest が既定で拒否するため、画面を足したら使う API を manifest にも登録する（完全性テストがある）。
- RAG 固有の画面の差分: [docs/frontend-messaging-spec.md](./docs/frontend-messaging-spec.md)（文書詳細の失敗表示）、[docs/frontend-workspace-state-spec.md](./docs/frontend-workspace-state-spec.md)（離脱ガードと作業状態の対象・保存 key）、[docs/frontend-page-archetypes-spec.md](./docs/frontend-page-archetypes-spec.md)（ページの型 A〜D と、対象の操作の `RowActionMenu` / `ObjectActionBar` への割り当て・例外）。
- lint は `frontend/eslint.config.mjs` が `../../platform/docs/design-system/adherence.oxlintrc.json` を import する。
- `ToggleChip` をタブとして使っている箇所（ビューの切替）は `Tabs` に置き換え、絞り込みの箇所だけ `ToggleChip` に残す。
- `--font-mono`（ID・ログ・SQL の等幅書体）は共有の tokens が定義し、書体ファイルは `frontend/src/fonts.css` で `@fontsource/google-sans-code` を自前ホストする。

## RAG 設定責務

> **3 層モデル（確定）**: 文書 = 処理レシピを持つ / KB = 純スコープ（コレクション）/ Search Answer Profile → KB。
> KB は「レシピ」と「検索スコープ」を兼任しない。レシピは文書単位、検索・回答設定は Search Answer Profile。

### 文書（Document）= レシピ

文書は中身に加えて **1〜3 件の独立した処理レシピ（preprocess / parser / chunking）** を自身のプロパティとして持つ。各レシピは設定、ジョブ、成果物、エラー、工程状態を個別に保持する。

- ファイル準備（preprocess）。
- 文書解析（parser / OCR engine）と、図・画像の読み取り（Vision。解析エンジンに関係なく解析の後に backend の共通の段で読み取る。切り替えはレシピだけ）。global の既定エンジンは Docling（PDF と画像だけ）。それ以外の形式は取込前に止めて処理レシピで Unstructured などを選ぶよう案内し、自動では振り分けない（判定は `backend/app/rag/parser_source_guard.py` の 1 か所。Unstructured のサービスは既定では配備しない）。
- 文書分割（chunking strategy、chunk size、overlap、parent-child）。
- 索引構築（vector index build、GraphRAG、navigation summary、field extraction）。
- 品質 gate（解析品質、chunk 品質、公開前チェック）。

レシピの既定値は **global（「検索・回答設定」配下の ファイル準備 / 文書解析 / 文書分割 など）** から解決し、各レシピが選んだ値で上書きする。**KB からは解決しない**。embedding / HNSW は単一固定。同じ文書のジョブは直列実行し、異なる文書のジョブは並行実行できる。

### ナレッジベース（KB）= コレクション（純スコープ）

KB は **どの文書を検索対象にするか（membership）だけ**を持つ純スコープ。**レシピ（preprocess / parser / chunking）も検索・回答設定も持たない**。

- 文書 membership（N:N。1 文書が複数 KB に所属可、1 KB が複数文書を束ねる）。
- 名称 / 説明 / スコープ。
- 例外として、項目抽出の項目の定義（何を取り出すか）は KB ごとに持てる（無ければ全体の既定、複数 KB に属する文書は和集合。docs/knowledge-base-management.md の 8.4.1a）。項目抽出を行うかどうかは文書レシピ / global が決める。

文書の KB 出し入れは `rag_document_knowledge_bases` の行 add/delete **のみ**で、chunk へ波及しない（再プラン / materialize / GC を起こさない）。KB UI から preprocess / parser / chunking・検索方法・安全チェック・品質評価を出さない。KB の legacy adapter/query config は読み取りのみ許容し、runtime では使わず、次回保存で再保存しない。

### Search Answer Profile

Search Answer Profile は **検索・回答に使う設定だけ**を持つ。

- 参照 KB scope。
- 検索方法（回答の検索と生成）、安全チェック。
- feedback 集計。

設定責務は次の通りとし、検索・回答プロファイルには Sidebar 全項目を複製しない。

| 設定群 | グローバル既定 | 文書レシピ上書き | Search Answer Profile 上書き |
|---|---|---|---|
| ファイル準備 / 文書解析 / 文書分割 / GraphRAG | 可 | 可 | 不可 |
| 検索インデックス | 可 | 不可 | 不可 |
| 検索方法 / 安全チェック | 可 | 不可 | 可 |
| 回答プロンプト | 可 | 不可 | 不可 |
| 品質評価 | 可 | 不可 | 不可 |

関係情報の構築（構築する / しない。文書と章・節の見出しのつながり）は文書レシピで選ぶ（ナレッジベースの関係情報グラフで見るためのもので、回答の検索では使わない。検索時のグラフ拡張と claims / community summary の構築は持たない）。共有 Oracle 索引の設定は Search Answer Profile へ保存しない。

検索時の解決順は **request 明示 > Published Search Answer Profile > global defaults**。KB の legacy query override は使わない（保存時にも新規保存しない）。

### 複数レシピ融合（精度向上）

同じ文書の検索精度を上げたい場合はレシピを最大 3 件まで追加し、それぞれを独立して materialize する。**各レシピの直近成功した active chunk_set はすべて検索対象**とし、hybrid RRF + rerank で融合する。回答に渡す根拠の選択（`rag_engine` の `evidence_spans`）では、本文が同じ根拠に加えて、同じ文書で解析の要素の ID（`element_ids`。親子分割の親は子の `source_record_refs`）が小さい側の 8 割以上重なり、短い側の本文の 9 割以上がもう一方に含まれる根拠を 1 つにまとめる（source-span 重複除去。順位の高い位置に範囲の広いほうを残し、まとめた chunk_id は `source_aliases` に残す。要素の ID の無い分割は本文が同じものだけをまとめる。#1331）。主レシピ、候補、配信中、昇格の概念は持たず、配信モード（`single / fused / routed`）の設定も持たない。設定変更後の再処理中や再処理失敗時も、直前の active chunk_set を検索対象として維持する。KB membership を変えてもレシピ集合や chunk_set は変わらない。

レシピ追加・削除は親文書行をロックして **最少 1 件・最大 3 件**を保証する。活動中ジョブのあるレシピは編集・削除できない。成功時だけ新 chunk_set を active に原子切替し、失敗時は他レシピと旧 active 出力を変更しない。

GraphRAG、navigation summary、field extraction が planning のみで実 materialize 未完の場合は、UI / API diagnostics にその状態を表示する。

## ディレクトリ構成

```text
backend/                  FastAPI アプリ
  app/
    main.py               エントリ(CORS, ルーター, lifespan)
    config.py             設定(pydantic-settings)
    api/routes/           health / documents / search / knowledge_bases /
                          search_answer_profiles / evaluation / settings / services
    clients/              OCI / Oracle / Object Storage clients
    rag/                  ingestion / parsing / chunking / 回答フロー(answer_engine) /
                          guardrail / evaluation / search answer profile
    schemas/              common / search / knowledge_base / search_answer_profile / settings
  tests/                  pytest
frontend/                 Vite + React Router + TypeScript
  src/App.tsx             React Router ルート定義
  src/components/         layout / search / knowledge-bases / search-answer-profiles / settings
  src/lib/                api / queries / routes / i18n / utils
services/                 parser / preprocess / pipeline(chunking / graphrag / vector_index / guardrail / evaluation)などのローカル実行単位
```

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
```

CI の job は `RAG / Backend`・`RAG / Frontend`・`RAG / E2E smoke` 等。

## テスト/検証方針

- backend は pytest、frontend のロジックは Vitest、UI/UX は Playwright。
- **実 Oracle のテスト（`oracle_db` の fixture）は、共有の開発 DB ではなくテスト専用の schema（DB ユーザー）で流すことを推奨する（#619）。** fixture はテストの開始時に未適用の migration を当て、テストが作った行を消すため、共有の DB では並行作業のチェックアウトの migration が手元のデータに当たる。接続先は共通 `.env` の `PLATFORM_ORACLE_*` を読むので、テスト専用のユーザーを書いた別のファイルを `PLATFORM_ENV_FILE=<ファイル> uv run pytest` で渡す。データを削除する migration（テーブルの DROP・行の DELETE）が未適用なら、fixture は何も当てずに実 Oracle のテストを skip し、理由を出す（適用は書き出しの後に `system_schema_cli initialize --allow-destructive` で行う。docs/deployment.md「既存環境の更新手順の共通の注意」）。
- データを削除する migration を足すときは、`oracle_schema.py` の `OracleSchemaSection` に `destructive_note`（削除されるデータと、前にする書き出し）を書く。付け忘れは `tests/test_system_schema_manager.py` が SQL から検出する。

## コーディング規約

1. RAG の検索・回答は Oracle AI Vector Search、OCI Enterprise AI、OCI GenAI の embedding / rerank を中心に構成する（別 RAG SaaS も入れない）。
2. KB の query の legacy config は runtime で無視し、保存時に新規保存しない。
3. 機能開発では、既存のパターン・API・UI コンポーネントを優先する。
