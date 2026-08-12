# フロントエンド ボタン設計 仕様書（Button Spec）

> **このファイルはシステム全体のボタン(大きさ・命名・スタイル・配置)を統一する正本(spec)です。**
> ボタンを新規実装・改修するときは**必ず本仕様に従う**こと。類似機能のボタンは、サイズ・variant・配置・命名を揃える。
> 逸脱が必要な場合は AGENTS.md §コーディング規約 8/10 に従い理由を添えて確認する。
>
> 対象スタック: Vite + React + TypeScript + Tailwind v4 + shadcn/ui(`class-variance-authority`)。
> 準拠: `ui-ux-pro-max` §2 Touch & Interaction / §4 Style Selection(`primary-action` / `state-clarity`)/ §1 Accessibility / §8 Forms。
> 関連: 操作結果の通知は [frontend-messaging-spec.md](./frontend-messaging-spec.md)(Toast / FormStatus / ConfirmDialog)を使う。

---

## 0. 原則

1. **アクションボタンは必ず共通 [`<Button>`](../frontend/src/components/ui/button.tsx) を使う。** 同等スタイルを生 `<button>` で再実装しない。
2. **1 画面の主ボタン(primary)は 1 つ**(`primary-action`)。他は secondary / ghost に従属させる。
3. **類似機能は同じ size・variant・配置・文言キー規則**にする。
4. **文言は i18n 経由**、アイコンは Lucide(emoji 禁止)。
5. **破壊的操作は danger + 確認ダイアログ**(`useConfirm`)。主アクションから視覚的に分離する。

---

## 1. ボタンの種別(4 分類)

| 種別 | 実体 | 用途 |
|---|---|---|
| **Action Button** | `<Button>` | 保存・実行・キャンセル・再試行など主たる操作 |
| **Icon-only Button** | `<Button>` か aria-label 付き生 `<button>` | 閉じる ×・削除・表示切替など。**aria-label 必須** |
| **Toggle / Segmented chip** | `aria-pressed` 付きピル群 | フィルタ・モード・対象切替(下記 §6) |
| **Nav item** | リンク/ボタン(Sidebar) | ナビゲーション。本 spec の対象外(ナビ規約に従う) |

---

## 2. サイズ(size)

`<Button>` の `size` で固定する。**生の高さ override(`min-h-10` 等)は使わない。**

| size | 高さ | アイコン | 使う場面 |
|---|---|---|---|
| `sm` | h-8 (32px) | 14 | 密度の高い文脈: ツールバー、テーブル行内、一括選択バー、**確認ダイアログのフッター**、ヘッダーのユーティリティ |
| `md` | h-9 (36px) | 15 | **既定**。本文中・カード内の単発アクション |
| `lg` | h-10 (40px) | 15–16 | **主アクションバー**(設定の保存/テスト/リセット、検索実行)・ページ主 CTA |

- **同一アクションバー内のボタンは size を揃える**(例: 保存=lg なら隣のテスト/リセットも lg)。
- フォーム入力と同じ行・同じ高さに並べる送信ボタンは、**入力高さに合わせる**ことを許可する(例: ログインは入力が `h-11`=44px なので送信も `h-11`)。この場合のみ高さ override を可とし、理由をコメントに残す。
- タッチ優先画面・モバイル主導線では 44px(`h-11`)を推奨(`touch-target-size`)。デスクトップ管理画面の通常操作は 32–40px で可。

---

## 3. スタイル(variant)

`<Button>` の `variant`(`buttonVariants` cva)で固定する。

| variant | 見た目 | 意味 / 使う場面 |
|---|---|---|
| `primary`(既定) | 塗り(bg-primary) | 画面の主 CTA(保存・実行・送信)。**1 画面 1 つ** |
| `secondary` | 枠線 + bg-card | 並列の副アクション(接続テスト、再読込、非破壊キャンセル、再試行) |
| `ghost` | 透明 + hover 背景 | 低強度の補助(リセット、選択解除、文脈内の削除トリガ) |
| `danger` | 塗り(bg-danger) | 破壊的確定(削除確定)。**確認ダイアログの確定ボタン**等 |

- hover / disabled / focus-visible は `buttonVariants` 既定に従う(`state-clarity`)。disabled は `opacity-50` + 操作不可。
- 破壊的操作は **danger variant + 確認ダイアログ**。一覧の行内削除など「トリガ」自体は ghost(アイコン)で可だが、**確定は必ず ConfirmDialog の danger ボタン**。

---

## 4. 配置(placement)

- **主アクションバー**は、フォーム/カードの**末尾**に `border-t border-border pt-4` で区切って置く。`flex flex-wrap items-center gap-2`。
- **並び順**: primary → secondary → ghost(左から重要度順)。結果表示(`FormStatus`)はバー内の末尾に置く。
- **ページレベル操作**はローカル [`<PageHeader>` / `<PageActionBar>`](../frontend/src/components/PageHeader.tsx) に集約する。独立したページ上部アクション行を追加しない。
- **破壊的アクションは通常アクションから空間的に分離**(`destructive-nav-separation` / `destructive-emphasis`)。
- レスポンシブ: ページヘッダー操作は `lg` 未満で compact 表示にし、タイトル/説明とボタンが横方向に押し合わないよう縦積みにする。`whitespace-nowrap` でラベル折返しを防ぐ。

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
- **構造**: `ContentActionBar` は `aria-label` 必須。複雑な左側状態は `leading`、単純な情報は `title` / `description` / `meta` を使う。
- **レスポンシブ**: 375px では折り返し可。ただし操作はコンテンツの上に残し、本文やコードの左起点を押し下げすぎない。ページ横スクロールを出さない。
- **境界**: ページ全体・選択オブジェクト全体に作用する操作は `PageHeader` / `ObjectActionBar` に置き、`ContentActionBar` へ混在させない。

---

## 6. Toggle / Segmented chip

フィルタ・モード・対象切替の連動トグルは **共通 [`<ToggleChip>`](../frontend/src/components/ui/toggle-chip.tsx) を使う**(FileList のステータス絞り込み、検索のモード切替)。

```tsx
<div className="flex flex-wrap items-center gap-1" role="group" aria-label={t("…")}>
  {options.map((opt) => (
    <ToggleChip key={opt} selected={value === opt} onClick={() => setValue(opt)}>
      {t(LABEL[opt])}
    </ToggleChip>
  ))}
</div>
```

- 見た目(ピル): `rounded-full px-3 py-1 text-xs`、選択中 `bg-primary text-primary-foreground`、非選択 `border-border bg-card text-muted hover:bg-background`。
- グループは `role="group"` + `aria-label` で囲う。状態は色だけでなく `aria-pressed`(`<ToggleChip>` が付与)で伝える(`color-not-only`)。
- **Boxed segmented control**(コンテナ枠 + 内部分割)は、より目立つ排他選択に使う別パターン。`grid ... rounded-md border bg-background p-1` のコンテナに、各項目を `rounded`(full でない)+ `aria-pressed` で並べる。プロミネンスが異なる場合のみ使い、通常のトグルは `<ToggleChip>` を優先する。

---

## 7. 命名規則(naming)

- **コンポーネント**: アクションは常に `<Button>`。同一バーを再利用する場合は `XxxActionBar` のような呼称で部品化する。
- **ラベル文言キー**: `<domain>.<feature>.actions.<verb>`(例: `settings.model.actions.save` 相当)。汎用語は `common.*`(`common.confirm` / `common.cancel` / `common.delete` / `common.dismiss` / `common.undo`)。
- **aria-label**:
  - グループ内で同一ラベルが重複する操作は `「${セクション名}: ${ラベル}」`(例: `モデル設定: 保存`)。
  - 連番要素の操作は末尾に番号(例: `モデルを削除 1`)。
  - **Icon-only は aria-label 必須**(例: 閉じる=`common.dismiss`)。
- **イベントハンドラ**: コンポーネント内は `handle<Verb>`(`handleSave`)、props 経由は `on<Verb>`(`onSave` / `onRetry` / `onRemove`)。

---

## 8. アイコン / ローディング

- Lucide を使用。ラベル付きは**アイコンを左**に置き(`<Button>` 既定の `gap-1.5`)、`aria-hidden` を付ける。
- サイズ: §2 の対応(sm/md=14–15、lg=15–16)。`[&>svg]:shrink-0` 済み。
- 非同期処理は `loading` prop を使う(Loader2 スピナーに置換し自動 disable、`loading-buttons` / `submit-feedback`)。
  - 例外的に独自アイコンを回したい場合(更新ボタンの `RefreshCw` 回転など)は `disabled` + `className={cn(busy && "animate-spin")}` を使う。
- emoji をアイコンに使わない(`no-emoji-icons`)。

---

## 9. アクセシビリティ チェックリスト(必須)

- [ ] Icon-only に `aria-label`。
- [ ] hit area: 通常 ≥32px、アイコン専用は最低 `h-9 w-9`(36px)、タッチ主導線は 44px 目標。
- [ ] `cursor-pointer`(`<Button>` 済み)/ `focus-visible` リング(globals.css 済み)。
- [ ] disabled は `disabled` 属性 + `opacity-50`(`<Button>` 済み)。見た目だけの無効化をしない。
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

## 11. 移行状況

- ✅ `<Button>` に React 19 流の `ref` 対応を追加(ConfirmDialog のフォーカス制御で利用)。
- ✅ 手書き secondary ボタン(`StateViews` 再試行 / `DashboardHeader` 更新)を `<Button variant="secondary">` へ統一。
- ✅ 主アクションバーの高さ表現を `min-h-10` override → `size="lg"` に統一(設定各画面)。
- ✅ 共通 `<ToggleChip>` を抽出し、FileList フィルタ・検索モード切替を移行(`role="group"` 付与)。
- ✅ ページ上部操作を `<PageHeader>` / `<PageActionBar>` へ統一し、レスポンシブな `その他の操作` メニューへ対応。
