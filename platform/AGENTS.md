# AGENTS.md — Production Ready Platform

> **共通基盤（`platform/`）固有のルール**です。GitHub 運用・Issue / PR 規約・CI・共通の技術方針は、monorepo 共通の [../AGENTS.md](../AGENTS.md) を正本として先に適用します。
> Claude Code と Codex の両方が参照します。`CLAUDE.md` はこのファイルを `@AGENTS.md` で取り込みます。ルールを変更する際は **必ずこのファイル（共通ルールは ../AGENTS.md）を編集**してください。
> 共有パッケージの運用ルール（版管理・変更時の確認範囲）は [CONTRIBUTING.md](./CONTRIBUTING.md) を参照。
> Issue には `platform` label を付け、PR title の scope は `platform` にする。`platform/` の変更は統合 CI で全製品の job を実行する。

## デザインシステム / UI

- **UI に触る変更（`packages/ui` および各製品（`rag/` `nl2sql/` `agent/`）の `frontend/`）では、`docs/design-system/` を正本とする。** 作業前に [ARCHITECTURE.md](./docs/design-system/ARCHITECTURE.md) を読む。トークン値・コンポーネント仕様・意図的な見た目の変更点は [README.md](./docs/design-system/README.md) に、実装の参照は [components-reference.md](./docs/design-system/components-reference.md) にある。
- **依存の向きを逆流させない。** デザインシステムの決定 → `packages/ui` → 各製品（`file:../../platform/packages/ui` リンク）の一方向のみ。製品側でコンポーネントやトークンを新規実装してはならない。必要になった場合は `packages/ui` に入れる Issue を立てる。
- **各製品が持てるのは `nav-config.ts`（ナビ構造）、`i18n.ts`（業務コピー）、データ取得・状態管理・権限、ドメイン enum → コンポーネント prop の対応表、画面固有の業務レイアウトのみ。** 色・型・余白・角丸・影・モーションは `packages/ui` が持つ。
- 1製品しか使わないもの（`WorkflowProgressStrip`、オントロジーグラフ等）はその製品のディレクトリに置いてよい。判断基準は **「他の2製品がこれを欲しがるか」** — 欲しがるなら `packages/ui` に入れる。

### 禁止事項

- 生の hex（`#1a73c1` 等）と生の px を書く。トークンを `var()` で参照する。
- 製品の `globals.css` に色トークンを定義する。`globals.css` で `@import "tailwindcss"` の後に `@import "@engchina/production-ready-ui/styles.css"` する（`main.tsx` から JS で import すると共有ユーティリティが生成されない）。
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

- 対象は各製品の `frontend/src/**/*.{ts,tsx}`。検出するのは次の 7 つ: 生の hex、inline style の生の px、デザインシステムに無い書体、文字サイズ・行間・字間・角丸の任意値（`text-[10px]` 等）、旧トークン名（ユーティリティ / CSS 変数）、`@engchina/production-ready-ui` の内部パス import、`loading` 中の `Button` ラベルの差し替え。
- prop の妥当性は TypeScript の型チェックに任せ、lint では検査しない（コンポーネントごとの許可 prop 一覧は廃止した）。
- oxlint にはネイティブの `no-restricted-syntax` が無い。そのため、同じ `{selector, message}` 形式を受け取る JS プラグイン `docs/design-system/design-system-plugin.mjs` を platform に置き、adherence 設定から相対パスで読み込む。
- **各製品はルールもプラグインもコピーせず、platform の設定を相対パス（`../../platform/docs/design-system/…`）で参照する。** monorepo なので CI でもローカルでも同じパスで解決できる。コピーすると、ルール変更が各製品に届かない。

```jsonc
// oxlint（frontend/.oxlintrc.json）
{
  "extends": ["../../platform/docs/design-system/adherence.oxlintrc.json"]
}
```

```js
// ESLint（frontend/eslint.config.mjs）— 同じセレクタを ESLint 標準の no-restricted-syntax に渡す
import adherence from "../../platform/docs/design-system/adherence.oxlintrc.json" with { type: "json" };
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
- ルールを変えるときは、NL2SQL（oxlint）と RAG / Agent（ESLint）の `src` で違反件数を確認し、PR の `検証結果` に書く。違反が残る場合は、同じ PR で製品側を直すか、製品ごとの追従 Issue を作ってから merge する。
- 製品固有のルールは各製品の設定に追加してよい。ただし、同じルール名（`design-system/restricted-syntax` / `no-restricted-syntax`）を上書きすると adherence のセレクタが消えるので、別のルール名にする。

## CI / 検証コマンド

PR の CI は suite root の `.github/workflows/ci.yml`（`Platform / UI`・`Platform / backend_core` job と、影響を受ける全製品の job）で実行される。変更範囲に応じて merge 前にローカルでも同じ command を実行し、結果を PR の `検証結果` に記載する。

```bash
# frontend (@engchina/production-ready-ui) — platform/ で実行
npm ci && npm run typecheck && npm test && npm run build

# backend (production-ready-backend-core)
cd packages/backend_core
uv sync --locked --dev
uv run black --check . && uv run ruff check . && uv run mypy src
uv run pytest --cov=pr_backend_core && uv run bandit -r src && uv run pip-audit
```
