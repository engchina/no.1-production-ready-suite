# AGENTS.md — No.1 Production Ready Suite（monorepo 共通ルール）

> monorepo 全体の共通ルールの正本（Claude Code と Codex が直接読む。`CLAUDE.md` は置かない）。作業対象のディレクトリの `AGENTS.md` もあわせて適用し、矛盾する場合は製品固有のルールを優先する（GitHub 運用と CI は本ファイルが優先）。
> 本ファイルには規則と正本への入口だけを書く。寸法・部品の仕様・画面の振る舞い・CI の内部は、各節が指す正本にある。

## 構成

| ディレクトリ | 内容 | 固有ルール |
|---|---|---|
| `platform/` | 3 製品の共通基盤: `packages/ui`（`@production-ready/ui`）、`packages/system-settings`（`@production-ready/system-settings`。共通のシステム設定・ユーザー / ロール管理の画面）、`packages/backend_core`（`pr_backend_core`）、`packages/system_settings_backend`（`pr_system_settings`。共通の設定 API と認証基盤）、`docs/design-system/`、`deploy/`（1 台の Compute の配備） | [platform/AGENTS.md](./platform/AGENTS.md) |
| `rag/` | Production Ready RAG | [rag/AGENTS.md](./rag/AGENTS.md) |
| `nl2sql/` | Production Ready NL2SQL（SQL 専用の自然言語問い合わせ） | [nl2sql/AGENTS.md](./nl2sql/AGENTS.md) |
| `agent/` | Production Control Plane for AI Agents | [agent/AGENTS.md](./agent/AGENTS.md) |
| `terraform/` | OCI Resource Manager の統合 stack（ADB 1 つ＋選んだ製品をすべて入れる 1 台の Compute） | [terraform/README.md](./terraform/README.md) |

- **依存の向きは `platform/` → 各製品の一方向。** 製品同士はコードで依存せず、HTTP API で連携する。Agent → RAG / NL2SQL は呼び先の `POST /api/mcp` を、Run の利用者を `sub` にした短命のサービストークン（署名鍵は `PLATFORM_SERVICE_TOKEN_SECRET`）で呼び、画面用の Cookie / CSRF の API を機械から呼ばない。呼び先は画面と同じ権限・対象範囲で判定する（[backend-standard.md](./platform/docs/backend-standard.md)「製品間の連携（MCP とサービストークン）」）。
- 製品は `platform/` を相対パスで参照する（`file:../../platform/packages/ui`・`path = "../../platform/packages/backend_core"`・`../../platform/docs/design-system/…`）。publish や version pin はしない。
- 各製品の backend / frontend / uv の venv は独立している。配備は 1 台の Compute にまとめる（「配備」）。
- commit message の `engchina/no.1-production-ready-<製品>#N` は、統合（#71）前の旧 repo（archive 済み）の番号。

## 言語

- 応答・Issue / PR・commit message・コードコメントは日本語（identifier・API path・file path・command・固有名詞は英語のまま）。ユーザーが会話で別の言語を指定したら、応答だけ合わせる。
- システムの主要言語は日本語。UI 文言・エラー・通知・LLM への指示と出力は日本語を前提とし、ユーザー向け文言は i18n 経由で管理する。
- **利用者が入力する自然言語は、3 製品で「質問」と呼ぶ（#1183）。** 「クエリ」は SQL・技術の概念（サブクエリ・クエリ文字列など）にだけ使う（識別子・API・i18n のキーは変えなくてよい）。3 製品で同じ機能の説明の文（チャットの副題・空の状態・処理の経過・停止の案内など）は同じ書き方にし、製品の名詞だけを変える（例: 副題「〈対象〉に質問し、〈確かめるもの〉を確かめながら会話を続ける」、空の状態「質問を入力して会話を始めます」「同じ会話の中では、前の質問と〈回答 / SQL〉を踏まえて…」）。

## 開発ワークフロー / GitHub 運用

- **`main` へ直接 commit / push しない。** Issue を先に作り（label `product:rag` / `product:nl2sql` / `product:agent` / `platform`。複数ならすべて）、作業ブランチ `codex/<issue-number>-<short-topic>`（使えなければ Issue 番号と作業内容が分かる名前）で変更し、PR を出す。`platform/` と製品にまたがる変更は 1 つの PR でよい。
- **検証と PR 本文の更新が済み、PR の最新 commit の `CI OK` が成功したら、ユーザーの確認を求めず `gh pr merge <PR> --merge`（merge commit）で merge する。** PR の作成や CI の成功の報告だけで終わらない。
- **CI はエージェント自身が待ち、merge の可否は `scripts/pr-merge-check.sh <PR>` で判定する**（「pass したら声をかけて」と止まらない。待つ間は別の独立した作業をしてよい。実行環境の PR / CI の監視機能を使ってもよい）。スクリプトは最新 commit の CI の run を待って `CI OK` を確かめ、`ready-to-merge`（終了 0）/ `needs-update-branch`（3）/ `ci-failed`（1）を出す。merge はしない（`--update` で `gh pr update-branch` も行う、`--no-wait` は待たない）。`gh pr checks --watch --required` は使わない（CI の開始直後は `CI OK` がまだ無く、待たずに終わる）。
- **main の取り込みは必要なときだけ（#339。ruleset は up-to-date を求めない）。** PR の最新の CI の開始後に、main で PR が触る製品のディレクトリ（`rag/` `nl2sql/` `agent/` `terraform/`。PR が `platform/` か `.github/` を触るなら全部）か `platform/`・`.github/` が変わっていたら、main を取り込んで（`gh pr update-branch <PR>` か `git merge origin/main`）CI をやり直す。続けて merge するときは、前の merge の後にもう一度判定する。
- CI の失敗・merge conflict は直して再検証してから merge する。ruleset を迂回した強制 merge はしない。解消できなければ原因と未完了の操作を明示する。
- **main が赤になったら（`ci-failure-issue.yml` が label `ci-failure` の Issue を作る）、ほかの作業より先に直す。** 直す PR はその Issue を `Closes` する。
- **merge 後はローカルを `main` に切り替え、`git fetch --prune` のうえ `origin/main` へ fast-forward してから完了を報告する**（PR の merge 状態・ブランチ・同期を確かめる）。未保存の変更を破棄する `reset --hard` 等は使わない。同期できなければ理由と残作業を明示する。
- docs-only や緊急の修正も同じ流れ。例外が要るなら理由を添えてユーザーに確認する。
- 並行する別のセッションの未コミット変更・stash・worktree を、確認なしに破棄・上書きしない。

### GitHub Issue / Pull Request の記述規約

見出しと各項目の書き方はテンプレート（[Issue](./.github/ISSUE_TEMPLATE/issue.md)・[PR](./.github/PULL_REQUEST_TEMPLATE.md)）にある。`gh issue create` / `gh pr create` で本文を渡すときも、テンプレートの見出しと注記どおりに書く。

- 日本語で書く。タイトルは対象と事象が分かる具体的な文にする（「修正」「対応」だけにしない）。
- 確認した事実と推測を分け、未確定は「調査中」「未確認」と書いて判明したら更新する。再現・レビューに要る具体情報（API・設定 key・status code・error message・再現値）を書き、secret・token・個人情報・実 credential は書かない。
- 製品の用語は各製品の `AGENTS.md` に従う（例: RAG の「DocRAG」は地の文にも識別子にも使わない）。
- **Issue** は `問題` / `症状` / `原因` / `修正方針` の 4 項目を必ず含める（原因が未確定でも仮説か「調査中」を書く）。`完了条件` は判定できる形で書く。
- **PR title** は `<type>(<scope>): <日本語の要約> (#<issue-number>)`（`scope` は `rag` / `nl2sql` / `agent` / `platform`。複数・全体なら省略可）。本文は `関連 Issue`（`Closes #N` / `Refs #N`）/ `背景 / 原因` / `変更内容` / `検証結果` / `既知の制約・残課題`。
- `検証結果` は実行した command と結果を正確に書き、失敗・skip・未実行を隠さない。ローカルで実行しなかった全件の検査は CI の job の結果を引用してよい。`platform/` の変更は影響を受ける製品の結果、UI の変更は Playwright の結果、OCI / Oracle / LLM はスタブと実サービスの確認を分けて書く（詳細はテンプレート）。
- PR 作成後の追加修正・検証結果の変化は、本文を最終状態へ更新してから merge する。

## CI

- PR と `main` の push で `.github/workflows/ci.yml` が動き、`changes` job が**変更のあった製品の job だけ**を動かす（platform の frontend / backend の分け方と、全製品を動かす変更の規則は `ci.yml` の冒頭）。製品のテストが platform の別の側のファイルを読むなら、`changes` の filter にそのファイルを足す。
- 必須 check は **`CI OK`** だけ（skip は成功扱い）。**job を追加したら `ci-ok` の `needs` に足し、すべての job に `timeout-minutes`（通常 10〜15 分、e2e は実測の倍程度）を付ける。**
- PR の e2e は約 1 分の smoke と、差分に影響を受ける spec（`e2e-impact`）。全件は `e2e-nightly.yml` が毎晩実行する。キャッシュの方針は `ci.yml` の platform の節のコメント。
- backend の pytest は CI で並列（`-n auto`）に実行する。**テストは並列でも直列でも通るように書く**（`tmp_path`・`parametrize` の `ids=`・差し替えられる待ち・`xdist_group`。[backend-standard.md](./platform/docs/backend-standard.md)「テストの並列実行」）。
- `pip-audit` はその backend の `uv.lock` / `pyproject.toml`（か `ci.yml`）が変わったときだけ（全件は `dependency-audit-nightly.yml`）、`bandit` は毎回。
- **schedule の workflow を足したら `ci-failure-issue.yml` の `workflows` にも足す**（nightly の失敗も Issue になる）。
- secret 検出は `.gitleaks.toml` / `.gitleaksignore`（書き方は `.gitleaks.toml` の冒頭）。誤検知は fingerprint 単位で `.gitleaksignore` に理由付きで除外する。
- Dependabot の patch / minor は `CI OK` の後に自動 merge される。major・一式の更新・group PR の失敗の扱いは `.github/dependabot.yml` の冒頭。
- Python の整形は `ruff format`（black は使わない。CI は `uv run ruff format --check .`）。整形だけの commit は `.git-blame-ignore-revs` に載せる。pre-commit（`.pre-commit-config.yaml`）は gitleaks と、commit する backend のファイルへの `ruff format --check` / `ruff check`。
- root の `scripts/` は `Suite / Scripts` が shellcheck と `scripts/tests/` で検査する。

## デザインシステム / UI（platform が正本）

- **UI に触る前に [ARCHITECTURE.md](./platform/docs/design-system/ARCHITECTURE.md) を読む。** トークン値・部品の仕様・意図的な見た目の変更は [README.md](./platform/docs/design-system/README.md)（以下 README）、実装の参照は [components-reference.md](./platform/docs/design-system/components-reference.md)、画面の振る舞いは [UX 契約](./platform/docs/ux-contracts/README.md)。
- **依存は「デザインシステムの決定 → `platform/packages/ui` → 各製品」の一方向。** 製品で部品やトークンを新規実装せず、`packages/ui` に入れる（同じ PR でよい）。
- **製品が持てるのは**: ナビ構造（nav config）と業務コピー（i18n）、データ取得・状態管理・権限、ドメイン enum → prop の対応表、画面固有の業務レイアウト、1 製品しか使わない部品（他の 2 製品が欲しがるなら `packages/ui`）。
- **色・型・余白・角丸・影・モーション・フォーカス表示・テーマは `packages/ui` が持つ。** `frontend/src/globals.css` は `@import "tailwindcss"` → `@import "@production-ready/ui/styles.css"` → `@source "../node_modules/@production-ready/ui/dist"` と、画面固有のレイアウトだけ（`main.tsx` から import すると共有ユーティリティが生成されない）。

### 禁止事項

- 生の hex・`rgba()` などの色の関数・生の px（inline style の数値を含む）。色は `--color-*` トークン（`bg-surface` / `text-fg-muted` 等）。旧名（`bg-card` / `text-muted` / `var(--primary)` 等）は未定義。
- `globals.css` に色トークンや `.dark { … }` の上書きを書く。
- 共有部品（`TextField` / `PageHeader` / `Button` / `StatusBadge` 等）の再実装。
- 操作部品の高さ・幅の手書き（「操作部品の高さと幅」）。
- `<table>` の手書き（`DataTable` を使う）。例外は元の文書の表を再現して編集するグリッド（RAG の `ReviewTextEditor.tsx`）だけで、理由をコメントに書く。
- 余白コンテナの手書き（`<div className="px-8 py-6">` 等）。`PageBody` を使う。
- `ToggleChip` をタブに使う（見方の切り替えは `Tabs`、絞り込みは `ToggleChip`）。
- `loading` 中のボタンのラベルの差し替え。アイコンは `icon={Upload}` で渡し、`loading` を渡す `Button` は必ず `icon` を持つ。
- `Spinner` 以外の回転するアイコン（`animate-spin` / `animate-[spin…]`・inline style の `animation: spin`・lucide の `Loader` / `Loader2` / `LoaderCircle` / `LoaderPinwheel`）。処理中は `Spinner`、ボタンは `loading`、領域は `ProcessingIndicator` / `TimedLoadingState`。`Spinner` の形と箱は変えない（README §4「`Spinner`」、e2e の `expectSpinnerStable`）。
- `focus(-visible):ring-*` のフォーカス表示と `focus(-visible):outline-none`。表示はグローバルの `:focus-visible` に任せ、調整は `focus-visible:outline-*` / `-outline-offset-*`。
- 必須の印の手書き（`*`・独自のバッジ・`RequiredBadge` の直置き）と「(任意)」。必須の欄だけに部品の `required`（入力は `TextField` / `SelectField` / `SecretField`、それ以外は `FieldLabel` / `FieldLegend` / `Fieldset`。README §4「必須の表示」）。
- 製品ごとのアクセント色（製品は wordmark・ナビ・内容で区別する）。
- 絵文字と手描き SVG。アイコンは `lucide-react`（14 / 16 / 20 / 24px）。
- 共有 UI の内部パス（`dist/components/**`・`dist/tokens/*.css`）の import・テストでの読み込み。パッケージのルートと `styles.css` だけを使う。

### 画面の構成

```tsx
<AppShell sidebar={<Sidebar … footer={<SidebarAccountFooter … />} />}>
  <PageHeader title="…" actions={[{ id, kind: "primary", label, icon }]} tabs={<Tabs … />} />
  <PageBody>
    <Section title="…">…</Section>
  </PageBody>
</AppShell>
```

- この構成を外れた画面は review で差し戻す。`PageHeader` の `actions` は配列で渡す（JSX を渡さない。danger → utility → secondary → primary の順に並び、右端が primary）。画面を移るだけの操作は `ButtonLink`（`<Link className={buttonVariants(...)}>` に手書きしない）。
- **詳細・作成・編集の画面**: 「一覧へ戻る」は `PageHeader` の `back`、「保存」「作成」は右端の primary、「変更を破棄」はその左。対象への操作は最初のカードの `ObjectActionBar` 1 か所、保存の失敗は `SaveErrorBanner`。例外を含む正本は README §4「詳細・作成・編集の画面の操作」。
- `PageHeader` と `PageBody` の `wide` は必ず同じ値にする（1920px で左端がずれる）。
- 単位の境界: 文字サイズとコントロール高さは px、余白とレイアウト寸法は rem（14px ルート）。本文は `"Noto Sans JP", "Roboto", system-ui, sans-serif` の 14px。

### 操作部品の高さと幅

正本は README §4「操作部品の高さと幅」と `page-archetypes.md`「入力欄・選択欄・ボタンの高さと幅」。

- 高さは `size`（`sm` 32 / **`md` 36（既定）** / `lg` 40px。タッチ端末は 44px）。**1 つの行の中の入力欄・選択欄・ボタンには同じ `size` を渡す。**
- 幅は入る値とラベルの長さで `width`（`xs`〜`lg` / `full`）。grid のセルの欄には渡さず、grid の外の単独の選択欄・短い値の欄には必ず渡す（例外: RAG の検索・チャットの「検索・回答プロファイル」の選択は、問い合わせの入力と端をそろえて `full`）。入力欄とその操作の行は `FieldActionRow`、複数行の高さは `rows`。
- **製品で書かない**: `touchTarget`、操作部品への `h-*` / `min-h-*`、欄への `w-*` / `max-w-*`（lint が検出する）。
- **フォームの入力は共有の部品**（`SelectField` / `SearchableSelectField` / `TextField` / `SecretField` / `SearchField` / `TextareaField`）。ネイティブで残すのは checkbox / radio / file / range / hidden / color と部品で表せない所だけで、理由を添えて lint を局所的に除外し `fieldControlClassName({ size, width })` を使う。e2e は combobox を押して選び（`chooseSelectFieldOption`）、高さは入力方式で期待する（`expectedControlHeight`）。

### 読み込み中・一覧・ページング

3 製品で NL2SQL の基準にそろえ、新しい一覧は NL2SQL の同種の画面を見本にする。部品と定数は components-reference.md「読み込み中と一覧の表示密度」、一覧の型と例外は `page-archetypes.md`「一覧の型と、基準から外す例外」。

- **読み込み中**: 領域の先頭に `TimedLoadingState`（または `ProcessingIndicator`）で「〜を読み込んでいます」と経過時間を 1 か所だけ出し、領域は内容の形の `Skeleton` で覆う。文字だけの「読み込み中…」・空白・画面全体のスピナーは使わない。再読み込みは前の内容を出したまま、ボタンの `loading` で示す（`messaging.md` §3.6）。
- **一覧**: `DataTable` の `stickyHeader` と `visibleRows`（共通の定数）で表の中を縦スクロールにし、製品で行数・高さを書かない。
- **ページング**: 共通の `Pagination` / `usePagination`（10 件）を表の直下に置き、1 ページなら出さない。サーバー側は `OffsetPagination`、カーソル型は `useCursorPages` + `CursorPagination`、件数の選択は `PageSizeSelect`、文言は `paginationLabels()`。製品でページングのつなぎを書かない。ページ番号は作業状態に残す。backend は `pr_backend_core.api` の helper（backend-standard.md「一覧のページング」）。
- **一覧の絞り込み**は `SearchField`（検索ボタンを置かない）。重い検索（LLM・ベクトル検索・SQL の生成）は `type="search"` にせず、ボタンと Enter（`isSubmitEnter`）で実行する（`page-archetypes.md`「一覧の絞り込みの検索」）。
- 基準から外す一覧は、`page-archetypes.md` の例外の表に理由を書く。
- Playwright で読み込み中・行が多いとき・2 ページ以上を desktop と 375px で確かめる。

### 操作の結果・状態のメッセージ

正本は [`messaging.md` §10](./platform/docs/ux-contracts/messaging.md#10-操作の結果状態の出し方位置幅形出す情報705)。新しい画面は最初から、既存の画面は触るときに従う。結果は起点の操作の**直下**に**全幅**で出し、テスト・接続確認は結果パネル、保存は Toast / `FormStatus`、処理の失敗は danger の `Banner`、対象の状態は見出しの `StatusBadge`。技術的な詳細は「詳細」に畳み、自動で消さない。

### lint

- 遵守ルールの正本は `platform/docs/design-system/adherence.oxlintrc.json` と `design-system-plugin.mjs`。各製品は **コピーせず相対パスで参照する**（書き方は [platform/AGENTS.md](./platform/AGENTS.md)「lint」）。
- 製品固有のルールは adherence と同じルール名を上書きせず（セレクタが消える）、別名にする。
- 誤検知・正当な例外は `// oxlint-disable-next-line <rule>`（ESLint は `eslint-disable-next-line`）と理由のコメントで局所的に除外する。ルールを緩めるなら adherence を変える Issue を立てる。

### 既存ルールとの優先順位

- 部品の見た目と振る舞い（サイズ・variant・アイコン・loading・ヘッダーの並び・フォーカス・ダークテーマ）は、`ui-ux-pro-max` skill の一般論や製品の `docs/` より `platform/docs/design-system/` を優先する。
- 画面の振る舞いは `platform/docs/ux-contracts/` が 3 製品共通の正本。製品の `docs/frontend-*.md` には、それらが決めない製品固有の差分（割り当て・例外）だけを書く。

### UI 変更の検証

- **UI/UX の作業（設計・実装・レビュー・改善）は必ず `ui-ux-pro-max` skill を使う。**
- 変更ごとに Playwright で実画面を確かめる: desktop と 375px、必要に応じて空 / 読込 / エラー / ブロック、ライト / ダーク、1280px / 1920px（タイトルと本文の左端が揃う）、キーボード（最初の Tab で「本文へスキップ」、フォーカスリング、`Tabs` の ← → / Home / End）。状態は色だけで表さない。意図的な見た目の変更は README §7 と照合して PR に書く。
- ローカルの e2e は関係する spec だけを、1 回おおむね 1 分以内で（`-g` や spec のパス）。
- **画面のコードを変えたら、その画面を検証する既存の spec も同じ PR で直す（#885）。** 影響を受ける spec は `python3 platform/scripts/e2e_impact.py select --product <製品> --base origin/main --download`（CI の `e2e-impact` と同じ選択）で選び、PR の前に実行する。spec は `test` を製品の fixture（RAG・Agent `e2e/fixtures/test.ts`、NL2SQL `tests/e2e/_helpers/test.ts`）から import する（CI の `check-imports`）。

## 共通の技術方針

- **AI / DB は OCI / Oracle に集約する**: LLM / VLM = **OCI Enterprise AI**、embedding / rerank = **OCI Generative AI**（Cohere Embed v4 / Rerank v4 fast）、ベクトル検索 = **Oracle AI Vector Search**、データの正本 = **Oracle AI Database**。この分担を取り違えない。外部ベクトル DB・別 LLM provider・別 SaaS は入れず、逸脱するなら理由を添えてユーザーに確認する。
- **Oracle の製品名は公式名で、バージョン（`26ai` 等）を入れない**（「Oracle AI Database」「Oracle AI Vector Search」「Oracle Autonomous AI Database」）。対応バージョンは [terraform/README.md](./terraform/README.md)「Autonomous AI Database（全製品で共有）」だけに書く（`adb_db_version`・`oracle_26ai` などの値・識別子は変えない）。
- Backend は Python 3.12 + FastAPI + Pydantic v2 + uv と `pr_backend_core`。Frontend は Vite + React Router + TypeScript + Tailwind + 共有 UI + TanStack Query + Zustand。
- **Python は 3.12 に固定する**（全 `pyproject.toml` の `requires-python = ">=3.12,<3.13"`、直下の `.python-version` は `3.12`。uv は project の中で直下の `.python-version` を読まないため、上限は `requires-python` で掛ける）。版を変えるときは `pyproject.toml`・`uv.lock`・CI の `python-version`・各製品の `init_script.sh` の `uv python install` / `--python` を同じ PR でそろえる。
- シークレットは `.env` / secret store 経由（ハードコード・commit・API 応答への展開をしない）。LLM の出力は Pydantic で検証してから使う。
- OCI / Oracle / LLM を呼ぶ層は CI では決定論スタブ / 録画応答でテストし、実サービスは手動 / ステージングで確かめる。
- 実装と同時にテストを追加・更新し、該当範囲の lint・型チェック・テストの結果を報告する。
- 文書（.docx / .pptx / .xlsx / .pdf）の生成は対応する skill（`document-skills:*` / `pdf`）を使う。

### 配備

- 自前のコードは Docker を使わずネイティブで動かす（開発は `uv run` / `npm run dev`、本番は systemd + Nginx。Dockerfile・compose は持たない。Agent も組み込み Runtime で、Docker の Runtime は持たない）。
- Terraform stack は `terraform/stack/` に 1 つだけ。ADB を 1 つ作り（新規 / 既存）、選んだ製品（`deploy_rag` / `deploy_nl2sql` / `deploy_agent`、最低 1 つ）を 1 台の Compute に入れる。製品固有の入力は `rag_` / `nl2sql_` / `agent_` を付ける。
- Compute の上では `platform/deploy/suite-init.sh` が各製品の `init_script.sh`（製品は単独では配備しない）を順に呼び、Nginx の site（`/rag/` `/nl2sql/` `/agent/` の prefix・HTTPS・自作の CA・公開の port）を 1 つだけ書く。Nginx の location は `platform/deploy/suite-nginx.sh` だけに書く。frontend の base は build の `FRONTEND_BASE_PATH`（未指定は `/`。配備は `/<製品>/`）。
- CI は `Suite / Terraform` が `terraform/scripts/package_stack.py`・`verify_stack_contract.py` と `platform/deploy/tests/` を実行する。
- release tag は `suite-v*`（`terraform-release.yml` が zip と sha256 を公開する）。製品ごとの release（`<製品>-v*`）は作らない（既存の `nl2sql-v*` は残す）。README 等は `releases/latest` ではなく tag を固定して参照する。

### ローカルの検証の範囲

ローカルでは変更した範囲だけを検査し、全件は CI に任せる。PR の `検証結果` には、ローカルの command と CI の job の結果を分けて書く。

- backend: 変更したファイルの `ruff check` / `ruff format --check`、関係するテスト（`uv run pytest tests/test_<対象>.py`）と `uv run pytest --lf -x`。`mypy` は変更したパッケージを渡してよい。全件を流すなら `-n auto`。全テスト・全体の `mypy`・`pip-audit` は CI。
- frontend: `npm run lint` と `npm run build`（型検査を兼ねる）、関係する単体テスト（`npx vitest related <ファイル>` / `--changed`、NL2SQL は `node --import jiti/register --test tests/<対象>.test.ts`）。
- e2e: 関係する spec だけ（「UI 変更の検証」）。
- worktree の準備は `scripts/setup-worktree.sh <製品…>`。

### 共通の仕組みと製品固有の仕組みの分け方

- **3 製品で同じ機能は platform に 1 セットだけ置く。** システム設定（OCI 認証・アップロード保存先・モデル・データベース・外観と証明書）とユーザー / ロール管理は、画面を `packages/system-settings`、API を `packages/system_settings_backend` に置き、製品は `api` や権限判定を渡す薄いラッパーだけを持つ。製品固有の機能は製品固有のセクションに置く（例: NL2SQL の権限管理・Deep Data Security は「セキュリティ設定」）。
- **サイドナビの下部は 3 製品で同じ並びと名前**: 業務のセクション →「改善・運用」（あれば）→「セキュリティ設定」→「ユーザーとロール」→「運用設定」（システムテーブルが先頭）→「システム設定」。backend の権限カタログの `group` と並びもそろえる。
- **ナビのアイコンは、同じ機能なら 3 製品で同じ、違う機能なら違うもの**（1 製品の中で重ねない）。共通の項目は `packages/system-settings` のアイコン、製品間で同じ機能は 権限管理 `LockKeyhole`・システムテーブル `TableProperties`・品質評価 `FlaskConical`・フィードバック `ThumbsUp`・検索・回答プロファイル / 業務プロファイル `BriefcaseBusiness`。各製品のテスト（RAG `src/lib/route-permissions.test.ts`、NL2SQL `tests/nav-config-icons.test.ts`、Agent `e2e/appearance.spec.ts`）が検出する。
- ロールの基本情報は共通のロール管理が、ロールの権限は製品の権限管理が扱う（共通のロール API は権限を変えない）。
- **権限管理のメニューの一覧は左のナビを正本にする**: 製品の `nav-config.ts` から `permissionNavSections` / `arrangePermissionsByNav` で作り、backend の `group` / `label` を画面に使わない（名前はサイドナビの表示名）。ナビに無い capability はナビの後ろに置く。画面のあるメニュー権限は必ずナビの項目にし、ずれは各製品のテスト（RAG `src/lib/permission-nav.test.ts`、NL2SQL `tests/permission-nav.test.ts`、Agent `e2e/permission-catalog.spec.ts`）が検出する。権限の code・保存値は変えない。

### 設定（`.env`）とデータベース object の命名

- **3 製品共通の設定は `platform/.env`（雛形 `platform/.env.example`）1 つで、変数名は `PLATFORM_` で始める。** システム設定画面の保存先・共通認証の設定もここで、`model-settings.json` も共有する。3 製品が同じファイルを読み、場所は `PLATFORM_ENV_FILE` で変えられる。
- **製品の `backend/.env` には、その製品だけが使う変数を `RAG_` / `NL2SQL_` / `AGENT_` で置く**（共通の設定を重複させない）。変数名は Settings の属性名から決まる（規則と、共通の設定の足し方は backend-standard.md「設定（`.env`）の変数名」）。
- **テーブルと index / constraint / sequence / view は、共通の仕組みが `PLATFORM_`、製品固有が `RAG_` / `NL2SQL_` / `AGENT_` で始める**（例: NL2SQL のロールの権限・Data Grant は `NL2SQL_`）。
- 名前を変えるときは旧名との互換を持たない（旧名の環境変数は読まず、テーブルは migration で改名する）。既存環境の更新手順を配備ドキュメントに書く。
- 3 製品は同じ schema を共有するため、他製品の接頭辞のオブジェクトを業務データとして扱わない（例: NL2SQL の業務プロファイルの対象・管理 SQL から除く）。
- ロールの割り当て・復元では製品をまたぐ権限昇格を防ぐ（`pr_system_settings.auth` の `PRODUCT_ROLE_PERMISSION_TABLES`）。製品が権限テーブルを足すときは、この登録と `PLATFORM_ROLES` への `ON DELETE CASCADE` の FK を用意する。
