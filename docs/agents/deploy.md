# 配備

> root の [AGENTS.md](../../AGENTS.md) から移した詳細（#2276）。root の規則と同じ強さで守る。規則を変えるときはこのファイルを直し、root には規則の要約と案内だけを書く。
> 正本: `terraform/stack/`・`platform/deploy/`・[terraform/README.md](../../terraform/README.md)。

- 自前のコードは Docker を使わずネイティブで動かす（開発は `uv run` / `npm run dev`、本番は systemd + Nginx。Dockerfile・compose は持たない。Agent も組み込み Runtime で、Docker の Runtime は持たない）。
- Terraform stack は `terraform/stack/` に 1 つだけ。ADB を 1 つ作り（新規 / 既存）、選んだ製品（`deploy_rag` / `deploy_nl2sql` / `deploy_agent`、最低 1 つ）を 1 台の Compute に入れる。製品固有の入力は `rag_` / `nl2sql_` / `agent_` を付ける。
- Compute の上では `platform/deploy/suite-init.sh` が各製品の `init_script.sh`（製品は単独では配備しない）を順に呼び、Nginx の site（`/rag/` `/nl2sql/` `/agent/` の prefix・HTTPS・自作の CA・公開の port）を 1 つだけ書く。Nginx の location は `platform/deploy/suite-nginx.sh` だけに書く。frontend の base は build の `FRONTEND_BASE_PATH`（未指定は `/`。配備は `/<製品>/`）。
- **3 製品は既定で ADB のアプリ用の DB ユーザー（ADMIN ではない。既定の名前 `ADB_APP_USER`）で接続する**（#1668・#1675）。ユーザーの作成と grant は Compute の `suite-init.sh` が製品の配備の前に ADMIN で行い（`platform/deploy/suite-db-user.py`）、Terraform からは DB に接続しない（ADB は Compute からしか届かない）。DB の新しい機能を使うときは、付ける権限（`PDB_DBA` と Deep Data Security の 3 つ）で足りるかを確かめ、[terraform/README.md](../../terraform/README.md)「アプリ用の DB ユーザー」の表を更新する。
- **Compute の OS は Oracle Linux 9（image の OCID は `terraform/stack` で固定）、ログインと 3 製品のプロセスのユーザーは `opc`**（#1611）。配備のスクリプトは `dnf`（Nginx は AppStream の module）・`firewalld`・SELinux（enforcing のまま。context と boolean は `suite-init.sh` の `configure_selinux`）を前提にし、Ubuntu の分岐を持たない。
- CI は `Suite / Deploy`（`suite-deploy.yml`。release も再利用する）が各製品の `init_script.sh` と配備のスクリプト・`terraform/scripts/package_stack.py`・`verify_stack_contract.py`・`platform/deploy/tests/` を 1 つの job で実行し、`Suite / Oracle Linux 9` が `oraclelinux:9` の container で package の名前と Nginx の配置を確かめる（`Suite / Deploy` は製品の frontend・共有 UI・Markdown だけの変更では動かない。#1592）。実際の配備の通しは `deploy-nightly.yml` が毎晩、`oraclelinux:9` の systemd の container で `suite-init.sh` を実行して Nginx を通した 3 製品を確かめる（Oracle・SELinux・OCI の network は対象外。理由は `.github/scripts/deploy-run-through.sh` の冒頭。#1595）。
- release tag は `suite-v*`（`terraform-release.yml` が zip と sha256 と provenance を公開する）。release する commit は main にあり、その commit で main の CI が成功している必要がある（手動の実行は main から。#1594）。製品ごとの release（`<製品>-v*`）は作らない（既存の `nl2sql-v*` は残す）。README の配備ボタンは `releases/latest/download/<asset>` を指す（asset 名は変えない。新しい release を GitHub の Latest にする）。
