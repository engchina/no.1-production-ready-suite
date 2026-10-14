# No.1 Production Ready Suite

Oracle / OCI 上で動く本番品質の AI 業務アプリ群（**RAG / NL2SQL / Agent**）と、その共通基盤をまとめた monorepo です。

## OCI へのデプロイ

ボタンを押すと、大阪リージョン（`ap-osaka-1`）を選んだ状態で OCI Resource Manager の stack の作成画面が開きます。1 つの Autonomous AI Database と、3 製品（RAG / NL2SQL / Agent）をまとめて入れる 1 台の Compute を作ります。

- **latest**: 東京（`ap-tokyo-1`）/ 大阪（`ap-osaka-1`）リージョンだけをサポートします（既定は大阪）。

  [![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-suite/releases/latest/download/production-ready-suite-terraform-stack.zip)

ボタンは[最新の release](https://github.com/engchina/no.1-production-ready-suite/releases/latest) の `production-ready-suite-terraform-stack.zip` を読み込みます。

入力の変数・手動でのアップロード・配備後の確認・トラブルシューティングは [terraform/README.md](./terraform/README.md) を参照してください。

## 配備した後の初期設定（推奨のロールとユーザー）

配備の直後にログインできるのは、構成のシステム管理者（ログイン ID `system_admin`）だけです。
日々の管理と一般の利用には、次のロールを作ることを推奨します。`system_admin` は初期設定・システム設定の変更・緊急時だけに使います。

| ロールコード | ロール名 | 権限 |
|---|---|---|
| `APP_ADMIN` | アプリ管理者 | 3 製品の「システム設定」**以外**のすべての権限 |
| `APP_USER` | 一般利用者 | 3 製品の「AI 活用」の権限だけ |

- 「システム設定」（OCI 認証・アップロード保存先・モデル・データベース・外観と証明書）は `APP_ADMIN` にも付けず、`system_admin` だけが変えます（接続と認証の設定を日々の管理の操作で壊さないため）。

動作確認用のユーザー（配備の後に、ロールと権限が意図どおりかを確かめるためのもの）:

| ログイン ID | 表示名 | ロール |
|---|---|---|
| `app_admin` | アプリ管理者 | `APP_ADMIN` |
| `app_user` | 一般利用者 | `APP_USER` |

- **実際の利用では、このユーザーを共用せず、1 人に 1 つのユーザー（例 `taro.yamada`）を作って `APP_ADMIN` か `APP_USER` を付けてください。** 共用すると、実行履歴・承認の判断者・版の公開者などに誰の操作かが残りません。動作確認が済んだら、`app_admin` / `app_user` は「ユーザー管理」で無効にしてください。
- ロールコードは英大文字（画面で小文字を入れても大文字にそろえます）、ログイン ID は英小文字で書いています。

1. **ロールを作る**: どれか 1 つの製品に `system_admin` でログインし、「ユーザーとロール」→「ロール管理」で `APP_ADMIN` と `APP_USER` を作ります。ロールとユーザーは 3 製品で共通なので、1 回作れば 3 製品で使えます。
2. **権限を付ける**: 権限は製品ごとなので、**RAG・NL2SQL・Agent のそれぞれで**「セキュリティ設定」→「権限管理」を開き、`APP_ADMIN` には「システム設定」の項目以外のすべての権限を、`APP_USER` にはサイドナビの「AI 活用」の項目の権限だけを付けます。
   検索・回答プロファイル・業務プロファイル・エージェントなど「利用できる対象」を絞る項目があれば、`APP_USER` に使わせる対象を選びます。
3. **ユーザーを作る**: 「ユーザーとロール」→「ユーザー管理」で動作確認用の `app_admin`（ロール `APP_ADMIN`）と `app_user`（ロール `APP_USER`）を作り、それぞれでログインして、見えるメニューと操作を確かめます。

## 配備した後の運用

Compute（Oracle Linux 9）に `opc` で SSH して実行します（`ssh opc@<IP>`。output の `ssh_to_instance`。repository は `/u01/aipoc/no.1-production-ready-suite`）。

最新のコード（main）を取得して反映する（取得 → build → 全サービスの再起動）:

```bash
sudo /u01/aipoc/no.1-production-ready-suite/platform/deploy/suite-update.sh
```

全サービスを再起動する（今のコードから build してから再起動。`suite-pull.sh` で取得したコードもこれで反映されます）:

```bash
sudo /u01/aipoc/no.1-production-ready-suite/platform/deploy/suite-restart.sh
```

- build は、依存の同期・共有 UI と各製品の frontend・DB の migration・systemd・Nginx まで行います。build せずに再起動だけするときは `--no-build`、状態だけを見るときは `--status`。
- 2 つのスクリプト（`--status` を除く）は、最初に ADB の状態を確かめ、止まっていれば `platform/.env` の OCI の認証（システム設定 › OCI 認証 で保存した `~/.oci/config`）で起動し、AVAILABLE になってから続けます（最大 600 秒）。起動できないとき（OCI の認証が無い・権限が無い・時間切れ）も止まらず、理由と手で起動する方法を warning として出し、build と再起動を続けます（DB に接続できなくてもアプリケーションは起動します。#1756）。DB に接続できない間も `system_admin` はログインでき、「システム設定 › データベース」から ADB を起動すれば、スクリプトを実行し直さなくてもそのまま DB を使えます（#1762）。要る IAM の権限は [terraform/README.md](./terraform/README.md#adb-の自動の起動1745) を参照してください。
- 取得だけを行うときは `platform/deploy/suite-pull.sh`（追跡ファイルに変更があると中止します）。
- 画面で保存した設定（各製品の `backend/.env`・`platform/.env`）、Wallet、HTTPS の CA は保たれます。
- 時間がかかるので `tmux` の中で実行すると、SSH が切れても止まりません。log は `/var/log/suite-ops.log`。
- OS を再起動すると、各製品のサービスと Nginx は自動で起動します。

## 構成

| ディレクトリ | 内容 |
|---|---|
| [`platform/`](./platform/) | 共通基盤。共有 UI `@production-ready/ui`、共有のシステム設定（`@production-ready/system-settings` / `pr_system_settings`）、共有 backend `pr_backend_core`、デザインシステム、3 製品共通の UX 契約（[`platform/docs/ux-contracts/`](./platform/docs/ux-contracts/README.md)） |
| [`rag/`](./rag/) | Production Ready RAG — 文書のナレッジ構築と、検索・回答プロファイルからの検索・回答 |
| [`nl2sql/`](./nl2sql/) | Production Ready NL2SQL — SQL 専用の自然言語問い合わせ |
| [`agent/`](./agent/) | Production Control Plane for AI Agents — Business Agent・Skill・Runtime の管理 |
| [`skills/`](./skills/) | 組み込みのスキル（Agent Skills の形式 `skills/<name>/SKILL.md`）。Agent が読み、外部の Agent にも入れられる |

- 依存は `platform/` → 各製品の一方向です。各製品は `platform/` を相対パスで参照し、backend / frontend / 実行環境（uv の venv）/ 配備はそれぞれ独立しています。
- 開発・起動方法は各ディレクトリの `README.md` を参照してください。

## 外部の Agent でスキルを使う

`skills/` の 3 つのスキル（業務 RAG 調査・構造化データ照会・資料とデータの横断調査）は Agent Skills の形式なので、Claude Code・Codex などの外部の Agent にも入れられます。Claude Code では plugin marketplace から入れ、RAG / NL2SQL の MCP を自分の API キーで `rag` / `nl2sql` の名前で設定します。

```bash
claude plugin marketplace add https://github.com/engchina/no.1-production-ready-suite.git --sparse .claude-plugin skills
claude plugin install production-ready-skills@production-ready-suite
claude mcp add --scope user --transport http rag https://<host>/rag/api/mcp --header 'Authorization: Bearer ${PR_API_KEY}'
claude mcp add --scope user --transport http nl2sql https://<host>/nl2sql/api/mcp --header 'Authorization: Bearer ${PR_API_KEY}'
MCP_TOOL_TIMEOUT=600000 claude
```

手順の詳細（`claude -p` の許可・設定の JSON・つまずく所・Codex）は [skills/README.md](./skills/README.md)、API キーは [platform/docs/mcp-api-keys.md](./platform/docs/mcp-api-keys.md) を参照してください。

## 開発ルール

- 共通ルール（GitHub 運用・Issue / PR 規約・CI・デザインシステム）: [AGENTS.md](./AGENTS.md)
- 製品固有ルール: 各ディレクトリの `AGENTS.md`
- Claude Code（2.1.277 以降）と Codex は `AGENTS.md` を直接読み込みます。`CLAUDE.md` は置いていません。

## CI

`.github/workflows/ci.yml` が変更のあった製品の job だけを実行します（`platform/` の変更時は全製品）。必須 check は `CI OK` です。
