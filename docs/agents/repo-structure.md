# repo の構成と製品間の連携

> 扱う範囲: monorepo のディレクトリと package の場所、依存の向きの詳細、製品間・外部の MCP クライアントとの連携の方法、製品から `platform/` を参照する方法。root の [AGENTS.md](../../AGENTS.md) の目次から読み、root の規則と同じ強さで守る（規則を変えるときはこのファイルを直す）。

## 構成

| ディレクトリ | 内容 |
|---|---|
| `platform/` | 3 製品の共通基盤: `packages/ui`（`@production-ready/ui`）、`packages/system-settings`（`@production-ready/system-settings`。共通のシステム設定・ユーザー / ロール管理の画面）、`packages/backend_core`（`pr_backend_core`）、`packages/system_settings_backend`（`pr_system_settings`。共通の設定 API と認証基盤）、`packages/evaluation_scoring`（`pr_evaluation_scoring`。製品と suite の評価が共有する採点の部品）、`packages/enterprise_ai`（`pr_enterprise_ai`。OCI Enterprise AI の URL の規則と失敗の分類。標準 library だけ）、`packages/anthropic_messages`（`pr_anthropic_messages`。Anthropic Claude の Messages API を公式の SDK で呼ぶ部品）、`docs/design-system/`、`deploy/`（1 台の Compute の配備） |
| `rag/` | Production Ready RAG |
| `nl2sql/` | Production Ready NL2SQL（SQL 専用の自然言語問い合わせ） |
| `agent/` | Production Control Plane for AI Agents |
| `suite/` | 製品をまたぐスイートのアプリケーション（独立の uv project。常駐のサービスは持たない）。`evaluation/`（`pr_suite_evaluation`）は製品横断の評価の CLI で、製品を公開の MCP と API キーだけで呼ぶ（[README](../../suite/evaluation/README.md)。#3240） |
| `skills/` | 組み込みのスキル（Agent Skills の形式 `skills/<name>/SKILL.md`）。Agent が読み、Claude Code・Codex などの外部の Agent にも配る（`.claude-plugin/` の plugin marketplace。#1816。[設計](../../agent/docs/agent-control-plane-design.md)） |
| `terraform/` | OCI Resource Manager の統合 stack（ADB 1 つ＋選んだ製品をすべて入れる 1 台の Compute。[README](../../terraform/README.md)） |

## 依存の向きと連携

- **依存の向きは `platform/` → 各製品・`suite/` の一方向。** platform は製品・suite を、製品は suite を import しない。suite は platform の公開の package だけに依存し、製品の module・DB・保存ファイルを読まない（`suite/evaluation/tests/test_boundaries.py`）。製品同士はコードで依存せず、HTTP API で連携する（rag は `rag/backend/tests/test_product_boundaries.py`）。Agent → RAG / NL2SQL は呼び先の `POST /api/mcp` を、Run の利用者を `sub` にした短命のサービストークン（署名鍵は `PLATFORM_SERVICE_TOKEN_SECRET`）で呼び、画面用の Cookie / CSRF の API を機械から呼ばない。呼び先は画面と同じ権限・対象範囲で判定する（[backend-standard.md](../../platform/docs/backend-standard.md)「製品間の連携（MCP とサービストークン）」）。
- **外部の MCP クライアント（Claude Code など）は 3 製品の `POST /api/mcp` を、利用者が自分で発行した API キー（`Authorization: Bearer prak_…`）で呼ぶ**（RAG / NL2SQL は調べるツール、Agent は業務 Agent へのチャットのツール。[手順](../../platform/docs/mcp-api-keys.md)。#1805・#1874）。RAG / NL2SQL は Agent を呼ばない（向きは外部のクライアント → Agent → RAG / NL2SQL）。
- 製品は `platform/` を相対パスで参照する（`file:../../platform/packages/ui`・`path = "../../platform/packages/backend_core"`・`../../platform/docs/design-system/…`）。publish や version pin はしない。
- 各製品の backend / frontend / uv の venv は独立している。配備は 1 台の Compute にまとめる（[deploy.md](./deploy.md)）。
