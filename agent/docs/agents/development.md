# Agent の開発と検証

> agent/AGENTS.md「開発・検証」の詳細。agent/AGENTS.md の規則と同じ強さで守る。

- `scripts/check-all.sh` は backend の ruff format / ruff check / mypy / pytest・bandit と frontend の lint / build を実行する。ローカルの既定は Playwright と pip-audit を省く。関係する spec だけ `SKIP_E2E=0 E2E_ARGS="e2e/<対象>.spec.ts" scripts/check-all.sh`、全部は `FULL=1`。
- CI の job は `Agent / Backend`・`Agent / Frontend`・`Agent / E2E smoke`。smoke に入れる spec は `ci.yml` の `agent-e2e` に書く（ログイン・認証・Run の stream・Runtime の主導線など、壊れると全体に響くもの。合計が CI でおおむね 1 分に収まる量）。
- 組み込み Runtime のテストは SDK の `ScriptedModel`（`agents.testing`）でモデルを台本にし、OCI へは接続しない。実環境の OCI Enterprise AI での確認は手動 / ステージングで行い、PR に分けて書く。

## worktree で backend を起動するとき（#1916）

主の `.env`（`platform/.env` の `PLATFORM_ORACLE_*`）のまま起動しない。主の dev server（8020）と同じ DB の Run・業務 Agent の保存先（`AGENT_RUNTIME_CHECKPOINTS` / `AGENT_RUNTIME_ENTITIES`）に 2 つ目のプロセスが書く。書き込みは head を lock して別のプロセスの変更を Run・業務 Agent の単位で取り込むので更新は消えないが、起動した側は主の dev server の実行中の Run を「中断した Run」として失敗にし（#1894）、互いのメモリは次に書くまで古いまま。

worktree の確認は pytest（保存先は memory・偽の Oracle）と CI の実 Oracle の結合テストで行い、実画面で要るなら主の dev server を使う。どうしても起動するなら、別の DB（別の `PLATFORM_ENV_FILE`）か `AGENT_RUNTIME_REPOSITORY_BACKEND=memory` にする。重なったときはログ `agent_runtime_checkpoint_concurrent_write`（相手の `other_writer_id`）が出る。
