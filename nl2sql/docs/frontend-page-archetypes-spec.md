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
6. 一覧行と詳細パネルの対象オブジェクト操作は `EntityAction` を単一情報源にし、行は `RowActionMenu`、詳細は `ObjectActionBar` で表示する。複数の行内文字ボタンを新規追加しない。行/詳細の menu は最近の実スクロール祖先を優先して方向判定し、下端では上方向へ反転、上下とも不足する場合は menu 内部をスクロールさせる。
7. 一覧/詳細の単一選択は、行の非アクション領域クリックで選択し、選択状態は行全体の背景と `aria-current` で示す。キーボード向けには先頭セルの対象名ボタンを残し、行メニューには削除・アーカイブなど実操作だけを入れる。
8. ページ級操作は `PageHeader` 右側に置き、`primary/secondary` の作業開始グループと `utility` のページツールグループを分ける。`lg` 未満は最高優先度 1 件 + `その他の操作` へ収める。
9. コードブロック・プレビュー・結果などのコンテンツ内操作は `ContentActionBar` で右上に寄せる。ページ級・オブジェクト級・行級・内容級の操作を混在させない。

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
- 行の非アクション領域クリックで選択し、右側詳細を即時更新する。`詳細` ボタンを行内に重複配置しない。
- 一覧行の対象操作は `RowActionMenu` 1 個へ集約し、詳細側は同じ action descriptor を `ObjectActionBar` へ渡す。
- 詳細側で常時表示する操作は最大 2 個の非破壊・高頻度操作に限定し、危険操作と低頻度操作は overflow menu に入れる。
- 行 menu は表や分割ペインの scroll container に裁切されないこと。件数が少なく一覧 container が実スクロール状態でない場合も、viewport 内に浮かせて表示する。
- **割当**: 実行履歴。A 型の一覧内詳細も本規約を流用可。

### C. ツール/ワークフロー（入力 → アクション → 結果）
上部=入力、実行ボタン（`loading`）、下部=結果（`DataTable` + `Pagination`）。段階インジケータは必要時のみ。
- **割当**: SQL 生成 / SQL から質問を生成。分割ペインを使う場合は §3 準拠。

### D. ダッシュボード/状態
メトリクスカード + `StatusBadge` + セクション。編集は最小。
- **割当**: SQL生成評価。

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

### アクション配置レイヤー
| レイヤー | 影響範囲 | 置き場所 / プリミティブ |
|---|---|---|
| ページ級 | 現在ページ全体 | `PageHeader` 右側 |
| オブジェクト級 | 選択中の 1 オブジェクト | `ObjectActionBar` 右側 |
| 行級 | 一覧の 1 行 | `RowActionMenu` |
| 内容級 | 直下のコード/プレビュー/結果 | `ContentActionBar` 右側 |

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
