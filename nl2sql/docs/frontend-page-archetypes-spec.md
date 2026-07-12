# フロントエンド ページアーキタイプ / 統一プリミティブ 仕様書

> **このファイルは NL2SQL 管理画面の「ページレイアウトの型」と「横断プリミティブの使い方」を統一する正本(spec)です。**
> 対象は **システム設定セクション以外の全ページ**。メッセージ機構は [frontend-messaging-spec.md](./frontend-messaging-spec.md)、ボタンは [frontend-button-spec.md](./frontend-button-spec.md) を併せて正本とする。
> 逸脱が必要な場合は AGENTS.md §コーディング規約 8/16 に従い理由を添えて確認する。

---

## 0. 設計原則

1. 各ページは **4 つのアーキタイプのいずれか**に属する（§1）。独自レイアウトを勝手に増やさない。
2. 共通シェル = `PageHeader`（+任意 `StatusBar`）+ 共有プリミティブ。新規 CSS/UI を作らない。
3. 色は **意味論トークン**のみ（`text-foreground` / `bg-card` / `border-border` / `text-muted-foreground` / `text-danger` …）。生パレット class（`slate-`/`sky-`/`red-`…）を新規に足さない（§4）。
4. 一覧・結果表は共有 `DataTable` + `Pagination`、詳細併置は共有 `FixedSplitPane`、確認は `useConfirm`、通知は §messaging の 6 チャネル。
5. 文言はすべて i18n 経由（`src/lib/i18n.ts` の `t()`）。パッケージ側プリミティブは i18n 非依存（翻訳済み文字列/ラベルを props で受ける）。

---

## 1. ページアーキタイプ（4 種）

### A. 一覧 → 全画面エディタ（エンティティ CRUD）
`ProfileManagementPage`（業務プロファイル）を参照実装とする。
- URL の検索パラメータ（`?id=` / `?profile=` 等）を**単一情報源**とし、`null`=一覧 / `"new"`=新規 / `<id>`=編集。
- 一覧: 共有 `DataTable`（検索/ソート/`Pagination`）+ 「新規」ボタン + 任意 `StatusBar`。
- エディタ: 全幅で `<section>` を**縦積み**（基本 → オブジェクト → 学習 → 実行…）。上部に 戻る / Save(primary) / Delete(danger)。
- **dirty-guard**: 未保存で離脱時は破棄確認（`useConfirm`）。`isDirty` は共通フックに集約。
- **パンくず**: 一覧 › 対象名 を表示（方向感）。
- **割当**: テーブルの管理 / ビューの管理 / 業務プロファイル / 用語・同義語 / 検証用サンプルデータ / コメント管理 / アノテーション管理 / 質問分類モデル管理 / フィードバック管理。
- 旧タブ（list/create/import 等）は **一覧上のアクション** か **エディタ内の節** へ平坦化。破壊的操作は `useConfirm` + `ADMIN_EXECUTE` ゲートへ集約。

### B. マスタ詳細ブラウズ（読み取り/点検）
一覧 + 詳細を共有 `FixedSplitPane` で常時併置（§3 の規約に従う）。
- **割当**: 実行履歴。A 型の一覧内詳細も本規約を流用可。

### C. ツール/ワークフロー（入力 → アクション → 結果）
上部=入力、実行ボタン（`loading`）、下部=結果（`DataTable` + `Pagination`）。段階インジケータは必要時のみ。
- **割当**: SQL 生成 / SQL 確認・修復 / SQL から質問を生成。分割ペインを使う場合は §3 準拠。

### D. ダッシュボード/状態
メトリクスカード + `StatusBadge` + セクション。編集は最小。
- **割当**: 品質評価 / NL2SQL 安全境界・Readiness（URL は `/settings/` だが改善運用所属＝対象内）。

> システム設定（OCI 認証 / アップロード保存先 / モデル / データベース）は**対象外**。

---

## 2. 共有プリミティブ API（`@engchina/production-ready-ui`）

### Pagination（新規）
```ts
usePagination<T>(items: T[], pageSize?: number)
  => { page, setPage, totalPages, pageItems, range: { start, end, total } }
<Pagination page totalPages onPageChange summary prevLabel nextLabel className? />
```
- `summary` / `prevLabel` / `nextLabel` は翻訳済み文字列（caller が `t()` で用意）。件数は等幅数字（`tnum`）。
- 既定 `pageSize` = `DEFAULT_PAGE_SIZE`（10）。手書き `PAGE_SIZE` を廃し本定数へ。

### DataTable（新規）
```ts
<DataTable columns rows getRowKey sort? onSortChange? empty? loading? className? />
// columns: { key, header, render?, sortable?, align?, className? }[]
```
- ソートは `aria-sort`、空/読込は State views 連動、横スクロールは内蔵コンテナ（`overflow-x-auto` + `min-w-0`）。
- ソート/選択が不要な単純表は薄く使う（列 render のみ）。複雑表は段階移行、無理に一括置換しない。

### 既存（再利用）
`Button` / `Card` / `Banner` / `FormStatus` / `FieldError` / `SelectField` / `Switch` / `ToggleChip` / `Skeleton` / `StatusBadge` / `LoadingState`・`ErrorState`・`EmptyState` / `ConfirmProvider`・`useConfirm` / `toast`・`Toaster` / `PageHeader` / `Breadcrumbs` / `Sidebar`・`AppShell`。

---

## 3. 分割ペイン（slide）の統一

`FixedSplitPane`（`frontend/src/components/layout/FixedSplitPane.tsx`）を全 B/C ページで同一規約で使う。
- `splitId` 命名: `<feature>-<view>`（例: `table-management-list`）。localStorage キーは `fixed-split-pane` 規約に一任。
- `preferredWidePane`: 一覧+詳細は詳細側（通常 `right`）を wide 既定。
- 左右枠 class は共通ヘルパ（`DbObjectManagementPanelShell` を一般化した `PanelShell`）に集約し各ページで重複させない。
- 狭幅（<xl）は縦積みへフォールバック（既存 CSS 準拠）。divider の a11y（`role="separator"` / Arrow/Home / grip）は現行を基準とする。

---

## 4. カラートークン対応表（生 class → 意味論トークン）

> **注意**: 定義済みトークンは `tokens.css` の `--background #f7f8fa` / `--foreground #1c1e21` / `--card #fff` / `--border #e3e6ea` / `--muted #6b7280`(=**灰色テキスト**) / `--primary #1a73c1` / `--ring` / `--success(-bg)` / `--warning(-bg)` / `--danger(-bg)` / `--info(-bg)` のみ。**`muted-foreground` / `card-foreground` は存在しない**(使うと無効ユーティリティで色が付かない)。副次テキストは `text-muted`。`bg-muted` は濃い灰色なので**パネル背景に使わない**(パネルは `bg-background`)。

| 生 class | トークン |
|---|---|
| `text-slate-900` / `-950` / `-800` / `-700` | `text-foreground` |
| `text-slate-600` / `-500` / `-400` | `text-muted` |
| `bg-white` | `bg-card` |
| `bg-slate-50` | `bg-background` |
| `bg-slate-100`（skeleton 等） | `bg-muted/30`〜`/40` |
| `border-slate-200` / `-300` | `border-border` |
| `text-red-*` / `bg-red-*` / `border-red-*` | `text-danger` / `bg-danger-bg` / `border-danger/30` |
| `text-emerald-*` / `green-*` | `text-success` / `bg-success-bg` |
| `text-amber-*` / `yellow-*` | `text-warning` / `bg-warning-bg` |
| `text-sky-*` / `blue-*`（アクセント/選択/フォーカス） | `text-primary` / `bg-primary/10` / `border-primary` / `focus:ring-ring/40` / `focus:border-primary` / `text-info`（用途で選択） |
| モーダル scrim `bg-slate-950/50` | `bg-black/50`（黒オーバーレイは維持） |
| コードブロック `bg-slate-950 text-slate-50` | 維持可（意図的なダーク面。テーマ chrome ではない） |

重量級ファイルから着手: 共有 `DbObjectManagementShared`（テーブル/ビュー/SQL分析の見た目の核）/ `DbAdminShared` / `DataManagementPage` / `EvaluationPage` / `QuestionLearningPage`。

---

## 5. Definition of Done（ページ移行時）

1. アーキタイプシェルへ載せ替え（§1）。
2. 手書き table/pagination/modal を共有プリミティブへ置換（§2）。
3. 生カラー class → トークン（§4）。
4. メッセージ 6 チャネル準拠（messaging-spec）、Toast 配線、`useConfirm` 集約。
5. `npm run lint && npm run build` + Vitest 通過。
6. **Playwright**: 主要導線・375px/デスクトップ・空/読込/エラー・キーボード/`Esc`/focus 復帰・Pagination・分割ペイン divider。
7. `ui-ux-pro-max` チェックリストで自己レビュー。
