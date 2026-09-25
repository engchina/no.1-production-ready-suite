# No.1 Production Ready Suite

Oracle / OCI 上で動く本番品質の AI 業務アプリ群（**RAG / NL2SQL / Agent**）と、その共通基盤をまとめた monorepo です。

| ディレクトリ | 内容 |
|---|---|
| [`platform/`](./platform/) | 共通基盤。共有 UI `@engchina/production-ready-ui`、共有のシステム設定（`@engchina/production-ready-system-settings` / `pr_system_settings`）、共有 backend `pr_backend_core`、デザインシステム、3 製品共通の UX 契約（[`platform/docs/ux-contracts/`](./platform/docs/ux-contracts/README.md)） |
| [`rag/`](./rag/) | Production Ready RAG — 文書のナレッジ構築と、業務ビューからの検索・回答 |
| [`nl2sql/`](./nl2sql/) | Production Ready NL2SQL — SQL 専用の自然言語問い合わせ |
| [`agent/`](./agent/) | Production Control Plane for AI Agents — Business Agent・Skill・Runtime の管理 |

- 依存は `platform/` → 各製品の一方向です。各製品は `platform/` を相対パスで参照し、backend / frontend / Docker image / 配備はそれぞれ独立しています。
- 開発・起動方法は各ディレクトリの `README.md` を参照してください。

## 開発ルール

- 共通ルール（GitHub 運用・Issue / PR 規約・CI・デザインシステム）: [AGENTS.md](./AGENTS.md)
- 製品固有ルール: 各ディレクトリの `AGENTS.md`
- Claude Code は `CLAUDE.md`、Codex は `AGENTS.md` を読み込みます。

## CI

`.github/workflows/ci.yml` が変更のあった製品の job だけを実行します（`platform/` の変更時は全製品）。必須 check は `CI OK` です。

## 経緯

2026-09-25 に、旧4 repo（`engchina/no.1-production-ready-platform` / `-rag` / `-nl2sql` / `-agent`）を履歴ごと統合しました（[#71](https://github.com/engchina/no.1-production-ready-suite/issues/71)）。旧 platform repo を rename したのが本 repository で、旧 rag / nl2sql / agent repo は archive 済みです。
