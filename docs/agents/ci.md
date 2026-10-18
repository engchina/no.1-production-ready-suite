# CI とローカルの検証の範囲

> root の [AGENTS.md](../../AGENTS.md) から移した詳細（#2276）。root の規則と同じ強さで守る。規則を変えるときはこのファイルを直し、root には規則の要約と案内だけを書く。
> 正本: `.github/workflows/ci.yml`（冒頭のコメントと各 job）と、そこから呼ぶ workflow・script。

## CI

- PR と `main` の push で `.github/workflows/ci.yml` が動き、`changes` job が**変更のあった製品の job だけ**を動かす（platform の frontend / backend の分け方と、全製品を動かす変更の規則は `ci.yml` の冒頭）。製品のテストが platform の別の側のファイルを読むなら、`changes` の filter にそのファイルを足す。
- 必須 check は **`CI OK`** だけ（skip は成功扱い）。**job を追加したら `ci-ok` の `needs` に足し、すべての job に `timeout-minutes`（通常 10〜15 分、e2e は実測の倍程度）を付ける。**
- PR の e2e は約 1 分の smoke と、差分に影響を受ける spec（`e2e-impact`。smoke の spec は重ねず、全件なら smoke を飛ばす）。全件は `e2e-nightly.yml` が毎晩実行する。キャッシュの方針は `ci.yml` の platform の節のコメント。
- **PR の e2e は desktop が全件、375px（mobile）の project は画面幅に依存する test（`@responsive` の tag と、名前の括弧で `mobile` / `375px` を前提にするもの）だけ**（`ci.yml` の `E2E_MOBILE_SCOPE=responsive`。#1582）。nightly とローカルは両方の project で全件。
- **実 Oracle の結合テスト**（Oracle AI Database Free。`.github/workflows/oracle-integration.yml`）は、3 製品を `integration-nightly.yml` が毎晩、PR と main の push では DB の層（migration・system schema・Oracle の adapter / store・実 Oracle の fixture。`ci.yml` の changes の `<製品>_oracle`）を変えた製品だけを実行する。DB の層のファイルを足したら filter にも足す。**実 Oracle のテストは skip のまま成功にしない**: この CI では RAG の `oracle_db` が `RAG_TEST_REQUIRE_ORACLE=1` で失敗にし、`.github/scripts/pytest-skip-gate.py` が許可の一覧に無い skip と 0 件を失敗にする（#1595。DB の準備が壊れても「全部 skip で成功」にならない）。
- backend の pytest は CI で並列（`-n auto`）に実行する。**テストは並列でも直列でも通るように書く**（`tmp_path`・`parametrize` の `ids=`・差し替えられる待ち・`xdist_group`。[backend-standard.md](../../platform/docs/backend-standard.md)「テストの並列実行」）。
- `pip-audit` はその backend の `uv.lock` / `pyproject.toml`（か `ci.yml`）が変わったときだけ、`bandit` は毎回。`npm audit` は PR では実行しない。全件は `dependency-audit-nightly.yml` が毎晩（pip-audit と、4 つの `package-lock.json` の `npm audit --omit=dev --audit-level=high`）。
- **依存の manifest / lock（`package.json`・`package-lock.json`・`uv.lock`・`pyproject.toml`）か workflow・composite action を変えた PR は、`Dependency review` が追加・更新した依存の脆弱性（high 以上で失敗）と license を確かめる**（#1588）。
- CodeQL（python・javascript-typescript・actions）は `codeql.yml` が毎週の schedule と手動だけで実行する（PR では実行しない。#1594）。
- **schedule の workflow を足したら `ci-failure-issue.yml` の `workflows` にも足す**（nightly の失敗も Issue になる）。job の時間切れ（cancelled）も Issue になる（#1586）。
- runner は `runs-on: ubuntu-24.04`（`ubuntu-latest` は使わない。#1587）。本番の Compute の OS は Oracle Linux 9 だが、GitHub に Oracle Linux の runner が無いので ubuntu-24.04 を使い、OS の差は `oraclelinux:9` の container の job（`Suite / Oracle Linux 9`。配備のスクリプトの変更時）で確かめる（#1611）。
- main の push の CI は commit ごとに実行し、続けて merge しても前の run を取り消さない（PR だけ古い run を取り消す。#1585）。main の CI（push・毎日の schedule・手動）は、main で最後に成功した CI の commit からの変更で job を選ぶ。Dependabot の自動 merge（`GITHUB_TOKEN`）は push の CI を起動しないので、毎日の schedule で確かめる（#1609）。
- secret 検出は `.gitleaks.toml` / `.gitleaksignore`（書き方は `.gitleaks.toml` の冒頭）。誤検知は fingerprint 単位で `.gitleaksignore` に理由付きで除外する。
- Dependabot は 3 製品の backend（uv）・frontend（npm）、platform の package（uv・npm。lock だけを上げる）、RAG のサービス、actions を対象にする。公開から 5 日の `cooldown` を置き、npm は monthly（lock の変更は e2e の全件になる）、security updates は entry ごとの group にまとめる。patch / minor は `CI OK` の後に自動 merge される。major・一式の更新・group PR の失敗の扱いは `.github/dependabot.yml` の冒頭。
- Python の整形は `ruff format`（black は使わない。CI は `uv run ruff format --check .`）。整形だけの commit は `.git-blame-ignore-revs` に載せる。pre-commit（`.pre-commit-config.yaml`）は gitleaks と、commit する backend のファイルへの `ruff format --check` / `ruff check`。
- root の `scripts/` と `.github/` は `Suite / Scripts` が検査する（shellcheck・`scripts/tests/`・workflow の actionlint・zizmor。zizmor の設定と除外の理由は `.github/zizmor.yml`）。
- `AGENTS.md` の大きさは `Suite / Scripts` の `scripts/check-agents-md-size.sh` が確かめる（直下は 20 KiB、直下と各ディレクトリの `AGENTS.md` の合計は 32 KiB まで。`AGENTS.md`・`docs/agents/` の変更でも動く。#2276）。超えたら詳細を `docs/agents/`（製品は `<製品>/docs/agents/`）へ移す。

## ローカルの検証の範囲

ローカルでは変更した範囲だけを検査し、全件は CI に任せる。PR の `検証結果` には、ローカルの command と CI の job の結果を分けて書く。

- backend: 変更したファイルの `ruff check` / `ruff format --check`、関係するテスト（`uv run pytest tests/test_<対象>.py`）と `uv run pytest --lf -x`。`mypy` は変更したパッケージを渡してよい。全件を流すなら `-n auto`。全テスト・全体の `mypy`・`pip-audit` は CI。
- frontend: `npm run lint` と `npm run build`（型検査を兼ねる）、関係する単体テスト（`npx vitest related <ファイル>` / `--changed`、NL2SQL は `node --import jiti/register --test tests/<対象>.test.ts`）。
- e2e: 関係する spec だけ（[ui.md](./ui.md)「UI 変更の検証」）。
- worktree の準備は `scripts/setup-worktree.sh <製品…>`。
