# No.1 Production Ready Suite

Oracle / OCI 上で動く本番品質の AI 業務アプリ群（**RAG / NL2SQL / Agent**）と、その共通基盤をまとめた monorepo です。

## OCI へのデプロイ

ボタンを押すと、大阪リージョン（`ap-osaka-1`）を選んだ状態で OCI Resource Manager の stack の作成画面が開きます。1 つの Autonomous AI Database と、選んだ製品（RAG / NL2SQL / Agent）をまとめて入れる 1 台の Compute を作ります。

- **suite-v0.1.0**（最新）: 東京（`ap-tokyo-1`）/ 大阪（`ap-osaka-1`）リージョンだけをサポートします（既定は大阪）。
  - RAG / NL2SQL / Agent Control Plane を 1 つの stack にまとめた、最初の統合 release です。

  [![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-suite/releases/download/suite-v0.1.0/production-ready-suite-terraform-stack.zip)

ボタンは release [suite-v0.1.0](https://github.com/engchina/no.1-production-ready-suite/releases/tag/suite-v0.1.0) の asset `production-ready-suite-terraform-stack.zip`（checksum は `.sha256`）を読み込みます。版を固定せず常に最新の release を使う場合は、配備の URL の `releases/download/suite-v0.1.0` を `releases/latest/download` に替えてください。

入力の変数・手動でのアップロード・配備後の確認・トラブルシューティングは [terraform/README.md](./terraform/README.md) を参照してください。

## 構成

| ディレクトリ | 内容 |
|---|---|
| [`platform/`](./platform/) | 共通基盤。共有 UI `@production-ready/ui`、共有のシステム設定（`@production-ready/system-settings` / `pr_system_settings`）、共有 backend `pr_backend_core`、デザインシステム、3 製品共通の UX 契約（[`platform/docs/ux-contracts/`](./platform/docs/ux-contracts/README.md)） |
| [`rag/`](./rag/) | Production Ready RAG — 文書のナレッジ構築と、検索・回答プロファイルからの検索・回答 |
| [`nl2sql/`](./nl2sql/) | Production Ready NL2SQL — SQL 専用の自然言語問い合わせ |
| [`agent/`](./agent/) | Production Control Plane for AI Agents — Business Agent・Skill・Runtime の管理 |

- 依存は `platform/` → 各製品の一方向です。各製品は `platform/` を相対パスで参照し、backend / frontend / 実行環境（uv の venv）/ 配備はそれぞれ独立しています。
- 開発・起動方法は各ディレクトリの `README.md` を参照してください。

## 開発ルール

- 共通ルール（GitHub 運用・Issue / PR 規約・CI・デザインシステム）: [AGENTS.md](./AGENTS.md)
- 製品固有ルール: 各ディレクトリの `AGENTS.md`
- Claude Code（2.1.277 以降）と Codex は `AGENTS.md` を直接読み込みます。`CLAUDE.md` は置いていません。

## CI

`.github/workflows/ci.yml` が変更のあった製品の job だけを実行します（`platform/` の変更時は全製品）。必須 check は `CI OK` です。
