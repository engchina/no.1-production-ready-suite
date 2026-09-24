# AGENTS.md — Production Ready Platform

> このファイルは **Claude Code と Codex の両方が参照する正本(single source of truth)** です。
> `CLAUDE.md` はこのファイルを `@AGENTS.md` で取り込みます。ルールを変更する際は **必ずこのファイルを編集**してください。
> 共有パッケージの運用ルール(semver / release / 各業務 repo への配布)は [CONTRIBUTING.md](./CONTRIBUTING.md) を参照。

## 開発ワークフロー / GitHub 運用

- **`main` ブランチへ直接 commit / push / 変更しない。** すべての変更は GitHub Issue を先に作成し、Issue に紐づく作業ブランチで行う。
- 作業ブランチ名は既定で `codex/<issue-number>-<short-topic>` とする。既存 ref との衝突などで使用できない場合も、Issue 番号と作業内容が分かる名前を使う。
- 変更後は Pull Request を作成し、関連 Issue、変更内容、検証結果を PR description に明記する。
- **変更・必要な検証・PR 本文の更新が完了し、PR の最新 commit に対する CI/checks が成功したら、追加のユーザ確認を求めず自動で `main` へ merge する。** PR 作成や CI 成功の報告だけで作業を終了しない。必須 CI が存在しない場合は、PR 上で checks 状態を確認し、成功した代替検証を PR 本文に明記してから merge する。
- CI/checks の失敗や merge conflict がある場合は、原因を修正・解消し、最新 commit を再検証してから merge する。branch protection を迂回した強制 merge は行わない。解消できない場合は原因と未完了の操作を明示する。
- **merge 後はローカルブランチを必ず `main` に切り替え、`git fetch --prune` で削除済みリモートブランチの参照を掃除したうえで `origin/main` へ fast-forward 同期してから完了を報告する。** PR の merge 状態、ローカルブランチ、同期状態を確認する。ユーザの未保存変更を破棄する `reset --hard` 等は使わず、変更を保持したまま安全に同期する。同期できない場合は理由と残作業を明示する。
- docs-only の小さな変更や緊急修正も原則として同じ Issue → branch → PR → CI/checks → main merge の流れに従う。例外が必要な場合は、理由を添えてユーザ確認を取る。

### GitHub Issue / Pull Request の記述規約

#### 共通

- Issue / PR のタイトルと本文は**原則として日本語**で記述する。code identifier、API path、file path、command、製品・ライブラリの固有名詞は英語のままでよい。
- タイトルは対象と事象が分かる具体的な文にする。「不具合」「修正」「対応」だけの曖昧なタイトルにしない。
- 本文は Markdown 見出しで構造化し、確認した事実と推測を区別する。未調査・未確定の項目は断定せず「調査中」「未確認」と明記し、判明後に本文を更新する。
- API、関数、設定 key、status code、error message、再現値など、調査・レビュー・回帰テストに必要な具体情報を記載する。secret、token、個人情報、実 credential は記載しない。

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

- PR title は原則として `<type>: <日本語の要約> (#<issue-number>)` とする。`type` は変更内容に合わせて `feat` / `fix` / `docs` / `test` / `refactor` / `chore` 等を使用する。
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
- `検証結果` には実行した正確な command と結果を記載する。失敗・skip・未実行を隠さず、今回の変更によるものか既存問題かを分ける。実行できない test がある場合は理由と代替確認を記載する。
- UI/UX 変更(`packages/ui`)では、対象 Vitest spec と、利用側アプリで確認した主要導線・viewport・重要状態の結果を記載する。見た目を変更した場合は必要に応じて screenshot または visual check の結果を添える。
- docs-only など test 対象外の場合も `検証結果` を省略せず、`git diff --check` 等の実施結果と、コード test を実行しない理由を記載する。
- PR 作成後に追加修正や検証結果の変化があった場合は、コメントだけで済ませず PR 本文を最終状態へ更新してから review / merge する。

## デザインシステム / UI

- **UI に触る変更（`packages/ui` および各業務 repo の `frontend/`）では、`docs/design-system/` を正本とする。** 作業前に [ARCHITECTURE.md](./docs/design-system/ARCHITECTURE.md) を読む。トークン値・コンポーネント仕様・意図的な見た目の変更点は [README.md](./docs/design-system/README.md) に、実装の参照は [components-reference.md](./docs/design-system/components-reference.md) にある。
- **依存の向きを逆流させない。** デザインシステムの決定 → `packages/ui` → 各業務 repo（`file:` リンク）の一方向のみ。業務 repo 側でコンポーネントやトークンを新規実装してはならない。必要になった場合は `packages/ui` に入れる Issue を立てる。
- **業務 repo が持てるのは `nav-config.ts`（ナビ構造）、`i18n.ts`（業務コピー）、データ取得・状態管理・権限、ドメイン enum → コンポーネント prop の対応表、画面固有の業務レイアウトのみ。** 色・型・余白・角丸・影・モーションは `packages/ui` が持つ。
- 1製品しか使わないもの（`WorkflowProgressStrip`、オントロジーグラフ等）は業務 repo に置いてよい。判断基準は **「他の2製品がこれを欲しがるか」** — 欲しがるなら `packages/ui` に入れる。

### 禁止事項

- 生の hex（`#1a73c1` 等）と生の px を書く。トークンを `var()` で参照する。
- 業務 repo の `globals.css` に色トークンを定義する。`globals.css` で `@import "tailwindcss"` の後に `@import "@engchina/production-ready-ui/styles.css"` する（`main.tsx` から JS で import すると共有ユーティリティが生成されない）。
- `TextField` / `PageHeader` / ボタン等の共有コンポーネントを再実装する。
- `<table>` を手書きする。`DataTable` を使う。
- `<div style={{ padding: "1.5rem 2rem" }}>` のような余白コンテナを手書きする。`PageBody` を使う。
- `ToggleChip` をタブ代わりに使う。タブ＝ビュー切替は `Tabs`、チップ＝データの絞り込みは `ToggleChip`。
- `loading` 中にボタンのラベルを「実行中…」等に差し替える。ラベルは変えず、先頭アイコンがスピナーに置き換わる。
- **製品ごとのアクセント色を作る。** 製品は wordmark・ナビ・内容で区別する。
- 絵文字と手描き SVG。アイコンは `lucide-react` のコンポーネントを使い、共有コンポーネントには `icon={Upload}` のように `LucideIcon` として渡す（Lucide 名の文字列では渡さない）。
- コンポーネント内部パス（`components/core/**` 等）への直 import。パッケージのルートから import する。

### 画面の構成

すべての画面は次の構成に従う。この順序を外れた画面は review で差し戻す。

```tsx
<AppShell sidebar={<Sidebar … />}>
  <PageHeader title="…" actions={…} tabs={<Tabs … />} />
  <PageBody>
    <Section title="…">
      …
    </Section>
  </PageBody>
</AppShell>
```

- `PageHeader` と `PageBody` に `wide` を渡す場合は**必ず両方に同じ値**を渡す。片方だけだと 1920px で左端が 240px ずれる。
- `wide` 画面では**カードの中身も 100% の幅を使う**。フォーム・危険な操作区画・検索欄のコンテナに max-width を付けず、フォームは grid の段組み、検索欄は toolbar の比率配分で埋める（README §4「wide 画面の 100% 充填」）。
- 単位の境界: **文字サイズとコントロール高さは px**（ルート非依存）、**余白とレイアウト寸法は rem**（14px ルート）。

### UI 変更の検証

- `packages/ui` の変更は、**ライト / ダークの両テーマ**と、**1280px / 1920px の両幅**で確認する。
- 色・コントラストに関わる変更では、`docs/design-system/reference/*.html` をブラウザで開いて実測値と突き合わせる。
- キーボード操作（Tab 順、フォーカスリングの視認性、`Tabs` の ← → / Home / End）を確認する。
- 状態を表す UI は**色だけに依存しない**こと。`StatusBadge` / `Banner` / `Toast` はアイコンで冗長に符号化する。グレースケールにして判別できるか確認する。
- `docs/design-system/README.md` §9 の検収基準を PR の `検証結果` に転記する。

### lint

`docs/design-system/adherence.oxlintrc.json` が、デザインシステム遵守ルールの正本である。**新規コードにこの lint を通すことが、デザインシステムからのドリフトを止める唯一の現実的な手段である。**

- 対象は各 repo の `frontend/src/**/*.{ts,tsx}`。検出するのは次の 7 つ: 生の hex、inline style の生の px、デザインシステムに無い書体、文字サイズ・行間・字間・角丸の任意値（`text-[10px]` 等）、旧トークン名（ユーティリティ / CSS 変数）、`@engchina/production-ready-ui` の内部パス import、`loading` 中の `Button` ラベルの差し替え。
- prop の妥当性は TypeScript の型チェックに任せ、lint では検査しない（コンポーネントごとの許可 prop 一覧は廃止した）。
- oxlint にはネイティブの `no-restricted-syntax` が無い。そのため、同じ `{selector, message}` 形式を受け取る JS プラグイン `docs/design-system/design-system-plugin.mjs` を platform に置き、adherence 設定から相対パスで読み込む。
- **各 repo はルールもプラグインもコピーせず、platform の設定を参照する。** アプリ CI は `file:` リンクのために platform を sibling に checkout しているので、同じ相対パスで解決できる。コピーすると、ルール変更が各 repo に届かない。

```jsonc
// oxlint（frontend/.oxlintrc.json）
{
  "extends": ["../../no.1-production-ready-platform/docs/design-system/adherence.oxlintrc.json"]
}
```

```js
// ESLint（frontend/eslint.config.mjs）— 同じセレクタを ESLint 標準の no-restricted-syntax に渡す
import adherence from "../../no.1-production-ready-platform/docs/design-system/adherence.oxlintrc.json" with { type: "json" };
const { rules } = adherence.overrides[0];
export default [
  // …
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": rules["design-system/restricted-syntax"],
      "no-restricted-imports": rules["no-restricted-imports"],
    },
  },
];
```

- adherence 設定は oxlint と ESLint の両方が読むため、コメントや独自キーを書かない純粋な JSON に保つ（`x-omelette` のような未知のキーがあると、oxlint 1.82.0 は読み込みに失敗する）。
- ルールを変えるときは、NL2SQL（oxlint）と RAG / Agent（ESLint）の `src` で違反件数を確認し、PR の `検証結果` に書く。違反が残る場合は、各 repo の追従 Issue を作ってから merge する。
- アプリ固有のルールは各 repo の設定に追加してよい。ただし、同じルール名（`design-system/restricted-syntax` / `no-restricted-syntax`）を上書きすると adherence のセレクタが消えるので、別のルール名にする。

## CI / 検証コマンド

PR の CI は `.github/workflows/ci.yml` で実行される。変更範囲に応じて merge 前にローカルでも同じ command を実行し、結果を PR の `検証結果` に記載する。

```bash
# frontend (@engchina/production-ready-ui)
npm ci && npm run typecheck && npm test && npm run build

# backend (production-ready-backend-core)
cd packages/backend_core
uv sync --locked --dev
uv run black --check . && uv run ruff check . && uv run mypy src
uv run pytest --cov=pr_backend_core && uv run bandit -r src && uv run pip-audit
```
