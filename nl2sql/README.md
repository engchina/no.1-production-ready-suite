# Production Ready NL2SQL

Production Ready NL2SQL is a production-oriented NL2SQL reference implementation
for deploying an Oracle 26ai-backed application with OCI Enterprise AI and OCI
Generative AI.

## Deploy to OCI

Click the button below to open OCI Resource Manager with the Osaka region
(`ap-osaka-1`) selected by default.

[![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-suite/releases/download/nl2sql-v0.1.32/production-ready-nl2sql-terraform-stack.zip)

The button downloads the nl2sql-v0.1.32 Terraform Resource Manager stack release asset
from the `no.1-production-ready-suite` monorepo:

`production-ready-nl2sql-terraform-stack.zip`

Latest release:
[nl2sql-v0.1.32](https://github.com/engchina/no.1-production-ready-suite/releases/tag/nl2sql-v0.1.32).
The monorepo publishes releases for several products, so always pin the
`nl2sql-v*` tag instead of `releases/latest`. Releases up to v0.1.31 remain in the
archived `no.1-production-ready-nl2sql` repository, but they clone the old
two-repository layout and must not be used for new deployments.

The Compute deployment serves the frontend through Nginx on HTTP port `80` and
proxies API calls through the same origin at `/api/...`.

At least one GitHub Release must publish that asset before the one-click deploy
URL can create a stack. For manual packaging, upload steps, required variables,
and troubleshooting, see [terraform/README.md](terraform/README.md).

## OCI Compute のソース更新後の再デプロイ

NL2SQL は monorepo `/u01/aipoc/no.1-production-ready-suite` の `nl2sql/` に、共有 platform は同じ repository の
`platform/` に配置します。[`scripts/update-after-pull.sh`](scripts/update-after-pull.sh) は `platform/` の更新も NL2SQL に反映します。
**スクリプト自身は `git pull` を含む Git 操作を行わないため、先に suite repository を更新してください。**
旧構成（`/u01/aipoc/no.1-production-ready-nl2sql` と `/u01/aipoc/no.1-production-ready-platform` の2つの repository）から
移行する場合は [docs/monorepo-server-migration.md](docs/monorepo-server-migration.md) の手順に従ってください。
#211 より前に作った環境（`backend/.env` に `ORACLE_DSN` などの旧名を置いている環境）は、更新前に
[docs/configuration.md](docs/configuration.md) の「既存環境の更新手順（#211）」で共通 `.env`（`platform/.env`）へ移してください。

**実行前に、Oracle データベースが起動済みで、Compute から接続可能であることを確認してください。**
OCI Autonomous Database が `Stopped`（停止済み）の場合は起動し、`Starting`（起動処理中）の場合は
`Available`（利用可能）になるまで待ちます。共通 `.env`（`platform/.env`）で設定した接続アカウントには、
ログインとシステムテーブルの migration に必要な権限が必要です。
DB に接続できず migration が失敗すると、frontend のビルドが成功していても新しい画面は公開されず、
旧 frontend が維持され、external worker は停止・無効化されます。

通常のデプロイユーザー (`ubuntu`) で、次のコマンドを実行します。
[`scripts/git-pull.sh`](scripts/git-pull.sh) が suite repository を更新し、
**pull が成功した場合だけ** `&&` に続く再デプロイを実行します。

```bash
cd /u01/aipoc/no.1-production-ready-suite/nl2sql &&
./scripts/git-pull.sh && sudo ./scripts/update-after-pull.sh
```

ソースの取得だけなら `./scripts/git-pull.sh` を実行します（`sudo` は不要）。
DB の起動が必要なのは再デプロイ時で、ソース取得だけなら DB 接続は不要です。
suite repository が `main` で追跡ファイルに未保存変更がないことを事前確認し、
`git pull --ff-only --no-rebase origin main` の後に取得 commit と `HEAD` の一致を検証します。
branch の自動切替や変更の破棄・stash は行いません。未追跡ファイル（例: `backend/..env.lock`）は
保持しますが、取得ファイルとの衝突は Git が拒否します。失敗時はエラーを確認してから再実行してください。
pull が成功するまで再デプロイへ進まないでください。
共有 platform は既定で suite repository の `platform/` を参照し、別配置では `PLATFORM_REPO_DIR` を指定できます。

引数なしで実行すると、共有コンポーネントの更新を次のように反映します。

- **共有 UI**: platform で `npm ci` と
  `npm run build --workspace @engchina/production-ready-ui` を実行した後、
  NL2SQL frontend の依存を同期し、再ビルドした成果物を公開します。
- **共有 backend core**: NL2SQL backend は platform の `packages/backend_core` を
  ローカルの editable 依存として参照しています。`uv sync --locked --no-dev --python 3.12`
  による依存同期と backend・worker の再起動を経て、更新されたコードを読み込みます。

**platform だけが更新された場合も、同じ手順を使用できます。**
反映対象は NL2SQL が利用する共有コンポーネントであり、platform 内のすべての独立アプリを
デプロイするものではありません。通常の更新処理には OCI の権限修復、NL2SQL の
database migration、サービス再起動、health check も含まれます。

`--repair-only` は依存同期とビルド・frontend 公開を省略するため、共有コンポーネントの更新を
反映するときは **引数なし** で実行してください。`--check` は読み取り専用の確認です。
詳細な前提条件と運用手順は [terraform/README.md](terraform/README.md) を参照してください。

## Stack Boundaries

This deployment keeps the approved architecture:

- LLM/VLM: OCI Enterprise AI
- Embedding/rerank: OCI Generative AI
- Vector search and application state: Oracle 26ai
