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
- 製品は `platform/` を相対パスで参照する（frontend: `file:../../platform/packages/ui` / `file:../../platform/packages/system-settings`、backend: `path = "../../platform/packages/backend_core"`、lint: `../../platform/docs/design-system/…`）。パッケージの publish や version pin は行わない。
- 各製品の backend / frontend / Docker image / 配備は独立している。まとめているのはソースと CI だけ。
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
- CI/checks の失敗や merge conflict がある場合は、原因を修正・解消し、最新 commit を再検証してから merge する。branch protection / ruleset を迂回した強制 merge は行わない。解消できない場合は原因と未完了の操作を明示する。
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
- `検証結果` には実行した正確な command と結果を記載する。失敗・skip・未実行を隠さず、今回の変更によるものか既存問題かを分ける。実行できない test がある場合は理由と代替確認を記載する。command は各製品の `AGENTS.md` の開発コマンドを基本とする。
- `platform/` を変更した場合は、影響を受ける製品の検証結果（統合 CI の該当 job を含む）も記載する。
- UI/UX 変更では、対象 Playwright spec、desktop / 375px viewport、主要導線と重要状態(空/読込/エラー/ブロック)の結果を記載する。見た目を変更した場合は必要に応じて screenshot または visual check の結果を添える。
- OCI / Oracle / LLM を呼ぶ範囲の変更では、CI 上の決定論スタブによる確認と、手動/ステージングでの実サービス確認をそれぞれ区別して記載する。
- docs-only など test 対象外の場合も `検証結果` を省略せず、`git diff --check` 等の実施結果と、コード test を実行しない理由を記載する。
- PR 作成後に追加修正や検証結果の変化があった場合は、コメントだけで済ませず PR 本文を最終状態へ更新してから review / merge する。

## CI

- PR と `main` への push で `.github/workflows/ci.yml`（統合 CI）が動く。`changes` job が変更パスを判定し、**変更のあった製品の job だけ**を実行する。`platform/` または `ci.yml` を変更した場合は全製品の job を実行する。
- 必須 check は **`CI OK`** の1つだけ（skip された job は成功扱い、failure / cancelled があれば失敗）。job を追加したら `ci-ok` の `needs` にも追加する。
- secret 検出は root の `.gitleaks.toml` / `.gitleaksignore`（pre-commit hook は `.pre-commit-config.yaml`）。CI の gitleaks-action は gitleaks 8.24 系のため、allowlist は単一の `[allowlist]` で書く（`[[allowlists]]` は解釈されない）。誤検知の除外は fingerprint 単位で `.gitleaksignore` に理由付きで追加する。
- OCI Resource Manager の Terraform stack は root の `terraform/stack/` に1つだけ置く（#217）。ADB を1つ（新規 / 既存）作り、選んだ製品（`deploy_rag` / `deploy_nl2sql` / `deploy_agent`、最低1つ）ごとに Compute を1台作る。製品固有の入力は `rag_` / `nl2sql_` / `agent_` の接頭辞を付ける。Compute 上の配備手順は各製品の `init_script.sh` が持つ。CI は `Suite / Terraform` job が `terraform/scripts/package_stack.py` と `verify_stack_contract.py` を実行する。
- Terraform stack の release tag は `suite-v*`（例: `suite-v0.1.0`）。`.github/workflows/terraform-release.yml` が zip と sha256 を公開する。製品ごとの release（`<製品>-v*`、#94）は作らない（既存の `nl2sql-v0.1.32` 等は残す）。README 等では `releases/latest` ではなく tag を固定して参照する。
- Dependabot（`.github/dependabot.yml`）の patch / minor 更新は `CI OK` 成功後に自動 merge される（`dependabot-auto-merge.yml`）。

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
- `loading` 中にボタンのラベルを「実行中…」等に差し替える。ラベルは変えず、`icon` がスピナーに置き換わる。子要素にアイコンを書かず `icon={Upload}` で渡す。
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
- **e2e の量**：ローカルの検証も PR の検証も、変更に関係する spec だけを選び、1 回おおむね 1 分以内で終わる量にする（`-g` や spec のパスで絞る）。Playwright の全件は `.github/workflows/e2e-nightly.yml` が毎晩実行する。PR の CI（`ci.yml` の `rag-e2e` / `nl2sql-e2e`）は約 1 分の smoke だけを実行する（#184）。
- ライト / ダークの両テーマで確認する。
- 1280px / 1920px の両幅で確認する。1920px では PageHeader のタイトルと本文の左端が揃うこと。
- キーボード操作（最初の Tab で「本文へスキップ」、フォーカスリングの視認性、`Tabs` の ← → / Home / End）を確認する。
- 状態を表す UI は色だけに依存しない（`StatusBadge` / `Banner` / `Toast` はアイコン付き）。
- 意図的な見た目の変更は `platform/docs/design-system/README.md` §7 と照合し、PR の `検証結果` に記載する。

## 共通の技術方針

- AI / DB は OCI / Oracle に集約する（回答生成・構造化抽出 = OCI Enterprise AI、embedding / rerank = OCI Generative AI の Cohere Embed v4 / Rerank v4 fast、ベクトル検索・データの正本 = Oracle 26ai）。外部ベクトル DB、別 LLM provider、別 SaaS を導入しない。逸脱が必要な場合は理由を添えてユーザ確認する。
- Backend は Python 3.12 + FastAPI + Pydantic v2 + uv、共通基盤は `pr_backend_core`。Frontend は Vite + React Router + TypeScript + Tailwind + `@engchina/production-ready-ui` + TanStack Query + Zustand。
- シークレット（OCI 認証・DB 接続・ADB wallet 等）は `.env` / secret store 経由。ハードコード・commit・API 応答への展開を禁止する。
- LLM 出力は Pydantic スキーマで検証してから保存・利用する。
- OCI / Oracle / LLM を呼ぶ層は CI では決定論スタブ / 録画応答でテストし、実サービス検証は手動 / ステージングで行う。
- 実装と同時に対応するテストを追加・更新し、変更後は該当範囲の lint・型チェック・テストを実行して結果を報告する。

### 共通の仕組みと製品固有の仕組みの分け方

- **3製品で同じ機能は platform に 1 セットだけ置く。** システム設定（OCI 認証・アップロード保存先・モデル・データベース・外観）とユーザー管理・ロール管理は、画面を `packages/system-settings`、API（または API 契約）を `packages/system_settings_backend` に置き、製品は `api` や権限判定を渡す薄いラッパーだけを持つ（#70 / #206）。
- **製品固有の機能は、共通のメニューに混ぜず製品固有のメニューセクションに置く。** 例: NL2SQL の権限管理（ロールごとの機能権限・業務プロファイル利用権限）と Deep Data Security は「NL2SQL セキュリティ」。共通の「ユーザーとロール」「システム設定」はナビの末尾にそろえる。
- ロールの基本情報（コード・名称・説明・アーカイブ）は共通のロール管理が扱い、ロールに付ける権限は製品ごとの権限管理が扱う。共通のロール API は権限を変更しない。

### 設定（`.env`）とデータベース object の命名

- **3製品共通の設定は、platform の共通 backend `.env` 1 ファイルで管理し、変数名は `PLATFORM_` で始める。** システム設定画面の保存先もこのファイルにする。配備では同じファイルを3製品のコンテナにマウント（または env_file で渡す）する。
- **各製品の `backend/.env` には、その製品だけが使う変数だけを置き、`RAG_` / `NL2SQL_` / `AGENT_` で始める。** 共通の設定を製品の `.env` に重複して持たない。
- **3製品共通の仕組み（ユーザー管理・ロール管理など）が使うテーブルは `PLATFORM_` で始める。製品固有の仕組みが使うテーブルは `RAG_` / `NL2SQL_` / `AGENT_` で始める。** index / constraint / sequence / view なども同じ接頭辞にそろえる。例: ユーザー・ロール・セッションは `PLATFORM_`、NL2SQL のロールの機能権限・業務プロファイル利用権限・Data Grant は `NL2SQL_`。
- 名前を変えるときは旧名との互換を持たない（旧名の環境変数は読まず、テーブルは migration で改名する）。既存環境の更新手順を配備ドキュメントに書く。
- ユーザー・ロール・セッションのテーブルは `PLATFORM_*` へ移した（[#212](https://github.com/engchina/no.1-production-ready-suite/issues/212)）。`.env` の一元化は [#211](https://github.com/engchina/no.1-production-ready-suite/issues/211) で行う。新しく追加する変数・テーブルは最初からこの規則に従う。
- 3 製品は同じ Oracle schema を共有する前提のため、各製品は他製品の接頭辞（`PLATFORM_` / `RAG_` / `NL2SQL_` / `AGENT_`）のオブジェクトを業務データとして扱わない（例: NL2SQL の業務プロファイルの対象一覧・管理 SQL から除外する）。
- ユーザーとロールを共有するため、ロールの割り当て・復元では製品をまたぐ権限昇格を防ぐ（`pr_system_settings.auth` の `PRODUCT_ROLE_PERMISSION_TABLES`）。製品が権限テーブルを追加するときは、この登録と `ON DELETE CASCADE` の FK（`PLATFORM_ROLES` への参照）を合わせて用意する。
