# ボタンの役割・配置・命名（Button）

> 3製品共通の正本。ボタンを新しく作る・直すときは本規約に従う。類似機能のボタンは、サイズ・variant・配置・命名をそろえる。
> 見た目（色・高さ・アイコン枠・loading・disabled・フォーカス）は [デザインシステム README §4 Button](../design-system/README.md) が正本。
> 操作結果の通知は [messaging.md](./messaging.md)（Toast / FormStatus / ConfirmDialog）を使う。
> 製品ごとの役割の割り当て・文言・例外は、各製品の `docs/frontend-button-spec.md` に書く。

---

## 0. 原則

1. **アクションボタンは必ず共通 `Button`（`@engchina/production-ready-ui`）を使う。** 同じ見た目を生 `<button>` で作り直さない。
2. **同時に操作する領域の主ボタン（primary）は原則 1 つ**（`primary-action`）。他は secondary / ghost に従わせる。
3. **類似機能は同じ size・variant・配置・文言 key の規則**にする。
4. **文言は i18n 経由**、アイコンは Lucide（emoji 禁止）。
5. **破壊的操作は danger + 確認ダイアログ**（`useConfirm`）。主アクションから視覚的に分離する。

---

## 1. ボタンの種別（4 分類）

| 種別 | 実体 | 用途 |
|---|---|---|
| **Action Button** | `<Button>` | 保存・実行・キャンセル・再試行など主たる操作 |
| **Icon-only Button** | `<Button iconOnly>` | 閉じる ×・削除・表示切替など。**aria-label 必須** |
| **Toggle / Segmented chip** | `aria-pressed` 付きの選択 | フィルタ・モード・対象の切替（§6） |
| **Nav item** | リンク / ボタン（Sidebar） | ナビゲーション。本規約の対象外 |

---

## 2. 見た目とサイズの正本

色・高さ・アイコン枠・loading・disabled・フォーカスは [デザインシステム README §4 Button](../design-system/README.md) と共通 `Button` が管理する。役割 × 配置の選択表も同節を正とする。製品では寸法・色・角丸を上書きせず、共有ボタンを作り直さない。

## 3. 役割の割り当て（共通の考え方）

- 工程を進める操作（対象の取得、生成、保存、実行など、入力を確定して次へ進む操作）は主操作（`primary/lg`）。
- ページ全体の表示更新・外部データの再取得は `PageHeader` の `utility`。通常表示は `secondary/md`。
- コピー、ダウンロード、状態の再確認、追加読込は局所ツール（`secondary/sm`）。追加読込は `ListPlus`、更新は `RefreshCw`。
- 入力欄に並ぶ取得 / 接続テストは入力と同じ 44px。`touchTarget` と `icon` を渡す。
- 同じ操作行は主操作・補助操作とも同じ size。非同期操作は必ず `icon` prop を使い、loading 中もラベルと幅を保つ。
- `danger` は実際の破壊的確定に使う。選択・未選択の変化で variant やアイコンを切り替えず、`disabled` だけを変える。

各製品の具体的な割り当て（どの画面のどの操作が主操作か）は、製品の `docs/frontend-button-spec.md` に書く。

## 4. 配置

工程 / フォームの主操作は入力内容の末尾に置く。補助操作を同じ高さで並べ、破壊的操作は離す。
ページ操作は共通 `PageHeader`、局所ツールは `ContentActionBar`、対象オブジェクトの操作は `ObjectActionBar` に置く。

---

## 4.1 ボタン押下後のスクロール / フォーカス

- **ナビゲーション以外のボタン押下では、ページ全体を自動で先頭へ戻さない。** ローカルな実行・保存・更新・コピーは、今のスクロール位置と作業文脈を保つ。
- 実行結果・エラー・処理中表示は、影響を受ける最小の領域に置く。結果 / エラーが表示範囲の外に出る場合だけ `scrollIntoView({ block: "nearest", inline: "nearest" })` で最小限誘導する。
- 処理中に利用者が手動でスクロールした場合、その処理の完了時に自動で誘導しない。利用者の移動を優先する。
- 既存の結果を再実行で一時的に消す画面は、`ActionResultRegion` で直前の結果領域の高さを保ち、ページ高さの急な縮小による `scrollTop` のクランプを防ぐ。
- `prefers-reduced-motion: reduce` ではスクロールを `auto` にする。通常時の誘導は必要最小限の smooth scroll を許す。
- URL hash、ブラウザの戻る / 進む、サイドナビなど明示的なナビゲーションは、各製品の App のスクロール復元 / hash target の規則を正とし、本節の対象外とする。

---

## 5. ページヘッダー操作

ページ単位の操作は `PageHeader` の `actions`（`PageAction` descriptor）で宣言し、表示順・variant・モバイルでの縮約は共有部品に任せる。

```tsx
<PageHeader
  title={t("nav.tables")}
  actions={[
    { id: "create", kind: "primary", label: t("table.create"), icon: Code2, onClick: startCreate },
    { id: "import", kind: "secondary", label: t("table.import"), icon: Upload, onClick: startImport },
    { id: "refresh", kind: "utility", label: t("common.action.refresh"), icon: RefreshCw, onClick: load, loading },
  ]}
/>
```

- 固定の並びは **danger → utility（今の表示の再取得 → 外部データの同期）→ secondary → primary**（右端が主操作）。配列順ではなく `kind` で並べ、同じ `kind` の中は宣言順を保つ。
- `utility`（表示更新など）は `secondary` と同じ枠付きの見た目で表示される。compact メニューの中は `ghost`。
- `primary` はページ全体で最大 1 件。`primary/secondary` は作業開始のグループ、`utility` はページツールのグループ、`danger` は危険操作のグループとし、グループの境界に軽い区切りと余白を置く。
- 共通文言は `common.action.refresh`＝`表示を更新` を使う（今の表示の GET に限る）。外部データや構造の同期を始める操作は、製品ごとに別の文言にする。
- 非同期操作は `loading` を渡し、処理中の再送信を防ぐ。成功の Toast と回復可能なエラーの表示は [messaging.md](./messaging.md) に従い handler 側で行う。
- `lg` 未満で操作が 2 件以上なら、最も優先度の高い操作だけを表示し、残りを `その他の操作` メニューへ入れる。メニューは `aria-expanded` / `aria-controls` / `role="menu"`、Esc、矢印 / Home / End、フォーカスの復帰を満たす。
- ヘッダーには今の判断を変える状態・リスク・バックグラウンドの進捗だけを置く。件数は一覧 / タブ / 操作パネルへ、同期の最終時刻は同期操作の近くへ置く。

---

## 5.1 オブジェクト操作（一覧行 / 詳細）

一覧 + 詳細の管理画面では、同じ対象オブジェクトの操作を `EntityAction` descriptor で 1 回だけ定義し、表示場所ごとに `RowActionMenu` / `ObjectActionBar` へ渡す。左の一覧と右の詳細で別々にボタン列を組まない。

- **ページ操作**：新規作成・表示更新など、対象を選ばない操作は `PageHeader` に置く。
- **一覧行**：行内は常に表示する `RowActionMenu` 1 個だけにする。複数の文字ボタンを横に並べない。
- **行選択**：一覧 / 詳細の単一選択の画面では、行の操作以外の領域のクリックで対象を選び、右側の詳細または編集対象を更新する。`詳細` / `編集` のような選択だけの導線は `RowActionMenu` に入れない。
- **詳細**：`ObjectActionBar` は最大 2 個の非破壊・高頻度の操作を `secondary` で表示し、残りを `その他の操作` メニューへ入れる。
- **危険操作**：無効化・アーカイブ・削除などは、一覧行 / 詳細ではメニュー項目として扱い、確定は `ConfirmDialog` または専用の確認ダイアログの `danger` ボタンで行う。
- **危険操作の確認面**：確認ダイアログ / 確認語の入力領域は中立の背景にする。danger は左のアクセント・文言の色・状態 badge・確定ボタンに限り、ヘッダー / 本文 / 対象欄を広い danger の背景で塗らない（[messaging.md §3.5](./messaging.md#35-confirmdialog)）。
- **一括操作**：複数選択を入れる場合は一括操作のバーを使い、一括モードの間は行内の `RowActionMenu` を disabled または非表示にする。
- **アクセシビリティ**：メニューの trigger は `aria-haspopup="menu"` / `aria-expanded` / `aria-controls`、メニューは `role="menu"`、項目は `role="menuitem"`。Esc で閉じ、ArrowUp/Down/Home/End で移動し、Esc の後は trigger にフォーカスを戻す。
- **配置**：行内の trigger は右寄せの icon-only ghost、詳細の操作バーはヘッダーの右側で `secondary` + overflow。danger の項目はメニュー内で区切り線を置く。メニューの面は scroll container の中に absolute で置かず、viewport 基準の fixed / portal で表示する。
- **メニューの表示方向**：方向は trigger に最も近い実スクロール祖先を優先して判定し、なければ viewport を使う。下の空きが足りなければ上へ反転する。上下どちらも足りなければ、空きが大きい側を選び `max-height` + 内部スクロールにする。件数が少なく container が実際にはスクロールしていない場合は、メニューを container に閉じ込めず viewport 内に表示する。

---

## 5.2 コンテンツ内の局所ツール操作

コードブロック、プレビュー、結果、局所のテーブルなど、特定のコンテンツだけに作用する操作は `ContentActionBar` にまとめる。左側はタイトル・説明・状態などの情報、右側は操作ボタンにする。

- **配置**：操作はコンテンツ面の右上に置く。左側は本文の読み始め、タイトル、説明、状態表示に残す。
- **対象**：コピー、ダウンロード、出力、局所プレビューの実行など、直下または同じカード内のコンテンツだけに作用する操作。
- **見た目**：`<Button variant="secondary" size="sm">` を既定にする。低頻度の補助でも ghost にせず、同じ局所ツール群では variant と size をそろえる。
- **工程を進める操作**：対象情報の取得、生成、AI 抽出など、入力を確定して次の工程へ進む操作は局所ツールではなく主アクションとして `size="lg"` にする。`ContentActionBar` で説明と操作を並べる場合も同じ。同じバーの再試行ボタンも `lg` にそろえる。
- **構造**：`ContentActionBar` は `aria-label` 必須。複雑な左側の状態は `leading`、単純な情報は `title` / `description` / `meta` を使う。
- **レスポンシブ**：375px では折り返してよい。ただし操作はコンテンツの上に残し、本文やコードの開始位置を押し下げすぎない。ページの横スクロールを出さない。
- **境界**：ページ全体・選択オブジェクト全体に作用する操作は `PageHeader` / `ObjectActionBar` に置き、`ContentActionBar` に混ぜない。

## 5.3 一括選択バー

複数選択のリストで全選択を出す場合は、必ず全解除も同じスコープに並べ、`BulkSelectionActions` を使う。

- **配置**：リストの直上またはグループ見出しの先頭側（日本語 UI では左側）に置く。グループ見出しではタイトル・件数の下に左揃えで置き、右寄せにしない。対象のスコープ（全件 / 表示中 / グループ）が変わる位置に混ぜない。
- **業務操作との分離**：全選択・選択解除は左側の選択範囲コントロールとしてまとめ、選択項目に適用する追加・保存・削除・実行などの一括操作は右側へ分ける。
- **variant**：全選択は `secondary`、全解除は `ghost`。どちらも `size="sm"` を既定とする。
- **disabled**：対象がすべて選択済みなら全選択を disabled、選択が 0 件なら全解除を disabled。読取専用・処理中は両方 disabled。
- **aria-label**：グループ単位の同名ボタンは `"{name} をすべて選択"` / `"{name} の選択をすべて解除"` のようにスコープ名を含める。
- **文言**：全範囲は `すべて選択` / `選択をすべて解除`、表示中の範囲は `表示中をすべて選択` / `表示中の選択をすべて解除` とし、`全選択` / `全解除` の短縮形は使わない。
- **レスポンシブ**：`flex-wrap` で折り返し、375px でも横スクロールを出さない。ボタンの文言は `whitespace-nowrap` を保ち、隣のテキストと重ねない。

---

## 6. Toggle / Segmented chip

絞り込み・モード・対象の切替は `aria-pressed` 付きの選択（`ToggleChip` または `<Button variant="secondary" aria-pressed={selected}>`）を使う。同じグループは `role="group"` + `aria-label` でまとめる。同じ対象の別の見方への切替は `Tabs` を使い、`ToggleChip` をタブの代わりにしない（デザインシステムの規約）。

タブ（`role="tab"`）、combobox、listbox の option、情報一覧の選択行、Sidebar のナビゲーションはアクションボタンと構造が違うため、各共通部品のレイアウトと ARIA を保つ。これらの生 `<button>` は明示的な適用除外であり、保存・コピー・開閉・削除を独自 CSS で作る例外ではない。

---

## 6.1 並べ替え列頭

- 並べ替えの列頭は共通の `SortHeader`（`DataTable` の sort）を正とする。通常のアクションの `Button` や選択トグルの見た目を使わない。
- 文字と並べ替えアイコンだけを表示し、どの状態でも外枠・角丸・影・塗りの背景を付けない。選択中の方向は矢印とアクセシブルな状態で示す。
- 操作領域は desktop 32px / touch 44px を保ち、一覧の見える行数を変えない。
- Enter / Space / Tab とネイティブの disabled を保つため、内部は意味上の `<button type="button">` を使う。これは列頭専用の構造コントロールとしての明示的な適用除外である。
- キーボードフォーカスは列名の下線で示す。ボタン風の外枠を付けない。

---

## 7. 命名規則（naming）

- **コンポーネント**：アクションは常に `<Button>`。同じバーを再利用する場合は `XxxActionBar` のような名前で部品にする。
- **ラベルの文言 key**：`<domain>.<feature>.actions.<verb>`（例：`settings.model.actions.save` 相当）。汎用語は `common.*`（`common.confirm` / `common.cancel` / `common.delete` / `common.dismiss` / `common.undo`）。
- **初期化・クリアの操作**：handler が実際に変える状態に合わせ、空にする操作は `対象をクリア`、既定値へ戻す操作は `対象をリセット`、選択・絞り込みの解除は `対象を解除`、表示だけを消す操作は `対象表示を消去` とする。単独の `クリア` / `リセット` は使わない。共通 `ClearActionButton` の `label` は必須とし、ファイル用の既定ラベルを他の操作に流用しない。
- **ファイル選択の解除ボタン**：640px 未満ではファイル入力の下に置き、入力とボタンの間を 8px 以上空ける。名前を具体的にしても、選んだファイル名の表示幅を潰さない。desktop は同じ行に置く。
- **新しい作業を始める操作**：作業全体を初期化する操作は `新しい〇〇を開始` とする。破棄の確認では、何が消えて何が既定値に戻るかを説明し、確定ボタンも起点と同じ文言にする。
- **aria-label**：
  - グループ内で同じラベルが重なる操作は `「${セクション名}: ${ラベル}」`（例：`モデル設定: 保存`）。
  - 連番の要素の操作は末尾に番号（例：`モデルを削除 1`）。
  - **Icon-only は aria-label 必須**（例：閉じる＝`common.dismiss`）。
- **イベントハンドラ**：コンポーネント内は `handle<Verb>`（`handleSave`）、props 経由は `on<Verb>`（`onSave` / `onRetry` / `onRemove`）。

製品ごとの具体的なラベル（リセット操作の対象と範囲の表など）は、製品の `docs/frontend-button-spec.md` に書く。

---

## 8. アイコン / ローディング

- Lucide を使う。ラベル付きは**アイコンを左**に置き（共通 `Button` の `icon` prop）、`aria-hidden` を付ける。
- サイズは共通 `Button` の `icon` スロットが制御する。
- 非同期処理は `loading` prop を使う（共通 `Button` がアイコンをスピナーに置き換え、自動で disabled にする）。
  - **ボタン内の loading 表示は共通 `Button` に任せる。** `Loader2` や `Spinner` を子要素として描画しない。loading 中にラベルを「実行中…」などに差し替えない。
  - **同じ処理の動的なスピナーは 1 つだけ**にする。主ボタンが `loading` の場合、同じ処理を説明する `ProcessingIndicator` / `TimedLoadingState` は `activityIcon="none"` にして、静的なラベル・経過時間・slow hint だけを表示する。
  - 更新・同期などの busy 表示も `<Button loading>` を使い、ボタン内のアイコンに個別に `animate-spin` を付けない。
- emoji をアイコンに使わない（`no-emoji-icons`）。

---

## 9. アクセシビリティ チェックリスト（必須）

- [ ] Icon-only に `aria-label`。
- [ ] 操作領域：desktop 32/36/40px、icon-only 36px、mobile / coarse pointer は 44px。
- [ ] `cursor-pointer` / `focus-visible` のリング（共通 `Button` 済み）。
- [ ] disabled は `disabled` 属性 + disabled の意味の色（共通 `Button` 済み）。見た目だけの無効化をしない。
- [ ] トグルは `aria-pressed`、色だけで状態を伝えない。
- [ ] 破壊的操作は danger + 確認（`useConfirm`）、主アクションと分ける。

---

## 10. 使用例（正）

```tsx
// 主アクションバー（設定）
<div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
  <Button size="lg" icon={Save} loading={save.isPending} onClick={handleSave}>
    {t("settings.model.actions.save")}
  </Button>
  <Button size="lg" variant="secondary" icon={TestTube2} onClick={handleTest}>
    {t("settings.model.actions.test")}
  </Button>
  <FormStatus tone="success" message={saved ? t("...saved") : undefined} />
</div>

// 破壊的操作（行内の trigger → 確認ダイアログ）
<Button
  variant="ghost"
  size="sm"
  iconOnly
  icon={Trash2}
  aria-label={`${section}: ${t("common.delete")}`}
  onClick={async () => {
    if (await confirm({ title, tone: "danger", confirmLabel: t("common.delete") })) remove();
  }}
/>
```

---

## 12. 根拠と検証

- [Carbon Button usage](https://carbondesignsystem.com/components/button/usage/)：操作の重要度に合わせた variant、文脈に合わせた size、危険操作の段階的な強調。
- [Carbon Button style](https://carbondesignsystem.com/components/button/style/)：色・寸法・状態を部品側の token へ集める考え方。
- [WCAG 2.2 Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum/)：AA の 24 CSS px（例外あり）を下限として参照。3製品はモバイルを 44px にする。
- `ui-ux-pro-max`：touch target 44px、間隔 8px、キーボード focus、loading feedback と reduced motion。

各製品は、実際の部品を使って light / dark × desktop / mobile-375 の寸法・状態・操作・focus・danger の確認・overflow を Playwright で確認する（例：NL2SQL の `tests/e2e/button-standards.spec.ts`）。
