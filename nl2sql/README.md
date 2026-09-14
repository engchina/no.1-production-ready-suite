# Production Ready NL2SQL

Production Ready NL2SQL is a production-oriented NL2SQL reference implementation
for deploying an Oracle 26ai-backed application with OCI Enterprise AI and OCI
Generative AI.

## Deploy to OCI

Click the button below to open OCI Resource Manager with the Osaka region
(`ap-osaka-1`) selected by default.

[![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-nl2sql/releases/download/v0.1.31/production-ready-nl2sql-terraform-stack.zip)

The button downloads the v0.1.31 Terraform Resource Manager stack release asset:

`production-ready-nl2sql-terraform-stack.zip`

Latest release:
[v0.1.31](https://github.com/engchina/no.1-production-ready-nl2sql/releases/tag/v0.1.31).
To use the moving latest release instead of this pinned version, replace
`releases/download/v0.1.31` with `releases/latest/download` in the deploy URL.

The Compute deployment serves the frontend through Nginx on HTTP port `80` and
proxies API calls through the same origin at `/api/...`.

At least one GitHub Release must publish that asset before the one-click deploy
URL can create a stack. For manual packaging, upload steps, required variables,
and troubleshooting, see [terraform/README.md](terraform/README.md).

## OCI Compute のソース更新後の再デプロイ

[`scripts/update-after-pull.sh`](scripts/update-after-pull.sh) は、NL2SQL と同じ親ディレクトリにある
`/u01/aipoc/no.1-production-ready-platform` のローカルソース更新も NL2SQL に反映します。
**スクリプト自身は `git pull` を含む Git 操作を行わないため、先に両リポジトリを更新してください。**

**実行前に、Oracle データベースが起動済みで、Compute から接続可能であることを確認してください。**
OCI Autonomous Database が `Stopped`（停止済み）の場合は起動し、`Starting`（起動処理中）の場合は
`Available`（利用可能）になるまで待ちます。`backend/.env` で設定した接続アカウントには、
ログインとシステムテーブルの migration に必要な権限が必要です。
DB に接続できず migration が失敗すると、frontend のビルドが成功していても新しい画面は公開されず、
旧 frontend が維持され、external worker は停止・無効化されます。

通常のデプロイユーザー (`ubuntu`) で、次のコマンドを実行します。
[`scripts/git-pull.sh`](scripts/git-pull.sh) が platform → NL2SQL の順に更新し、
**両方の pull が成功した場合だけ** `&&` に続く再デプロイを実行します。

```bash
cd /u01/aipoc/no.1-production-ready-nl2sql &&
./scripts/git-pull.sh && sudo ./scripts/update-after-pull.sh
```

ソースの取得だけなら `./scripts/git-pull.sh` を実行します（`sudo` は不要）。
DB の起動が必要なのは再デプロイ時で、ソース取得だけなら DB 接続は不要です。
両 repository が `main` で追跡ファイルに未保存変更がないことを事前確認し、
`git pull --ff-only --no-rebase origin main` の後に取得 commit と `HEAD` の一致を検証します。
branch の自動切替や変更の破棄・stash は行いません。未追跡ファイル（例: `backend/..env.lock`）は
保持しますが、取得ファイルとの衝突は Git が拒否します。失敗時はエラーを確認してから再実行してください。
途中まで更新された repository は元に戻さないため、両方が成功するまで再デプロイへ進まないでください。
共有 platform は既定で NL2SQL と同じ親 directory を参照し、別配置では `PLATFORM_REPO_DIR` を指定できます。

古い checkout に `scripts/git-pull.sh` がまだない場合は、最初の一度だけ NL2SQL の `main` で
次を実行してスクリプトを取得します。

```bash
cd /u01/aipoc/no.1-production-ready-nl2sql &&
git pull --ff-only origin main &&
./scripts/git-pull.sh && sudo ./scripts/update-after-pull.sh
```

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
