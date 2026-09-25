# ページの型と共有プリミティブ（Page archetypes）

> 3製品共通の正本。システム設定以外のページのレイアウトの型と、共有プリミティブの使い方を決める。
> 画面の骨格（`AppShell` / `PageHeader` / `PageBody` / `Section`）とトークンは [デザインシステム](../design-system/README.md)、ボタンは [buttons.md](./buttons.md)、通知は [messaging.md](./messaging.md) を併せて正本とする。
> 各ページがどの型に属するかは、製品の `docs/frontend-page-archetypes-spec.md` に書く。

---

## 0. 設計原則

1. 各ページは **4 つの型のどれか** に属する（§1）。独自のレイアウトを勝手に増やさない。
2. 共通の骨格 = `PageHeader`（+ 必要なら状態バー）+ 共有プリミティブ。新しい CSS / UI を作らない。
3. 色は**意味のトークン**だけ（[デザインシステム README §5 / §6](../design-system/README.md)）。生のパレットの class（`slate-` / `sky-` / `red-` …）を足さない。
4. 一覧・結果の表は共有 `DataTable` + `Pagination`、詳細の併置は `FixedSplitPane`、確認は `useConfirm`、通知は [messaging.md](./messaging.md) の 6 チャネル。
5. 文言はすべて i18n 経由。共有パッケージのプリミティブは i18n に依存しない（翻訳済みの文字列・ラベルを props で受ける）。
6. 一覧の行と詳細パネルの対象オブジェクトの操作は `EntityAction` を 1 つの定義にし、行は `RowActionMenu`、詳細は `ObjectActionBar` で出す（[buttons.md §5.1](./buttons.md#51-オブジェクト操作一覧行--詳細)）。
7. 一覧 / 詳細の単一選択は、行の操作以外の領域のクリックで選び、選択の状態は行全体の背景と `aria-current` で示す。キーボード向けに先頭セルの対象名のボタンを残し、行のメニューには削除・アーカイブなどの実際の操作だけを入れる。
8. ページの操作は `PageHeader` の右側に置く（[buttons.md §5](./buttons.md#5-ページヘッダー操作)）。
9. コードブロック・プレビュー・結果などのコンテンツ内の操作は `ContentActionBar` で右上に寄せる。ページ・オブジェクト・行・コンテンツの操作を混ぜない。

---

## 1. ページの型（4 種）

### A. 一覧 → 全画面エディタ（エンティティの CRUD）

- URL の検索パラメータ（`?id=` など）を**唯一の情報源**とし、`null` = 一覧 / `"new"` = 新規 / `<id>` = 編集。
- 一覧：共有 `DataTable`（検索 / ソート / `Pagination`）+「新規」ボタン + 必要なら状態バー。
- エディタ：全幅で `Section` を**縦に積む**。上部に 戻る / 保存（primary）/ 削除（danger）。
- **離脱ガード**：未保存のまま離れるときは破棄を確認する（[workspace-state.md](./workspace-state.md#未保存変更の離脱ガード)）。
- **パンくず**：一覧 › 対象名 を出す。
- 古いタブ（list / create / import など）は、**一覧上の操作** か **エディタ内の節** に平らにする。破壊的な操作は `useConfirm` に集める。

### B. マスタ詳細の閲覧（読み取り / 点検）

一覧と詳細を `FixedSplitPane` で常に並べる（§3）。

- 行の操作以外の領域のクリックで選び、右の詳細をすぐ更新する。`詳細` ボタンを行の中に重ねて置かない。
- 一覧の行の操作は `RowActionMenu` 1 個にまとめ、詳細側は同じ action の定義を `ObjectActionBar` に渡す。
- 詳細側に常に出す操作は、非破壊・高頻度の最大 2 個に限る。危険な操作と低頻度の操作は overflow メニューに入れる。
- 行のメニューは表や分割ペインの scroll container に切られないこと。件数が少なく一覧の container が実際にはスクロールしていない場合も、viewport の中に浮かせて出す。

### C. ツール / ワークフロー（入力 → 操作 → 結果）

上に入力、実行ボタン（`loading`）、下に結果（`DataTable` + `Pagination`）。段階の表示は必要なときだけ。分割ペインを使う場合は §3 に従う。

### D. ダッシュボード / 状態

メトリクスのカード + `StatusBadge` + セクション。編集は最小にする。

> システム設定（OCI 認証 / アップロード保存先 / モデル / データベース / 外観）は共有パッケージ `@engchina/production-ready-system-settings` の画面を使い、本規約の対象外。

---

## 2. 共有プリミティブ（`@engchina/production-ready-ui`）

### Pagination

```ts
usePagination<T>(items: T[], pageSize?: number)
  => { page, setPage, totalPages, pageItems, range: { start, end, total } }
<Pagination page totalPages onPageChange summary prevLabel nextLabel className? />
```

- `summary` / `prevLabel` / `nextLabel` は翻訳済みの文字列（呼び出し側が `t()` で用意する）。件数は等幅数字（`tnum`）。
- 既定の `pageSize` は `DEFAULT_PAGE_SIZE`（10）。画面ごとの `PAGE_SIZE` を作らない。

### DataTable

```ts
<DataTable columns rows getRowKey sort? onSortChange? empty? loading? className? />
// columns: { key, header, render?, sortable?, align?, className? }[]
```

- ソートは `aria-sort`、空 / 読込は State views と連動、横スクロールは内蔵のコンテナで行う。`<table>` を手書きしない。
- ソート / 選択が要らない単純な表は列の render だけで使う。複雑な表は段階的に移し、無理に一度に置き換えない。

### そのほか

`Button` / `Card` / `Banner` / `FormStatus` / `FieldError` / `SelectField` / `Switch` / `ToggleChip` / `Tabs` / `Skeleton` / `StatusBadge` / `LoadingState`・`ErrorState`・`EmptyState` / `ConfirmProvider`・`useConfirm` / `toast`・`Toaster` / `PageHeader` / `PageBody` / `Section` / `Breadcrumbs` / `Sidebar`・`AppShell`。

### 操作の置き場所

| 階層 | 影響範囲 | 置き場所 / プリミティブ |
|---|---|---|
| ページ | 今のページ全体 | `PageHeader` の右側 |
| オブジェクト | 選択中の 1 オブジェクト | `ObjectActionBar` の右側 |
| 行 | 一覧の 1 行 | `RowActionMenu` |
| コンテンツ | 直下のコード / プレビュー / 結果 | `ContentActionBar` の右側 |

`FixedSplitPane` はまだ `packages/ui` にない（[README](./README.md#実装の置き場所)）。

---

## 3. 分割ペイン

`FixedSplitPane` を B / C 型のページで同じ規約で使う。

- `splitId` は `<feature>-<view>`（例：`table-management-list`）。localStorage の key は部品に任せる。
- `preferredWidePane`：一覧 + 詳細では詳細側（通常 `right`）を既定で広くする。
- 左右の枠の class は共通の panel shell にまとめ、ページごとに重ねて書かない。
- 狭い幅（`xl` 未満）は縦積みに切り替える。divider は `role="separator"`、矢印 / Home キー、grip を備える。

---

## 4. 色

色のトークンとその対応は [デザインシステム README §5（トークン名の対応表）/ §6（新規トークン）](../design-system/README.md) が正本。生のパレットの class や旧名（`bg-card` / `text-muted` / `bg-primary` など）を新しいコードで使わない。

---

## 5. ページを移すときの完了条件

1. 型の骨格に載せ替える（§1）。
2. 手書きの table / pagination / modal を共有プリミティブに置き換える（§2）。
3. 生の色の class をトークンにする（§4）。
4. [messaging.md](./messaging.md) の 6 チャネルに従い、Toast と `useConfirm` にまとめる。
5. 製品の lint / build / Vitest を通す。
6. **Playwright**：主な導線・375px / desktop・空 / 読込 / エラー・キーボード / `Esc` / フォーカスの戻り・Pagination・分割ペインの divider。
7. `ui-ux-pro-max` のチェックリストで自己レビューする。
