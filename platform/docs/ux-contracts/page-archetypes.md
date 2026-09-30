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
- **保存の失敗**：欄に結び付く失敗は欄の直下、それ以外はヘッダーの直下の `SaveErrorBanner` の 1 か所だけ（Toast・フォームの下の `FormStatus` に重ねない。[messaging.md §3.3.1](./messaging.md#331-全画面のエディタの保存の失敗585)）。
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

### 一覧の絞り込みの検索（#535）

検索には 2 種類あり、操作を分ける（NN/g・Material 3・GOV.UK Design System・WCAG 2.2 に合わせる）。

| 種類 | 例 | 操作 | 部品 |
|---|---|---|---|
| **一覧の絞り込み**（画面上の一覧・表を名前などで絞る。結果は同じ一覧の行） | ナレッジベース・業務ビュー・文書・フィードバック（RAG）、DB オブジェクト・プロファイル・履歴・学習候補・フィードバック履歴・スキーマ参照（NL2SQL）、メモリ（Agent）、ユーザー・ロール・権限の対象（system-settings） | **入力に合わせて絞り込む。検索ボタンを置かない。** 入力が止まって 300ms で反映し、Enter は debounce を待たずにすぐ反映する。値があるときは末尾に消去（×。Escape でも消す） | `SearchField` |
| **重い検索・問い合わせ**（LLM・ベクトル検索・SQL の生成と実行を呼ぶもの。結果は回答・生成物） | RAG 検索・チャット・検索テスト・文書の検索比較（RAG）、SQL 生成・オントロジーの問い合わせ・分類器の予測（NL2SQL）、Run の目標（Agent） | **明示的に実行する。** ボタンと Enter（複数行は Ctrl/⌘+Enter）。入力中には実行しない | `TextField`（`type="search"` にしない）+ `Button`、Enter の判定は `isSubmitEnter` |

一覧の絞り込みの規則:

1. **入力に合わせて絞り込む**（debounce 300ms、Enter はすぐ）。検索ボタン・「絞り込み」ボタンを隣に置かない。同じ一覧のほかの条件（`SelectField`・`ToggleChip` の状態・種類）も、選んだらすぐ適用する。
2. **日本語入力**: IME の変換中（`compositionstart`〜`compositionend`、`isComposing`、Safari の確定の Enter の `keyCode 229`）は、入力でも Enter でも絞り込まない。変換を確定した値で絞り込む。`SearchField` と `isImeComposing` / `isSubmitEnter` が判定する。製品で `event.key === "Enter"` だけで判定しない。
3. **件数を伝える**: 結果の件数を `aria-live`（`SearchField` の `resultCountLabel`）で読み上げる。画面の件数の表示（`StatusBadge` 等）はそのままでよい。
4. **0 件**: 空の状態に「検索に一致する〜がありません」と、「検索語をクリア」（`ClearActionButton`）を出す。データがそもそも無いときの空の状態と文言を分ける。
5. **作業状態**: 検索語は作業状態として残す（[workspace-state.md](./workspace-state.md)）。`SearchField` の `value` には保存している**適用中**の検索語を渡し、入力中の文字は部品が持つ。検索語が変わったらページングを 1 ページ目へ戻し、選択を解除する（変わらない Enter・blur ではページと選択を失わない）。
6. **サーバー側で絞り込む一覧**: 古い応答で新しい結果を上書きしない。TanStack Query は検索語を query key に入れ、`placeholderData: keepPreviousData` で前の一覧を出したまま取り直す。手で取得する一覧は、要求の連番か `AbortController` で最後の要求の応答だけを使う。読込中は一覧の領域で示す（操作したボタンが無いので、`ProcessingIndicator` / `TimedLoadingState` のスピナーを出す）。
7. **親で遅延させない**: debounce は `SearchField` の 1 か所だけにする。親で `useDebouncedValue` などを重ねない（反映が遅れる）。

レビューのチェックリスト（lint が見るのは 1 だけ）:

- [ ] 一覧の絞り込みの検索欄が `SearchField`（adherence の lint が `type="search"` の `TextField` / `<input>` を検出する）
- [ ] 検索欄の隣に「検索」「絞り込み」「適用」のボタンが無い
- [ ] IME の変換中に問い合わせ・絞り込みをしない（e2e で `compositionstart` → `isComposing` の `input` → `compositionend` を確かめる）
- [ ] 0 件の空の状態に「検索語をクリア」がある
- [ ] 検索語が作業状態に残り、変えるとページが 1 ページ目に戻る
- [ ] 重い検索は `type="search"` にせず、明示的に実行し、Enter の判定が `isSubmitEnter`

例外（理由があって外す）:

| 対象 | 例外 | 理由 |
|---|---|---|
| **監査ログの条件フォーム**（Agent の監査） | 複数の条件（Run ID・ツール・状態・エラーコード・警告・1 ページの件数）をまとめて「フィルター適用」で問い合わせる。テキストの条件の Enter は「フィルター適用」と同じ（`isSubmitEnter`） | Run ID・エラーコードは完全一致の条件で、入力途中の値で問い合わせても意味が無い。監査ログの問い合わせは条件を組み合わせてから 1 度だけ送る。適用前の条件は作業状態に別に残す |
| **グラフの強調の検索**（NL2SQL のオントロジーのグラフのツールバー） | `SearchField` を使わず、ツールバーの枠の中に枠なしの入力欄を置く。入力のたびに一致した概念を強調し、前へ / 次へで移る（Escape の消去は IME の変換中は行わない） | 一覧の行を減らさない強調で、問い合わせもしない。ツールバーのほかの操作と同じ高さ・枠の複合部品として e2e で寸法を固定している |
| **検索できる選択の候補の絞り込み**（`SearchableMultiSelect` / `SearchableSelectField`。RAG のナレッジベース・業務ビューの選択。#578） | 検索欄は `SearchField`（300ms・IME の変換中は絞らない・消去）だが、role=combobox で候補の一覧（listbox）を絞る。Enter・矢印は候補の選択（IME の変換中は無視）。0 件は一覧の中に「一致する〜がありません」を出し、「検索語をクリア」のボタンは置かない（検索欄の × と Esc で消す）。候補が多い（RAG の KB は 201 件以上）ときはサーバー側で検索し、続きは「さらに表示」（単一選択はスクロール・最後の候補からの ↓） | 一覧ではなく選択肢の listbox の型（APG の Combobox）。検索語は作業状態に残さない（選んだ値を残す） |

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
