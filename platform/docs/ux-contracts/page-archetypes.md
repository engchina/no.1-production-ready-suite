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
7. 一覧 / 詳細の単一選択は、行の操作以外の領域のクリックで選び、選択の状態は行全体の背景と `aria-current` で示す。キーボード向けに先頭セルの対象名のボタン（共有の `RowTitleButton`。選択中は `current` で `aria-current`）を残し、行のメニューには削除・アーカイブなどの実際の操作だけを入れる。
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

### 一覧の型と、基準から外す例外（#265 / #403）

一覧は次の基準にそろえる（ルートの `AGENTS.md`「読み込み中・一覧・ページング」）。

- 表は `DataTable` の `stickyHeader` + `visibleRows`（md 未満 5 行・md 以上 8 行）、表ではない行リストは `INFORMATION_LIST_SCROLL_CLASS`。それを超える行は一覧の中で縦スクロールにし、ページ全体を伸ばさない。手書きの `max-h-[…]` で高さを決めない。
- ページングは一覧の直下の共通 `Pagination`（10 件/ページ）。1 ページしかないときは出さない。
  - クライアント側で全件を持つ一覧: `usePagination` / `PagedDataTable`。
  - offset / limit / total の API: `offsetPagination` / `offsetForPage`（RAG のチャットの会話一覧など）。
  - カーソル（`next_cursor`）と `total` を返す API で「前へ / 次へ」だけを送る一覧: 前へ戻るカーソルを画面が積み、ページ番号と件数を `Pagination` に渡す（NL2SQL の SQL生成評価の結果明細・最近の job。`cursorPagination`）。

次の一覧は、理由があって基準から外す。新しく例外を足すときは、ここに理由を書く。

| 一覧 | 例外 | 理由 |
|---|---|---|
| **選択と連動する一覧**（RAG の文書詳細の chunk・抽出セグメント） | ページングしない。5 / 8 行の高さ（`INFORMATION_LIST_SCROLL_CLASS`）で固定し、中をスクロールする。chunk は xl 以上でプレビューの横に並ぶとき、プレビューと同じ高さのタブのパネルがスクロールするので、一覧には高さを付けない（スクロールを二重にしない） | プレビュー・抽出の要素・引用の deep-link から、どの chunk でも選べる必要がある。ページに分けると、選んだ chunk が別のページに隠れる。選んだ chunk が見える範囲の外にあれば、そのスクロール領域の中だけを動かして見せる（ページ全体は動かさない）。deep-link はフォーカスごと移す |
| **カーソル型の API の「さらに読み込む」**（NL2SQL の実行履歴・プロファイル一覧・DB オブジェクトの一覧（テーブル・ビュー・データ管理・メタデータ SQL・スキーマ参照・対象の選択）・Deep Data Security の対象オブジェクト） | 共通の `Pagination` を使わず、一覧の下の「さらに読み込む」で次のカーソルの分を足していく。一覧の高さは基準どおり（5 / 8 行で中をスクロール） | API がカーソル（`next_cursor`）だけを返し、任意のページへの移動を持たない。データ辞書・DB のオブジェクトは件数を数えるのが重く、件数を返さない API もある。読み込んだ行を残したまま選択・検索と組み合わせるため、追加読み込みの型のままにする。読み込んだ範囲は「N / M 件を読込済み」などで示す |
| **分析の一覧**（RAG のフィードバック） | 1 ページの件数を 25 / 50 / 100 から選べる。ページ番号で任意のページへ移れる。件数とページは URL に残す | 評価の傾向を期間・理由・対象で絞り込んで見渡す分析の画面で、1 度に見る件数を利用者が決める意味がある。URL を共有すると同じ範囲を見られる。表の中の縦スクロール（`stickyHeader` + `visibleRows`）は基準どおり。番号のページ送りは今は RAG だけが使うので製品に置く（他の製品が欲しがったら `packages/ui` に上げる） |
| **LLM 判定の結果明細**（NL2SQL の SQL生成評価の結果明細の表） | 表の高さは `visibleRows` ではなく固定（30.5rem）。ページングは基準どおり（10 件/ページ） | 1 行に期待 SQL・生成 SQL のブロック（最大 8rem）が並ぶ。8 行ぶんの実測では 1 画面を超える |

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

いずれも `@engchina/production-ready-ui` から使う。

---

## 3. 分割ペイン

`@engchina/production-ready-ui` の `FixedSplitPane` を B / C 型のページで同じ規約で使う。構造 CSS は共有の `tokens.css` が配布する。

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
