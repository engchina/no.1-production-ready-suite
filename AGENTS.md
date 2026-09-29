# AGENTS.md — No.1 Production Ready Suite（monorepo 共通ルール）

> このファイルは **monorepo 全体に適用する共通ルールの正本**です。Claude Code と Codex の両方が参照します。
> 製品固有のルールは各ディレクトリの `AGENTS.md`（`platform/` `rag/` `nl2sql/` `agent/`）にあり、**作業対象のディレクトリの `AGENTS.md` もあわせて適用**します。
> 共通ルールと製品固有ルールが矛盾する場合は、製品固有ルールを優先します（ただし GitHub 運用と CI は本ファイルが優先）。
> `CLAUDE.md` は各階層の `AGENTS.md` を `@AGENTS.md` で取り込みます。ルール変更は **AGENTS.md 側を編集**してください。

## 構成

| ディレクトリ | 内容 | 固有ルール |
|---|---|---|
| `platform/` | 3製品の共通基盤。`packages/ui`（`@engchina/production-ready-ui`）、`packages/system-settings`（`@engchina/production-ready-system-settings`、共通のシステム設定画面とユーザー管理・ロール管理画面）、`packages/backend_core`（`pr_backend_core`）、`packages/system_settings_backend`（`pr_system_settings`、共通のシステム設定 API と共通認証基盤（ユーザー・ロール・セッション・ログイン））、デザインシステム（`docs/design-system/`） | [platform/AGENTS.md](./platform/AGENTS.md) |
| `rag/` | Production Ready RAG（ナレッジ構築・業務ビュー・検索・回答） | [rag/AGENTS.md](./rag/AGENTS.md) |
| `nl2sql/` | Production Ready NL2SQL（SQL 専用の自然言語問い合わせ） | [nl2sql/AGENTS.md](./nl2sql/AGENTS.md) |
| `agent/` | Production Control Plane for AI Agents | [agent/AGENTS.md](./agent/AGENTS.md) |
| `terraform/` | 3製品を OCI Resource Manager で配備する統合 stack（ADB 1つ＋選んだ製品ごとの Compute） | [terraform/README.md](./terraform/README.md) |

- **依存の向きは `platform/` → 各製品の一方向。** 製品同士はコードで依存しない。製品間の連携（例: Agent が RAG / NL2SQL を呼ぶ）は HTTP API 経由にする。
  - Agent → RAG / NL2SQL は、呼び先の `POST /api/mcp`（MCP）を、Run の利用者を `sub` にした短命のサービストークン（署名鍵は共通 `.env` の `PLATFORM_SERVICE_TOKEN_SECRET`）で呼ぶ。画面用の Cookie / CSRF の API を機械から呼ばない。呼び先は画面と同じ権限・対象範囲で判定する。詳細は [platform/docs/backend-standard.md](./platform/docs/backend-standard.md) の「製品間の連携（MCP とサービストークン）」（#230〜#233）。
- 製品は `platform/` を相対パスで参照する（frontend: `file:../../platform/packages/ui` / `file:../../platform/packages/system-settings`、backend: `path = "../../platform/packages/backend_core"`、lint: `../../platform/docs/design-system/…`）。パッケージの publish や version pin は行わない。
- 各製品の backend / frontend / 実行環境（uv の venv）/ 配備は独立している。まとめているのはソースと CI だけ。
- 2026-09-25 に旧4 repo（`no.1-production-ready-{platform,rag,nl2sql,agent}`）を統合した（#71）。旧 repo は archive 済みで、commit message 内の `engchina/no.1-production-ready-<製品>#N` は旧 repo の Issue / PR を指す。

## 言語

- システムの主要言語は日本語。UI 文言・エラーメッセージ・通知・LLM への指示と出力は日本語を前提とし、ユーザー向け文言は i18n 経由で管理する。
- Issue / PR / commit message / コードコメントは日本語で書く。code identifier、API path、file path、command、製品・ライブラリの固有名詞は英語のままでよい。

## 開発ワークフロー / GitHub 運用

- **`main` ブランチへ直接 commit / push / 変更しない。** すべての変更は GitHub Issue を先に作成し、Issue に紐づく作業ブランチで行う。
- 作業ブランチ名は既定で `codex/<issue-number>-<short-topic>` とする。既存 ref との衝突などで使用できない場合も、Issue 番号と作業内容が分かる名前を使う。
- Issue には対象の label（`product:rag` / `product:nl2sql` / `product:agent` / `platform`）を付ける。複数にまたがる場合はすべて付ける。
- 変更後は Pull Request を作成し、関連 Issue、変更内容、検証結果を PR description に明記する。`platform/` と製品にまたがる変更は、1つの PR にまとめて同時に検証してよい。
- **変更・必要な検証・PR 本文の更新が完了し、PR の最新 commit に対する CI/checks（必須 check `CI OK`）が成功したら、追加のユーザ確認を求めず自動で `main` へ merge する。** PR 作成や CI 成功の報告だけで作業を終了しない。merge は **Create a merge commit** で行う（main の ruleset が削除・force push を禁止し、`CI OK` を必須にしている）。
  - **CI の完了はエージェント自身が待つ（#438）。** PR の作成・push・`gh pr update-branch` の後に CI が実行中でも、「pass したら声をかけてください」と報告して止まらず、ユーザーに merge の指示を求めない。待ち方は、PR の最新 commit の CI の run を `gh run watch` で待ち（下の例）、終わったら `gh pr checks <PR 番号>` で `CI OK` が pass であることを確かめる。実行環境が提供する PR / CI の監視機能を使ってもよい。待っている間に別の独立した作業（次の Issue の調査など）を進めてよい。
    - `gh pr checks --watch --required` は使わない（#446）。`CI OK` は最後の job で、他の job が終わるまで check として存在しないため、CI の開始直後は対象が 0 件になり、待たずに終わる。

    ```bash
    sleep 20   # push の直後は run がまだ作られていないことがある
    run_id=$(gh run list --workflow CI --branch <作業ブランチ> --limit 1 --json databaseId -q '.[0].databaseId')
    gh run watch "$run_id" --exit-status   # exit code 0 なら run が成功
    gh pr checks <PR 番号> | grep "CI OK"
    ```

  - 複数の PR を続けて merge するときは、先の PR の merge で main が進むので、後の PR は下の「main の取り込み」の判定をやり直し、必要なら取り込んだ後の CI をもう一度待ってから merge する。
- CI/checks の失敗や merge conflict がある場合は、原因を修正・解消し、最新 commit を再検証してから merge する。branch protection / ruleset を迂回した強制 merge は行わない。解消できない場合は原因と未完了の操作を明示する。
- **main の取り込みは必要なときだけ行う（#339）。** main の ruleset は、PR の branch が main の最新を含むこと（up-to-date、strict）を求めない。strict のときは main が進むたびに「main の取り込み → CI の再実行」が直列に起き、1 週間で merge 133 件に対して取り込みが 126 回あり、PR が main に入るまでの待ちの大半を占めていたためやめた（`allow_update_branch` は有効）。
  - merge の前に、**PR の最新の CI が走ったとき以降に、main で PR が触る製品のディレクトリ（`rag/` `nl2sql/` `agent/` `terraform/`）か `platform/`・`.github/` が変わっていたら**、main を取り込んで（`gh pr update-branch <PR 番号>` か `git merge origin/main`）CI をやり直してから merge する。変わっていなければ取り込まずに merge してよい。
  - 判定の例（`<CI の base>` は、PR の最新の CI が始まった時点の main の commit）:

    ```bash
    git fetch origin
    started=$(gh run list --workflow CI --branch <作業ブランチ> --limit 1 --json createdAt -q '.[0].createdAt')
    ci_base=$(git rev-list -1 --first-parent --before="$started" origin/main)   # <CI の base>
    git diff --name-only "$ci_base"..origin/main -- rag/ platform/ .github/     # 何か出たら取り込んで CI をやり直す
    ```

  - それでも main が壊れることはある（別々の PR が同時に入った組み合わせ）。main の push の CI と nightly が失敗すると `.github/workflows/ci-failure-issue.yml` が Issue（label `ci-failure`）を作る。**main が赤になったら、ほかの作業より先に直す。** 直す PR はその Issue を `Closes` する。
- **merge 後はローカルブランチを必ず `main` に切り替え、`git fetch --prune` で削除済みリモートブランチの参照を掃除したうえで `origin/main` へ fast-forward 同期してから完了を報告する。** PR の merge 状態、ローカルブランチ、同期状態を確認する。ユーザの未保存変更を破棄する `reset --hard` 等は使わず、変更を保持したまま安全に同期する。同期できない場合は理由と残作業を明示する。
- docs-only の小さな変更や緊急修正も原則として同じ Issue → branch → PR → CI/checks → main merge の流れに従う。例外が必要な場合は、理由を添えてユーザ確認を取る。
- 並行して作業する別のセッションやプロセスの未コミット変更・stash・worktree を、確認なしに破棄・上書きしない。

### GitHub Issue / Pull Request の記述規約

#### 共通

- Issue / PR のタイトルと本文は**原則として日本語**で記述する。
- タイトルは対象と事象が分かる具体的な文にする。「不具合」「修正」「対応」だけの曖昧なタイトルにしない。
- 本文は Markdown 見出しで構造化し、確認した事実と推測を区別する。未調査・未確定の項目は断定せず「調査中」「未確認」と明記し、判明後に本文を更新する。
- API、関数、設定 key、status code、error message、再現値など、調査・レビュー・回帰テストに必要な具体情報を記載する。secret、token、個人情報、実 credential は記載しない。
- 製品ごとの用語規約（例: RAG の `ナレッジ構築` / `業務ビュー` / `検索・回答設定`）は各製品の `AGENTS.md` に従う。

#### Issue

- Issue の種別にかかわらず、最低でも `問題`、`症状`、`原因`、`修正方針` の4項目を含める。初回登録時に原因が未確定でも `原因` を省略せず、現時点の仮説または「調査中」と記載する。

```markdown
## 問題

何が問題なのかを記載する。

## 症状

どのような入力や条件で何が起きるのかを、画面/API/状態/error message などの観測事実に基づいて記載する。

## 原因

どの code path、data flow、または設計が原因と考えられるかを記載する。未確定の場合は仮説と未確認事項を区別する。

## 修正方針

どこを、どのような考え方で修正するかを、責務境界と変更しない範囲を含めて記載する。
```

- 可能であれば `影響範囲`、`再現手順`、`関連ファイル`、`必要なテスト` も追加する。必要に応じて `期待動作`、`完了条件`、`補足`、`ログ`、`スクリーンショット`、`代替案`を追加する。
- feature / docs / refactor / investigation Issue では、`問題` に背景や現在の不足、`症状` に現状の制約や具体例、`原因` に設計上の理由または調査対象を記載し、4項目を Issue の性質に合わせて具体化する。
- 長い log は必要箇所だけを抜粋し、再現に不要な出力を貼らない。
- `完了条件`は「対応する」のような作業表現だけにせず、期待状態と必要な test / lint / build / 手動確認を判定可能な形で列挙する。

#### Pull Request

- PR title は原則として `<type>(<scope>): <日本語の要約> (#<issue-number>)` とする。`type` は `feat` / `fix` / `docs` / `test` / `refactor` / `chore` / `ci` 等、`scope` は `rag` / `nl2sql` / `agent` / `platform`（複数にまたがる場合や monorepo 全体は省略可）。
- PR 本文は原則として次の見出しを使用する。

```markdown
## 関連 Issue

Closes #<issue-number>

## 背景 / 原因

Issue の要点と、この変更が必要な理由を記載する。bug fix では根因を記載する。

## 変更内容

- 変更した責務・挙動を具体的に記載する
- schema / API / UI / data migration / compatibility への影響を記載する

## 検証結果

- `<実行した command>` — pass / fail / skip と件数
- 手動確認または Playwright の対象 flow / viewport / 状態

## 既知の制約・残課題

- 未対応範囲、既知の制約、follow-up Issue を記載する。なければ「なし」と記載する。
```

- `関連 Issue` には、merge で完了する Issue は `Closes #N`、参照のみは `Refs #N` と記載する。複数ある場合はすべて列挙する。
- `変更内容` は commit の羅列ではなく、reviewer が挙動差分と責務境界を判断できる粒度で記載する。変更していない重要範囲や backward compatibility も必要に応じて明記する。
- `検証結果` には実行した正確な command と結果を記載する。失敗・skip・未実行を隠さず、今回の変更によるものか既存問題かを分ける。実行できない test がある場合は理由と代替確認を記載する。command は各製品の `AGENTS.md` の開発コマンドを基本とする。ローカルで実行しなかった全件の検査（backend の全テスト・mypy・pip-audit・e2e の smoke 等）は、PR の CI の job 結果（job 名と pass / fail）を引用してよい（「共通の技術方針」の「ローカルの検証の範囲」）。
- `platform/` を変更した場合は、影響を受ける製品の検証結果（統合 CI の該当 job を含む）も記載する。
- UI/UX 変更では、対象 Playwright spec、desktop / 375px viewport、主要導線と重要状態(空/読込/エラー/ブロック)の結果を記載する。見た目を変更した場合は必要に応じて screenshot または visual check の結果を添える。
- OCI / Oracle / LLM を呼ぶ範囲の変更では、CI 上の決定論スタブによる確認と、手動/ステージングでの実サービス確認をそれぞれ区別して記載する。
- docs-only など test 対象外の場合も `検証結果` を省略せず、`git diff --check` 等の実施結果と、コード test を実行しない理由を記載する。
- PR 作成後に追加修正や検証結果の変化があった場合は、コメントだけで済ませず PR 本文を最終状態へ更新してから review / merge する。

## CI

- PR と `main` への push で `.github/workflows/ci.yml`（統合 CI）が動く。`changes` job が変更パスを判定し、**変更のあった製品の job だけ**を実行する。
  - `platform/` は frontend（`packages/ui`・`packages/system-settings`・`docs/design-system/`・`package.json`・`package-lock.json`）と backend（`packages/backend_core`・`packages/system_settings_backend`）に分けて判定する。platform の frontend の変更は各製品の frontend / e2e の job を、backend の変更は各製品の backend の job を動かす（#339）。
  - `ci.yml`・`.github/actions/` と、どちらにも属さない platform のファイル（`.env.example`・`contracts/`・`scripts/`・`templates/` 等）の変更は、両方として全製品の job を動かす。platform の Markdown（`AGENTS.md`・README・`docs/ux-contracts/` 等）だけの変更では製品の job を動かさない。
  - 製品のテストが platform の別の側のファイルを読むとき（例: NL2SQL の frontend の契約テストが `system_settings_backend` の `model.py` / `database.py` を読む）は、`changes` の filter にそのファイルを足す。
- 必須 check は **`CI OK`** の1つだけ（skip された job は成功扱い、failure / cancelled があれば失敗）。job を追加したら `ci-ok` の `needs` にも追加する。
- **すべての job に `timeout-minutes` を付ける**（通常 10〜15 分、e2e は実測の倍程度）。既定の 360 分のまま止まると、runner と PR の待ちを長く塞ぐ。
- キャッシュ（#339）: uv は main の push だけが保存し PR は復元だけ（repo の上限 10 GB を PR の cache で埋めないため）、`.mypy_cache` は lock とブランチを key に main から復元、共有 UI の dist は `.github/actions/platform-ui`（platform の frontend のソースの hash）、Playwright のブラウザは `.github/actions/playwright-browsers`（Playwright の版）がキャッシュする。
- Agent は `Agent / Backend`・`Agent / Frontend`（lint・build）・`Agent / E2E smoke` の 3 job に分けている（#339）。
- backend の pytest は、CI では pytest-xdist で並列に実行する（`uv run pytest -n auto`。Agent は `scripts/check-all.sh` に `PYTEST_ARGS="-n auto"` を渡す。#344）。テストは並列でも直列でも通るように書く。
  - 書き出すファイルは `tmp_path` に置く（backend 直下など固定のパスを、別のテストと共有しない）。
  - `parametrize` のテスト ID を、実行のたびに変わる値（作成時刻を含む zip / xlsx / docx の bytes 等）から作らない。worker ごとに ID が変わり収集が失敗するので、`ids=` で固定する。
  - retry / backoff の待ちは `time.sleep` を直接呼ばず、module 変数などで差し替えられるようにし、テストは conftest で待たない関数にする（例: NL2SQL の `reverse_generation._retry_sleep`）。
  - 同時に動かせないテスト（RAG の実 Oracle のテスト）は `xdist_group` で 1 つの worker にまとめる（RAG は `--dist loadgroup` を pyproject の `addopts` に入れている）。
- `pip-audit` は、その backend の `uv.lock` / `pyproject.toml`（または `ci.yml`）が変わったときだけ PR / main の CI で実行する。全件は `.github/workflows/dependency-audit-nightly.yml` が毎晩実行する。`bandit` は毎回実行する。
- nightly（`e2e-nightly.yml`・`rag-evaluation-nightly.yml`・`dependency-audit-nightly.yml`）と main の push の CI が失敗すると、`ci-failure-issue.yml` が Issue（label `ci-failure` と製品の label）を作る。同じ workflow・製品の open な Issue があれば comment で追記する。schedule の workflow を追加したら、`ci-failure-issue.yml` の `workflows` にも追加する。
- secret 検出は root の `.gitleaks.toml` / `.gitleaksignore`（pre-commit hook は `.pre-commit-config.yaml`）。CI の gitleaks-action は gitleaks 8.24 系のため、allowlist は単一の `[allowlist]` で書く（`[[allowlists]]` は解釈されない）。誤検知の除外は fingerprint 単位で `.gitleaksignore` に理由付きで追加する。
- OCI Resource Manager の Terraform stack は root の `terraform/stack/` に1つだけ置く（#217）。ADB を1つ（新規 / 既存）作り、選んだ製品（`deploy_rag` / `deploy_nl2sql` / `deploy_agent`、最低1つ）ごとに Compute を1台作る。製品固有の入力は `rag_` / `nl2sql_` / `agent_` の接頭辞を付ける。Compute 上の配備手順は各製品の `init_script.sh` が持つ。CI は `Suite / Terraform` job が `terraform/scripts/package_stack.py` と `verify_stack_contract.py` を実行する。
- Terraform stack の release tag は `suite-v*`（例: `suite-v0.1.0`）。`.github/workflows/terraform-release.yml` が zip と sha256 を公開する。製品ごとの release（`<製品>-v*`、#94）は作らない（既存の `nl2sql-v0.1.32` 等は残す）。README 等では `releases/latest` ではなく tag を固定して参照する。
- Dependabot（`.github/dependabot.yml`）の patch / minor 更新は `CI OK` 成功後に自動 merge される（`dependabot-auto-merge.yml`）。失敗し続ける major は `ignore` に理由付きで書き、peer dependency で結び付く一式（eslint 等）は major も 1 本の PR にまとめる。group PR が失敗したときの扱いは `dependabot.yml` の冒頭に書く。
- pre-commit（`.pre-commit-config.yaml`）は gitleaks と、commit する backend の Python ファイルだけへの `ruff format --check` と `ruff check`（その backend の uv 環境と設定）を実行する。
- Python の整形は `ruff format`（#345。black から置き換えた）。設定は各 backend の `pyproject.toml` の `[tool.ruff.format]`（black と同じ既定。行の長さは `[tool.ruff]` の 100 を共有）で、CI の `Format check` は `uv run ruff format --check .`。black は使わない。整形だけの commit は `.git-blame-ignore-revs` に載せる。

## デザインシステム / UI（platform が正本）

- **UI に触る変更（各製品の `frontend/`）の前に、[platform/docs/design-system/ARCHITECTURE.md](./platform/docs/design-system/ARCHITECTURE.md) を読む。**
  - トークン値・コンポーネント仕様・意図的な見た目の変更点: 同 `README.md`
  - 実装の参照: 同 `components-reference.md`
- **依存の向きは「デザインシステムの決定 → `@engchina/production-ready-ui`（`platform/packages/ui`）→ 各製品」の一方向。** 製品側でコンポーネントやトークンを新規実装しない。必要になったら `platform/packages/ui` へ入れる Issue を立てる（monorepo なので、同じ PR で platform と製品を同時に変更してよい）。
- **製品が持てるのは次だけ。**
  - ナビ構造（nav config）と業務コピー（i18n）
  - データ取得・状態管理・権限
  - ドメイン enum → コンポーネント prop の対応表（例: 状態 → `StatusBadge` の `variant`）
  - 画面固有の業務レイアウト
  - 1製品しか使わない部品は置いてよい。判断基準は「他の2製品がこれを欲しがるか」で、欲しがるなら `packages/ui` に入れる
- **色・型・余白・角丸・影・モーション・フォーカス表示・テーマ（light / dark / auto）は `packages/ui` が持つ。** `frontend/src/globals.css` は `@import "tailwindcss"` → `@import "@engchina/production-ready-ui/styles.css"` → `@source "../node_modules/@engchina/production-ready-ui/dist"` と、画面固有のレイアウトだけにする。`main.tsx` から JS で import すると共有ユーティリティが生成されない。

### 禁止事項

- 生の hex（`#1a73c1` 等）と生の px を書く。色は `--color-*` トークン（`bg-surface` / `text-fg-muted` / `border-border-control` 等のユーティリティ）を使う。旧名（`bg-card` / `text-muted` / `bg-primary` / `var(--primary)` / `--graph-line` 等）は platform で削除済みで、書くと未定義になり色が付かない。
- `globals.css` に色トークンや `.dark { … }` の上書きを定義する。
- `TextField` / `PageHeader` / `Button` / `StatusBadge` などの共有コンポーネントを再実装する。
- `<table>` を手書きする。`DataTable` を使う。例外は「元の文書の表を再現して編集するグリッド」（見出し行がなく、列数が表ごとに変わるもの。RAG の `ReviewTextEditor.tsx`）だけで、使う理由をコードのコメントに書く（#129）。
- `<div className="px-8 py-6">` や `style={{ padding: "1.5rem 2rem" }}` のような余白コンテナを手書きする。`PageBody` を使う。
- `ToggleChip` をタブ代わりに使う。タブ＝同じ対象の別の見方に切り替えるのは `Tabs`、チップ＝データの絞り込みは `ToggleChip`。
- `loading` 中にボタンのラベルを「実行中…」等に差し替える。ラベルは変えず、`icon` がスピナーに置き換わる。子要素にアイコンを書かず `icon={Upload}` で渡す。`loading` を渡す `Button` は必ず `icon` を持つ。
- フォーカスの表示を `focus:ring-*` / `focus-visible:ring-*` で作る、`focus(-visible):outline-none` で消す。フォーカスの表示はグローバルの `:focus-visible`（outline）1 つに任せ、形の調整は `focus-visible:outline-*` / `-outline-offset-*` で行う（#355）。
- 製品ごとのアクセント色を作る。製品は wordmark・ナビ・内容で区別する。
- 絵文字と手描き SVG。アイコンは `lucide-react`（14 / 16 / 20 / 24px のみ）。
- `@engchina/production-ready-ui` の内部パス（`dist/components/**` や `dist/tokens/*.css`）を import したりテストで読んだりする。パッケージのルートと `styles.css` だけを使う。

### 画面の構成

```tsx
<AppShell sidebar={<Sidebar … footer={<SidebarAccountFooter … />} />}>
  <PageHeader title="…" actions={[{ id, kind: "primary", label, icon }]} tabs={<Tabs … />} />
  <PageBody>
    <Section title="…">…</Section>
  </PageBody>
</AppShell>
```

- `PageHeader` の `actions` は配列で渡す（danger → utility → secondary → primary の順に自動で並び、右端が primary になる）。
- `PageHeader` と `PageBody` に `wide` を渡す場合は必ず両方に同じ値を渡す。片方だけだと 1920px でタイトルと本文の左端がずれる。
- 単位の境界: 文字サイズとコントロール高さは px、余白とレイアウト寸法は rem（14px ルート）。
- 本文は日本語第一フォントスタック `"Noto Sans JP", "Roboto", system-ui, sans-serif`、本文ベース `14px`。

### 読み込み中・一覧・ページング（3 製品で統一。NL2SQL が基準）

- **読み込み中**: 内容（一覧・詳細・フォームの初期値など）を取得している間は、その領域の先頭に `TimedLoadingState`（または `ProcessingIndicator`）で「〜を読み込んでいます」と経過時間を出し、領域は**内容の形をした灰色の `Skeleton` で覆う**（寸法を予約して CLS を出さない）。形は `packages/ui` の `TableSkeleton`（表）・`ListSkeleton`（行リスト）・`FormSkeleton`（設定カード・エディタ）か `Skeleton` の組み合わせで作り、`TimedLoadingState` の子に置く。同じ取得の経過時間は 1 か所だけに出す。テキストだけの「読み込み中…」、領域を空白のままにすること、画面全体を塞ぐスピナーは使わない（UX 契約 `messaging.md` §3.6）。再読み込み（表示を更新）中は、前の内容を出したまま更新し、操作したボタンの `loading` で示す。
- **一覧の縦スクロール**: 件数が多い一覧は `DataTable` の `stickyHeader`（表頭を固定）と `visibleRows`（**md 未満 5 行・md 以上 8 行**）を使い、それを超える行は表の中で縦スクロールにする（ページ全体を伸ばさない）。行の最小高さは 3.5rem。値は `packages/ui` の共通定数（`INFORMATION_TABLE_VISIBLE_ROWS`・`INFORMATION_TABLE_ROW_CLASS`・表ではない行リストは `INFORMATION_LIST_SCROLL_CLASS` など）を使い、製品で数値を書かない。
- **ページング**: 共通の `Pagination` / `usePagination`（既定 10 件/ページ）を表の直下に置き、1 ページしかないときは出さない。サーバー側のページングでも同じ部品を使う（offset / limit / total は `offsetPagination` / `offsetForPage` で変換する）。ページ番号は作業状態として保持する（UX 契約 `workspace-state.md`。`usePagination` は `page` / `onPageChange` で制御でき、`resetKey` が変わったときだけ 1 ページ目へ戻す）。
- **例外**: 選択と連動する一覧（RAG の chunk）・カーソル型の API の「さらに読み込む」（NL2SQL）・分析の一覧の件数の切り替え（RAG のフィードバック）は、理由付きで基準から外している。一覧は UX 契約 `page-archetypes.md` の「一覧の型と、基準から外す例外」。新しく例外を足すときは、そこに理由を書く（#403）。
- 新しい一覧・読み込み中の表示を作るときは NL2SQL の同種の画面を見本にし、Playwright で読み込み中（応答を遅らせる）・行数が多いとき（縦スクロール）・2 ページ以上（ページング）を desktop と 375px で確認する。

### lint

- 遵守ルールの正本は `platform/docs/design-system/adherence.oxlintrc.json`（と JS プラグイン `design-system-plugin.mjs`）。各製品は **コピーせず相対パスで参照する**（oxlint は `extends`、ESLint は JSON を import して `no-restricted-syntax` / `no-restricted-imports` に渡す）。書き方は [platform/AGENTS.md](./platform/AGENTS.md) の「lint」を参照。
- 製品固有のルールを足す場合は、adherence と同じルール名を上書きしない（セレクタが消える）。別名のルールにする。
- 誤検知や正当な例外は `// oxlint-disable-next-line <rule>`（ESLint は `eslint-disable-next-line`）に理由コメントを添えて局所的に除外する。ルール自体を緩める必要がある場合は adherence 設定を変更する Issue を立てる。

### 既存ルールとの優先順位

- トークン・コンポーネントの見た目と振る舞い（サイズ・variant・アイコン・loading・ヘッダーの並び順・フォーカス・ダークテーマ）は、`ui-ux-pro-max` skill の一般論や各製品の `docs/` より `platform/docs/design-system/` を優先する。
- 画面の振る舞い（画面内の配置・文言キーの命名・通知チャネルの使い分け・ページの型・状態保持等）は `platform/docs/ux-contracts/` を3製品共通の正本とする。
- 各製品の `docs/frontend-*.md` は、デザインシステムと UX 契約が規定しない製品固有の差分（割り当て・例外・記録）だけを書く。

### UI 変更の検証

- **UI/UX に関する作業（設計・実装・レビュー・改善）は必ず `ui-ux-pro-max` skill を使う。**
- UI/UX 変更ごとに Playwright で実画面を確認し、desktop と 375px 幅を最低限検証する。空/読込/エラー/ブロック状態も必要に応じて確認する。
- **e2e の量**：ローカルの検証も PR の検証も、変更に関係する spec だけを選び、1 回おおむね 1 分以内で終わる量にする（`-g` や spec のパスで絞る）。Playwright の全件は `.github/workflows/e2e-nightly.yml` が毎晩実行する。PR の CI（`ci.yml` の `rag-e2e` / `nl2sql-e2e` / `agent-e2e`）は約 1 分の smoke だけを実行する（#184 / #339）。
- ライト / ダークの両テーマで確認する。
- 1280px / 1920px の両幅で確認する。1920px では PageHeader のタイトルと本文の左端が揃うこと。
- キーボード操作（最初の Tab で「本文へスキップ」、フォーカスリングの視認性、`Tabs` の ← → / Home / End）を確認する。
- 状態を表す UI は色だけに依存しない（`StatusBadge` / `Banner` / `Toast` はアイコン付き）。
- 意図的な見た目の変更は `platform/docs/design-system/README.md` §7 と照合し、PR の `検証結果` に記載する。

## 共通の技術方針

- AI / DB は OCI / Oracle に集約する（回答生成・構造化抽出 = OCI Enterprise AI、embedding / rerank = OCI Generative AI の Cohere Embed v4 / Rerank v4 fast、ベクトル検索・データの正本 = Oracle 26ai）。外部ベクトル DB、別 LLM provider、別 SaaS を導入しない。逸脱が必要な場合は理由を添えてユーザ確認する。
- 自前のコード（3製品の backend・worker・frontend、RAG の前処理 / parser）は Docker イメージを作らず、ネイティブで動かす（開発は `uv run` / `npm run dev`、本番は Compute 上の systemd + Nginx。各製品の `init_script.sh`）。Dockerfile・自前の compose は持たない（#286 / #356）。Docker を使うのは第三者が構築済みのイメージを配るもの（Agent の Runtime の OpenClaw・Hermes・DeerFlow。`agent/docker-compose.yml`、digest 固定）だけ。
- Backend は Python 3.12 + FastAPI + Pydantic v2 + uv、共通基盤は `pr_backend_core`。
- **Python の版は 3.12 に固定する（#286）。** すべての `pyproject.toml` の `requires-python` は `">=3.12,<3.13"`、リポジトリ直下の `.python-version` は `3.12`。uv は project の中では直下の `.python-version` を読まないため、上限は `requires-python` で掛ける。版を変えるときは、全 `pyproject.toml`・`uv.lock`（`uv lock`）・CI の `python-version`・各製品の `init_script.sh` の `uv python install` / `--python` を同じ PR でそろえる。Frontend は Vite + React Router + TypeScript + Tailwind + `@engchina/production-ready-ui` + TanStack Query + Zustand。
- シークレット（OCI 認証・DB 接続・ADB wallet 等）は `.env` / secret store 経由。ハードコード・commit・API 応答への展開を禁止する。
- LLM 出力は Pydantic スキーマで検証してから保存・利用する。
- OCI / Oracle / LLM を呼ぶ層は CI では決定論スタブ / 録画応答でテストし、実サービス検証は手動 / ステージングで行う。
- 実装と同時に対応するテストを追加・更新し、変更後は該当範囲の lint・型チェック・テストを実行して結果を報告する。
- **ローカルの検証の範囲（#339）**：ローカルでは変更した範囲だけを検査し、全件は CI に任せる。
  - backend: 変更したファイルの `ruff check` / `ruff format --check`（直すときは `uv run ruff format <ファイル>`）、関係するテストファイル（`uv run pytest tests/test_<対象>.py`）と、直前に失敗したもの（`uv run pytest --lf -x`）。`mypy` は変更したパッケージを渡してよい。全テスト・全体の `mypy`・`pip-audit` は CI が実行する。ローカルで全テストを流すときは `uv run pytest -n auto`（または `-n 4`）で並列にしてよい（既定は直列のまま）。
  - frontend: `npm run lint` と `npm run build`（型検査を兼ねる）。単体テストは関係するものだけ（Vitest は `npx vitest related <変更したファイル>` か `npx vitest --changed`、NL2SQL のロジックテストは `node --import jiti/register --test tests/<対象>.test.ts`）。
  - e2e: 変更に関係する Playwright の spec だけ（「UI 変更の検証」の e2e の量）。
  - PR の `検証結果` には、ローカルで実行した command と、CI の job 結果（全件の検査）を分けて書く。
  - worktree の準備は `scripts/setup-worktree.sh <製品…>`（触る製品の frontend だけ `npm ci --prefer-offline --no-audit --no-fund` し、platform の共有 UI を build する）で短くできる。

### 共通の仕組みと製品固有の仕組みの分け方

- **3製品で同じ機能は platform に 1 セットだけ置く。** システム設定（OCI 認証・アップロード保存先・モデル・データベース・外観）とユーザー管理・ロール管理は、画面を `packages/system-settings`、API（または API 契約）を `packages/system_settings_backend` に置き、製品は `api` や権限判定を渡す薄いラッパーだけを持つ（#70 / #206）。
- **製品固有の機能は、共通のメニューに混ぜず製品固有のメニューセクションに置く。** 例: NL2SQL の権限管理（ロールごとの機能権限・業務プロファイル利用権限）と Deep Data Security は「NL2SQL セキュリティ」。共通の「ユーザーとロール」「システム設定」はナビの末尾にそろえる。
- ロールの基本情報（コード・名称・説明・アーカイブ）は共通のロール管理が扱い、ロールに付ける権限は製品ごとの権限管理が扱う。共通のロール API は権限を変更しない。

### 設定（`.env`）とデータベース object の命名

- **3製品共通の設定は、platform の共通 `.env`（`platform/.env`、雛形は `platform/.env.example`）1 ファイルで管理し、変数名は `PLATFORM_` で始める。** システム設定画面の保存先（OCI 認証・アップロード保存先・モデル・データベース）と、共通認証の構成管理者・認証ポリシーもこのファイルにする。`model-settings.json` も3製品で共有する。配備では同じファイルを3製品のコンテナにマウント（または env_file で渡す）し、場所は `PLATFORM_ENV_FILE` で上書きできる。
- **各製品の `backend/.env` には、その製品だけが使う変数だけを置き、`RAG_` / `NL2SQL_` / `AGENT_` で始める。** 共通の設定を製品の `.env` に重複して持たない。
- **3製品共通の仕組み（ユーザー管理・ロール管理など）が使うテーブルは `PLATFORM_` で始める。製品固有の仕組みが使うテーブルは `RAG_` / `NL2SQL_` / `AGENT_` で始める。** index / constraint / sequence / view なども同じ接頭辞にそろえる。例: ユーザー・ロール・セッションは `PLATFORM_`、NL2SQL のロールの機能権限・業務プロファイル利用権限・Data Grant は `NL2SQL_`。
- 名前を変えるときは旧名との互換を持たない（旧名の環境変数は読まず、テーブルは migration で改名する）。既存環境の更新手順を配備ドキュメントに書く。
- 環境変数名は Settings の属性名から `pr_backend_core.config.product_settings_config` が決める（[#211](https://github.com/engchina/no.1-production-ready-suite/issues/211)）。共通の属性（`PLATFORM_SETTING_FIELDS`）は `PLATFORM_` + 属性名（先頭の `app_` は除く）、それ以外は製品の接頭辞 + 属性名（接頭辞で始まる属性は重ねない）。共通の設定を足すときは `PLATFORM_SETTING_FIELDS` と `platform/.env.example` に加える。既存環境は `platform/scripts/migrate_env_to_platform.py` で移す。
- ユーザー・ロール・セッションのテーブルは `PLATFORM_*` へ移した（[#212](https://github.com/engchina/no.1-production-ready-suite/issues/212)）。新しく追加する変数・テーブルは最初からこの規則に従う。
- 3 製品は同じ Oracle schema を共有する前提のため、各製品は他製品の接頭辞（`PLATFORM_` / `RAG_` / `NL2SQL_` / `AGENT_`）のオブジェクトを業務データとして扱わない（例: NL2SQL の業務プロファイルの対象一覧・管理 SQL から除外する）。
- ユーザーとロールを共有するため、ロールの割り当て・復元では製品をまたぐ権限昇格を防ぐ（`pr_system_settings.auth` の `PRODUCT_ROLE_PERMISSION_TABLES`）。製品が権限テーブルを追加するときは、この登録と `ON DELETE CASCADE` の FK（`PLATFORM_ROLES` への参照）を合わせて用意する。
