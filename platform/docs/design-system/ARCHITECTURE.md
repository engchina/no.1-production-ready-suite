# ARCHITECTURE — RAG / NL2SQL / Agent はこのデザインシステムをどう使うか

3チームが読む**契約書**です。迷ったらこの文書が優先します。

---

## 1. 依存の向き

これが一番大事です。**3アプリはデザインシステムのプロジェクトを直接参照しません。**

```
Claude Design（design system プロジェクト）
    仕様を決める場所。コードは出荷しない。
    トークン・コンポーネント仕様・コピー規約・アクセシビリティ要件の唯一の決定場所。
        │
        │  ハンドオフ → docs/design-system/ にコミット（以降ここが正本）
        ↓
packages/ui  =  @engchina/production-ready-ui
    唯一の実装。3アプリが共有する 1 パッケージ。
        │
        │  file:../../no.1-production-ready-platform/packages/ui
        ↓
RAG / NL2SQL / Agent
    業務ロジックとナビゲーションだけを持つ。
```

**逆流させないこと。** アプリ側で見つけた改善は、アプリに実装してから昇格させるのではなく、
デザインシステム側で決めてから `packages/ui` に入れます。今 NL2SQL が独自の `button.css` と
`.dark` を持っているのは、この向きが逆になった結果です（それを解消するのが本ハンドオフ）。

---

## 2. 各アプリの入り口

### 2-1. スタイルの読み込み

```css
/* frontend/src/globals.css */
@import "tailwindcss";
@import "@engchina/production-ready-ui/styles.css";
@source "../node_modules/@engchina/production-ready-ui/dist";
```

**`main.tsx` から JS で import しないでください。** `@tailwindcss/postcss` は JS から import した CSS を
別の Tailwind ルートとして処理するため、トークン CSS の `@theme inline` と `@source` が `globals.css` 側に効かず、
`bg-primary` 等の共有ユーティリティが生成されません（RAG で実測）。

**そして各アプリの `globals.css` からトークン定義ブロックを削除します。**
`:root { --primary: … }` や `.dark { … }` がアプリ側に残っていると二重管理になり、
必ず片方だけ更新される事故が起きます。

アプリの `globals.css` に残してよいのは、そのアプリ固有のレイアウト（業務画面特有の
グリッドなど）だけです。**色・型・余白・角丸・影を書いてはいけません。**

### 2-2. テーマ切替

`<html>` の属性だけで切り替わります。CSS の `color-scheme` が全トークンを解決します。

```ts
document.documentElement.dataset.theme = "dark";   // "light" | "dark" | "auto"
```

| 指定 | 挙動 |
|---|---|
| 属性なし | ライト（既定） |
| `data-theme="light"` | ライト |
| `data-theme="dark"` | ダーク |
| `data-theme="auto"` | **OS 設定に追従** |
| `class="light"` / `class="dark"` | 旧 API。既存3アプリの実装のまま動く |

新規コードは `data-theme` を使ってください。`.light` / `.dark` は移行期間のための互換です。

### 2-3. アイコン

アイコンは `lucide-react` のコンポーネントを props に渡します（`icon={Upload}`。型は `LucideIcon`）。
`currentColor` で描かれ、寸法は `packages/ui` が 14 / 16 / 20 / 24px に固定します。アプリ側で `lucide-react@0.468` を
依存に持ち、バージョンを `packages/ui` と揃えてください。**絵文字と手描き SVG は禁止です**
（`Spinner` が唯一の自作グリフ）。

---

## 3. 1画面の書き方（これが全画面の型）

```tsx
import {
  AppShell, Sidebar, PageHeader, PageBody, Section,
  Tabs, TabPanel, DataTable, Pagination, Button, StatusBadge,
} from "@engchina/production-ready-ui";
import { RefreshCw, Upload } from "lucide-react";

export function DocumentIndexScreen() {
  const [view, setView] = useState("all");
  const [sort, setSort] = useState({ key: "updated", direction: "desc" });

  return (
    <AppShell sidebar={<Sidebar product="RAG" sections={navConfig} currentPath={path} account={me} />}>
      <PageHeader
        breadcrumbs={[{ label: "ナレッジ構築" }, { label: "文書インデックス" }]}
        title="文書インデックス"
        subtitle="アップロード済み文書の解析・索引状態を確認します。"
        actions={[
          { id: "upload", kind: "primary",   label: "文書アップロード", icon: Upload },
          { id: "reload", kind: "secondary", label: "再読込", icon: RefreshCw, loading: reloading },
        ]}
        tabs={
          <Tabs value={view} onChange={setView} items={[
            { id: "all",     label: "すべて",   count: 124 },
            { id: "indexed", label: "索引済み", count: 118 },
            { id: "failed",  label: "失敗",     count: 6 },
          ]} />
        }
      />
      <PageBody>
        <TabPanel id="all" value={view}>
          <Section title="文書一覧">
            <DataTable columns={columns} rows={rows} sort={sort} onSortChange={setSort} />
            <Pagination page={page} totalPages={total} onPageChange={setPage} summary="1–20 / 124 件" />
          </Section>
        </TabPanel>
      </PageBody>
    </AppShell>
  );
}
```

**構成は必ず `AppShell` → `Sidebar` + (`PageHeader` → `PageBody` → `Section`) の順です。**
この順序を外れた画面はレビューで差し戻してください。

`PageHeader` と `PageBody` に `wide` を渡す場合は**必ず両方に同じ値**を渡します。
片方だけだと 1920px モニタで左端が 240px ずれます。

---

## 4. 責任の境界

| `packages/ui` が持つ | 3アプリが持つ |
|---|---|
| 色・型・余白・角丸・影・モーションの全トークン | `nav-config.ts` — ナビ構造 |
| すべてのコンポーネントと状態（hover / focus / active / disabled） | `i18n.ts` — 業務コピー |
| ARIA・キーボード操作・スキップリンク・強制カラーモード | データ取得・状態管理・権限 |
| テーマ（light / dark / auto）と面スコープ | ドメイン enum → `StatusBadge` variant の対応表 |
| 日本語の行組版（禁則処理） | 画面固有の業務レイアウト |

### ドメイン enum のマッピングはアプリの責任

`packages/ui` は業務語彙を知りません。アプリが対応表を持ちます。

```ts
// RAG
const FILE_STATUS: Record<FileStatus, StatusBadgeProps["variant"]> = {
  INDEXED: "success",
  PARSING: "warning",
  FAILED:  "danger",
  QUEUED:  "neutral",
};

<StatusBadge variant={FILE_STATUS[file.status]} label={t(`fileStatus.${file.status}`)} />
```

ラベルは必ず翻訳済みの文字列を渡します。`packages/ui` 側に日本語を足さないこと。

---

## 5. アプリがやってはいけないこと

| 禁止 | 代わりに |
|---|---|
| 色トークンを自前で定義する | `packages/ui` の `--color-*` を使う |
| 生の hex / 生の px を書く | トークンを `var()` で参照 |
| `TextField` / `PageHeader` / ボタンを再実装する（**今3〜4回ずつ再実装されています**） | 共有コンポーネントを使う |
| `<table>` を手書きする | `DataTable` |
| `<div style={{ padding: "1.5rem 2rem" }}>` を書く | `PageBody` |
| `ToggleChip` をタブ代わりに使う | `Tabs`（タブ＝ビュー切替 / チップ＝絞り込み） |
| `loading` 中にラベルを「実行中…」に差し替える | ラベルは変えない。アイコンがスピナーに置き換わる |
| **製品ごとのアクセント色を作る** | 製品は wordmark とナビと内容で区別する |
| コンポーネント内部への直 import | パッケージのルートから import |
| 絵文字・手描き SVG | `lucide-react` のアイコン |

### lint で機械的に守る

`docs/design-system/adherence.oxlintrc.json` を3アプリの lint 設定から参照すると、`src/**/*.{ts,tsx}` の以下がエラーになります。
取り込み方（oxlint は `extends`、ESLint は同じ JSON を `no-restricted-syntax` に渡す）は、リポジトリ直下の `AGENTS.md`「lint」節を参照してください。

- 生の hex（`#1a73c1` など）→ 色トークンを使う
- inline style の生の px → 余白・寸法トークンか Tailwind のユーティリティを使う（Tailwind のレイアウト寸法 `min-w-[640px]` 等は画面固有のレイアウトとして許容）
- デザインシステムに無い書体 → `var(--font-sans)` / `var(--font-mono)`
- 文字サイズ・行間・字間・角丸の任意値（`text-[10px]` / `rounded-[3px]`）→ `text-xs` / `rounded-md` 等のトークン
- 旧トークン名（`bg-card` / `var(--primary)` 等。§5 の表の左列）→ 新名
- `@engchina/production-ready-ui/dist/**` など内部パスへの直 import → パッケージのルートから import する
- `loading` 中に `Button` のラベルを差し替える → ラベルは固定する

prop の妥当性（`Button` に存在しない prop を渡す等）は lint ではなく TypeScript の型チェックで検出します。

**新規コードにこの lint を通すのが、ドリフトを止める唯一の現実的な手段です。**

---

## 6. アプリ別の作業

### NL2SQL — 削除作業が主

3アプリで最も実装が進んでいるため、**独自実装が `packages/ui` に昇格した分を消す**のが仕事です。

| 削除するもの | 理由 |
|---|---|
| `components/ui/button.css` | ボタン仕様が `packages/ui` に昇格（32/36/40px、6px radius） |
| `ui/status-badge.tsx` | `StatusBadge` に昇格（トークン + 1px border + アイコン） |
| 独自 `PageHeader.tsx` | `PageHeader` に昇格（breadcrumbs / status / データ駆動 actions） |
| `globals.css` の `.dark` 上書き | ダークテーマが `packages/ui` に昇格 |
| 独自 `TextField` | `TextField` に昇格 |
| サイドバーフッターの独自実装 | `SidebarAccountFooter` に昇格 |

**残すもの（ドメイン固有）**
- `WorkflowProgressStrip`
- オントロジー / 関係グラフ → `tokens/colors-graph.css` を `@import` して
  `--color-graph-*` を使う（種別 = 塗り / 状態 = 線）

### RAG / Agent — 受け取るだけ

今ライト専用なので、**ダークテーマが無償で付いてきます**（`data-theme="dark"` を付けるだけ）。

- **Agent** はサイドバーにフッターが無いので `SidebarAccountFooter`（ユーザー・ロール・ログアウト・テーマ切替）が入ります
- **RAG** は `--font-mono` が無かったので、ID とログが Google Sans Code になります

### 3アプリ共通

1. `globals.css` のトークンブロックを削除し、`styles.css` を `globals.css` で `@import`（§2-1）
2. 手書きの padding div を `PageBody` に置換
3. `ToggleChip` のタブ流用を `Tabs` に置換
4. ボタンを `icon` プロップに移行（`icon={Upload}`。子にアイコンを書くのをやめる）
5. `StatusBadge` の `pending` を `warning` に置換
6. `.pr-icon-button` の使用箇所を `<Button variant="ghost" iconOnly>` に置換

これがハンドオフ `README.md` の **PR 5** です。

---

## 7. 変更を入れたいときの手順

アプリ開発中に「このコンポーネントにこの prop が欲しい」と思ったときの正しい順序です。

1. **アプリに実装しない。** そこで実装すると、また NL2SQL と同じ状況になります
2. Claude Design のデザインシステムプロジェクトで仕様を決める
   （トークンか、コンポーネントの prop か、新規コンポーネントか）
3. ハンドオフを受けて `packages/ui` に実装し、マイナーバージョンを上げる
4. 3アプリが `file:` リンク越しに受け取る

**例外はドメイン固有のものだけ**です。`WorkflowProgressStrip` やオントロジーグラフのように
1製品しか使わないものはアプリ側に置いて構いません。判断基準は
**「他の2製品がこれを欲しがるか」** — 欲しがるなら `packages/ui` です。

---

## 8. バージョン運用

現在3アプリは `file:` リンクでローカルのソースを直接参照しています
（`/u01/workspace/no.1-production-ready-platform/packages/ui`）。

この構成では **`packages/ui` を壊すと3アプリが同時に壊れます。** そのため:

- `packages/ui` の変更は必ず3アプリで起動確認してからマージする
- 破壊的変更（本ハンドオフのような）は `compat.css` のようなエイリアス層を用意し、
  **`packages/ui` の PR とアプリの PR を分離できる状態**にする
- 将来アプリのリリースサイクルが分かれるなら、`file:` からバージョン付きの
  レジストリ配布に移す判断が必要です

---

## 9. 迷ったときの参照先

| 知りたいこと | 見る場所 |
|---|---|
| トークン名と値 | `README.md` §5 / §6、`reference/colors-*.html` |
| 文字サイズ | `README.md` §3、`reference/type-scale.html` |
| ボタンのアイコンと loading | `README.md` §4、`reference/buttons-tabs.html` |
| タブとチップの使い分け | `reference/buttons-tabs.html` |
| コンポーネントの実装 | `components-reference.md` |
| 意図的な見た目の変更（QA 共有用） | `README.md` §7 |
| 検収基準 | `README.md` §9 |
| まだ決まっていないこと | `README.md` §11 |
| UI 変更時の禁止事項とレビュー観点 | リポジトリ直下の `AGENTS.md`「デザインシステム / UI」節 |

---

## 10. この文書の位置

この文書はリポジトリの `docs/design-system/ARCHITECTURE.md` にあります。
`AGENTS.md` の「デザインシステム / UI」節から参照されており、`CLAUDE.md` は
`@AGENTS.md` で AGENTS.md を取り込むため、**Claude Code と Codex は UI に触るとき
自動でこのルールを読みます**。zip を配布したりプロンプトを貼る運用は不要です。

デザインシステム側（Claude Design）で決定が変わったときは、`docs/design-system/` の
該当ファイルを差分で更新する PR を出してください。**git の履歴が、誰がいつ何を
変えたかの唯一の記録になります。**
