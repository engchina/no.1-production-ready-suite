# フロントエンド ボタン設計 仕様書（Button Spec）

> **このファイルはシステム全体のボタン(大きさ・命名・スタイル・配置)を統一する正本(spec)です。**
> ボタンを新規実装・改修するときは**必ず本仕様に従う**こと。類似機能のボタンは、サイズ・variant・配置・命名を揃える。
> 逸脱が必要な場合は AGENTS.md §コーディング規約 8/10 に従い理由を添えて確認する。
>
> 対象スタック: Vite + React + TypeScript + Tailwind v4 + 共有 UI(`@engchina/production-ready-ui`) + アプリ共通 Button/CSS。
> 準拠: `ui-ux-pro-max` §2 Touch & Interaction / §4 Style Selection(`primary-action` / `state-clarity`)/ §1 Accessibility / §8 Forms。
> 関連: 操作結果の通知は [frontend-messaging-spec.md](./frontend-messaging-spec.md)(Toast / FormStatus / ConfirmDialog)を使う。

---

## 0. 原則

1. **アクションボタンは必ず共通 [`<Button>`](../frontend/src/components/ui/button.tsx) を使う。** 同等スタイルを生 `<button>` で再実装しない。
2. **同時に操作する領域の主ボタン(primary)は原則 1 つ**(`primary-action`)。他は secondary / ghost に従属させる。
3. **類似機能は同じ size・variant・配置・文言キー規則**にする。
4. **文言は i18n 経由**、アイコンは Lucide(emoji 禁止)。
5. **破壊的操作は danger + 確認ダイアログ**(`useConfirm`)。主アクションから視覚的に分離する。

---

## 1. ボタンの種別(4 分類)

| 種別 | 実体 | 用途 |
|---|---|---|
| **Action Button** | `<Button>` | 保存・実行・キャンセル・再試行など主たる操作 |
| **Icon-only Button** | `<Button iconOnly>` | 閉じる ×・削除・表示切替など。**aria-label 必須** |
| **Toggle / Segmented chip** | `aria-pressed` 付きピル群 | フィルタ・モード・対象切替(下記 §6) |
| **Nav item** | リンク/ボタン(Sidebar) | ナビゲーション。本 spec の対象外(ナビ規約に従う) |

---

## 2. サイズ(size)

`<Button>` の `size` で固定する。**生の高さ override(`min-h-10` 等)は使わない。**

| size | desktop 実寸 | 左右 padding | 使う場面 |
|---|---|---|---|
| `sm` | 32px | 12px | ページヘッダー、一覧行、局所ツール、ページング、一括選択、確認ダイアログ |
| `md` | 36px | 16px | 既定。カード内の単発操作・選択切替 |
| `lg` | 40px | 20px | 主フォームの保存・生成・実行と、同じバーのキャンセル・入力クリア |

- **640px 未満または `pointer: coarse` は全 size で高さ・最小幅 44px、左右 padding 12px**。desktop の icon-only は 36×36px、mobile は 44×44px。`iconOnly` と `aria-label` を必ず指定する。
- **入力横・認証フォーム・compact ヘッダーは `touchTarget` で全 viewport 44px 高**にする。入力自身も実寸 44px に揃える。`h-11` は root=14px で 38.5px のため使わない。
- 同じアクションバーは size を揃える。位置を指定する `w-full` / `sm:w-auto` / `flex-*` は可。ページ側の高さ・padding・文字サイズ・色・border override は禁止する。
- 日本語ラベルは全 size で **14px / line-height 20px / weight 500**、アイコンは **16px**、ラベルとの gap は **8px**。角丸は **6px**、border は全 variant **1px**（塗り・ghost は transparent）。root font-size に依存しない。
- ボタン間隔は **8px 以上**。通常の `gap-2` は root=14px で 7px のため、アクション群は `gap-[8px]` を使用する。
- グラフの排他選択バーは `data-button-layout="segmented"` で左右 padding 8px、操作高さ 40/44px を維持し、375px でもラベルを切らない。
- 並べ替え列頭は Action Button の対象外とし、専用 `SortHeader`（§6.1）を使用する。
- 説明文を含む選択カードは `data-button-layout="choice"` で最小 64px + 内容に応じた自動高さ。menu item / disclosure / field-icon / segmented は共通 CSS の named layout に限る。

---

## 3. スタイル(variant)

`<Button>` の `variant`(`buttonVariants` cva)で固定する。

| variant | 見た目 | 意味 / 使う場面 |
|---|---|---|
| `primary`(既定) | 塗り(bg-primary) | 画面の主 CTA(保存・実行・送信)。**操作領域ごとに原則 1 つ** |
| `secondary` | 枠線 + bg-card | 並列の副アクション(接続テスト、再読込、非破壊キャンセル、再試行) |
| `ghost` | 透明 + hover 背景 | 低強度の補助(リセット、選択解除、文脈内の削除トリガ) |
| `danger` | 塗り(bg-danger) | 破壊的確定(削除確定)。**確認ダイアログの確定ボタン**等 |

- 外観の正本は [`button.css`](../frontend/src/components/ui/button.css)。`buttonVariants()` を使うリンクにも同じ規約が適用される。
- **primary**: light `#1a73c1` / dark `#286abd` + 白文字。**danger**: light `#b91c1c` / dark `#bd3844` + 白文字。
- **secondary**: card 背景 + foreground 文字、light border `#8893a3` / dark border `#5d6878`。補助的な区切り線より強い境界でクリック可能な領域を示す。
- **ghost**: 透明背景 + foreground 文字。削除トリガ・メニュー項目には `tone="danger"` を使う。secondary + danger tone は赤い枠線、ghost + danger tone は赤文字で示し、確定時のみ danger の塗りにする。
- touch device では hover を適用せず、タップ後に hover 色を残さない（`@media (hover: hover)`）。
- **hover**: 塗りは黒 12% 混合、secondary/ghost は foreground 7% 混合。**active**: 塗りは黒 24%、secondary/ghost は foreground 12%。レイアウト移動・拡大縮小はしない。
- **focus-visible**: `--ring` 色の **2px outline + 2px offset**。`prefers-reduced-motion` で transition を停止し、forced-colors ではシステム色の境界を保つ。
- **disabled/loading**: ネイティブ disabled + `--disabled-bg` / `--disabled`、opacity=1。hover/active で有効色へ戻さない。loading は `aria-busy` と StableLoadingIcon 1 個を表示し、二重送信を防ぐ。
- **選択**: `aria-pressed="true"` は primary の細い枠線・薄い背景・内側線。主 CTA の塗りと区別し、色以外に aria 状態やチェックアイコンで伝える。
- **リンク**: navigation は `<a>` / Router Link の意味を保ち `buttonVariants()` で外観だけ共有する。無効なリンクは href を外し、`aria-disabled` と handler の guard を付ける。

---

## 4. 配置(placement)

- **主アクションバー**は、フォーム/カードの**末尾**に `border-t border-border pt-4` で区切って置く。`flex flex-wrap items-center gap-[8px]`。
- **並び順**: primary → secondary → ghost(左から重要度順)。結果表示(`FormStatus`)はバー内の末尾に置く。
- **ページレベル操作**はローカル [`<PageHeader>` / `<PageActionBar>`](../frontend/src/components/PageHeader.tsx) に集約する。独立したページ上部アクション行を追加しない。
- **破壊的アクションは通常アクションから空間的に分離**(`destructive-nav-separation` / `destructive-emphasis`)。
- レスポンシブ: ページヘッダー操作は `lg` 未満で compact 表示にし、タイトル/説明とボタンが横方向に押し合わないよう縦積みにする。`whitespace-nowrap` でラベル折返しを防ぐ。

---

## 4.1 ボタン押下後のスクロール / フォーカス

- **ナビゲーション以外のボタン押下では、ページ全体を自動で先頭へ戻さない。** ローカルな実行・保存・更新・コピーは現在のスクロール位置と作業文脈を保持する。
- 実行結果・エラー・処理中表示は、影響を受ける最小領域に置く。結果/エラーが現在の表示範囲外に出る場合のみ `scrollIntoView({ block: "nearest", inline: "nearest" })` で最小誘導する。
- ユーザーが処理中に手動スクロールした場合、その operation 完了時の自動誘導は行わない。ユーザーの移動を優先する。
- 既存結果を再実行で一時的に消す画面は、共通 `ActionResultRegion` で直前結果領域の高さを保持し、ページ高さの急縮小による `scrollTop` クランプを防ぐ。
- `prefers-reduced-motion: reduce` ではスクロール挙動を `auto` にする。通常時の誘導は必要最小限の smooth scroll を許容する。
- URL hash、ブラウザ戻る/進む、サイドナビなど明示的なナビゲーションは、`App` の scroll restoration / hash target ルールを正とし、この規約の対象外とする。

---

## 5. ページヘッダー操作

ページレベル操作は `PageAction` descriptor で宣言し、`PageActionBar` に表示順・variant・モバイル縮約を委ねる。

```tsx
<PageHeader
  title={t("nav.tables")}
  actions={[
    { id: "create", kind: "primary", label: t("table.create"), icon: Code2, onClick: startCreate },
    { id: "import", kind: "secondary", label: t("table.import"), icon: Upload, onClick: startImport },
    { id: "refresh", kind: "utility", label: t("common.action.refresh"), icon: RefreshCw, onClick: load, loading },
    { id: "schema-refresh", kind: "utility", label: t("common.action.schemaRefresh"), icon: RefreshCw, onClick: refreshSchema },
  ]}
/>
```

- 固定順序は **primary → secondary → utility(現在表示の再取得 → 外部データ/DB 構造同期) → danger**。配列順に依存せず `kind` で整列し、同一 `kind` 内の宣言順を保つ。
- `primary` はページ全体で最大 1 件。`primary/secondary` は作業開始グループ、`utility` はページツールグループ、`danger` は危険操作グループとして、グループ境界に軽い区切りと余白を置く。
- 共通文言は `common.action.refresh`=`表示を更新`、`common.action.schemaRefresh`=`DB 構造を再取得` を使用する。前者は現在表示の GET、後者は Schema Refresh Job の開始に限定する。
- 非同期操作は `loading` を渡し、処理中の再送信を防止する。成功 Toast と回復可能なエラー表示は [frontend-messaging-spec.md](./frontend-messaging-spec.md) に従い handler 側で行う。
- `lg` 未満で操作が 2 件以上なら、先頭の最高優先度操作だけを表示し、残りを `その他の操作` メニューへ収める。`DB 構造を再取得` は `lg+` では工具グループ最右、`lg` 未満では overflow 内へ入れる。メニューは `aria-expanded` / `aria-controls` / `role="menu"`、Esc、矢印/Home/End、フォーカス復帰を満たす。
- compact のページ操作は 44px、`lg+` は `size="sm"`。ラベルと既存 `data-testid` は可能な限り維持する。
- ヘッダーには現在の判断を変える状態・リスク・バックグラウンド進捗だけを置く。件数は一覧/タブ/操作パネルへ、DB 構造の最終取得時刻は同期操作の近くへ置く。

---

## 5.1 オブジェクト操作（一覧行 / 詳細）

一覧 + 詳細の管理画面では、同じ対象オブジェクトの操作を `EntityAction` descriptor で 1 回だけ定義し、
表示場所ごとに `RowActionMenu` / `ObjectActionBar` へ渡す。左の一覧と右の詳細で別々にボタン列を組まない。

- **ページ操作**: 新規作成・表示更新など対象を選ばない操作は `PageHeader` に置く。
- **一覧行**: 行内は常時表示の `RowActionMenu` 1 個だけにする。複数の文字ボタンを横並びにしない。
- **行選択**: 一覧/詳細の単一選択画面では、行の非アクション領域クリックで対象を選択し、右側詳細または編集対象を更新する。`詳細` / `編集` のような純粋な選択導線は `RowActionMenu` に入れない。
- **詳細**: `ObjectActionBar` は最大 2 個の非破壊・高頻度操作を `secondary` で表示し、残りを `その他の操作` メニューへ入れる。
- **危険操作**: 無効化・アーカイブ・削除・DROP は一覧行/詳細では menu item として扱い、確定は `ConfirmDialog` または専用確認ダイアログの `danger` ボタンで行う。
- **危険操作の確認面**: 確認ダイアログ/確認語入力領域は中立背景(`bg-card` / `bg-background`)に統一し、danger は左アクセント・文言色・状態 badge・確定ボタンへ限定する。広い `bg-danger-bg` 面でヘッダー/本文/対象欄を塗らない。
- **一括操作**: 複数選択を導入する場合は batch action bar を使い、batch mode 中は行内 `RowActionMenu` を disabled または非表示にする。
- **アクセシビリティ**: menu trigger は `aria-haspopup="menu"` / `aria-expanded` / `aria-controls`、menu は `role="menu"`、item は `role="menuitem"`。Esc で閉じ、ArrowUp/Down/Home/End で移動し、Esc 後は trigger にフォーカスを戻す。
- **配置**: 行内 trigger は右寄せの icon-only ghost、詳細 action bar はヘッダー右側で `secondary` + overflow。danger item は menu 内で区切り線を置く。menu surface は scroll container 内へ absolute 配置せず、viewport 基準の fixed/portal で表示する。
- **menu の表示方向**: 方向判定は trigger の最近の実スクロール祖先を優先し、該当祖先がない場合は viewport を使う。下方向の空きが足りない場合は上方向へ反転する。上下どちらにも十分な空きがない場合は、空きが大きい側を選び `max-height` + 内部スクロールにする。表の件数が少なく container が実スクロール状態でない場合は、menu を container に閉じ込めず viewport 内で表示する。

---

## 5.2 コンテンツ内の局所ツール操作

コードブロック、プレビュー、結果、局所テーブルなど、特定コンテンツだけに作用する操作は
[`<ContentActionBar>`](../frontend/src/components/ContentActionBar.tsx) に集約する。左側はタイトル・説明・状態などの情報、右側は操作ボタンにする。

- **配置**: 操作はコンテンツ面の右上に置く。左側は本文の読み始め、タイトル、説明、状態表示に残す。
- **対象**: コピー、ダウンロード、SQL 出力、局所プレビュー実行など、直下または同一カード内のコンテンツだけに作用する操作。
- **見た目**: ボタンは `<Button variant="secondary" size="sm">` を既定にする。低頻度の補助でも ghost に落とさず、同じ局所ツール群では variant と size を揃える。
- **工程を進める操作**: 対象情報の取得、SQL 生成、AI 抽出など、入力を確定して次工程へ進む操作は局所ツールではなく主アクションとして `size="lg"` にする。`ContentActionBar` で説明と操作を並べる場合も同様。同じバーの再試行ボタンも `lg` に揃える。コメント/アノテーション管理、オントロジー情報取得、ビューの JOIN/WHERE 抽出、生成データの対象テーブル取得へ共通適用する。
- **構造**: `ContentActionBar` は `aria-label` 必須。複雑な左側状態は `leading`、単純な情報は `title` / `description` / `meta` を使う。
- **レスポンシブ**: 375px では折り返し可。ただし操作はコンテンツの上に残し、本文やコードの左起点を押し下げすぎない。ページ横スクロールを出さない。
- **境界**: ページ全体・選択オブジェクト全体に作用する操作は `PageHeader` / `ObjectActionBar` に置き、`ContentActionBar` へ混在させない。

## 5.3 一括選択バー

複数選択リストで全選択を出す場合は、必ず全解除も同じスコープに並べて表示し、
[`<BulkSelectionActions>`](../frontend/src/components/BulkSelectionActions.tsx) を使う。

- **配置**: リスト直上または group header 内の leading side(日本語 UI では左側)に置く。group header ではタイトル・件数の下に左揃えで配置し、`ml-auto` / `justify-end` で右寄せしない。対象スコープ(全件 / 表示中 / schema / 権限 group)が変わる位置へ混在させない。
- **業務操作との分離**: 全選択・選択解除は左側の選択範囲コントロールとしてまとめ、選択項目へ適用する追加・保存・削除・実行などの batch action は右側へ分離する。
- **variant**: 全選択は `secondary`、全解除は `ghost`。どちらも `size="sm"` を既定とする。
- **disabled**: 対象がすべて選択済みなら全選択を disabled、対象の選択が 0 件なら全解除を disabled。読取専用・処理中は両方 disabled。
- **aria-label**: group 単位の同名ボタンは `"{name} をすべて選択"` / `"{name} の選択をすべて解除"` のように scope 名を含める。
- **文言**: 全範囲は `すべて選択` / `選択をすべて解除`、表示中の範囲は `表示中をすべて選択` / `表示中の選択をすべて解除` とし、`全選択` / `全解除` の短縮形は使わない。
- **レスポンシブ**: `flex-wrap` で折り返し、375px でも横スクロールを出さない。ボタン文言は `whitespace-nowrap` を保ち、隣接テキストと重ねない。

---

## 6. Toggle / Segmented chip

フィルタ・モード・対象切替は `<Button variant="secondary" aria-pressed={selected}>` を使う。
同じグループは `role="group"` + `aria-label` + `gap-[8px]` でまとめる。説明を含む選択カードは `data-button-layout="choice"` を指定する。

タブ (`role="tab"`)、combobox、listbox option、情報一覧の選択行、Sidebar navigation はアクションボタンとは構造が異なるため、各共通部品のレイアウトと ARIA を維持する。これらの生 `<button>` は明示的な適用除外であり、保存・コピー・開閉・削除を独自 CSS で実装する例外ではない。

---

## 6.1 並べ替え列頭

- 全画面の並べ替え列頭は [`SortHeader`](../frontend/src/components/SortHeader.tsx) と専用 CSS を正本とする。通常アクションの `Button` / `buttonVariants` / 選択トグル外観を使用しない。
- 文字と並べ替えアイコンだけを表示し、通常・選択・hover・active・focus 状態でも外枠・角丸・影・塗り背景を付けない。選択中の方向は矢印とアクセシブルな状態で示す。
- 文字組みは **0.75rem / line-height 1rem / weight 600**、gap 4px、padding 0。操作領域は desktop 32px / touch 44px、表ヘッダー高は35px / 47pxを維持し、一覧の可視行数を変えない。
- Enter / Space / Tab とネイティブの disabled を維持するため、内部は意味上の `<button type="button">` を使用する。これは列頭専用の構造コントロールとしての明示的な適用除外である。
- キーボードフォーカスは列名の2px下線（offset 3px）で示す。ボタン式の外枠を復活させない。

---

## 7. 命名規則(naming)

- **コンポーネント**: アクションは常に `<Button>`。同一バーを再利用する場合は `XxxActionBar` のような呼称で部品化する。
- **ラベル文言キー**: `<domain>.<feature>.actions.<verb>`(例: `settings.model.actions.save` 相当)。汎用語は `common.*`(`common.confirm` / `common.cancel` / `common.delete` / `common.dismiss` / `common.undo`)。
- **SQL 生成の主操作**: 自然言語から SQL を生成し、安全確認後に実行まで進むボタンは `SQL を生成して実行` とする。ボタンを参照する案内文も同じ名称に揃え、SQL 入力を直接実行する `SQL 実行` と区別する。
- **初期化・クリア操作**: 画面の対象を含めて命名する。SQL 生成は `新しいクエリを開始`、SQL 入力は `SQL をクリア`、ファイル取込は `取込条件をクリア`、合成データ生成は `生成条件をクリア`、データ表示は `表示結果をクリア`、Profile 保存の確認解除は `実行確認をクリア` とする。新規クエリの破棄確認は、クエリ・結果の消去と生成条件・実行オプションの初期化を説明し、確定ボタンも起点と同じ文言にする。
- **aria-label**:
  - グループ内で同一ラベルが重複する操作は `「${セクション名}: ${ラベル}」`(例: `モデル設定: 保存`)。
  - 連番要素の操作は末尾に番号(例: `モデルを削除 1`)。
  - **Icon-only は aria-label 必須**(例: 閉じる=`common.dismiss`)。
- **イベントハンドラ**: コンポーネント内は `handle<Verb>`(`handleSave`)、props 経由は `on<Verb>`(`onSave` / `onRetry` / `onRemove`)。

---

## 8. アイコン / ローディング

- Lucide を使用。ラベル付きは**アイコンを左**に置き(`<Button>` 既定の 8px gap)、`aria-hidden` を付ける。
- サイズ: 全 size で 16px。`[&>svg]:shrink-0` 済み。
- 非同期処理は `loading` prop を使う(安定した `StableLoadingIcon` に置換し自動 disable、`loading-buttons` / `submit-feedback`)。
  - **ボタン内の loading icon は 180 度対称の `StableLoadingIcon` に統一する。** `Loader2`、共有 `Spinner`、単一の欠けた円弧など、回転角で見た目の重心が上下に動く icon をボタン内で直接使わない。
  - **同じ operation の動的 spinner は 1 つだけ**にする。主ボタンが `loading` の場合、同じ処理を説明する
    `ProcessingIndicator` / `TimedLoadingState` は `activityIcon="none"` にして、静的ラベル・経過時間・slow hint
    のみを表示する。
  - 結果領域の `TimedLoadingState placement="result"` は既定で iconless。主操作ボタンが唯一の動的 feedback を担う。
  - 更新・同期などの busy 表示も `<Button loading>` を使い、ボタン内アイコンへ個別に `animate-spin` を付けない。
- emoji をアイコンに使わない(`no-emoji-icons`)。

---

## 9. アクセシビリティ チェックリスト(必須)

- [ ] Icon-only に `aria-label`。
- [ ] hit area: desktop 32/36/40px、icon-only 36px、mobile/coarse pointer は 44px。
- [ ] `cursor-pointer`(`<Button>` 済み)/ `focus-visible` リング(globals.css 済み)。
- [ ] disabled は `disabled` 属性 + semantic disabled 色(`<Button>` 済み)。見た目だけの無効化をしない。
- [ ] トグルは `aria-pressed`、色のみで状態を伝えない。
- [ ] 破壊的操作は danger + 確認(`useConfirm`)、主アクションと分離。

---

## 10. 使用例(正)

```tsx
// 主アクションバー（設定）
<div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
  <Button size="lg" loading={save.isPending} onClick={handleSave}>
    <Save size={16} aria-hidden />
    {t("settings.model.actions.save")}
  </Button>
  <Button size="lg" variant="secondary" onClick={handleTest}>
    {t("settings.model.actions.test")}
  </Button>
  <Button size="lg" variant="ghost" onClick={handleReset}>
    {t("settings.model.actions.reset")}
  </Button>
  <FormStatus tone="success" message={saved ? t("...saved") : undefined} />
</div>

// 破壊的操作（行内トリガ → 確認ダイアログ）
<Button variant="ghost" size="sm" aria-label={`${section}: ${t("common.delete")}`} onClick={async () => {
  if (await confirm({ title, tone: "danger", confirmLabel: t("common.delete") })) remove();
}}>
  <Trash2 size={14} aria-hidden />
</Button>
```

---

## 11. 過去の移行記録（現在の規約は §2–6 / §12）

- ✅ `<Button>` に React 19 流の `ref` 対応を追加(ConfirmDialog のフォーカス制御で利用)。
- ✅ 手書き secondary ボタン(`StateViews` 再試行 / `DashboardHeader` 更新)を `<Button variant="secondary">` へ統一。
- ✅ 主アクションバーの高さ表現を `min-h-10` override → `size="lg"` に統一(設定各画面)。
- ✅ 共通 `<ToggleChip>` を抽出し、FileList フィルタ・検索モード切替を移行(`role="group"` 付与)。
- ✅ ページ上部操作を `<PageHeader>` / `<PageActionBar>` へ統一し、レスポンシブな `その他の操作` メニューへ対応。


## 12. 根拠・適用範囲・検証

- [Carbon Button usage](https://carbondesignsystem.com/components/button/usage/): 操作の重要度に合わせた variant と、文脈に合わせた size、危険操作の段階的な強調を採用。
- [Carbon Button style](https://carbondesignsystem.com/components/button/style/): 色・寸法・状態を部品側の token へ集約する考え方を採用。具体寸法・角丸は本製品の業務 UI 向けに選定。
- [WCAG 2.2 Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum/): AA の 24 CSS px（例外あり）を下限として参照。本製品はモバイルを 44px に設定する。
- `ui-ux-pro-max`: touch target 44px、間隔 8px、キーボード focus、loading feedback と reduced motion。

適用: PageHeader / ObjectActions / FormActionBar / ContentActionBar / BulkSelectionActions、確認ダイアログ、入力横、ログイン、各設定、クエリ・生成 SQL・結果、データ/テーブル/ビュー、Profile、学習・履歴・評価、権限、グラフ操作、ページング、エラー再試行、通知内操作。共有パッケージの状態 hook/store はそのまま使い、アクションを含む Pagination / ErrorState / Toaster の表示はアプリ共通 Button を使う。

`tests/e2e/button-standards.spec.ts` は実 React 部品を使って light/dark × desktop/mobile-375 の寸法・状態・操作・focus・danger 確認・overflow・ページング・再試行を検証する。各機能の既存 Playwright spec はユーザーフローの回帰を担当する。
