# No.1 Production Ready Suite

Oracle / OCI 上で動く本番品質の AI 業務アプリ群（**RAG / NL2SQL / Agent**）と、その共通基盤をまとめた monorepo です。

## OCI へのデプロイ

ボタンを押すと、大阪リージョン（`ap-osaka-1`）を選んだ状態で OCI Resource Manager の stack の作成画面が開きます。1 つの Autonomous AI Database と、3 製品（RAG / NL2SQL / Agent）をまとめて入れる 1 台の Compute を作ります。

- **latest**: 東京（`ap-tokyo-1`）/ 大阪（`ap-osaka-1`）リージョンだけをサポートします（既定は大阪）。

  [![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-suite/releases/latest/download/production-ready-suite-terraform-stack.zip)

ボタンは[最新の release](https://github.com/engchina/no.1-production-ready-suite/releases/latest) の `production-ready-suite-terraform-stack.zip` を読み込みます。

入力の変数・手動でのアップロード・配備後の確認・トラブルシューティングは [terraform/README.md](./terraform/README.md) を参照してください。

## 配備した後の運用

Compute に SSH で入って実行します（repository は `/u01/aipoc/no.1-production-ready-suite`）。

最新のコード（main）を取得して再配備する（依存の同期・build・DB の migration・全サービスの再起動まで）:

```bash
sudo /u01/aipoc/no.1-production-ready-suite/platform/deploy/suite-update.sh
```

全サービスを再起動する（コードは変えない。`--status` で状態だけを表示）:

```bash
sudo /u01/aipoc/no.1-production-ready-suite/platform/deploy/suite-restart.sh
```

- 画面で保存した設定（各製品の `backend/.env`・`platform/.env`）、Wallet、HTTPS の CA は更新の後も保たれます。
- 取得だけを行うときは `platform/deploy/suite-pull.sh`（追跡ファイルに変更があると中止します）。
- 時間のかかる更新は `tmux` の中で実行すると、SSH が切れても止まりません。log は `/var/log/suite-update.log`。
- OS を再起動すると、各製品のサービスと Nginx は自動で起動します。

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
