# AGENTS.md — Production Control Plane for AI Agents

> Agent（`agent/`）固有のルール。共通のルールは root の [../AGENTS.md](../AGENTS.md) を先に適用する。詳細は下の表の文書に書く。
> Issue の label は `product:agent`、PR title の scope は `agent`。

## 作業ごとに読む文書

| 作業 | 読む文書 |
|---|---|
| 組み込み Runtime・ツールの実行・業務 Agent の版・保存先・スキル | [docs/agents/runtime.md](./docs/agents/runtime.md) |
| MCP（外部への公開・MCP 接続・サービストークン・再試行） | [docs/agents/mcp.md](./docs/agents/mcp.md) |
| 画面・ナビ・用語・「AI 活用」のデータの見え方・権限・Playwright | [docs/agents/ui.md](./docs/agents/ui.md) |
| API・認証・RBAC・Snapshot・技術スタック | [docs/agents/api-security.md](./docs/agents/api-security.md) |
| 検証のコマンド・E2E smoke・Runtime のテスト・worktree での起動 | [docs/agents/development.md](./docs/agents/development.md) |
| コードの置き場所 | [docs/agents/structure.md](./docs/agents/structure.md) |
| 設計の全体 | [docs/agent-control-plane-design.md](./docs/agent-control-plane-design.md) |

## プロジェクト目標

**業務・業界の Agent Platform — 業務 Agent を作り、Skill と MCP（RAG・NL2SQL・その他）を安全に使わせる。**

業務 Agent の定義、Skill、実行（組み込み Runtime）、共通の Run・Event・Artifact・Approval・Audit を 1 つの製品で持つ。主要概念と依存方向（`業務 Agent → Skill → ツール（MCP / resource）`）:

1. **業務 Agent** — 業務の指示、説明、有効状態、`skill_ids`、使うモデル（`model_id`。空なら既定のテキストモデル）、データの範囲（`data_scopes`。RAG / NL2SQL の使えるプロファイルと既定。空なら利用者が使えるすべて。#1378）。
2. **Skill** — AgentSkills 互換の指示本体。必要なツール（MCP / Control Plane のツール）を宣言する。

## アーキテクチャ不変条件

全文は [docs/agents/runtime.md](./docs/agents/runtime.md)。特に次を守る。

- 実行は Control Plane の**組み込み Runtime**（`features/agent/builtin_runtime.py`。OpenAI Agents SDK・OCI Enterprise AI の Responses API）。別の LLM provider は組み込まず、SDK の tracing は常に無効にする（業務データを外部へ送らない）。外部の Runtime・Binding・Docker の Runtime は持たない（#754）。
- ツールの実行は `tool_registry.invoke`（ポリシー・ガードレール・監査・成果物）を必ず通す。「拒否」のツールはモデルに渡さず、「承認」のツールは SDK の `needs_approval` で中断して `waiting_approval` にし、却下したツールは実行しない。
- 業務 Agent は版を持ち、「公開」した版だけが利用者の Run に使われる（公開の版は使うスキルの中身を固定する）。下書きで実行できるのは Agent 管理（admin）と、個人の業務 Agent の持ち主（`agent.agents.build`。#2523）だけ。
- 保存先は `AGENT_RUNTIME_REPOSITORY_BACKEND` に従う（Oracle では `AGENT_RUNTIME_*` / `AGENT_CONTROL_PLANE_ITEMS`。テーブルはシステムテーブルが作り、アプリは DDL を実行しない）。MCP 接続の秘密は `app.secret_box` で暗号化し、テストは memory に固定する。
- **Skill と実行の五つの不変条件**（#3240。正本は runtime.md の同名の節）: ① ツールはスキルの宣言からだけ渡す（業務 Agent への直接の指定は 422。宣言は認可ではない。ツールの層はスキルを import しない）② 明示の構成を解決できなければ暗黙に切り替えずに失敗する ③ 未対応の操作を成功にしない（渡していない名前の呼び出しは実行せず理由を残す）④ 認可は検証済みの認証文脈から ⑤ 検索・ツールの結果を権限・承認の根拠にしない。
- Agent はスキルだけを持つ（プラグイン・マーケットプレイス・独立 Workflow engine は作り直さない）。スキルの `references/` は読むが、同梱の `scripts/` などのコードは読まず・実行しない。

## MCP 境界

全文は [docs/agents/mcp.md](./docs/agents/mcp.md)。特に次を守る。

- 外部の MCP クライアントには業務 Agent へのチャット（`POST /api/mcp`）を共通の API キーだけで公開し、キーを発行した利用者の権限・対象範囲で判定する。MCP から作った Run は承認の要るツールを呼ばない。RAG は Agent を呼ばない。
- RAG / NL2SQL / 外部 MCP は「MCP 接続」1 つの仕組みで管理し、ツールは組み込み Runtime から `tool_registry.invoke` を通してだけ呼ぶ（例外は画面の操作の「RAG の図の根拠・根拠の原文を開く」だけ）。外部 API key・token を snapshot、API、ログ、Artifact に出さない。
- RAG / NL2SQL へは公開の契約（`platform/contracts/`）だけに依存し、内部の module・表・画面の API・内部の規則の一致を前提にしない（#3240。`tests/test_product_boundaries.py`）。
- 呼び先へのサービストークンの `sub` は Run の利用者（承認後の実行も承認者ではない）。書き込みのツールは、呼び先に届いた後の失敗では再送しない。

## UI/UX

詳細は [docs/agents/ui.md](./docs/agents/ui.md)。特に次を守る。

- ナビは **AI 活用** → **Agent 構築** → **改善・運用** → セキュリティ設定 → ユーザーとロール → 運用設定 → システム設定。既定の入口はチャット。Tools・ツール権限をナビに出さない。
- 画面・ナビの文言に「Control Plane」「Runtime」のような基盤の用語を使わない（Run → 「実行」、Skill → 「スキル」、組み込み Runtime → 「実行環境」。識別子・URL・権限コードは変えない）。

## API とセキュリティ

詳細は [docs/agents/api-security.md](./docs/agents/api-security.md)。新しい API は必ず権限 manifest（`app/security/permissions.py`）に登録する（登録外は 403）。承認の決定者は Cookie の利用者から決め、request の値を使わない。

## 開発・検証

詳細は [docs/agents/development.md](./docs/agents/development.md)。

- 完了前に `scripts/check-all.sh` を実行する。Runtime のテストは OCI へ接続しない。
- **worktree で Agent の backend を主の `.env` のまま起動しない**（#1916。主の dev server の実行中の Run を失敗にする）。確認は pytest と CI の実 Oracle の結合テストで行い、実画面で要るなら主の dev server を使う。
