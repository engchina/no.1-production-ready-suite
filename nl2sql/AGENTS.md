# AGENTS.md — Production Ready NL2SQL

> NL2SQL（`nl2sql/`）固有のルール。共通のルールは root の [../AGENTS.md](../AGENTS.md) を先に適用する。詳細は下の表の文書に書く。
> Issue の label は `product:nl2sql`、PR title の scope は `nl2sql`。

## 作業ごとに読む文書

| 作業 | 読む文書 |
|---|---|
| 機能の全体（チャット・SQL 生成・データ準備・改善・運用）とコードの置き場所 | [docs/agents/structure.md](./docs/agents/structure.md) |
| 画面・ナビ・lint・NL2SQL 固有の部品、「質問」「検索」の用語の細則 | [docs/agents/ui.md](./docs/agents/ui.md) |
| 「AI 活用」のデータの見え方・role の権限昇格の防止・状態保持 | [docs/agents/security-contracts.md](./docs/agents/security-contracts.md) |
| 技術スタック（モデル・DB・ベクトルの次元・ライブラリ） | [docs/agents/stack.md](./docs/agents/stack.md) |
| オントロジー（生成・検証・Markdown・画面） | [docs/agents/ontology-standard.md](./docs/agents/ontology-standard.md) |
| 新機能の設計・比較検討 | [docs/reference-rag-projects.md](./docs/reference-rag-projects.md) の該当カテゴリ |

## プロジェクト概要

> **製品名と固有 namespace は `Production Ready NL2SQL` / `NL2SQL` を正とする。**
> 新規 DB object、cookie、storage key、context、UI の製品ラベルへ汎用 `RAG` prefix を使用しない。
> `GraphRAG`、`RAGAS`、`RAGFlow` など技術・製品の固有名詞はこの置換対象外とする。

Oracle AI Database の業務データに、自然言語で SQL を生成・検証・実行する SQL 専用の NL2SQL の参照実装。特定の業務ドメインに固定しない。機能（AI 活用 / データ準備 / 改善・運用）は [docs/agents/structure.md](./docs/agents/structure.md)。

- チャットは SQL を作る前に回答前の確認を自動で行い、確認のカード（3 製品共通の `ClarificationCard`。SQL 生成の画面と同じ部品・文言）で「この内容で SQL を生成して実行」を押すまで SQL を作らない。

## 用語

細則（「検索」のままでよいもの・変えない文言）は [docs/agents/ui.md](./docs/agents/ui.md)「用語」。

- 利用者が入力する自然言語は **`質問`**（root「言語」）。`クエリ` は SQL・技術の概念にだけ使い、旧称の「検索クエリ」も使わない。
- 利用者の自然言語の入力とその実行を **「検索」と呼ばない**（#1189）。SQL の生成と実行は「SQL を生成して実行」（ボタン）・「SQL の生成と実行」（説明の文）、その結果は「実行結果」。
- `frontend/tests/i18n-key-contract.test.ts` が辞書の文言を検査する。

## UI/UX

詳細（サイドナビの構成・ラベルの二段管理・lint・固有の部品・画面の差分 spec）は [docs/agents/ui.md](./docs/agents/ui.md)。

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
node scripts/run-unit-tests.mjs tests/<対象>.test.ts   # ロジックテスト
npx playwright test tests/e2e/<対象>.spec.ts               # e2e（関係する spec だけ）
```

- CI の job は `NL2SQL / Backend`・`NL2SQL / Frontend`・`NL2SQL / E2E smoke`。backend は pytest、frontend のロジックは `node --test`（`frontend/tests/*.test.ts`）、UI とユーザー操作は Playwright。
- 時間のかかる処理（DB 構造の再取得・SQL 生成評価・オントロジー構築・合成データ生成）は永続 job にし、API は job を投入して返す。本番では `init_script.sh` が作る systemd の worker（`production-ready-nl2sql-{schema-refresh,quality-evaluation,ontology,synthetic}-worker`）が処理する（[docs/compute-operations.md](./docs/compute-operations.md)）。

## 権限と状態保持

3 製品共通の契約は platform の [UX 契約](../platform/docs/ux-contracts/README.md) が正本。権限・会話・実行履歴・状態保持に触る前に [docs/agents/security-contracts.md](./docs/agents/security-contracts.md) を読む。特に、実行は操作した本人の権限と SQL の実行の接続（#904 の分け方を崩さない）で行い、role の更新・割当で権限昇格を許可しない。

## オントロジーの標準（#1729）

規則の全文は [docs/agents/ontology-standard.md](./docs/agents/ontology-standard.md)、対応表は [docs/profile-ontology-capabilities.md](./docs/profile-ontology-capabilities.md)「オントロジーの標準」。中身は Palantir Foundry の Ontology を基準に専門性を保ち、記述は日本語が主（訳は `ontology_terms.json` の 1 つの対応表）、画面の説明・診断は平易な日本語、生成は best effort（推測で埋めない）、LLM に渡す Markdown は業務の知識だけにする。

## コーディング規約

**Backend の ASGI event loop 上で同期 I/O を直接実行しない。** 詳細は [docs/backend-concurrency-contract.md](./docs/backend-concurrency-contract.md) を正とする。同期 domain service / Oracle repository / file・CLOB・Excel 処理 / OCI SDK 呼び出しは sync FastAPI route（`def`）、CLI / worker、または `backend/app/api/concurrency.py` の `run_sync_io(...)` 内だけで実行する。`async def` route は `UploadFile`・SSE / WebSocket・async client など本当に `await` が必要な場合に限定する。
