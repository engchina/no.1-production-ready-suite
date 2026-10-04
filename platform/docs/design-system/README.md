# Handoff: 基盤トークン再設計 + タイプスケール + 新コンポーネント

**対象リポジトリ:** `engchina/no.1-production-ready-suite` の `platform/packages/ui` (`@engchina/production-ready-ui`)
**影響範囲:** `packages/ui` + 消費側3アプリ（RAG / NL2SQL / Agent）
**種別:** 基盤リファクタリング（見た目の意図的な変更を多数含む）
**忠実度:** **hifi** — 全値が確定済み。ここに書かれた hex・px・トークン名をそのまま実装してください。

---

## 0. このバンドルは何か

このフォルダはリポジトリの **`docs/design-system/`** に置かれています。UI に触る変更では
ここが正本です（`AGENTS.md` の「デザインシステム / UI」節から参照されています）。

| ファイル / フォルダ | 何か | 扱い |
|---|---|---|
| `ARCHITECTURE.md` | **3アプリがこの DS をどう使うかの契約書。3チーム全員が読む** | 最初にこれを読む |
| `css/` | design system の**実ソース**（プレーン CSS） | `packages/ui/src/styles/` に**ほぼそのまま移植できます** |
| `adherence.oxlintrc.json` + `design-system-plugin.mjs` | 生の hex / inline style の生の px / 書体 / 型・角丸の任意値 / 旧トークン名 / 内部パス import / loading 中のラベル差し替え / 任意の表示（「(任意)」・placeholder の「任意」）/ 手書きの必須表示を検出する lint ルール | 各製品の lint 設定から相対パス（`../../platform/…`）で参照する（`AGENTS.md`「lint」節） |
| `components-reference.md` | 新規・変更されたコンポーネントの**参照実装**（React + インラインスタイル） | **そのまま出荷しない。** `packages/ui` の既存 `.tsx` / Tailwind の書き方に合わせて書き直す |
| `reference/` | **目視確認用の HTML** | ブラウザで開いて見た目を確認するだけ。製品コードではない |

`reference/` をブラウザで開くと、ライト／ダーク並びで全トークンと、ボタン・タブの実物が見えます。

---

## 1. 作業の3本柱

| # | 柱 | 何を解決するか |
|---|---|---|
| **A** | 色トークンの3層化 | `.dark` の全値手書き複製、WCAG 1.4.11 不適合、暗い面の直書き、ダークの段が1つしかない問題 |
| **B** | タイプスケールと単位の境界 | 本文 12.25px / 表 10.5px（業界下限割れ）、px と rem の二重系 |
| **C** | 新コンポーネントと統一基準 | `Tabs` / `PageBody` 不在による3アプリの分岐、ボタンのアイコンと loading の不統一 |

---

## 2. 柱 A — 色トークンの3層化

### 現状の問題

| # | 現状 | 問題 |
|---|---|---|
| 1 | 色トークンが1層。`:root` に hex 直書き、`.dark` に**全40値を手書き複製** | 「なぜダークの `--primary` が `#69adff` なのか」を誰も説明できない。追加時に必ず2箇所編集が必要 |
| 2 | `--control-border: var(--border)` = `#e3e6ea` | 白背景に **1.25:1**。入力欄の枠線が **WCAG 1.4.11（非テキストUI、3:1必須）不適合** |
| 3 | `--button-border: #8893a3`（3.10:1）が別トークン | secondary ボタンだけ枠線が2.5倍濃い。入力欄と並ぶと別の設計言語に見える |
| 4 | サイドバーが `#ffffff` / `rgb(255 255 255 / .1)` 直書き | 暗い「第3の色文脈」が未トークン化。サイドバー内でコンポーネントを再利用できない |
| 5 | `--ring: #1a73c1` をサイドバー（`#11253f`）上でも使用 | **1.9:1**。キーボード操作でフォーカス位置が見えない |
| 6 | ダークで `--card` = `#181c23` の1段のみ、影は `rgb(0 0 0 / .1)` | ダークで影は見えないため、**popover / dialog / toast / card が全部同じ面に見える** |
| 7 | プレースホルダが `--muted` の70%透過 | 実効 3.2:1 前後で AA 不合格 |
| 8 | `--disabled: #9ca3af` | 2.5:1。WCAG 免除だが実用上読めない |
| 9 | `--z-overlay: 1000` の1段、`SelectField` は `zIndex: 50` 直書き | 重なり順の仕様が無い |
| 10 | 情報色 `#1d4ed8` がアクセント青 `#1a73c1` と別の青 | 「違う色」と気づかれない程度に近く、単なるブレに見える |
| 11 | 状態色が色のみ | success `#047857` (L=0.139) と danger `#b91c1c` (L=0.110) の輝度がほぼ同じ。**1型・2型色覚では見分けられない** |
| 12 | `forced-colors` / `prefers-contrast` 未対応 | Windows ハイコントラストは官公庁・金融の調達要件になりがち |

### アーキテクチャ

```
TIER 1   tokens/palette.css         原色。--neutral-0…1000 / --blue-* / --green-* / --amber-* / --red-*
                                    ★ コンポーネントから参照禁止。ランプ上の位置だけが意味を持つ
TIER 2   tokens/colors.css          --color-surface / --color-fg-muted / --color-border-control …
                                    ★ アプリとコンポーネントが参照してよいのはここだけ
TIER 2.5 [data-surface="inverted"]  常に暗い面（サイドバー）
         [data-surface="code"]      常に暗い面（SQL / ログ）
         [data-surface-tint="accent"] 淡アクセント面の上の文字だけを深くする（DataTable の選択行）
TIER 3   components/components.css  hover / focus / disabled の状態のみ。TIER 2 のみ参照
         tokens/a11y.css            forced-colors / prefers-contrast の応答層（最後に @import）
```

**命名規約:** `--color-<category>-<role>[-<state>]`
`category` = `canvas | surface | fg | border | accent | success | warning | danger | info | focus`

### テーマ切替は `color-scheme` 一本

各トークンは CSS の `light-dark()` で **1宣言＝両テーマ**を解決します。`.dark` に全値を複製する運用は廃止。

```html
<html>                     <!-- ライト（既定） -->
<html class="light">       <!-- ライト（明示。旧 API。既存3アプリ互換） -->
<html data-theme="dark">   <!-- ダーク -->
<html class="dark">        <!-- ダーク（旧 API。既存3アプリ互換） -->
<html data-theme="auto">   <!-- OS 設定に追従（新機能） -->
```

```css
:root                        { color-scheme: light; }
[data-theme="light"], .light { color-scheme: light; }
[data-theme="dark"],  .dark  { color-scheme: dark;  }
[data-theme="auto"]          { color-scheme: light dark; }
```

**ブラウザ要件:** `light-dark()` は Chrome 123+ / Safari 17.5+ / Firefox 120+。**社内の対応ブラウザ下限がこれを下回る場合は §6-B のフォールバックを使ってください。**

### 面スコープ（最重要の再利用ポイント）

```jsx
<aside data-surface="inverted">
  <Button variant="secondary">再読込</Button>   {/* 追加コード0行で暗い面用の見た目になる */}
</aside>
```

スコープは `color-scheme: dark` ごと差し替えるため、**明示的に上書きしていないトークンも自動的にダーク値になります**。これが `#ffffff` 直書きを全廃できた理由です。

---

## 3. 柱 B — タイプスケールと単位の境界

### 規約（新設）

> **文字サイズとコントロール高さは px（ルート非依存）。余白とレイアウト寸法は rem（14px ルート）。**

文字も rem だった旧構成では、`html { font-size: 14px }` のため本文 0.875rem = **12.25px**、表・メタ 0.75rem = **10.5px** になっていました。業界下限（本文 14px・補助 12px）を割っており、**日本語は同 px でもラテン文字より小さく見える**（字面率が高く画数が多い）ため、実際の可読性が最も低い箇所でした。

**ルート 14px は維持します**（3アプリが Tailwind の rem ユーティリティを直接使っているため）。したがって**余白は一切動きません**。

### 新しいスケール

| トークン | 値 | 用途 | 旧 |
|---|---|---|---|
| `--text-display` | 24px / 700 / 32 | KPI 数値、空状態の見出し | （無し） |
| `--text-page-title` | 20px / 700 / 28 | ページタイトル | 17.5px |
| `--text-section-title` | 16px / 600 / 24 | **セクション見出し（実質新設）** | 14px / 700・未使用 |
| `--text-card-title` | 14px / 600 / 20 | カード見出し | 12.25px |
| `--text-body` | 14px / 400 / 20 | 本文 | 12.25px |
| `--text-label` | 14px / 500 / 20 | ラベル・ボタン | 12.25px |
| `--text-meta` | 12px / 400 / 16 | 表・メタ・バッジ・パンくず | **10.5px** |
| `--text-code` | 13px / 400 / 1.6 | SQL・ID・ログ | 13px |

**階層が実質2段から4段になりました。** 旧構成はページ副題（12.25px）とカード見出し（12.25px）が同サイズで、「ページ > セクション > カード > 行」の4階層を表現できていませんでした。

### アイコン寸法（4段に統合）

旧構成は 13 / 15 / 16 / 18 / 22 / 24 の6値が JSX にマジックナンバーで散在。**Lucide は 2px ストロークなので奇数 px はサブピクセルでボケます。**

```
--icon-sm: 14px     密なセル、パンくず
--icon-md: 16px     ボタン、トースト、インライン
--icon-lg: 20px     nav 行、カード見出し
--icon-state: 24px  空/エラー状態の大アイコン
```

---

## 4. 柱 C — 新コンポーネントと統一基準

### `Tabs` / `TabPanel`（新規）

3アプリとも必ずタブを使うのに存在せず、各製品が発明する寸前でした。

- **下線スタイル固定**（管理コンソールの標準）。件数バッジ（`tnum`）付き
- **WAI-ARIA Tabs パターン**: ← → / Home / End、roving `tabIndex`、`aria-controls`
- `PageHeader` の `tabs` に渡すとヘッダー下端に吸い付く（**置き場所を一箇所に固定**）
- **入りきらないときは横にスクロールし、スクロールできる方向の端だけをフェードする**（#364）。フェードは `mask-image` で内容を透かすため、ヘッダー・カードのどの背景でも同じに見える（色を重ねない）。幅は `--tab-fade-width`（2rem）。入りきるとき（desktop の多くの画面）は何も付けない。スクロールバーは出さない
  - 出し分けはスクロール位置と大きさ（`ResizeObserver`）で `data-scroll-start` / `data-scroll-end` を付け外しする。動きは持たない（`prefers-reduced-motion` でも同じ）
  - 強制カラーモードでは文字を透かさず（mask を外す）、代わりに細いスクロールバーを出す
  - キーボード（← → / Home / End）・クリック・呼び出し側の変更で選んだタブは、**フェードの外まで**タブの列の中だけでスクロールして見せる（`scroll-padding-inline` = フェードの幅。ページ全体は動かさない）
- **強制カラーモードでは、選んだタブだけに `Highlight` の下線を出す**（#374）。選ばれていないタブの下線は透明の枠線で消していましたが、強制カラーモードでは透明の枠線も `CanvasText` に塗られ、すべてのタブに下線が出て選んだタブと区別できませんでした。選ばれていないタブは `forced-colors:border-b-[Canvas]`（背景と同じ system color）にします。選択は太字と `aria-selected` でも伝わります
  - 色の `transition` の途中の値は system color ではないため、強制カラーモードでは一瞬 `CanvasText` になります（150ms）。E2E は下線の色を `expect.poll` で待ちます

> **タブ＝同じ対象の別の見方に切り替える。チップ（`ToggleChip`）＝データを絞り込む。**
> 意味が違うので、下線（タブ）と pill（チップ）で見た目を明確に分けています。**流用しないこと。**
> 旧実装は `ToggleChip` をタブ代わりに使っており、「押したら内容が入れ替わるのか、減るだけなのか」が判別できませんでした。

**ペイン・カードの中の見方の切り替えも `Tabs` + `TabPanel`（#396）。セグメントコントロールは作らない。**

- 原本の処理前 / 処理後、同じ内容の表示形式（Markdown / HTML / JSON）のように、ページより小さい単位で同じ対象の見方を変えるときも `Tabs` を使い、中身を `TabPanel` で包む。タブの列は内容の直上に置き、関係する操作（ダウンロード等）は見出しの行に置く。1 画面に複数の `Tabs` を置くので `idPrefix` を分ける
- **枠（`border` + `bg-surface-sunken` + `p-0.5`）の中に `Button` や素の `<button>` を並べた手書きのセグメントを作らない。** RAG の原本プレビューで実測した旧実装は、外枠 1px（`--color-border`）と選択中のボタンの枠 1px（`--color-border-control`）が 1.75px の間隔で二重に並び、ボタンの間は 0px、角丸は外 5.25px に対して内 6px（内側の方が大きく、同心でない）、選択は `variant` の違いだけで `aria-pressed` / `aria-selected` が無く、矢印キーでも動かなかった
- 別の部品（セグメントコントロール）を足さなかった理由: 意味（同じ対象の別の見方）が `Tabs` と同じで、見た目だけが違う部品を足すと「下線＝見方、pill＝絞り込み」の区別がまた崩れる。WAI-ARIA APG に segmented control のパターンは無く、見方の切り替えの役割は `tablist` / `tab` / `tabpanel`（`radiogroup` はフォームの値の選択、`aria-pressed` はオン / オフのトグル）。キーボード（← → / Home / End、roving `tabIndex`）・強制カラーモード・入りきらないときのフェードは `Tabs` が既に持っている
- 使えないタブは `disabled` にし、理由は `disabledReason`（無効のときだけ HTML の `title`）で渡す。無効のタブはフォーカスを受けず Tooltip を出せないため。同じ理由を画面の別の場所（バナー等）に書いているときは渡さない
- 絞り込み・モード・オン / オフの切り替え（見方ではないもの）は従来どおり `ToggleChip` / `Button` の `pressed`（UX 契約 buttons.md §6）

**フォームを Tabs で分けたときは、エラーのあるタブを `invalid` で示す（#542）。**

- 同じ設定の別の枠（モデル設定のプライマリ接続 / セカンダリ接続など）をタブで分けると、選んでいないタブの中の欄のエラーが見えない。`TabItem` の `invalid` を渡すと、ラベルの後ろに danger 色の `CircleAlert`（14px）を出し、読み上げでは `invalidLabel`（既定「入力にエラーがあります」）をタブの説明（`aria-describedby`）として伝える。色だけに頼らない（アイコンの形と読み上げの説明）。`data-invalid` も付く
- 保存で止めたときは、最初のエラーがあるタブに切り替え、そのタブの最初のエラーの欄へフォーカスする（UX 契約 `messaging.md` §3.2.1。切り替えは呼び出し側が行う）
- 保存していない入力があるタブは、既存の `badge`（件数と同じ見た目）に「未保存」を出してよい。1 つの保存で複数のタブの入力を送るときに、別のタブの変更に気付けるようにする
- 欄のエラーそのものは従来どおり欄の直下の `FieldError`。タブの印はその場所を指す手がかりで、代わりにはしない

### `PageBody` / `Section`（新規）

readme が規定していた「左右ガター 2rem / セクション間 1.5rem」の**唯一の実装**。これが無かったため3アプリがそれぞれ `<div style={{padding:'1.5rem 2rem', …}}>` を手書きしていました。**design system で最も確実に壊れる場所が唯一未実装**という状態でした。

- `max-width: var(--content-max-width)` = **1440px** で中央寄せ
  `<main>` が無制限に伸びると10列の表が2400px に広がり、目が行を追えません（視線移動限界は約1000〜1200px）
- `Section` の見出しは 16px / 600（ページタイトル 20px とカード見出し 14px の間の段）

### wide 画面の 100% 充填（新設）— ★ カードの中身を左に残さない

`wide` の画面では、ページとカードだけでなく**カードの中身も 100% の幅を使います**。広い画面の行長・視線移動は「コンテナの幅を止める」ことではなく、**grid の段組みを増やす**ことで抑えます（Material 3 の canonical layouts、Carbon の 2x grid、Atlassian / Primer のフォームと同じ考え方）。

> 1 列のフォームを 1440px で止めてカードの右側を空けるのも、1 つの入力欄を 2,500px に伸ばすのも誤り。**コンテナは 100%、中身はレスポンシブ grid で段組みを増やして埋める。**

旧実装（NL2SQL #568）はカード内のフォームに `max-w-[var(--content-max-width)]` を付けたため、2,560px 表示でデータベース設定や「破壊的な再作成」区画がカード幅の左 60〜65% に収まり、右側が空白になりました。付けなかった一覧の検索欄は逆に 1 本で約 2,200px まで伸びていました。

| 対象 | 置き方 | 例 |
|---|---|---|
| コンテナ（カード / `Section` / `Banner` / 危険な操作区画 / 確認語欄の枠 / フォーム枠 / 一覧 / 表 / 検索欄の外枠） | **max-width を付けない**（`max-w-[var(--content-max-width)]` / `max-w-3xl` 等で止めない） | — |
| フォーム項目 | `grid gap-x-6 gap-y-4 lg:grid-cols-2`。短い値が並ぶなら `2xl:grid-cols-3`。**意味のペアは同じ行**、入力順は崩さない | ユーザー / パスワード、接続方式 / Wallet ZIP、リージョン / テナンシ |
| 長い値 | `col-span-full` | OCID・URL・パス・textarea・JSON / SQL エディタ・`Banner`・チェックボックスの説明 |
| grid の外に単独で置く選択欄・短い値の入力欄 | 部品の `width`（`xs` / `sm` / `md` / `lg`）で値の長さの幅にする（下の「操作部品の高さと幅」、#613）。**カードや行の幅いっぱいに伸ばさない** | チャットの検索・回答プロファイル（`lg`）、表示件数（`sm`）、保存期間（`md`） |
| 短い値と長い値のペア | 比率で配分（`lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]`） | リージョン（1）/ OCID（2） |
| 検索・絞り込みの toolbar | 検索欄と、フィルタ / ファイル選択 / 件数 / 一覧全体の操作を**同じ行に比率で配分**（`lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]` 等）。行全体は 100%。**並べる要素があるのに検索欄だけを行全体に伸ばさない** | 検索（2）/ 件数・XLSX 出力（1）、ファイル選択（2）/ アップロードモード（1） |
| 数値などの短い単独入力と実行ボタン | 同じ操作行に置く（`lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] lg:items-center`） | 取得件数上限（1）/ 実行・リセット（2） |
| 危険な操作区画 | 区画は 100%。広い画面では「見出し + 影響の説明」と「確認語 + 実行」を左右に分ける（`xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]`） | すべて再作成 |
| 統計タイル | 100% を等分（`grid sm:grid-cols-2 xl:grid-cols-4`） | 件数・状態の要約 |
| ボタン行 | 「カード内の操作行」のまま。**ボタン自体は引き伸ばさない**（640px 未満の `w-full` を除く） | 保存 / 接続テスト |

- **例外（幅を持ってよいもの）**: Modal / Dialog / Drawer / Popover / Toast / Tooltip の本体、`EmptyState` の文言、3 行以上の長文段落の `max-w-prose`（段落にだけ付け、コンテナには付けない）、アイコン / バッジ / チップの truncate 用 `max-w-*`、`max-w-full`。
- **1280px で崩さない**: 段組みは `lg`（1024px）以上でだけ増やす。サイドバーを引いた本文幅で窮屈なら `xl` / `2xl` から増やす。
- 業務 repo は Tailwind のレイアウトユーティリティ（`grid` / `col-span-*` / `grid-cols-[…]`）だけで実装する。3 製品で同じフォーム grid が必要になったら、業務 repo に部品を作らず `packages/ui` への追加を Issue にする。
- **検証**: Playwright で 1280 / 1920 / 2560px × ライト / ダークを表示し、「カード内幅（全幅の親の内幅）の 75% 未満・左寄せ・右隣に兄弟要素が無い、入力欄 / 表 / 入力欄を含む塊 / 枠付きの塊」（左寄せ取り残し）が 0 件であることを確認する（例外を除く）。

### `PageHeader`（変更）

1. **`position: sticky; top: 0`** — 長い表でもタイトルと主要操作に手が届く
2. **`<header>` は full-bleed、中身は `PageBody` と同じ計測コンテナ**
   ★ これが無いと 1920px モニタでタイトルと本文カードの左端が **240px ずれます**（実測済み）。
   `wide` は **`PageBody` の `wide` と必ず同じ値**にしてください
3. **アクションの並びを反転: `danger → utility → secondary → primary`**
   右寄せグループなので、**右端（最も押しやすい位置）が primary**。旧構成は primary が左端・danger が右端で、**最も破壊的な操作が最も押しやすい位置**にありました（Fitts の法則上、逆）
   → 破壊的な操作は本来オーバーフローメニュー（︙）に入れるべきです。`DropdownMenu` 実装後に移行
4. `tabs` スロット追加
5. **通常表示の `utility` は `secondary` と同じ枠付きボタン**。表示更新・DB 構造の再取得など、ページツールにも操作範囲を示す。`kind` による並び順・compact 時の優先度は維持し、オーバーフローメニュー内は従来どおり `ghost` とする。
6. **グループの境界に区切り線**（#355、buttons.md §5）。操作を「危険操作（`danger`）」「ページツール（`utility`）」「作業開始（`secondary` + `primary`）」の 3 グループに分け、隣り合うグループの間に縦の区切り線（`--color-border`、高さ 1.25rem、左右に `--space-1`）を置く。読み上げない装飾（`aria-hidden`）。狭い画面の「その他の操作」メニューでは、危険操作の前に区切り線（`role="separator"`）を置く。
7. **狭い画面（lg 未満）の折りたたみは、メニューに入るのが 2 つ以上のときだけ**（#582）。主操作 1 つ +「その他の操作」に畳むとき、メニューに入るのが 1 つだけ（「一覧へ戻る」+ 保存など）なら畳まず、広い画面と同じ並びで全部出す。1 項目のメニューは開く手間が増えるだけで、ボタン 2 つなら 375px でも 1 行に収まる。3 つ以上で「一覧へ戻る」がメニューに入る画面は、パンくず（`breadcrumbs`）で一覧へ 1 タップで戻れるようにする。
8. **「その他の操作」の読み上げ名に対象を足す**（#582）。見た目の文言は「その他の操作」のまま、読み上げ名（`aria-label`）とメニューの名前を「その他の操作（<操作のグループの名前>）」にする。`PageHeader` は `actionsLabel`（既定「ページ操作」）、`ObjectActionBar` / `FormActionBar` は `ariaLabel` を足す（`contextualMenuLabel`）。ページとカードの「その他の操作」が同じ名前で並び、読み上げ・音声操作で区別できなかった。見えている文言を先頭に置く（WCAG 2.5.3 Label in Name）。

### 詳細・作成・編集の画面の操作（新設）— ★ 戻るは左上、保存は右端の primary（#618）

詳細・作成・編集の画面の「一覧へ戻る」「保存」の置き場所・形・文言が、製品・画面ごとに違いました。RAG・Agent のエディタは `PageHeader` の右に「一覧へ戻る」（secondary）と保存（primary）を並べ、パンくずも出していました。NL2SQL・system-settings（ユーザー・ロール・権限）は本文の先頭に左寄せの「一覧に戻る」（ghost）を置き、保存はフォームの末尾の `FormActionBar` でした。文言も「一覧へ戻る / 一覧に戻る」「保存 / 保存する」「作成 / 作成する」「変更を破棄 / 変更を元に戻す」が混在していました。

```text
┌ PageHeader ───────────────────────────────────────────────────────────────┐
│ ← 一覧へ戻る                                                               │  ← back（左上。Tab の先頭）
│ 経理ビュー [状態]                        [変更を破棄] [保存（primary）]    │  ← 右端が primary、破棄はその左
└───────────────────────────────────────────────────────────────────────────┘
  SaveErrorBanner（保存の失敗。ヘッダーの直下の 1 か所。#585）
┌ 最初のカード ────────────────────────── [その他の操作 ▾]（ObjectActionBar）┐  ← 対象への操作は 1 か所
│ フォーム …                                                                  │
```

| 決めたこと | 理由 |
|---|---|
| **一覧へ戻るは `PageHeader` の `back`**: タイトルの上の左端に、左向きの矢印付きの ghost の `sm` ボタン（矢印の左端をタイトルの左端にそろえる）。右の操作の列に置かない。2 階層（一覧 › 対象）のパンくずは出さず、`back` がその役をする（3 階層以上の深い導線だけ `breadcrumbs`） | 戻る操作は画面の先頭・左上（Material 3 の top app bar の navigation icon、Apple HIG の navigation bar の戻るボタン、Polaris の `backAction`、GitHub Primer の `PageHeader` の parent link）。主操作と並べると「保存せずに戻る」と「保存」を取り違える。パンくずと戻るの 2 つの導線を重ねない |
| 文言は「一覧へ戻る」。読み上げ名は戻り先を足す（`editor.backToListOf`「{list}の一覧へ戻る」。見える文言を含む。WCAG 2.5.3） | 3 製品・全画面で 1 つにする |
| **保存・作成は `PageHeader` の右端の primary**（`kind: "primary"`。文言は「保存」「作成」）。**変更を破棄はその左の secondary**（「変更を破棄」。変更が無いときは disabled）。本文のカードの中に重ねて置かない | 右端が最も押しやすい位置（`PageHeader` の並び順と同じ）。Polaris・Atlassian・Carbon の page header と同じ。ヘッダーは lg 以上で sticky なので、長いフォームの下までスクロールしても保存に届く。キーボードでは本文の欄の Enter でも保存できる（フォームの submit） |
| **対象への操作（アーカイブ・削除・復元・パスワードのリセット・無効化など）は `ObjectActionBar` 1 か所**。最初のカード（system-settings はフォームのパネル）の見出しの右に置く。非破壊・高頻度の 2 つまでを直置き、残りと危険な操作は「その他の操作」 | 置き場所を 1 つにする（buttons.md §5.1）。保存と破棄の近くに危険な操作を並べない |
| 保存の失敗は `SaveErrorBanner`（ヘッダーの直下の 1 か所。#585 のまま）。欄に結び付く失敗は欄の直下 | 保存のボタンがヘッダーにあるので、失敗もヘッダーの直下に出す |
| 未保存の変更がある状態で戻るときの確認（#586）は、`back.onClick` から呼ぶ製品の離脱ガードのまま | 戻る導線の置き場所だけを変え、確認の規則は変えない |
| **例外**: 確認語が要る保存（NL2SQL の業務プロファイルの Oracle への反映）は、確認語欄（`ExecutionConfirmationField`）の操作行に置く。作業の画面（NL2SQL のテーブル・ビューの作成・取込）は、実行を確認語欄の操作行に置き、戻るだけを左上にする | 確認語と確定を 1 つの区画にまとめる（README §4「確認語欄」、#379） |
| 設定の画面（OCI・データベース・モデルなど、1 ページに複数のカードのフォームがある画面）は、今までどおりカードの末尾の `FormActionBar` | 1 ページに保存の対象が複数あるので、ページのヘッダーに 1 つの保存を置けない |

- 部品: `PageHeader` の `back`（`{ label, onClick, ariaLabel?, disabled?, testId? }`。`data-page-header-back`）。`actions` に `id: "back"` の操作を入れない。（adherence の lint が `PageHeader` の `actions` の `id: "back"` を検出する）
- 置き換えた画面: RAG のナレッジベース・検索・回答プロファイルのエディタ・文書の詳細・対象の読み込み中 / 失敗の画面、Agent のエージェント・Skill・MCP 接続・プラグイン・マーケットプレイスのエディタと詳細、NL2SQL の業務プロファイル・テーブル・ビューの作業の画面、system-settings のユーザー・ロール・権限管理の編集。RAG・Agent の `EditorBreadcrumbs` は削除。

### `Button`（変更）— ★ アイコンと loading の統一基準

アイコンの有無と loading 表示がバラバラだった問題を、**1つのルールで結びました。**

> **非同期の操作を起こすボタンは必ずアイコンを持つ。loading 中はアイコンがスピナーに置き換わる。**

**API 変更:** `icon` プロップ（`lucide-react` のコンポーネント。型は `LucideIcon`）を新設。**子要素にアイコンを直接書かない。**
（Lucide 名の文字列で受ける方式は全アイコンをバンドルに含めるため採用しない。RAG 実測で JS +38%）

```jsx
import { Ellipsis, RefreshCw, Upload } from "lucide-react";

<Button variant="primary" icon={Upload}>文書アップロード</Button>
<Button variant="secondary" icon={RefreshCw} loading={reloading}>再読込</Button>
<Button variant="ghost" iconOnly icon={Ellipsis} aria-label="その他の操作" />
```

**役割と配置から選ぶ（色と高さは別々に判断する）**

| 配置 / 操作 | variant | size（desktop） | アイコン |
|---|---|---|---|
| ページヘッダーの主操作 | `primary` | `md`（36px） | 操作を示すアイコン |
| ページヘッダーの表示更新・DB構造再取得 | `secondary`（`kind=utility`） | `md`（36px） | `RefreshCw` |
| 工程・フォーム末尾で次工程へ進む操作（情報取得・生成・保存・実行） | `primary` | `lg`（40px） | 取得 `Database` / 生成 `Sparkles` / 保存 `Save` / 実行 `Play` 等 |
| 同じ工程操作行の再試行・リセット・キャンセル | `secondary` / `ghost`（補助） | 主操作と同じ `lg` | `RefreshCw` / `X` 等 |
| コンテンツのコピー・ダウンロード・状態再確認 | `secondary` | `sm`（32px） | `Copy` / `Download` / `RefreshCw` |
| 一覧の追加読込 | `secondary` | `sm`（32px） | `ListPlus` |
| 一括選択 / 選択解除 | `secondary` / `ghost` | 同じ `sm`（32px） | `CheckSquare` / `X` |
| 確認ダイアログの確定 / 取消 | `primary` または `danger` / `secondary` | 共通ダイアログに従い、同じ行で統一 | ダイアログの専用規約を優先 |
| 入力欄に隣接する操作（取得・接続テスト） | 操作の役割で決定 | 入力欄と同じ段（既定 `md`。lg の入力欄の横は `lg`）。`touchTarget` は使わない（#613） | 操作を示すアイコン |

- **タッチ端末は全サイズ44px**（入力欄・選択欄も。#613）。32/36/40pxは位置に応じた密度の違いであり、primaryだけを大きくする規則ではない。同じ操作行のprimary/secondary/ghostと、同じ行の入力欄・選択欄は同じ高さにする（下の「操作部品の高さと幅」）。
- **取得という動詞だけでsecondaryにしない。** 対象選択・内容確認へ進む唯一の主操作はprimary。すでに表示した一覧の再読込や状態再確認はsecondary。
- disabledでも同じサイズとアイコンを保つ。未選択だからアイコンを消したり、高さを変えたりしない。
- サイズ・色・角丸・アイコン枠は`packages/ui`の`Button`とトークンが実装する。アプリはrole/placementからpropsを選び、独自CSSや同等部品を作らない。

**アイコンをいつ付けるか（role で決まる。好みで決めない）**

| 場面 | アイコン |
|---|---|
| ページ / 工程の primary | **必ず付ける**（一番速く見つける対象なので目印になる） |
| secondary・utility で定番のグリフがある | 付ける（再読込 `RefreshCw` / CSV 出力 `Download` / 削除 `Trash2` / 編集 `Pencil`） |
| ダイアログのアクション行 | **付けない**（tone アイコンと競合しノイズになる） |
| 表の行・密なツールバー | `iconOnly` + `aria-label`（場所が無い） |
| ページ送り | 方向に合わせる（前へ = 先頭に `ChevronLeft`、次へ = `trailingIcon` に `ChevronRight`） |

> **同じグループ内では全員がアイコンを持つか、全員が持たないか。** 混在したグループは壊れて見えます。

`trailingIcon` は方向・開閉・外部リンクのみ。アイコンを2つ持たせない。

**loading の規約**

- **ラベルは変えない。**「実行中…」「処理中…」に差し替えないこと（3アプリで訳が分岐し、幅も跳ねる）
- アイコンと同じ 16px 枠なので **幅が変わらない**。そのため **`loading` を渡す `Button` は必ず `icon` を持つ**（adherence の lint が「`loading` があるのに `icon` が無い `Button`」を検出する。`{...props}` で渡す場合は対象外）
- **フォーカスを保つ（#355）**: `loading` 中はネイティブの `disabled` を付けず、`aria-busy="true"` と `aria-disabled="true"` を付ける。フォーカス中のボタンに `disabled` を付けると、フォーカスが `body` へ外れ、キーボード・スクリーンリーダーの利用者が位置を失うため（Chromium で確認）
  - `loading` 中はクリック・Enter / Space・form の送信（入力欄での Enter による暗黙の送信を含む）を止める。呼び出し側の `onClick` / `onPointerDown` / `onMouseDown` は呼ばない。Tab・Escape などのキーはそのまま渡す
  - 見た目は `disabled` と同じ（`aria-disabled:` に同じ色・カーソル。hover / active の塗りは付かない）
  - `disabled` prop はネイティブの `disabled` のまま。`disabled` と `loading` が重なったら `disabled` を優先する（`aria-busy` は付く）
  - 完了後もフォーカスはボタンに残る。結果へフォーカスを移すかどうかは画面の規約（buttons.md §4.1）に従う
  - Playwright の `toBeDisabled()` は `aria-disabled` も無効と判定する。jest-dom の `toBeDisabled()` と CSS の `:disabled` は判定しないので、loading の検証は `aria-disabled` / `aria-busy` で行う
- **旧実装のバグ:** `loading` でスピナーを**追加**していたため、スピナー＋アイコン＋ラベルの三重表示で幅が跳ねていました
- 1秒を超えて領域全体が待ちになる処理は、ボタンではなく領域側で `LoadingState`。ボタンのスピナーは「この操作が進行中」だけを表す
- **スピナーは押したボタンだけ（#819）**: `loading` は、そのボタンが始めた処理の間だけ true にする。同じ処理の間に使えない他のボタンは `disabled` だけにし、スピナーを出さない（Material・Carbon・Apple HIG と同じ）。1 つの mutation を複数のボタンが使うときは、押したボタンを `activeOperation`・`mutation.variables`・行の id で区別し、共有の `busy` を複数のボタンの `loading` に渡さない。再取得のボタンに query の `isFetching` をそのまま渡さない（定期の取り直し・他の操作の後の invalidate でも回るため。押した取り直しを `useActionPending` などで持つ）。例外はボタンが表す状態そのもの（ADB の「起動」と `STARTING`）。詳細は UX 契約 buttons.md §8
- アイコンとスピナーは `sm` / `md` / `lg` とも **16px**（`BUTTON_ICON_SIZE`、`--icon-md`）。ボタンの高さで寸法を変えない（18px は §3 のアイコン寸法の 4 段に無く、`sm` の 14px は周りの 16px のアイコンとそろわない。#395）
- `loading` 中のスピナーの色は `--color-fg-muted`。地と文字は disabled と同じ（`--color-surface-disabled` / `--color-fg-disabled`）だが、`fg-disabled` のままではライトで地に対して 2.82:1 になり、処理中を示す図形の 3:1（WCAG 1.4.11）に届かないため（`fg-muted` はライト 4.39:1 / ダーク 7.28:1。#395）
- その場で結果を待ち、止められる操作（検索・チャットの送信など）は `loading` を使わず、実行中はボタンを「停止」に切り替える（UX 契約 buttons.md §3.1、#413）。スピナーは結果の領域の `ProcessingIndicator` が出す

**画面を移るだけの操作は `ButtonLink`（#800）**

- 別の画面で設定する導線（ロールの詳細の「権限管理で設定」など）は、`<button onClick={navigate}>` ではなくリンクにする（新しいタブで開ける・読み上げで「リンク」と分かる）。
- `ButtonLink` は `Button` と同じ見た目・寸法（`size`）・アイコンの寸法（16px）のリンク。アイコンは `icon` / `trailingIcon` で渡し、`<Link className={buttonVariants(...)}>` の子にアイコンを手書きしない。react-router の `Link` は `linkComponent` で渡す（`packages/ui` はルーターに依存しない）。既定の variant は `secondary`。`loading` / `disabled` は持たない（移動は待ちが無く、移動できないときはリンクを出さない）。

**variant と tone（danger の使い方）**

- 赤塗りの `variant="danger"` は **実際の破壊的な確定**（確認ダイアログの確定、確認語を入力した「危険な操作」区画の実行）にだけ使う。
- 確定の前の起点（確認ダイアログを開くボタン）や、取り消せる停止・拒否（処理中のジョブのキャンセル、承認の拒否、Run のキャンセル）は、`secondary` / `ghost` + `tone="danger"`（赤文字）にするか、「その他の操作」メニューに入れる。
- `variant="danger"` と `tone="danger"` は**型で同時に指定できない**（赤地に赤文字になり読めない）。

### `Spinner`（変更）— ★ 線の実寸・トラック・reduced-motion（#395）

処理中を示す回転アイコンは共有の `Spinner` 1 つだけ（`Button` の `loading`、`ProcessingIndicator` / `TimedLoadingState`、製品の状態バッジ）。lucide の `Loader2` / `RefreshCw` などに `animate-spin` を付けて回さない（adherence の lint が検出する）。

| 項目 | 決定 | 理由 |
|---|---|---|
| 形 | 全周のトラック + 270 度のアーク。外形は大きさによらず直径 20/24 | 欠けた円弧だけだとインクの重心が回転で動き、中心がずれて見える |
| 線の太さ | **実寸 2px**（14 / 16 / 20 / 24px で viewBox 上 3.429 / 3 / 2.4 / 2）。太くした分は円の半径を内側へ寄せる | 旧実装は viewBox 24 に線幅 2 固定で、16px では 1.33px、14px では 1.17px まで細り、等倍の画面でかすれていた。GitHub Primer・Fluent 2 の 16〜28px のスピナーも 2px |
| トラックの色 | `--color-spinner-track`: アークと同じ色（`currentColor`）をライト 30% / ダーク 35% で透かす。強制カラーモードは `GrayText` | アーク対トラックの境目 3:1 以上を保つ上限の濃さ（下の実測）。トラックを 3:1 の `--color-border-control` にすると、アーク（`--color-accent-fg`）との差がライト 1.58:1 / ダーク 2.43:1 になり、回っている部分が見分けにくくなる |
| 回転 | `transform` の等速（linear 1s）だけ | 合成スレッドで回るので、メインスレッドが詰まっても（回答の描画・SSE の解析中）止まらない。弧長の伸縮（`stroke-dasharray`）はメインスレッドで描き直すため詰まると止まり、加減速は 1 周ごとに遅くなる区間が「止まりかけ」に見える |
| `prefers-reduced-motion` | **回転を止めない**（#440）。`base.css` の一括無効化（`animation-duration: 0.01ms`）より詳細度の高い規則で 1s に戻す | その場で回るだけの小さな動きで、処理中を伝える本質的な表示（画面の移動や視差ではない）。#395 では回転を止めてアークの濃さを変えていたが、変化が小さく「固まった」と見えた（Windows のアニメーション効果オフ・リモートデスクトップ・VM で起きやすい）。macOS / iOS の標準の処理中表示も Reduce Motion で止まらない |
| 色 | 置かれた場所の文字色（`currentColor`）。`ProcessingIndicator` は `--color-accent-fg`、ボタンの `loading` は `--color-fg-muted` | 地に対してアーク 3:1 以上（下の実測） |

**実測（コントラスト比。アーク / 地、トラック / 地、アーク / トラック）**

| 置き場所 | ライト | ダーク |
|---|---|---|
| `ProcessingIndicator`（`surface`） | 4.92 / 1.52 / 3.24 | 7.34 / 2.03 / 3.62 |
| `TimedLoadingState`（`surface-sunken`） | 4.63 / 1.50 / 3.09 | 8.00 / 2.02 / 3.97 |
| 最も条件の悪い地（ライト `surface-hover`、ダーク `surface-overlay`） | 4.47 / 1.49 / 3.00 | 5.94 / 1.95 / 3.05 |
| `Button` の `loading`（`surface-disabled`、`fg-muted`） | 4.39 / 1.45 / 3.03 | 7.28 / 2.15 / 3.39 |

旧実装（トラック 25%）のトラック / 地はライト 1.39〜1.41:1、ダーク 1.59〜1.62:1。

**動くスピナーは同じ処理に 1 つだけ（UX 契約 messaging §3.7）。** 起点の `Button` が `loading` を出している間、同じ処理の `ProcessingIndicator` / `TimedLoadingState` は `activityIcon="none"`（ラベル・経過時間・時間がかかる案内だけ）にする。`ProcessingIndicator` がボタンの状態を自動で読み取る仕組みは持たない（同じ処理かどうかは画面にしか分からないため、呼び出し側が `activityIcon` で指定する）。

### タッチ端末の当たり判定（新設）— ★ 見た目は変えず 44px（#364）

- **タッチ端末（`pointer: coarse`）では、`ToggleChip` / `Switch` の当たり判定を 44px（`--control-height-touch`）以上にする。見た目の大きさは変えない。** マウス環境は変えない（WCAG 2.5.8 の 24px は満たしている。#338 のレビューで、マウス環境の 44px は見送り）。`Button` は `--button-height-*` が 44px になって実際に大きくなるが、チップとスイッチは密度を保つため擬似要素で当たり判定だけを広げる
- 仕組みは `pr-touch-target`（`structure/touch-target.css`）。`::before` が見た目の中心から縦横それぞれ 44px 以上に広がる当たり判定、`::after`（`z-index: 1`）が自分の見た目の範囲。**隣の部品の広げた当たり判定が自分の見た目を覆わない**（折り返したチップの行・間隔の狭いスイッチの並びでも、見た目の上のタップは必ずその部品に届く）
- 部品の間隔が、両側の広がりの合計（チップは約 19px、スイッチは 20px）より狭いときは、**重なった間の当たり判定は後ろ（下・右）の部品が取る**。どの部品にも見た目の上のタップは届くので誤タップにはならないが、前の部品の実効の当たり判定は 44px より小さくなる（例: `gap-2`（7px）で縦に並べたスイッチは、上が 34px・下が 41px）。どちらも 44px にするには `gap-6`（21px）以上を空ける
- スクロール領域の端（余白なし）に置くと、広げた当たり判定の分だけスクロールできる範囲が伸びることがある。カード・フォームの余白の中に置く
- 製品は何も書かない（`ToggleChip` / `Switch` を使えば付く）。同じことを製品で手書きしない

### 操作部品の高さと幅（新設）— ★ 同じ行は同じ高さ、選択欄は値の長さの幅（#613）

画面ごとに入力欄・選択欄・ボタンの高さと幅がそろっていませんでした。製品が `touchTarget` / `size="lg"` / `h-10` / `h-11` / `min-h-[44px]` を画面ごとに選び（3 製品で 75 か所。高さは 35 / 36 / 38.5 / 40 / 44px が混在）、同じ行でも部品ごとに高さが違いました。タッチ端末では Button だけが 44px になり、入力欄（36px）とずれていました。選択欄の幅には規則が無く、短い値（「DEFAULT」「90 日」）の選択欄が親の幅いっぱい（1,280px の画面で約 560px、1,920px で約 900px）に伸びていました。

**高さ（3 段 + タッチ端末）**

| 段 | 高さ | 使う場面 |
|---|---|---|
| `sm` | 32px（`--control-height-sm`） | 表の行・密なツールバーの中の操作、内容のコピー・ダウンロード、追加読込、一括選択、ページ送り、空の状態の「検索語をクリア」 |
| `md`（既定） | 36px（`--control-height-md`） | すべての入力欄・選択欄、一覧のツールバー（検索・絞り込み）、フォームの欄とその横の操作（取得・接続テスト）、ページヘッダーの操作、ダイアログ |
| `lg` | 40px（`--control-height-lg`） | 工程・フォームの末尾で次へ進む主操作の行（`FormActionBar`・保存・実行）、主な問い合わせの入力の行（チャットの入力欄と送信・検索テスト・オントロジーの質問）、確認語欄、ログインなど 1 つの作業だけの画面のフォーム |
| タッチ端末 | 44px（`--control-height-touch`） | `pointer: coarse` では 3 段とも 44px（入力欄・選択欄も）。画面幅ではなく入力方式で決める |

- **1 つの行の中では、入力欄・選択欄・ボタンを同じ段にする。** lg の Button の隣の入力欄は `size="lg"`、入力欄の横の操作は入力欄と同じ `md`。Button・`TextField`・`SearchField`・`SecretField`・`SelectField`・`SearchableSelectField` が同じ `size`（`sm` / `md` / `lg`）を受け取り、同じトークン（`--control-height-*`。`--button-height-*` / `--field-height` は別名）を参照する。
- **製品で `touchTarget` を使わない。** タッチ端末では全部品が 44px になるので、マウス環境で 44px にする理由が無い（以前の「入力欄に隣接する操作は `touchTarget`」は廃止。`TextField` の `touchTarget` は削除した）。`touchTarget` は `packages/ui` の画面の端の閉じる・メニューのボタン（Toast・Banner・AppShell の上端のバー）だけが使う。
- **製品で `h-*` / `min-h-*` を操作部品に書かない。** フォームの入力は共有の部品（`SelectField` / `TextField` など）で書く（#631。ネイティブの `<select>` は adherence の lint が検出する）。ネイティブの `<select>` / `<input>` を例外として残す画面（`<optgroup>` を使う選択など）も、`fieldControlClassName({ size, width })` で同じ見た目・高さにする。
- 例外（理由をコメントに書いて局所的に除外）: 説明文を含むカード型の選択肢（NL2SQL の回答エンジンの選択）。

**幅（値の長さで選ぶ 5 段）**

| 段 | 幅 | 入る値の例 |
|---|---|---|
| `xs` | 8rem（112px） | 数値・件数・短いコード（「10」「DEFAULT」） |
| `sm` | 12rem（168px） | 短い列挙（状態・種類・言語・「1 ページの表示件数」） |
| `md` | 20rem（280px） | 名前（ユーザー名・表名・評価の指標・保存期間） |
| `lg` | 28rem（392px） | 長めの名前（検索・回答プロファイル・ナレッジベース・モデル） |
| `full` | 親の幅 | URL・OCID・パス・文章・一覧の検索欄 |

- **フォームの grid のセルに置く欄は `width` を渡さない**（セルの幅いっぱい）。欄の最大幅は grid の段組みで決める（上の「wide 画面の 100% 充填」。1 つの欄を行全体に伸ばさない）。**grid の外に単独で置く選択欄・短い値の入力欄は必ず `width` を渡す**（カードの先頭の対象の選択、ツールバーの絞り込み、表示件数、操作の横の選択）。
- 最も長い選択肢と**ラベル**が 1 行に収まる段を選ぶ（ラベルが折り返す幅にしない）。
- sm（640px）未満はどの段も全幅。`width` は欄の外枠（ラベル・補足・エラーを含む）に付き、親より広くはならない（`sm:max-w-full`）。
- **製品で `w-*` / `max-w-*` を欄に書かない。** adherence の lint が検出する。

**複数行の入力と操作の行**

- 入力欄と、その値への操作（送信・実行・取得）を 1 行に置くときは `FieldActionRow` を使う。操作は**入力欄の下端にそろえる**（チャットの入力欄の型。ラベルが上にある 1 行の入力欄でも同じ）。375px では操作を入力欄の下に全幅で置く。欄の補足・エラーは `footer` に出す（欄の中に出すと、下端でそろえた操作が文の下端に合ってしまう）。
- 複数行の入力欄の高さは `rows` で決める。最小の高さは 1 行の入力欄と同じ段（`--control-height-md`）で、横の操作より低くならない。

**根拠（業界の指針）**

| 指針 | 取り入れたこと |
|---|---|
| [Carbon Form](https://carbondesignsystem.com/components/form/usage/)（入力欄・選択欄・ボタンが 32 / 40 / 48px の 3 段。同じ行は同じ段） | 部品をまたいで同じ段を使い、行の中では段をそろえる。既定は中の段 |
| [Material 3 Density / Accessibility](https://m3.material.io/foundations/designing/structure)（密度は 4dp 刻みで下げる、タッチの当たり判定は 48dp） | 段を 4px 刻み（32 / 36 / 40）にし、タッチ端末では当たり判定を広げる |
| [Apple HIG Layout](https://developer.apple.com/design/human-interface-guidelines/layout)（操作の最小 44 × 44pt）・[WCAG 2.2 SC 2.5.5 / 2.5.8](https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced.html) | タッチ端末では入力欄も含めて 44px。マウス環境は 2.5.8 の 24px を満たす密度のまま |
| [GOV.UK Design System Text input](https://design-system.service.gov.uk/components/text-input/)（「欄の幅は入る値の長さに合わせる」、固定幅は狭い画面では画面の幅に縮む）・Baymard Institute の入力欄の幅の研究 | 値の長さで選ぶ幅の段。狭い画面では全幅 |
| [Atlassian Textfield](https://atlassian.design/components/textfield/)（`width` で欄の最大幅を決める） | 幅を部品の prop にし、製品で幅のクラスを書かない |

### `Tooltip`（新規）— ★ アイコンだけのボタンの説明（#372）

アイコンだけのボタン（`iconOnly`）は、名前が `aria-label` にしかなく、目で確かめる手段が HTML の `title` 属性だけでした。`title` はマウスで長く止めないと出ず、キーボードのフォーカスでもタッチでも出ません。**WAI-ARIA APG の Tooltip パターン**に従う共有の `Tooltip` を入れ、`Button` の `iconOnly` から使います。

> **`iconOnly` の `Button` は既定で、`aria-label` と同じ文言の Tooltip を出す（opt-out）。** 出したくないときだけ `tooltip={false}`。

- **既定にした理由**: アイコンだけのボタンは、文字のボタンと違って名前が画面に無い。見える名前を持たせるのは部品の責任で、製品の書き忘れに任せない（opt-in だと 3 製品の全箇所に書き足す必要があり、書かれない所から欠ける）。文言は必須の `aria-label` を使うので、製品の追加の作業は無い。
- **二重に読み上げない**: 文言が `aria-label` と同じなら `aria-describedby` で結び付けず、吹き出しは `aria-hidden`。文言が違う（ショートカットキーを添える等。`tooltip="前のページ（PageUp）"`）ときだけ説明として結び付ける。説明にする吹き出しは閉じている間も `hidden` で置いておき、フォーカスした時点で説明が読まれるようにする。
- **`title` 属性は使わない**: Tooltip を出す `Button` は `title` を無視する（ブラウザの吹き出しと二重になる）。`iconOnly` でない、実質アイコンだけの `Button` にも `tooltip="…"` で出せる（見た目を変えずに `title` を置き換える）。

| 振る舞い | 決めたこと | 理由 |
|---|---|---|
| 出すきっかけ | ホバー（**400ms 待つ**）とキーボードのフォーカス（**すぐ**）。マウスで押したときのフォーカス（`:focus-visible` でない）では出さない | ポインタが通り過ぎただけで出さない。キーボードの利用者を待たせない |
| 続けて出す | ホバーで開いた Tooltip が閉じてから 300ms 以内に隣のボタンへ移ったら、待たずに出す | ツールバーをなぞるときに毎回待たせない |
| 消えない | トリガーから吹き出しへポインタを移しても消えない（離れてから 100ms の猶予）。フォーカス中はポインタが離れても消えない | WCAG 1.4.13 hoverable / persistent |
| 1 つだけ | 画面に出す吹き出しは 1 つだけ。別の Tooltip が開いたら、先に開いていたものを閉じる（ポインタを置いたままのボタンの吹き出しは、キーボードで別のボタンへ移ったら閉じる） | ホバーの吹き出しとフォーカスの吹き出しを重ねて出さない（#655） |
| 閉じる | Escape で閉じる（フォーカスもポインタも動かさない）。**開いている間の Escape は吹き出しだけを閉じ、囲むモーダル・メニューに伝えない**。ボタンを押したら閉じ、ポインタが離れるまで出さない | WCAG 1.4.13 dismissible。1 回目の Escape で文脈ごと閉じない（SelectField と同じ）。押した結果を隠さない |
| 位置 | 既定は上、トリガーの中央。入らなければ下に反転し、左右は画面の内側（8px）にずらす（`data-tooltip-placement`） | `computeFloatingMenuLayout`（#363）と同じ考え方 |
| 描く先 | body へ Portal（モーダルの中ならモーダルの中）。z-index は `--z-popover`、z-index を持つ層（固定ヘッダー・モーダル）の中ではその層より 1 段上 | 親の `overflow`（ツールバーの横スクロール等）に切られない。固定ヘッダーの下に隠れない |
| ポインタ | ホバーで開いたときだけ吹き出しがポインタを受ける。フォーカスで開いたときは下の要素を押せる | 1.4.13 の hoverable はポインタで開いたときの要件。キーボードで開いた吹き出しがマウスの操作を塞がない |
| タッチ端末 | `pointer: coarse` とタッチのポインタでは出さない（説明の結び付けは残す） | 指で吹き出しが隠れ、長押しの操作と競合する。読み上げの説明は変わらない |
| 見た目 | 常に暗い吹き出し（`data-surface="inverted"`・`--color-surface-overlay`・`--color-border`・`--shadow-popover`）、12px / 500、最大幅 16rem で折り返す。矢印は付けない | サイドバーの折りたたみ時のラベルと同じ見た目。トークンだけで作る |
| 動き | `overlay-in`（150ms の fade）だけ。`prefers-reduced-motion` では `base.css` が止める | 動きで意味を伝えない |

- 中身は短い文だけ。リンク・ボタンなど操作できる要素を入れない（APG: tooltip はフォーカスを受けない）。
- `disabled` のボタンはフォーカスを受けないので、キーボードでは出ない。使えない理由は画面の文言で示す。
- アイコン以外の要素（文字のボタン等）に説明を足すときは `<Tooltip content="…"><button …/></Tooltip>` で直接使える。トリガーはフォーカスできる要素 1 つにする（切り詰めた文字の `span` の全文表示には使わない）。
- 操作・欄の**補足の説明**（文で読むもの）は `Tooltip` ではなく次の `InfoTip` を使う。

### `InfoTip`（新規）— ★ 補足の説明は常設せず、ラベルの横の info アイコンから出す（#901）

チャット・検索の操作の行（RAG の「回答するモデル」、NL2SQL の「生成方法」など）が、操作の補足の説明を常に表示していて、画面を占めて読みにくくなっていました。補足の説明を出す共通の部品が無く、各画面が説明文を常設していたためです。ラベル（または操作）の横に info アイコンを置き、説明は必要なときだけ吹き出しで出します。

**業界の指針**（決めた振る舞いの根拠）

| 出典 | 指針 |
|---|---|
| Carbon（Tooltip / Toggletip） | 補足の短い説明は hover / focus で出す Tooltip、押して開閉する（Enter / Space・Esc・外側を押すと閉じる）のが Toggletip。**作業に欠かせない情報・入力に必要な条件は隠さない** |
| Inclusive Components「Tooltips & Toggletips」 | 説明だけを持つ info アイコンのボタン（Toggletip）。hover だけではタッチ端末で読めない（押している間しか出ない）。Esc と外側のクリックで閉じる |
| CMS Design System の Tooltip | info アイコンのトリガーで、hover・focus・click のどれでも開き、Esc で閉じる。「作業に必要な情報を吹き出しの操作の裏に隠さない」 |
| GOV.UK の hint / Material の helper text | 入力に必要な説明（形式・条件）は欄の近くに常設する。吹き出しに入れない |
| WCAG 1.4.13 / 2.1.1 / 2.5.8 | 出した内容は Esc で消せ、ポインタを移しても消えず、自分で閉じるまで残る。キーボードだけで開ける。当たり判定は 24px 以上 |
| Apple HIG | タッチ端末にホバーは無い。hover だけに頼った情報はタップで出せるようにする |

**常設の hint と `InfoTip` の使い分け**

| 置き場所 | 書く内容 | 例 |
|---|---|---|
| 常設（`TextField` の `helper`・欄の下の hint・`FieldError`・`FormStatus`・`Banner`） | 作業に欠かせない情報: 入力の形式・必須の条件・押せない理由・エラー・結果・保存したときの影響（索引を作り直さない等） | 「1〜100000 の整数」「AI要件確認を始めるにはクエリを入力してください。」 |
| `InfoTip` | 操作・選択肢がどう振る舞うかの**補足の説明**（知らなくても操作でき、知りたい人だけが読むもの）。とくに、密な操作の行（チップ・選択欄・スイッチの行）のラベルの横 | 回答するモデルの既定の振る舞い、生成方法・購読方式の違い |

- 設定画面のフォームのスイッチ・欄の説明（ラベルの下の helper）は常設のまま（フォームの hint は欄と一緒に読む）。`InfoTip` に移すのは、操作の行のラベルの横に常設していた補足の説明。
- 説明は 1〜3 文。リンク・ボタンなど操作できる要素は入れない（要るときはダイアログや詳細の画面に置く）。

| 振る舞い | 決めたこと | 理由 |
|---|---|---|
| アイコン | `lucide-react` の **`Info`（ⓘ）、16px**、`--color-fg-muted`（ホバー・開いている間は `--color-fg`） | 中身は「この操作の補足の情報」で、使い方の手引き・ヘルプの文書（`CircleHelp` の ? が示すもの）ではない。Carbon の Toggletip も information のアイコン。3 製品で 1 種類にそろえる |
| ボタン | `<button type="button">`、見た目 24px の円（WCAG 2.5.8）。タッチ端末（`pointer: coarse`）では見た目を変えず当たり判定だけ 44px（`pr-touch-target`、§4「タッチ端末の当たり判定」）。フォーカスは共通の `:focus-visible` の outline（offset 0 で円に沿わせ、隣のラベルに重ねない） | キーボードで届き、押せる。行の高さを変えない |
| 読み上げ | 名前は `label`（「回答するモデルの説明」など、何の説明か分かる名前）。説明の文は吹き出しを `aria-describedby` で結び付け、**閉じている間も `hidden` で置いておく**（開閉の状態は読み上げない）。`contentId` を渡すと、説明の対象の欄（`SelectField` の `describedBy`）からも同じ説明を結び付けられる | フォーカスした時点で説明が読まれる。開閉のたびに読み上げを足さない |
| ホバー | ポインタを乗せて **150ms** で出す（Tooltip の 400ms より短い。アイコンは説明を出すためだけにある）。ポインタが離れたら 100ms の猶予の後に閉じ、その間に吹き出しへ移れば消えない | 通り過ぎただけでは出さない。WCAG 1.4.13 hoverable |
| フォーカス | キーボードのフォーカス（`:focus-visible`）ですぐ出し、フォーカスが外れたら閉じる。マウスで押したときのフォーカスでは出さない | 2.1.1。Tab でたどる利用者も読める |
| 押す | クリック・タップ・Enter / Space で**開いたまま固定**する（ポインタが離れても閉じない）。もう一度押す・Escape・外側を押す・フォーカスを外すで閉じる。吹き出しの中を押しても閉じない（文を選べる） | タッチ端末にはホバーが無い（タップだけで開く）。Carbon の Toggletip と同じ閉じ方 |
| タッチ端末 | ホバー・フォーカスでは出さず、タップで開閉する | 指で吹き出しが隠れ、フォーカスとタップが同時に起きる |
| Escape | 吹き出しだけを閉じ、囲むモーダル・メニューに伝えない。フォーカスは動かさない | `Tooltip` と同じ（1.4.13 dismissible） |
| 1 つだけ | `Tooltip` と合わせて、画面に出す吹き出しは 1 つだけ（#655） | 吹き出しを重ねない |
| 見た目 | `Tooltip` と同じ暗い吹き出し（`data-surface="inverted"`・`--color-surface-overlay`・`--shadow-popover`）、12px / **400**・`leading-relaxed`、最大幅 **20rem**（狭い画面は画面幅 − 1rem）。位置・反転・Portal・重なり順も `Tooltip` と同じ | 文で読む説明なので、名前の吹き出し（500・16rem）より広く、行間を空ける |

```tsx
<span className="inline-flex items-center gap-0.5">
  <span className="text-xs font-medium text-fg-muted">{t("chat.compare.label")}</span>
  <InfoTip label={t("chat.compare.infoLabel")} content={t("chat.compare.default")} />
</span>
```

- E2E は、閉じている間を `toBeHidden()`、説明を `toHaveAccessibleDescription(...)`、開いた吹き出しを `contentTestId` で確かめる。タッチ端末の project ではホバーの代わりに `tap()` する。info アイコンの名前は「<ラベル>の説明」なので、同じ行のボタンを `getByRole("button", { name: "<ラベル>" })` で引くときは `exact: true` を付ける（部分一致で 2 つに当たる）。

### フォーカスの表示（新設）— ★ outline に一本化（#355）

- **フォーカスの表示は outline 1 つ。** 既定はグローバルの `:focus-visible`（`tokens/base.css`、2px の `--color-focus-ring`、`outline-offset: 2px`）が出す。部品・製品はフォーカスのために何も書かないのが基本。
- グローバルの `:focus-visible` は **`@layer base`** に置く。レイヤーの外に置くと Tailwind のユーティリティ（utilities レイヤー）に常に勝ち、部品の `focus-visible:-outline-offset-2` 等の調整が効かず、`focus-visible:outline-none` + `ring` を書いた箇所で outline と ring が二重に表示されていた。
- 形を変えるときは outline のユーティリティだけを使う: 内側に描く（スクロール領域の端で切れる行・タブ）は `focus-visible:-outline-offset-2`、詰めた部品は `focus-visible:outline-offset-1` など。
- **ring（box-shadow）をフォーカスの表示に使わない。`focus:` / `focus-visible:` で outline を消さない。** adherence の lint が `focus:ring-*` / `focus-visible:ring-*` / `focus-within:ring-*` / `peer-focus-visible:ring-*` / `focus(-visible):outline-none` を検出する（フォーカス以外の ring、例えば選択中の強調の `ring-2` は対象外）。
- **テキスト入力（input / textarea / select）** は例外で、枠線の色 + 内側 1px の影（`tokens/base.css`、レイヤーの外で常に優先）。label や隣の要素に outline が重ならないようにするため。入力欄に ring を足さない（影で上書きされて効かない）。
- **中の要素が見えない複合部品**（`sr-only` のファイル入力を包むドロップゾーン、枠の中に枠なしの入力欄を置く検索欄・コンボボックス）は、外枠に `focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-focus-ring`（`sr-only` の入力の隣の要素は `peer-focus-visible:outline-*`）を付ける。中の要素（チェックボックス・ラジオ）が見える場合は、外枠に付けない（中の要素の outline と二重になる）。
- `prefers-contrast: more` では 3px（`tokens/a11y.css`）、強制カラーモードでは `CanvasText` の輪郭。

### カード内の操作行（新設）— ★ 主操作と破壊的操作の配置

ページヘッダー（右寄せグループ、右端が primary）とダイアログ以外で、カード・フォーム・パネルの中の操作ボタンをどこに置くかの規約です。**主操作と破壊的な操作を隣に並べない**（誤操作防止。NN/g、Apple HIG）ことが、すべての型に共通する原則です。

| 場面 | 置き方 | 例 |
|---|---|---|
| カード末尾の主操作 | 区切り線（`border-t`）の下の操作行に**左寄せ**。並びは primary → secondary（読む順） | 保存 / 評価を開始 |
| 同じ対象について「確定するか、捨てるか」の二者択一の破壊的操作 | **同じ操作行の反対の端（右端、`sm:ml-auto`）**。`ghost` + `tone="danger"`。確認語が要らない操作は確認語欄の中に置かない | 合成データの「確認したデータを適用」（左）と「確認用データを破棄」（右端） |
| めったに使わない影響の大きい操作 | カード末尾の**「危険な操作」区画**に分ける（見出し + 影響の説明 + 確認語 + danger ボタン）| すべて再作成 / 構成を解除（GitHub の Danger Zone と同じ型） |
| 一覧の行・詳細ヘッダーの破壊的操作 | 行内に文字ボタンを並べず、**「その他の操作」メニュー**に入れ、区切り線の下に置く | 削除 / アーカイブ / 無効化 |
| バックグラウンドの job を止める「中止」 | **進捗表示のそば**（状態の見出しや進捗ストリップの右側）。開始のボタンとは別 | 評価の中止 / 構築の中止 |
| その場で結果を待つ操作の「停止」（#413） | **起点のボタンそのもの**。実行中は同じ位置・同じ要素のまま `secondary` の「停止」（`Square`）に切り替える。押せない実行（`loading`）と停止を並べない（UX 契約 buttons.md §3.1） | 検索 / チャットの送信 / 検索テスト |

- **狭い画面（640px 未満）** では操作行を縦に並べ、**主操作を上、破壊的操作を下**にする（`flex-col` → `sm:flex-row`）。各ボタンは `w-full sm:w-auto`。
- 破壊的操作の確定は、必ず `ConfirmDialog`（実際の動詞のラベル）または確認語で行う。配置だけで安全を担保しない。
- ページヘッダーは右寄せのため並びが逆（左端 danger → 右端 primary）になるが、「primary を最も押しやすい位置、danger を primary から最も遠い位置」という考え方は同じ。

**確認語欄（実行確認語）の色**
- 入力前は danger 色を使わない。ラベルは `--color-fg`、説明文は `--color-fg-muted`。操作前から赤いと、エラーが起きているように見えるため。
- 一致しない語を入力したとき（`aria-invalid="true"`）だけ、説明文と状態バッジを danger 色にする。
- 確認語欄であることは、見出し・状態バッジ（未入力 / 不一致 / 確認済み）・入力欄のフォーカス色で示す。

### `ExecutionConfirmationField`（新規）— ★ 確認語欄は 1 つの実装（#379）

破壊的な操作の実行確認語（type-to-confirm）の入力欄を `packages/ui` に置きました。NL2SQL の製品のコードと system-settings のコピーの 2 つに分かれていた実装を一本化し、Agent のスナップショットの置換の確認語も置き換えました。見た目と振る舞いは NL2SQL の実装が基準です（上の「確認語欄（実行確認語）の色」）。

- 一致の判定（`confirmed`）・確認語（`expectedLabel`）・説明（`helper`）・操作（`actions`）は製品が渡す。文言は `labels` で差し替え、未指定は既定（実行確認語 / 必須 / 入力条件: {phrase} / 未入力 / 不一致 / 確認済み）
- 製品で確認語の入力欄を手書きしない。props と実装の参照は `components-reference.md`「ExecutionConfirmationField」

### `Disclosure`（新規）/ `DisclosureChevron`（変更）— ★ 開閉できる領域は 1 つの実装（#397）

開閉できる領域（「処理の詳細(診断)」・技術詳細・SQL・根拠の一覧など）を、製品が `<details>` / `<summary>` と CSS（`list-none` / `[&::-webkit-details-marker]:hidden`）と Chevron の組み合わせで手書きしていました。RAG の多くは `summary` に `display: flex` を当てたためブラウザの三角が消え、代わりの Chevron も無く、**押せる見出しなのか静的な枠なのか、いま開いているのか**が見分けられませんでした。Chevron の形・位置・向きも 3 種類（右端の `ChevronDown` を 180° 回す / 先頭の `ChevronRight` を 90° 回す / 右端の `DisclosureChevron`）に分かれていました。`<details>` を包む共有の `Disclosure` を入れ、製品の手書きを置き換えます。

```jsx
import { Wrench } from "lucide-react";

// 枠付きの面（既定）。見出しの行全体が押せ、右端の Chevron が開閉の状態を示す。
<Disclosure summary="処理の詳細(診断)" icon={Wrench} open={open} onOpenChange={setOpen}>…</Disclosure>
// 件数・状態バッジは meta（Chevron の左）。補足は description（見出しの下）
<Disclosure summary="見出し" meta={<span className="tnum text-xs text-fg-muted">12</span>} surface="sunken">…</Disclosure>
// 枠なし（回答の根拠・表のセルの中）。Chevron は見出しの直後
<Disclosure variant="plain" size="sm" summary="分析">…</Disclosure>
// 状態色の区画（警告の一覧・危険な操作）
<Disclosure tone="danger" summary="構成を解除" description="…">…</Disclosure>
```

| 決めたこと | 理由 |
|---|---|
| **ネイティブの `<details>` / `<summary>` を包む**（button + region を自作しない） | Enter / Space・読み上げの「展開 / 折りたたみ」の状態・ページ内検索での自動展開（Chromium の hidden until found）をブラウザが持つ。APG の Disclosure パターンと同じ状態が伝わり、`aria-expanded` / `aria-controls` を書き忘れる余地がない |
| **開閉の状態は必ず `DisclosureChevron`**。折りたたみ = **右向き**、展開 = **下向き**（90° 回転、200ms ease-out、`prefers-reduced-motion` で止める） | 共有 `Sidebar` のセクション・ブラウザ標準の ▸ / ▾ と同じ向き。旧 `DisclosureChevron` は折りたたみが左向きで、Sidebar（右向き）と逆だった。回転の向きの変化で「その場で下に開く」ことを示す |
| Chevron の向きは部品の状態（React）から決める（`group-open` の CSS に頼らない） | `group-open` は祖先のどの `<details>` にも反応するため、外側が開いているだけで内側の閉じた Chevron が下向きになっていた（NL2SQL のオントロジー構築の工程表の中の工程など） |
| クリック・Enter / Space は部品が状態を切り替える（ブラウザの切り替えを止める）。受控（`open` + `onOpenChange`）/ 非受控（`defaultOpen`）のどちらも使える。ページ内検索でブラウザが開いたときは `toggle` で状態に戻す | React の状態を正本にし、受控の `open` と DOM の `open` をずらさない（ブラウザに切り替えさせると、親が `onToggle` で状態を戻さない限り DOM だけが開閉する） |
| **見出しの行全体が押せる**。`card` は高さ `--button-height-lg`（40px。タッチ端末は 44px）、左右 `--space-3`、hover で `--color-surface-hover`（状態色の面では文字色を 5% 重ねる） | 押せることを hover でも示す。当たり判定は Button と同じトークン |
| フォーカスは outline（`card` は枠の内側 `-outline-offset-2`）。ring を使わない | §4「フォーカスの表示」。枠線と輪郭を重ねない |
| `card` は開くと見出しと内容の間に区切り線（`border-t`）。内容の余白は `p-3`（`contentClassName` で上書き） | どこまでがこの領域の内容かを示す |
| 見出しに操作できる要素（ボタン・リンク）を置かない。`meta` は件数・バッジだけ | `<summary>` の中の操作は押し間違いと読み上げの混乱を招く（HTML の仕様でも対話的な内容は置けない） |

- **製品で `<details>` を手書きしない。** adherence の lint が JSX の `<details>` を検出します（#397）。`Disclosure` で表せない所（見出しを表の列にそろえる「展開できる表の行」、工程名と状態を 1 行に並べる工程表の行）だけ、理由を添えて局所的に除外し、状態は `DisclosureChevron` で示します。
- **button + region の開閉**（見出しの外に置くボタン・一覧の行の開閉・「全文表示」など）は `Disclosure` を使わず、`Button`（または `button`）に `aria-expanded`（と `aria-controls`）を付け、**`DisclosureChevron expanded={open}` を必ず置きます**。「＋ / −」の文字や、向きの変わらない `icon={ChevronDown}` で開閉を示さない。
- メニューを開くボタン（「その他の操作」）の Chevron も同じ `DisclosureChevron` です（閉 = 右向き / 開 = 下向き）。

### `StatusBadge`（変更）

- **アイコンを必須化**（`icon` 既定 `true`）。success / danger の輝度がほぼ同じで色覚型によって見分けられないため、**形で冗長に符号化**します。強制カラーモードでも意味が残ります
- `pending` バリアントは **非推奨**（旧実装で `warning` と**完全に同値**でした）。互換のため `VARIANTS.pending = VARIANTS.warning` を残していますが、呼び出し側は `warning` に置換してください

### 必須の表示（変更・#531）— ★ 必須の欄だけに「必須」。必須は「情報」であり「状態」ではない

必須の欄の見せ方を、3 製品と system-settings のすべてのフォームで次の 1 通りにします。`TextField` / `SelectField` / `SecretField` は `required` だけで出し（`requiredLabel` の既定が「必須」）、それ以外の入力は `FieldLabel` / `FieldLegend` / `Fieldset` で出します（components-reference「必須の表示」）。

```jsx
<TextField id="user" label="ユーザー名" required />
// textarea・ファイル選択・独自の入力
<FieldLabel htmlFor="sql" label="SQL" required />
<textarea id="sql" aria-required="true" />
// チェックボックスの群・複数選択（ラジオは role="radiogroup"）
<Fieldset legend="ロール" required error={rolesError}>…</Fieldset>
// 条件付きの必須だけ文言を上書きする
<TextField id="region" label="リージョン" required requiredLabel="OCI 運用時必須" />
```

| 決めたこと | 理由 |
|---|---|
| **必須の欄だけ**、ラベルの後ろに中立色のテキストタグ「必須」を付ける | 必須だけに印があれば、印の無い欄が任意だと分かります。ラベルの直後に置くのは、欄を読む順（ラベル → 必須 → 入力）で見落とさないためです |
| **記号 `*` ではなくテキスト「必須」** | `*` は意味を伝える凡例文（「* は必須入力項目です」）が要ります。しかし利用者は凡例を読みません。小さな記号は弱視の利用者に見落とされ、色（赤）に頼ると色覚の違いで伝わりません（NN/g「Required Fields」、WCAG 1.4.1）。GOV.UK Design System はアスタリスクを使わず、デジタル庁デザインシステムも入力欄のラベルにテキストの「※必須」を付けます。日本語の業務 UI では「必須」の文字が期待されます |
| **任意の欄には何も付けない。placeholder・ラベルに「(任意)」を書かない** | 印を 2 種類にすると、どちらが基準か迷わせます。placeholder は入力を始めると消え、コントラストも低く、ラベルの代わりになりません（WCAG 3.3.2 Labels or Instructions）。lint が placeholder の「任意」と「(任意)」「（任意）」を検出します |
| **状態色（warning / danger）を使わない。** 文字 `--color-fg-muted`、輪郭 `--color-border-strong`（装飾）、地は塗らない | 状態色は**利用者の対応が要る状態**の信号です。必須は操作前から決まっている項目の属性なので、状態色で出すと、フォームを開いた瞬間に注意表示が並んでしまいます。そうなると、本物の警告やエラーが埋もれます。未入力で送信したときの `FieldError`（danger）だけが状態色を使います |
| 文字コントラスト 4.5:1 以上 | 実測: light は surface 上 4.83:1、sunken 上 4.55:1。dark は surface 上 8.72:1、overlay 上 7.06:1。淡い灰色の必須表示は弱視の利用者が見落とします（NN/g） |
| 形は pill（`--radius-pill`）、`ring-inset` の輪郭で行の高さを変えない | バッジの角丸トークンに合わせます。アイコンは付けません。`StatusBadge`（状態 = アイコン必須）と区別するためです |
| **支援技術には `aria-required="true"`（またはネイティブの `required`）で伝え、タグは二重に読み上げない** | 入力の名前・役割・状態をプログラムで伝えます（WCAG 4.1.2 Name, Role, Value）。`TextField` / `SelectField` / `SecretField` / `FieldLabel` のタグは `aria-hidden` です（「必須、必須」を防ぐ）。fieldset（role=group）は `aria-required` を持てないため、`FieldLegend` / `Fieldset` のタグは読み上げます（群の名前「ロール 必須」として 1 回だけ読まれる）。`role="radiogroup"` の `Fieldset` は群に `aria-required` を付け、タグを読みません |
| 条件付きの必須も同じタグで、文言で区別する（例:「OCI 運用時必須」） | info 色のバッジで別扱いすると、必須表示が 2 種類になります |
| **未入力のエラーは欄の直下に「〇〇を入力してください。」「〇〇を選択してください。」**。保存時は最初のエラーの欄へフォーカスする | 何が足りないかと直し方を、問題の欄のそばで伝えます（WCAG 3.3.1 / 3.3.3、GOV.UK「Error message」）。文言の型とフォーカスは UX 契約 `messaging.md` §3.2.1 |

- **`requiredLabel` は渡さなくてよくなりました**（既定「必須」）。以前は呼び出し側が必ず渡す作りで、渡し忘れた欄は見た目で必須が分かりませんでした。条件付きの必須の文言だけ上書きします
- ネイティブの `required` 検証は `TextField` / `SelectField` に付けません。未入力の検出と `FieldError` の表示はアプリ側で行います（従来どおり。`SecretField` と `ExecutionConfirmationField` はネイティブの `required` も付けます）
- 必須の欄かどうかは、backend の検証（Pydantic の必須フィールド・`min_length`）か画面の送信ガード（未入力では保存できない）で決まっている欄だけです。表示のために必須を増やしたり減らしたりしません
- 必須表示を**アプリで再実装しない**でください（赤い `*`・warning バッジ・info バッジが 3 アプリに混在していました）。製品で `RequiredBadge` を直接ラベルに並べることも、adherence の lint が検出します（「必須」の span / `StatusBadge` / `t("common.required")` の直接表示・手書きの `*` も）

### `TextField` の先頭アイコン・後置スロット（変更）— ★ 検索欄を手書きしない（#384）

検索欄などの「アイコン付きの入力欄」を、製品が `relative` + 絶対配置の `Search` + `<input className="pl-9">` で手書きしていました（RAG 5・NL2SQL 3・system-settings 1 の 9 箇所）。高さが 31.5 / 35 / 38.5 / 44px、アイコンの位置が 0.625 / 0.75rem、地が surface / surface-sunken とばらばらでした（14px ルートでは `h-9` = 31.5px・`h-11` = 38.5px で、どのコントロールの高さのトークンにも合いません）。`TextField` にスロットを足し、共有部品で作ります。

```jsx
import { Search } from "lucide-react";

// 一覧の絞り込みの検索欄は TextField ではなく SearchField で作る（下の「`SearchField`」、#535）。
// TextField の先頭アイコン・クリアは、重い検索の質問欄や、単位・件数を後ろに置く入力欄に使う。

// lg の Button と同じ行: size="lg"（同じ行の部品は同じ size。#613 で touchTarget は削除）
<TextField id="search-query" label="RAG 検索" labelHidden size="lg" leadingIcon={Search} … />
```

| 決めたこと | 理由 |
|---|---|
| **高さはトークンだけ。** 既定は `md`（36px）。同じ行に `lg` の Button を置くときは `size="lg"`、密な行は `size="sm"`（`--control-height-*`、タッチ端末は 44px）。Button の `size` と同じ段（#613。`touchTarget` は削除） | 並ぶ操作部品と上端・下端がそろう。製品で `h-9` / `h-11` を書かない |
| 先頭アイコン（`leadingIcon`）は 16px（`--icon-md`）、左 `--space-3`、`--color-fg-muted`（無効の入力欄では `--color-fg-disabled`）。`aria-hidden`、`pointer-events: none` | 読み上げは label が担う。アイコンを押しても下の入力欄にフォーカスが入る |
| 文字の開始位置 = `--space-3` + `--icon-md` + `--button-gap`（34.5px） | アイコン付きの Button と同じ、アイコンと文字の間の 8px |
| クリア（`onClear` + `clearLabel`）は、**値があるときだけ**末尾に `ghost` の `iconOnly` Button（`X`）を出す。入力欄の枠線の内側に、入力欄の高さの正方形で置き、外側の角だけ `--radius-control` − 1px | 空の欄に押せない × を出さない。ホバーの地が入力欄の枠線に重ならない。`iconOnly` なので既定で Tooltip が出る |
| クリアボタンは入力欄の直後の Tab 順。押すと値を消して**入力欄にフォーカスを戻す**。ポインタで押したときは入力欄からフォーカスを外さない（`mousedown` を止める） | ボタンは値が空になると消えるので、戻さないとフォーカスが body へ落ちる。blur で検索語を確定する一覧（RAG のファイル一覧・ナレッジベース）が、消す前の値を確定しない |
| Escape でも消す。値があるときだけで、IME の変換中は消さない。消したときは囲むダイアログ・メニューに Escape を伝えない | 空の欄の Escape はダイアログを閉じる操作として残す。変換の取り消しと取り違えない |
| `type="search"` のブラウザ既定のクリア（`::-webkit-search-cancel-button`）を出さない | Chromium・Safari にしか無く、キーボードで届かず、読み上げ名を訳せない。共有のクリアと二重にしない |
| `trailing` は任意の要素（単位・件数・ボタン）。実際の幅（`ResizeObserver`）だけ文字の右の余白を空ける | 幅の決まらない要素でも文字と重ならない |
| `labelHidden` は label を `sr-only` にする。**検索欄でだけ使う**（フォームの入力欄では使わない） | 見出しの無い toolbar の検索欄。プレースホルダをラベルの代わりにしない |

- **adherence の lint が、アイコンの分の左の余白（`pl-7`〜`pl-12` / `ps-*` / `pl-[…]`、variant 付きを含む）を持つ `<input>` を検出します。** 検索欄は `TextField` で作ってください。
- 対象外（手書きのまま）: 枠の中に枠なしの入力欄を置く**複合部品**（NL2SQL のオントロジーのグラフのツールバーの検索欄）。外枠に `focus-within:outline-*` を付ける型（§4「フォーカスの表示」）で、`pl-*` を使いません。

### `TextareaField`（新規）— ★ 複数行の入力欄は 1 つの実装（#584）

3 製品が `<textarea>` を手書きしていました（RAG 17・NL2SQL 28・Agent 11・system-settings 1）。地が `bg-surface` / `bg-surface-sunken`、角丸が `rounded-md`（`--radius-control` ではない）、disabled が `opacity-50` / `opacity-60` / `bg-surface-hover`、ラベル・必須・エラー・文字数の付け方も画面ごとに違いました。`TextField` と同じ見た目と API の `TextareaField` にそろえます（props・決めたことは components-reference.md「TextareaField」）。

- 枠線・角丸（`--radius-control`）・地（`bg-surface`、read-only は `bg-surface-sunken`、disabled は `bg-surface-disabled`）・フォーカスは `TextField` と同じ。SQL・JSON などは `monospace`（`--font-mono` の 12px）。
- ラベル（`label` / `labelHidden`）・補足（`helper`）・必須（`required`）・エラー（`error`）・文字数（`showCount`）は `TextField` と同じ位置と読み上げ。
- adherence の lint が製品の JSX の `<textarea>` を検出する。例外（理由を添えて局所的に除外）は、RAG の `ReviewTextEditor.tsx` の元の文書の要素・表のセルを再現して編集するエディタ（ラベルにバッジを並べる・枠を表が持つ）と、NL2SQL のフィードバック管理の「管理者レビューコメント」（ラベルの行に「利用者コメントを反映」ボタンを並べる）の 3 か所だけ。
- 条件付きの必須（「「違う」のとき必須」など）は `requiredAnnouncedByControl={false}` で、`aria-required` を付けずにタグをラベルの一部として読ませる（`FieldLabel` と同じ）。

### `SearchField`（新規）— ★ 一覧の絞り込みは入力に合わせて、検索ボタンを置かない（#535）

一覧を絞り込む検索の操作が画面ごとに違いました。RAG の検索・回答プロファイルの一覧は検索欄の隣に「名前・説明で検索」のボタンがあり、ボタンか Enter で検索、ナレッジベース・文書の一覧は Enter か blur で検索、フィードバックは入力に合わせて（300ms）、NL2SQL は入力のたびに（一部は各ページの 250ms の debounce）、学習候補・アプリ内フィードバックは「絞り込み」ボタン、Agent のメモリは入力のたびに問い合わせていました。日本語入力の変換中の読み（「じ」「じん」…）でも問い合わせる画面がありました。`TextField` を包んだ `SearchField` にそろえます。規則（どの検索を入力に合わせるか・明示実行にするか・例外）は UX 契約 [page-archetypes.md「一覧の絞り込みの検索」](../ux-contracts/page-archetypes.md#一覧の絞り込みの検索535)。

```jsx
import { SearchField } from "@engchina/production-ready-ui";

// value には作業状態に保存している「適用中」の検索語を渡す。入力中の文字は部品が持つ。
<SearchField id="search-answer-profile-search" label="名前・説明で検索" labelHidden
  value={q} onSearch={(next) => setView({ ...view, q: next, offset: 0 })}
  clearLabel="検索語をクリア" resultCountLabel={`${total} 件が一致しました`}
  placeholder="名前・説明で検索" className="w-full sm:w-64" />
```

| 決めたこと | 理由 |
|---|---|
| `type="search"`・先頭の `Search`（16px）・値があるときの消去（×、Escape でも消す）は `TextField` のスロットをそのまま使う。見た目（高さ・角丸・地・アイコンの位置）は `TextField` と同じ | 検索欄の見た目を 1 つにする（#384 のまま） |
| **入力が止まって 300ms**（`SEARCH_FIELD_DEBOUNCE_MS`）で `onSearch` を呼ぶ。**Enter はすぐ**呼び、囲む form を送信しない。消去もすぐ | 1 打鍵ごとに問い合わせない。待たずに確定したい利用者は Enter で確定できる（NN/g・Material 3 の「入力に合わせた絞り込み」） |
| **IME の変換中は呼ばない。** `compositionstart` で待っている分を取り消し、`compositionend` で確定した値を予約する。変換を確定する Enter（`isComposing`、Safari の `keyCode 229`）では呼ばない | 未確定の読みで一覧を絞らない。確定の Enter を検索の Enter と取り違えない |
| `onSearch` には正規化した値（既定は `trim`、`normalize` で変えられる）を渡し、前回と同じなら呼ばない | 空白を足しただけ・Enter を押し直しただけでページを 1 ページ目へ戻さない・選択を解除しない |
| `value` が外から変わったとき（条件のリセット・作業状態の復元）だけ入力欄を合わせ、待っている分を捨てる。自分が渡した値の戻りでは入力欄を書き換えない | 入力中の文字を親の値で上書きしない |
| 入力欄が外れるとき（一覧 ⇄ 作成の切り替え・読込中の表示）に待っている分があれば、その場で確定する | 消した・入力した検索語が、一覧に戻ったときに元に戻らない |
| `resultCountLabel` は、検索語があるときだけ入力欄の後ろの `role="status"`（`aria-live="polite"`、`sr-only`）に入れる。領域は最初から置く | 絞り込んだ結果を画面を見ずに知る（WCAG 2.2 SC 4.1.3）。後から足した live region は読まれないことがある |
| `formatInput` で入力中の文字の見せ方を変えられる（例: 所有者名を大文字）。IME の変換中は変えない | 未確定の文字を書き換えない |
| 既定で `autocomplete="off"`・`enterkeyhint="search"` | ブラウザの入力履歴の候補を一覧の上に重ねない。モバイルのキーボードの Enter を「検索」にする |

- **adherence の lint が、`type="search"` の `TextField` / `<input>` を検出します。** 一覧の絞り込みは `SearchField` で作ってください。重い検索（LLM・ベクトル検索・SQL の生成）の質問欄は `type="search"` にせず、`TextField` と明示的な実行（ボタンと `isSubmitEnter` の Enter）で作ります。
- IME 対応の Enter の判定 `isImeComposing` / `isSubmitEnter` も `@engchina/production-ready-ui` から使います（RAG の `@/lib/keyboard` は再 export）。

### 操作部品の角丸（新設）— `--radius-control`（#384）

| トークン | 値 | 使う部品 |
|---|---|---|
| `--radius-control`（utility `rounded-control`） | **6px**（px。ルート非依存） | 入力欄（`TextField` / `SecretField`）・`SelectField`・`Button`・`Pagination` の「N / M ページ」 |
| `--button-radius` / `--input-radius` | `var(--radius-control)` の別名 | 旧名。新規コードは `rounded-control` |

- 3 つとも同じ値にします。以前は Button が 6px、入力欄・SelectField が `rounded-md`（0.375rem = 5.25px）で、隣に並べると角の形が違いました。高さ（`--button-height-*` / `--field-height`）と同じく px にし、ルートの文字サイズで変わらないようにします。
- 入力欄の中に置くボタン（クリア・`SecretField` の表示の切り替え）は、内側の角を `rounded-l-none`、外側の角を入力欄と同じ（枠線の内側なら 1px 小さく）にします。
- カード・バナー（`--radius-lg`）、ダイアログ（`--radius-xl`）、バッジ・チップ（`--radius-pill`）は操作部品ではないので別の値のままです。
- `cn()`（tailwind-merge）に `rounded-control` を角丸として登録しているので、呼び出し側の `rounded-none` / `rounded-full` などで上書きできます。

### `DataTable`（変更）

- **ソートの当たり判定を `<th>` 全体に**（`.pr-sort-header`）。旧実装は `<button>` が文字高（約15px）しかなく、**24px 最小タップ領域も割っていました**。hover も無く押せると分かりませんでした
- `wordBreak: "break-word"` を廃止 → 日本語が任意の文字で分断される問題を解消
- ヘッダーの地を `--color-surface-sunken` に
- 見出しセルは折り返さない。並べ替えボタンの高さは `--button-height-sm`（タッチ端末 44px）
- 一覧用の optional props（platform #56）: `stickyHeader`、`visibleRows`（表頭 + 先頭 N 行の実測高さで内部スクロール）、`scrollAriaLabel`（キーボードでスクロールできる region）、`selectedRowKey` / `isRowSelected`、`rowProps`、`renderRowDetail`、列の `rowHeader`。詳細は `components-reference.md`。**アプリで `<table>` を手書きしない**（例外は、元の文書の表を再現して編集する見出しなしのグリッドだけ。#129）

### `RowTitleButton`（新規）— ★ 一覧の行の題名のボタンは 1 つの実装（#421）

一覧の行を選んで詳細を開く（B 型）・エディタを開く（A 型）ときの、先頭セルの対象名のボタンです。行のクリック（`DataTable` の `onRowClick`）はマウスの補助で、キーボード（Tab → Enter / Space）の導線はこのボタンが持ちます（UX 契約 page-archetypes §0-7）。RAG と Agent がそれぞれ `EntityLayout` に同じ部品を持ち、NL2SQL と system-settings は手書きしていたものを `packages/ui` に一本化しました。

| 項目 | 決定 | 理由 |
|---|---|---|
| 選択の状態 | `current` で **`aria-current="true"`**。見た目の選択（淡アクセント面 + 左バー）は行（`selectedRowKey`）やカードが持ち、ボタンの見た目は変えない | APG: 押しても解除しない「詳細に表示中の項目」なので `aria-pressed`（トグル）ではない。開閉する領域を持たないので `aria-expanded` でもない。`aria-selected` は grid / listbox の役割が要る。`aria-current` は行（`DataTable`）と同じ意味で、Tab で届いたボタンでも「現在の項目」と読まれる。選択の見た目を行に 1 つだけ持たせ、アクセントを「現在の項目」の印に限る |
| 見た目 | 文字だけのボタン。題名は 14px / 500 / `--color-fg`、ホバーで下線（`underline-offset-2`）。補足（`subtitle`）は 12px / `--color-fg-muted` | 3 製品の多数（RAG・Agent）の見た目。題名を常にアクセント色にすると、選択行のアクセントと区別しにくい。押せることは行の hover の地・pointer・下線で示す |
| 長い題名 | 既定は切り詰めず、単語の途中でも折り返す（`overflow-wrap: anywhere`）。`maxLines`（1〜3）で切り詰めたときは、**実際に切り詰められているときだけ**ホバー（400ms）とキーボードのフォーカスで全文の `Tooltip` を出す。ただし先頭 120 文字まで（超えたら「…」） | 折り返しを優先し、省略するなら全文を見る手段を持たせる。Tooltip は短い文の部品なので、メモ・質問などの長い全文は詳細で見せる（吹き出しが一覧を覆わない）。読み上げ名には全文が入っているので、吹き出しは説明として結び付けない（`Tooltip` の `describe={false}`、`aria-hidden`） |
| フォーカス | グローバルの `:focus-visible`（outline 2px、offset 2px）。部品は何も書かない | §4「フォーカスの表示」 |
| タッチ端末 | `pr-touch-target` で、見た目の大きさを変えずに当たり判定を 44px 以上にする | §4「タッチ端末の当たり判定」。行全体も押せるが、カード一覧（375px）では題名が主な的になる |
| 使えないとき | `disabled`（`--color-fg-disabled`、`cursor: not-allowed`、下線なし） | NL2SQL の対象の選択で、選べないオブジェクトを示す |

- 製品は `current`・`aria-label`（「〜の詳細を表示」など、行の中で何が起きるかを含める）・`onClick` を渡すだけにする。`<button>` で題名を手書きしない。
- 題名に識別子の表示部品（NL2SQL の `IdentifierText` / `DbObjectName`、system-settings の ID）を渡してよい。色は部品の既定（`--color-fg`）にそろえ、題名の中でアクセント色を付けない。
- 一覧から別のページへ移る（ナレッジベース・文書の一覧など）ときはリンク（`<Link>`）のままにする（役割が違う）。
- **URL で開く対象（A 型のエディタの `?id=`・NL2SQL の `?profile=` など）は `href` を渡してリンクの形にする（#583）。** `<a href>` になり、Ctrl / ⌘ / Shift + クリック・中クリック・コンテキストメニューで新しいタブ・ウィンドウに開ける。修飾キーの無いクリック（Enter を含む）は既定の遷移を止めて `onClick` を呼ぶ（画面内で開き、ページを読み直さない。離脱の確認など画面の処理をそのまま通す）。見た目・当たり判定・`current` はボタンの形と同じで、`disabled` のときはボタンのまま。行のクリック（`onRowClick`）はリンクのクリックを重ねて扱わない。選択して詳細を切り替えるだけ（B 型の分割ペイン）はボタンのまま（URL を持たない・新しいタブで開く意味が無い）。
- 分割ペイン（`FixedSplitPane`）は既に共有部品で、製品の `RagSplitPane` / `AgentSplitPane` は保存 key の接頭辞と文言を渡す薄いラッパーなので、`packages/ui` には上げない。

### `ListToolbar` / `ListPicker` / `LoadMoreFooter`（新規）— ★ 検索欄は左、大量の候補は一覧で選ぶ（#600）

一覧の上の検索欄の位置が製品ごとに違いました（RAG は右端に固定幅、NL2SQL は左に 2:1、platform・Agent は全幅）。数千〜数万件の候補から選ぶ画面は、RAG の「文書を追加」が検索欄 + 選択欄 +「追加」で 1 件ずつ、NL2SQL の「許可する表・ビュー」は手書きのチェックボックスの一覧と仮想スクロールでした。規則は UX 契約 [page-archetypes.md「一覧のツールバー」「大量の候補から選ぶ」](../ux-contracts/page-archetypes.md)。

| 決めたこと | 理由 |
|---|---|
| `ListToolbar`: 左（2）に検索欄（先頭・残りを埋める）→ 絞り込み、右（1）に件数 → 一覧への操作（主操作は右端）。2:1 にするかはツールバー自身の幅（container query、48rem）で決め、狭いと縦に積んで検索欄を先頭に全幅 | 読む順と Tab の順の先頭に「見る範囲を決める」操作を置く（Atlassian・GitHub・Polaris と同じ向き）。横に並べたパネルの中でも同じ規則で崩れない。§4「wide 画面の 100% 充填」の比率配分 |
| `ListPicker`: ツールバー（検索）→ 選択の行（一括選択・「選択中だけ表示（K）」）→ 候補の一覧 → フッター（件数・さらに読み込む）→ 確定の操作行（任意） | NL2SQL の型を共通にした。選んだ候補は検索語を変えても残り、確かめられる |
| 候補の一覧は選択肢の listbox（`aria-multiselectable`・`aria-checked`・`aria-posinset` / `aria-setsize`、フォーカスは listbox に 1 つで `aria-activedescendant`）。グループは見出し（件数・一括選択のボタン）と listbox を分ける | Tab 1 回で一覧を抜けられる（候補の数だけ Tab を押させない）。listbox の中にボタンを置かない（ARIA の子の制約） |
| 行は 3.5rem、一覧は 5 / 8 行（md 未満 / 以上）。100 行を超えたら見えている行だけを描く | 数千件を読み込んでも重くならない。少ないときはページ内検索・読み上げで全部に届く |
| チェックの印は `--color-accent-emphasis` の塗り + `Check`（14px）、選んだ行は `--color-accent-subtle`。キーボードの位置は、キーボードで操作しているときだけ行の内側に outline（`--color-focus-ring`） | 選択の状態を色と形の両方で示す。マウスで選んだときに枠を出さない |
| 選べない候補（例: 追加済み）は `aria-disabled` と右端の理由の文言。一覧全体の `disabled`・囲む `<fieldset disabled>` の間は切り替えない | 色だけに頼らない。保存中に選択が変わらない |
| `LoadMoreFooter`: 左に件数、右に「さらに読み込む」、失敗は再試行付きの `Banner`（スピナーは 1 つ） | NL2SQL の `DbObjectSelectorFooter` を共通にした（NL2SQL は包むだけ） |

### 読み込み中と一覧の表示密度（新設、#265）— ★ 3 製品で NL2SQL の基準にそろえる

- 一覧の表示行数・行の高さは `packages/ui` の定数を使う（`INFORMATION_TABLE_VISIBLE_ROWS` = md 未満 5 行・md 以上 8 行、`INFORMATION_TABLE_ROW_CLASS` = 3.5rem など）。製品で数値を書かない
- `Skeleton` の見た目は 1 つ（`SKELETON_CLASS`: `--color-surface-hover` の地、`prefers-reduced-motion` で点滅しない）。形のある `TableSkeleton` / `ListSkeleton` / `FormSkeleton` を `TimedLoadingState` の子に置き、読み込み後の寸法を予約する
- ページングは共通の `Pagination` / `usePagination`（10 件）。サーバー側は `offsetPagination` / `offsetForPage`、ページ番号の保持は `usePagination` の `page` / `onPageChange` / `resetKey`
- API と標準の組み合わせは `components-reference.md`「読み込み中と一覧の表示密度」

### `AppShell`（変更）

- 外枠は `fixed inset-0` で視口に収め、本文は `main#pr-main` の中でスクロールする（#855）。document 側に第2の縦スクロールを作らない。長い一覧・詳細・設定の本文は切り捨てず、main のスクロールを維持する。
- **スクロールバーによる幅変更を防ぐ（#867）**: `AppShell` の本文・ナビ、`DataTable`、一覧・候補選択、SideSheet・Dialog の縦スクロール領域には `scrollbar-gutter: stable` を指定する。通常の scrollbar が出たり消えたりしても本文幅・折り返し・表示行数の測定を変えない。overlay scrollbar では余白は増えない。横だけをスクロールする Tabs や操作部品には追加しない。表の動的な高さは利用可能な内幅を一定にしたうえで測定する。
- チャットは会話の内部スクロールと入力欄を分ける。自動移動・引用への移動は会話コンテナの `scrollTo` で行い、祖先まで動かす `scrollIntoView` を使わない。狭い画面では会話の高さを制限し、長い回答でページを無限に伸ばさない。

- **`.pr-skip-link`（本文へスキップ）と `<main id="pr-main" tabIndex={-1}>` を出力。** サイドバーが20項目を超えるため、キーボード利用者が毎ページ全 nav を Tab 通過していました
- **md 未満（767px 以下）はナビをドロワーにする（#367。2026-09-28 に案 A に決定。案 B「アイコン列を維持」・案 C「下部タブバー」は採らない）。** 3 製品の nav config と `Sidebar` の props は変えず、`AppShell` がモードを切り替えます
  - サイドバーを隠し、画面の上端に反転面（サイドバーと同じ `data-surface="inverted"`）のバーを置く。左端に「メニュー」ボタン（lucide `Menu`、`aria-expanded` / `aria-controls`、44px）、その右に製品名（`Sidebar` の `title`）。バーは本文のスクロールの外にあり、どこまでスクロールしてもメニューに届く
  - 開くと左からドロワー（`role="dialog"` + `aria-modal`、幅 `--sidebar-width`、画面幅 − 3.5rem まで）と scrim（`--scrim`）。ドロワーの中の `Sidebar` は折りたたみの選好（`collapsed`）に関係なく展開して描き、折りたたみボタンの代わりに「メニューを閉じる」（`X`）を出す
  - 閉じ方は「閉じるボタン・Escape・scrim のタップ・ナビの選択（今のページのリンクを含む）・画面の移動」。閉じたら「メニュー」ボタンへフォーカスを戻す。画面を移ったときは、製品の画面遷移のフォーカス（本文 `#pr-main` へ移す）がそのあとに働く
  - 未保存の変更の離脱の確認（`useUnsavedChangesGuard` はリンクの click を capture で止める）でキャンセルしたときは、ドロワーを開いたままにする（別の項目を選び直せる。確認ダイアログはドロワーの上に出る）
  - 開いたら閉じるボタンへフォーカスし、Tab / Shift+Tab をドロワーの中で回す。背面（本文へスキップ・上端のバー・`<main>`）は `inert` にし、`<main>` のスクロールを止める
  - 上端のバーは `<header>` にしない（md 以上に無い banner の landmark を増やさず、`PageHeader` の `<header>` と取り違えない）。Playwright では `data-testid="nav-drawer-bar"`
  - 開閉は transform（200ms、ease-out）。`prefers-reduced-motion` では動かさない（`motion-reduce:transition-none` と §4 の一括無効化）
  - z-index は scrim が `--z-scrim`、ドロワーが `--z-dialog`。確認ダイアログ（ログアウトの前の確認など）はドロワーの上に出る
  - md 以上は従来どおり（サイドバーを本文の左に置き、折りたたみの状態を ui-store に保持する）。md 未満でドロワーを開閉しても ui-store の `sidebarCollapsed` は変えない
  - 文言は `navDrawerLabels`（既定 `{ menu: "メニュー", close: "メニューを閉じる" }`）で上書きできる。サイドバーの `footer` に置く部品は、`collapsed` を `useSidebarCollapsed(collapsed)` に通して使う（ドロワーの中で `false` になる。共通の `SidebarAccountFooter` / `SidebarAccountSection` は対応済み）

### `SideSheet`（新規）— ★ 画面の中の補助的な一覧を、狭い画面で本文の上に重ねる（#664）

画面の中の補助的なパネル（RAG のチャットの会話の履歴など、多くの利用者は使わないが、切り替えに要る一覧）は、**既定で閉じ、開閉ボタンで開く**（ChatGPT・Claude・Gemini・Microsoft Copilot の会話の履歴と同じ型）。広い画面（製品が決める。RAG のチャットは lg 以上）では製品が本文の横にインラインで置き（Material 3 の standard side sheet）、狭い画面ではこの `SideSheet` で本文の上に重ねる（modal side sheet）。製品でドロワー・シートを手書きしない。

| 決めたこと | 理由 |
|---|---|
| ナビのドロワー（§4 `AppShell`、#367）と同じ型: 画面の端（既定は左、`side="right"` も可）から滑り出し、scrim（`--scrim`、`--z-scrim`）・`role="dialog"` + `aria-modal`（`--z-dialog`）、幅 22rem・画面幅 − 3.5rem まで。見出しの行（`title` + 閉じるボタン `X`）と、中でスクロールする本文 | 開いたときの見た目・閉じ方をナビのドロワーとそろえる。scrim の外側に本文が少し見え、重ねていることが分かる |
| 閉じ方は「閉じるボタン・Escape・scrim のタップ」。製品は中の項目を選んだとき（会話の選択など）にも閉じる。閉じたら開く前にフォーカスがあった要素（`returnFocusRef` があればそこ）へ戻す | WAI-ARIA APG の Dialog（Modal）パターン。狭い画面では選んだら本文に戻る |
| 開いたら閉じるボタンへフォーカスし、Tab / Shift+Tab を中で回す。中の部品が処理した Escape（`preventDefault` 済み。名前の編集の取消など）では閉じない | 開閉ボタンから入ってすぐ閉じられる。入れ子の Escape を 1 回で文脈ごと閉じない |
| 閉じている間も描いたまま（`inert`・`visibility: hidden`）にし、開閉を transform（200ms、ease-out）で動かす。`prefers-reduced-motion` では動かさない | 開閉ボタンの `aria-controls` の先が常にある。動きで重なりの方向を示す |
| body へ Portal で描く。中の `SelectField` の一覧・Tooltip はシート（`aria-modal`）の中に描かれる。確認ダイアログはシートの上に出る。開いている間のシートは `translate: none`（`translate-x-0` の `0 0` でも fixed の子の基準がシートになり、一覧がシートの左端の分だけずれる。#810） | 親の overflow に切られない。モーダルの外の要素として読まれない |
| 開閉の状態は保持しない（再読込・画面の行き来で開いたまま戻さない）。インラインのパネルの開閉は製品が作業状態に残してよい | 戻ったときにモーダルが画面を塞がない（UX 契約 workspace-state.md の「確認ダイアログは戻るときに解除する」と同じ扱い） |

- 文言（`title` / `closeLabel`）は翻訳済みを渡す。開閉ボタンは製品の画面に置き、`aria-expanded`・`aria-controls`（`id` に渡した値）を付ける。Playwright では `data-testid`（シート）と `<testId>-scrim`、または role（`dialog` の名前）で操作する。

### `Toaster`（変更）— ★ 置き場所は上端の見出しの面（#411）

通知（Toast）は**主操作を覆わない位置**に出します。置き場所は `Toaster` が決め、製品では変えません（`placement` プロップは削除）。規則の正本は UX 契約 [messaging.md §3.1](../ux-contracts/messaging.md#31-toast)。考え方は「画面の上端の見出しの面（`PageHeader` / 上端のバー）に重ね、その面の操作は覆わない」です。

| 幅 | 置き場所（`data-toast-placement`） |
|---|---|
| md 以上 | `page-header`: `PageHeader`（`<header data-page-header>`）に重ね、ページの操作（`[data-page-header-actions]`）のすぐ左に右端をそろえる（間 1rem）。上端は `PageHeader` の上端 + 1rem。幅は内容に合わせて `--toast-width-min`（22rem）〜`--toast-width-max`（32rem）の間で広がる（置ける幅がそれより狭ければ置ける幅まで。#899）。ページの操作が無い、または操作がタイトルの下へ折り返して左にある（lg 未満）ときは、画面の右端から 1rem |
| md 以上（狭い） | `below-page-header`: ページの操作の左右のどちらにも 14rem が取れないときは、`PageHeader` の下端 + 1rem の右 |
| md 以上（見出しなし） | `top-right`: `PageHeader` がスクロールで見えない（lg 未満）・無い画面は、画面の右上（上端・右端から 1rem） |
| md 未満 | `top-bar`: 上端の全幅（右 1rem）。上端のバーに重ねて上端から 0.5rem。左はメニューのボタン（左の余白 0.5rem + 44px）の後ろ `calc(1rem + var(--control-height-touch))` から（上端のバーが無い画面は 1rem） |

- **下端・右上（`PageHeader` の下）に置かない。** 操作は `PageHeader` の右端、内容の面の右上（`ObjectActionBar` / `ContentActionBar`。`PageHeader` のすぐ下に来やすい）、内容の末尾（`FormActionBar`。ページの末尾の操作は必ず画面の下端に来る）に集まる。右下の通知は末尾の操作を覆い、ポインタが乗ると一時停止（#351）で消えなくなり、閉じるまで押せなかった（#391 / #411）。`PageHeader` の下の右上は、内容の面の右上の操作を覆う（Agent の連携機能の詳細の「その他の操作」で確認）。`PageHeader` のタイトルの面と上端のバーの製品名には操作が無い
- 一時停止（ホバー・フォーカス中はすべての通知の自動消滅を止める）と表示時間は変えない
- **幅は内容に合わせる（#899）**: md 以上は `min-width: min(var(--toast-width-min), 置ける幅)`・`max-width: min(var(--toast-width-max), 置ける幅)` の shrink-to-fit。上限（32rem = 448px、本文の 1 行が約 25 字）までは折り返さない。積んだ通知は最も広い通知の幅にそろう。md 未満は画面の幅（左右の間隔の内側）。以前の固定の 22rem では、短い 1 文（「Oracle Profile の反映が完了しました。」）でも「…完了しま / した。」と語の途中で 2 行になった。上限は Material の Snackbar（344〜672px）・Atlassian の Flag（400px）の間で、PageHeader のタイトルを覆いすぎない幅。Carbon の Toast（288px 固定）より広いのは、日本語の 1 文が英語より横に長いため
- 位置は通知が出ている間だけ、スクロール（祖先のどれでも）・リサイズ・`PageHeader` の大きさの変化に追従する（`requestAnimationFrame` でまとめる）。計算は `lib/toast-placement.ts` の `resolveToastPlacement`
- 新しい通知は下に足す（DOM の順 = 読み上げ・Tab の順 = 見た目の順）。登場は上から降りる（`toast-in`: `translateY(-0.5rem)` → 0、200ms、reduced-motion では動かさない）
- それでも覆いうるもの: 2 件以上積んだときの下の通知（本文の先頭。desktop は右寄り、375px は `PageHeader`）、lg 未満で `PageHeader` の「その他の操作」のメニューが左へ広がったときのメニューの上端、desktop のページのタイトル（一時的）

### メッセージの本文の折り返し（新設）— ★ 日本語を文節で折り返し、語の途中で切らない（#899）

メッセージの部品（`Toast`・`Banner`（`SettingsTestResultPanel`・`SaveErrorBanner` を含む）・`FormStatus`・`FieldError`・`ProcessingIndicator`・`BlockedPageNotice`・`ErrorState` / `EmptyState`・`ConfirmDialog`）の本文は、共有の CSS の `.pr-message-text` で折り返す。`MessageText` が自分に付けるので、`MessageText` で描く文字列はすべて対象になる。部品の面にも付けるので、`Banner` の子に渡した ReactNode も対象になる。

```css
.pr-message-text {
  word-break: normal;
  word-break: auto-phrase;   /* 文節で折り返す。Chrome / Edge 119+、<html lang="ja"> のときだけ */
  overflow-wrap: anywhere;   /* 1 行に入らない文節・URL・ID だけ、その中で折り返す */
  text-wrap: pretty;         /* 最後の行に 1〜2 文字だけ残さない */
}
```

| 決めたこと | 理由 |
|---|---|
| 1 文が 1 行に入るなら折り返さない。入らないときは文末（`MessageText` の文ごとの inline box）→ 文節の順に折り返す | 「完了しま / した。」のような語の途中の改行は、読み手が語を組み立て直す負担になる（W3C の日本語組版の要件 JLReq、Chrome の `auto-phrase` の説明） |
| 未対応のブラウザ（Firefox・Safari）は `auto-phrase` の宣言を捨て、直前の `normal`（`body` と同じ禁則処理つきの折り返し）に残る。`text-wrap: pretty` も未対応なら通常の折り返し | 機能の検出（`@supports`）を書かなくても、CSS の宣言の上書きで同じ劣化になる。見た目が崩れることはない |
| `overflow-wrap: anywhere` を必ず併せる | `auto-phrase` は文節を分けない単位にするため、狭い面（375px の Toast・表の中の `FormStatus`）で文節や URL が枠の外へ出る。最小幅（min-content）も文字単位になり、flex の中で面を押し広げない |
| 本文（`body`）全体には入れない | 表のセル・チップ・バッジ・ナビ・grid の狭い列では文節の幅が最小幅になり、列が広がる・はみ出す（`body` は `overflow-wrap: normal`）。`text-wrap: pretty` は長い文章で描画が遅くなる（MDN）。短い文章のメッセージだけに使う |
| 片仮名の複合語は、構成する語の間（「バック｜グラウンド」）で折り返すことがある | Chrome の `auto-phrase` の文節の判定。語の途中（「バックグ｜ラウンド」）では切らない |

- 文言は短くする: 1 文目は「何が起きたか」だけにし、補足は Toast の `description`、技術的な詳細は「詳細」（`Disclosure`）に分ける（UX 契約 messaging.md §3.1・§10.3）。幅を広げても、長い文は折り返す。
- 製品で `word-break` / `text-wrap` / `overflow-wrap` をメッセージに書かない（部品が持つ）。ID・パス・SQL は従来どおり `.pr-break-anywhere`。

### 削除

- **`.pr-icon-button` クラス。** 中身が hover ルールだけで、サイズ・radius・focus・disabled が未定義だったため製品ごとにサイズが違っていました。`<Button variant="ghost" iconOnly>` に一本化

---

## 5. トークン名の対応表（機械置換可）

`packages/ui` と3アプリの全 `.tsx` / `.css` / `.ts` に対して、**この順で**置換してください（長い名前が先）。

| 旧 | 新 |
|---|---|
| `--primary-fill-foreground` | `--color-fg-on-accent` |
| `--primary-foreground` | `--color-fg-on-accent` |
| `--primary-fill` | `--color-accent-emphasis` |
| `--primary` | `--color-accent-fg` ※文脈判断 |
| `--sidebar-foreground` | （スコープが供給）`--color-fg-muted` |
| `--sidebar-active` | `--color-accent-emphasis` |
| `--sidebar` | `--color-sidebar` |
| `--success-fill` | `--color-success-emphasis` |
| `--success-bg` | `--color-success-subtle` |
| `--success` | `--color-success-fg` |
| `--warning-bg` | `--color-warning-subtle` |
| `--warning` | `--color-warning-fg` |
| `--danger-fill` | `--color-danger-emphasis` |
| `--danger-bg` | `--color-danger-subtle` |
| `--danger` | `--color-danger-fg` |
| `--info-bg` | `--color-info-subtle` |
| `--info` | `--color-info-fg` |
| `--control-border` | `--color-border-control` |
| `--button-border` | `--color-border-control` |
| `--border` | `--color-border` |
| `--card` | `--color-surface` |
| `--background` | `--color-canvas` ※文脈判断 |
| `--foreground` | `--color-fg` |
| `--muted` | `--color-fg-muted` |
| `--ring` | `--color-focus-ring` |
| `--disabled-bg` | `--color-surface-disabled` |
| `--disabled` | `--color-fg-disabled` |
| `--code-fg` | （スコープが供給）`--color-fg` |
| `--code` | `--color-code-canvas` |
| `--graph-*` | `--color-graph-*` |
| `--z-overlay` | `--z-dialog` / `--z-toast` / `--z-dropdown`（用途で振り分け） |

### 機械置換できない3件 — 必ず目視

**`--primary`** — 文字色と塗り背景の両方に使われていました。
- 文字・アイコン・リンク → `--color-accent-fg`（淡青面に載せるなら `--color-accent-fg-strong`）
- 背景の塗り（ボタン、アクティブな nav 行、選択中のチップ） → `--color-accent-emphasis`

**`--background`** — ページ地と hover 面の両方に使われていました。
- ページ / シェルの地 → `--color-canvas`
- 表ヘッダ、入力の窪み → `--color-surface-sunken`
- 行 hover、メニュー行 hover → `--color-surface-hover`

**`--z-overlay`** — 用途で7段に分かれました（下記）。

---

## 6. 新規トークン（旧構成に対応物なし）

### 面と段 (elevation)

**ライトは影で、ダークは面の明度で段を作ります。** ダークで影がほぼ見えないのは物理的事実です。**この順序を崩さないこと。**

| トークン | light | dark | 用途 |
|---|---|---|---|
| `--color-canvas` | `#f7f8fa` | `#101318` | ページ / シェルの地 |
| `--color-surface` | `#ffffff` | `#181c23` | カード・パネル |
| `--color-surface-raised` | `#ffffff` | `#20252e` | popover, dropdown, toast |
| `--color-surface-overlay` | `#ffffff` | `#272d38` | dialog, command palette |
| `--color-surface-sunken` | `#f7f8fa` | `#101318` | 表ヘッダ、窪み |
| `--color-surface-hover` | `#f2f4f7` | `#20252e` | 行 hover |
| `--color-surface-disabled` | `#f2f4f7` | `#252b34` | 無効コントロールの地 |

影も両テーマで値が変わります（`tokens/elevation.css`）。ダークでは `rgb(0 0 0 / .45〜.70)` まで濃くしたうえで、**必ず `-raised` / `-overlay` と併用**してください。影だけでは段になりません。

### 文字 (4段)

| トークン | light | dark | コントラスト(light) | 用途 |
|---|---|---|---|---|
| `--color-fg` | `#1c1e21` | `#f2f4f7` | 15.3:1 | 本文・見出し |
| `--color-fg-muted` | `#6b7280` | `#b2bac5` | 4.61:1 | 副次テキスト、**プレースホルダ** |
| `--color-fg-subtle` | `#9aa4b2` | `#747f8e` | 2.6:1 | **装飾グリフ専用**（パンくずの `›`）。文章に使わない |
| `--color-fg-disabled` | `#8893a3` | `#747f8e` | 3.10:1 | 無効テキスト |
| `--color-fg-on-accent` | `#ffffff` | `#ffffff` | — | アクセント塗りの上 |
| `--color-fg-on-emphasis` | `#ffffff` | `#ffffff` | — | 状態色塗りの上 |

### 罫線 (3段) — ★本移行の中核

| トークン | light | dark | コントラスト | 要件 | 使う場所 |
|---|---|---|---|---|---|
| `--color-border` | `#e3e6ea` | `#343b46` | 1.25:1 | **なし（装飾）** | カード縁、表の罫線、ヘッダー下線、区切り |
| `--color-border-strong` | `#cfd5dd` | `#4a5260` | 1.6:1 | なし | 読ませたい区切り、入れ子の境界 |
| `--color-border-control` | `#8893a3` | `#5d6878` | **3.10 / 3.04:1** | **WCAG 1.4.11 で 3:1 必須** | 入力欄、セレクト、secondary ボタン、チップ = **「押せる／入力できる」ものの輪郭** |

**ボタンの枠線の方針（明文化）**
- `primary` / `danger`（塗り）→ `border: 1px solid transparent`。**透明な枠線を必ず残す**（secondary と並べた時の 1px ズレ防止）
- `secondary`（白地）→ `border-color: var(--color-border-control)`。**枠線は残す。** 白カード上でゴーストだけにすると「押せる」affordance が失われます
- `ghost` → 枠線なし
- **入力欄と secondary ボタンは同一トークン。** これで「隣り合う操作要素の枠線が2.5倍違う」問題が消えます

### アクセントと情報色 — ★青を1色相に統合

| トークン | light | dark |
|---|---|---|
| `--color-accent-fg` | `#1a73c1` (4.94:1) | `#69adff` (7.4:1) |
| `--color-accent-fg-strong` | `#155a97` (7.15:1) | `#7cbbff` |
| `--color-accent-emphasis` | `#1a73c1` | `#286abd` |
| `--color-accent-emphasis-hover` | 88% mix with `#000` | 同 |
| `--color-accent-emphasis-active` | 76% mix with `#000` | 同 |
| `--color-accent-muted` | emphasis 12% on surface | **22%** |
| `--color-accent-subtle` | emphasis 8% on surface | **16%** |

情報色 `#1d4ed8`（紫寄りの別の青）を**廃止**し、`--color-info-fg` をアクセントと**同一色相**の `--blue-650 #155a97` にしました（淡青面上 5.84:1、白上 7.15:1）。**色相は1つ、明度で役割を分ける**のが標準解です。

暗い面ではトーンが沈むため、`-muted` / `-subtle` の混合率をダークで上げています。塗りの上の文字は常に `--color-fg-on-accent`（白・両テーマ 4.9:1 以上）。**製品ごとのアクセント色は作らない。**

**淡アクセント面の上の文字（`[data-surface-tint="accent"]`、platform #64）。** `--color-accent-subtle` の上ではライトの `--color-fg-muted` が 4.36:1、`--color-accent-fg` が 4.44:1 に下がり AA を満たしません。淡アクセント面を塗る要素（`DataTable` の選択行）に `data-surface-tint="accent"` を付けると、内側の `--color-fg-muted` がライト `--neutral-650`（5.09:1、ダークは据え置き 7.58:1）に、`--color-accent-fg` が `--color-accent-fg-strong`（6.43:1 / 7.35:1）に替わります。`[data-surface]` と違って面のトークン一式は宣言し直さないので、`prefers-contrast: more` の強い値は残ります（a11y.css もこのセレクタを対象にしています）。

### 状態色 — 4系統 × 4役割

`success` / `warning` / `danger` / `info` のすべてが同じ4役割を持ちます（旧構成は系統ごとに役割が欠けていました）。

| 役割 | 用途 |
|---|---|
| `--color-<s>-fg` | 文字・アイコン |
| `--color-<s>-emphasis` | 塗り背景（上の文字は `--color-fg-on-emphasis`） |
| `--color-<s>-subtle` | 淡い面（Banner / StatusBadge の地） |
| `--color-<s>-border` | 輪郭（= `-fg` の30%混合。従来 `color-mix` を各所に直書き） |

実測コントラスト（`-fg` on `-subtle`）: light は success 4.89 / warning 4.54 / danger 5.36 / info 5.84、dark は success 7.25 / warning 8.36 / danger 5.87 / info 6.27。**全て AA 合格。**

### z-index (7段)

```
--z-popover:  200    ツールチップ、日付ピッカー
--z-sticky:   300    PageHeader、スキップリンク
--z-toast:    800    ToastRegion（モーダルの下）
--z-dropdown: 850    SelectField のリスト、コンボボックス、操作メニュー（通知の上）
--z-scrim:    900    モーダルの暗幕
--z-dialog:  1000    ConfirmDialog
--z-palette: 1200    コマンドパレット
```

**SelectField の一覧と操作メニュー（`FloatingActionMenu`。PageHeader の「その他の操作」も含む、#363）は body へ Portal で描きます（#352。SelectField はモーダルの中ならそのモーダルの中）。** z-index を持つ層（モーダル・固定ヘッダー）の中で開いたときは、その層より 1 段上に出します（`--z-dialog` の中なら 1001）。層の外では `--z-dropdown` のままです。

**開いたメニュー・SelectField の一覧は通知（Toast）の上に置きます（#431）。** md 未満では通知が画面の上端に全幅で積まれるため、通知が 2 つ重なると PageHeader の「その他の操作」のメニュー項目を覆い、押せなくなっていました。いま操作しているメニューを通知が塞がないよう `--z-dropdown`（850）を `--z-toast`（800）より上・`--z-scrim`（900）より下にします（Material Design の elevation でも menu 8dp は snackbar 6dp より上）。

**通知（Toast）はモーダルの下に置きます。** モーダル（`aria-modal`）が開いている間はモーダルの外を操作できないため、
通知を上に重ねても閉じるボタンや action は押せず、狭い画面では確認ダイアログのボタンを覆うだけになります
（NL2SQL #372 で 375px 幅の確認ダイアログのボタンが成功通知に塞がれた）。Material Design の elevation でも
dialog（24dp）は snackbar（6dp）より上です。モーダルが開いている間に出た通知は暗幕の下に表示されます（自動で消えない通知はモーダルを閉じた後も残ります）。

- モーダル内の操作の結果は、モーダルの中（`FormStatus` / `FieldError`）に出す。Toast に頼らない
- Toast はモーダルを閉じた後の結果通知（「削除しました」等）に使う

### レイアウトと日本語組版

```
--content-max-width: 1440px
--tab-height: 2.75rem
```

```css
body { line-break: strict; word-break: normal; overflow-wrap: normal; }
.pr-break-anywhere { overflow-wrap: anywhere; }   /* ID・パス・SQL だけに付ける */
.pr-message-text { word-break: normal; word-break: auto-phrase; overflow-wrap: anywhere; text-wrap: pretty; }
                                                  /* メッセージの部品の本文だけ（§4「メッセージの本文の折り返し」。#899） */
```

旧構成は規定が無く、`DataTable` のセルが `word-break: break-word` だったため **「デー／タベース」のように任意の文字で分断**され、禁則処理も効かず **行頭に 、。ー っ ゃ** が来ていました。Japanese-first を掲げるシステムとしては致命的な欠落でした。

### アクセシビリティモード（`tokens/a11y.css`）

- `@media (forced-colors: active)` — システム色（`Canvas` / `CanvasText` / `Highlight` / `HighlightText` / `GrayText` / `LinkText`）に総入れ替え。**強制カラーモードでは塗りだけの要素が消えるため、境界線を明示的に与えています。** hover 行は `Highlight` と `HighlightText` を**必ず対で**指定（背景だけ変えると文字が読めなくなる）
- `@media (prefers-contrast: more)` — 罫線と副次テキストを強め、フォーカスリングを 3px に

---

## 7. 意図的な見た目の変更（回帰ではありません）

QA に事前共有してください。**68点あります。**

| # | 変更 | 旧 → 新 | 理由 |
|---|---|---|---|
| 1 | **本文と表の文字が大きくなる** | 本文 12.25→**14px**、表・メタ 10.5→**12px** | 業界下限適合。**最も目につく変更**。表の行高が増えます |
| 2 | セクション見出しが増える | （事実上無かった）→ 16px / 600 | 4階層の確立 |
| 3 | **入力欄・セレクトの枠線が濃くなる** | `#e3e6ea` (1.25:1) → `#8893a3` (3.10:1) | WCAG 1.4.11 適合 |
| 4 | disabled テキストが濃くなる | `#9ca3af` (2.5:1) → `#8893a3` (3.10:1) | 実用可読性 |
| 5 | プレースホルダが濃くなる | `--muted` 70%透過 → 透過なし | AA 適合 |
| 6 | **ダークの popover / toast / dialog の背景が明るくなる** | すべて `#181c23` → `#20252e` / `#272d38` | ダークで段を表現 |
| 7 | サイドバー内のフォーカスリングが明るくなる | `#1a73c1` (1.9:1) → `#7cbbff` | キーボード操作で位置が見える |
| 8 | **日本語の改行位置が変わる** | 規定なし → `line-break: strict` | 禁則処理が効く。ID・パス・SQL は `.pr-break-anywhere` を明示 |
| 9 | **情報色が変わる** | `#1d4ed8`（紫寄り）→ `#155a97`（アクセントと同一色相） | 2つの青のブレを解消 |
| 10 | **StatusBadge にアイコンが付く** | ラベルのみ → アイコン + ラベル | 色覚型に依らず判別可能にする |
| 11 | **ヘッダーのアクション順が反転** | primary 左端 → **primary 右端** | 最も破壊的な操作が最も押しやすい位置だった |
| 12 | **PageHeader が sticky になる** | スクロールで消える → 上端に固定 | 長い表で主要操作に手が届く |
| 13 | 表ヘッダのソートがセル全体クリック可能に | 文字高のみ（約15px）→ セル全体 + hover | 24px 最小タップ領域 |
| 14 | **入力欄の「必須」バッジが中立色になる** | 琥珀色（`--color-warning-subtle` / `-fg`）→ 地なし + `--color-fg-muted` + `--color-border-strong` の輪郭 | 必須は状態ではなく情報。操作前から注意表示が並ぶのを止める |
| 15 | **通知がモーダルの下に表示される** | `--z-toast` 1100（モーダルの上）→ **800（暗幕の下）**。通知の action / 閉じるは共有 `Button`（ghost） | モーダル外は操作できないため、上に重ねると確認ダイアログのボタンを塞ぐだけになる（§6 z-index） |
| 16 | **ページヘッダーの補助操作に枠線を表示** | `utility` の `ghost` → `secondary` | 更新・再取得も取込と同じ操作範囲を示す。並び順とメニュー表示は維持 |
| 17 | **通知の消え方が変わる** | danger 8 秒・warning 6 秒で自動で消える → **danger は閉じるまで残る**、warning は 4 秒。ホバー・フォーカス中は消えず、離れたら残り時間から再開。Banner の閉じる × は共有 `Button`（ghost・iconOnly、16px のアイコン） | UX 契約 messaging §3.1 に合わせる。読んでいる・押そうとしている途中で消さない（WCAG 2.2.1）。閉じる × の見た目を Toast とそろえる（#351） |
| 18 | **SelectField の一覧が画面の下端で上に開く** | 常に下（親の overflow で切れる）→ body へ Portal で描き、下に入らなければ**上に反転**。モーダルの中ではモーダルの中に描き、暗幕とモーダルの上に出す | 画面の下端・表のセル・モーダルの中でも選択肢が見える（#352、components-reference「SelectField — 変更」） |
| 19 | **フォーカスの表示が outline 1 つになる** | Switch・折りたたみの見出し・一覧の行ボタン・コードブロック等で outline と ring（box-shadow）が二重 → **outline 1 つ**（2px、offset 2px）。タブ・表の並べ替え列頭は部品の指定どおり内側の outline。`loading` 中のボタンはフォーカスが外れず、リングが残る | グローバルの `:focus-visible` を `@layer base` へ移し、部品の outline の調整が効くようにした。ring はフォーカスに使わない（§4「フォーカスの表示」、#355） |
| 20 | **ページヘッダーの操作のグループに区切り線** | 区切りなし → 危険操作 / ページツール / 作業開始の境界に縦の区切り線。「その他の操作」メニューでは危険操作の前に区切り線 | buttons.md §5。README #16 で utility に枠線を付けた代わりに、グループの違いを区切りで示す（#355） |
| 21 | **破壊的でない操作が赤塗りでなくなる** | RAG のジョブのキャンセル・一括削除の起点・検索・回答プロファイルの削除の起点、Agent の承認の拒否・Run のキャンセルが `danger`（赤塗り）→ `secondary` / `ghost` + `tone="danger"`（赤文字）。Agent のストリーム方式の切り替えは手書きのセグメント → `ToggleChip` | 赤塗りは破壊的な確定だけに使う（§4 Button「variant と tone」、buttons.md §3）。確定は確認ダイアログの danger ボタンで行う（#355） |
| 22 | **操作メニューが画面の左右の外に切れない** | PageHeader の「その他の操作」メニューが常にボタンの右端揃え（`absolute right-0`。375px で操作が折り返すと左外に切れる）→ body へ Portal で描く `FloatingActionMenu` にし、ボタンが左寄りなら**左端揃え**、右寄りなら右端揃え、どちらも入らなければ画面の内側（左右 8px）にずらす。下に入らなければ上に反転。行の操作メニュー・ObjectActionBar・FormActionBar も同じ規則で左右を反転する | 狭い画面でもすべての項目が見えて押せる（buttons.md §5.1「メニューの表示方向」、#363） |
| 23 | **タッチ端末でチップとスイッチの当たり判定が広がる** | `ToggleChip`（約 26px）・`Switch`（24px）の当たり判定 = 見た目 → タッチ端末（`pointer: coarse`）でだけ 44px 以上。見た目の大きさとマウス環境は変わらない | Apple HIG 44pt / WCAG 2.5.5。Button と同じ `--control-height-touch`。見た目の外側を押しても切り替わる（§4「タッチ端末の当たり判定」、#364） |
| 24 | **入りきらないタブの端がフェードする** | 横にスクロールしても手がかりなし → スクロールできる方向の端だけを透かす（2rem）。キーボードで選んだタブはフェードの外までスクロール | 375px などで続きのタブがあることを見せる。強制カラーモードではフェードの代わりに細いスクロールバー（§4「Tabs」、#364） |
| 25 | **アイコンだけのボタンに説明の吹き出しが出る** | `iconOnly` の名前は `aria-label`（読み上げだけ）か HTML の `title`（マウスで長く止めたときだけ）→ ホバー（400ms）とキーボードのフォーカスで、`aria-label` と同じ文言の暗い吹き出しをボタンの上（入らなければ下）に出す。Escape で閉じる。タッチ端末では出さない。RAG のプレビューのツールバー・NL2SQL のグラフ操作などの `title` は `tooltip` に置き換えた | アイコンだけのボタンの名前を、キーボード・マウスのどちらでも目で確かめられるようにする（§4「`Tooltip`」、#372） |
| 26 | **読み込み中の Skeleton の見た目が 1 つになり、経過時間が付く** | 共有 `Skeleton` は `bg-border/60`、DataTable の読込行と NL2SQL の画面は `bg-surface-hover` と混在し、`prefers-reduced-motion` で点滅が止まらない部品があった。RAG の設定・一覧・文書詳細は塊やテキストだけ → すべて `--color-surface-hover` の地で reduced-motion では点滅しない。読み込み中は `TimedLoadingState`（「〜を読み込んでいます」と経過時間）+ 形のある `TableSkeleton` / `ListSkeleton` / `FormSkeleton`。`DataTable` の `loading` は `visibleRows` の行数で高さを予約 | UX 契約 messaging §3.6 / §3.7 を 3 製品でそろえる（#265。#338 から移した形のある Skeleton） |
| 27 | **RAG の一覧が 10 件/ページになり、表の中で縦スクロールする** | 文書・ナレッジベース・検索・回答プロファイルは 20 件/ページで自前の前へ / 次へ（1 ページでも表示）、高さは `bounded-scroll-area-lg`。承認済み FAQ・用語・ルール・評価のケース結果は全件を表示 → 共通の `Pagination`（10 件、件数と「N / M ページ」、1 ページなら出さない）を表の直下に置き、表頭を固定して md 未満 5 行・md 以上 8 行を超えた行は表の中で縦スクロール。ナレッジベース詳細の所属文書のページは再読込でも残る | NL2SQL の基準（ルートの AGENTS.md「読み込み中・一覧・ページング」、#265） |
| 28 | NL2SQL のページ送りの「N / M ページ」の枠 | 製品のコピー（`border-border`・高さ 2rem）→ 共通 `Pagination`（`border-border-control`・`--button-height-sm`） | 製品のコピーを削除し、共有部品に一本化（#265） |
| 29 | **md 未満のナビがドロワーになる**（#367。案 A に決定） | 375px などで 56px のアイコン列（展開すると本文を押し出す）→ **上端のバーの「メニュー」ボタンで開くドロワー**（scrim・フォーカスの閉じ込め・Escape / scrim / ナビの選択で閉じる）。本文は画面の全幅（375 − 56 = 319px → 375px）。md 以上は変わらない | 狭い画面で本文の幅を削らない。Material の modal navigation drawer と同じ型（§4 `AppShell`。2026-09-28 に案 A に決定） |
| 30 | **確認語欄が 1 つの実装になる** | Agent の実行時スナップショットの置換の確認語は `danger` の枠線の区画に素の入力欄（`h-10`・`bg-surface-sunken`）と説明だけ、置換ボタンは区画の下（`md`）→ 共有の `ExecutionConfirmationField`（中立の面・「入力条件: REPLACE」と状態のバッジ・44px の入力欄・区切り線の下に `lg` の置換ボタン、375px では全幅）。見出しの「確認入力」と説明の文言は変えない。system-settings のシステムテーブルの確認語欄は、確認語が `_` の直後で折り返すようになる（旧: 任意の位置）。NL2SQL は変わらない | 製品のコードと system-settings のコピーを `packages/ui` に一本化し、3 製品の確認語欄を同じ見た目・振る舞いにする。破壊的な操作の確認面を中立にする（UX 契約 buttons.md / messaging §3.5、§4「`ExecutionConfirmationField`」、#379） |
| 31 | 入力欄・SelectField の角丸が Button と同じになる | `rounded-md`（0.375rem = 5.25px）→ **6px**（`--radius-control`）。Button・`Pagination` の「N / M ページ」も同じトークン（値は 6px のまま） | 隣に並べた操作部品の角の形をそろえる（§4「操作部品の角丸」、#384） |
| 32 | **検索欄の高さ・アイコンの位置・地がそろう** | 手書きの 9 箇所: RAG のフィードバック 31.5px・ファイル一覧 35px・ナレッジベース 31.5px・検索と検索テスト 38.5px（地は `surface-sunken`）、NL2SQL のスキーマ参照 31.5px・DB 管理 38.5px、ユーザー・ロール管理と Deep Data Security 38.5px → 共有 `TextField`: **36px**（`--field-height`）。隣に lg の Button がある検索・検索テストは **40px**（`--button-height-lg`）、NL2SQL の DB オブジェクトの検索・所有者は **44px**（`touchTarget`、以前と同じ）。アイコンは左 0.75rem、文字は 34.5px から。地は `surface`。NL2SQL の DB オブジェクトの種類の select は枠線を `border-border` → `border-border-control`、角丸を `--radius-control` にし、検索・所有者とそろえた。RAG のナレッジベース詳細の「追加する文書を検索」（既に `TextField`）は先頭アイコンとクリアを足し、同じ行の SelectField・Button の `h-9`（31.5px）を外して 36px にそろえた | 並ぶ SelectField・Button と上端・下端がそろう。入力できるものの輪郭は 3:1（§4「`TextField` の先頭アイコン・後置スロット」、#384） |
| 33 | **検索欄のクリアが共有のボタンになる** | `type="search"` のブラウザ既定の ×（Chromium・Safari だけ。キーボードで届かない）→ 値があるときだけ入力欄の右端に「検索語をクリア」（NL2SQL の所有者の前方一致は「入力をクリア」）の × ボタン。Tab で届き、ホバー・フォーカスで Tooltip。Escape でも消える | どのブラウザでも同じ操作で消せる。キーボード・読み上げで使える（#384） |
| 34 | **強制カラーモードで、選ばれていないタブの下線が消える** | すべてのタブに `CanvasText` の下線（選んだタブと区別できない）→ 選んだタブだけ `Highlight` の下線 | 透明の枠線は強制カラーモードで system color に塗られるため、背景と同じ `Canvas` にする（§4「Tabs」、#374） |
| 35 | **Agent の一覧が 10 件/ページになり、表の中で縦スクロールする** | Agent の 14 の表は全件をページの高さで表示（ページ送りなし）、読み込み中は 4 本の棒（`LoadingState`）、実行先・Skill を取得している間に「未設定」「Skill を取得できません」などの警告が出た。監査は「表示件数」（既定 100 件）を 1 度に表示、メモリは 20 件で打ち切り → 共有の `PagedDataTable`（表頭の固定、md 未満 5 行・md 以上 8 行の縦スクロール、直下に 10 件/ページの `Pagination`）。一覧のページは作業状態に残り、Run・承認の 5 秒ごとの再取得でも戻らない。監査は API の offset / limit で送る（「1 ページの件数」、既定 10 件。CSV は条件に合う記録を最大 1,000 件）、メモリは 100 件まで取得してページで送る。読み込み中は `TimedLoadingState` と画面の形の Skeleton、取得中は警告を出さない | NL2SQL の基準（ルートの AGENTS.md「読み込み中・一覧・ページング」、#265） |
| 36 | **スピナーの線が太くなり、トラックが見えやすくなる。reduced-motion ではアークの濃さが変わる** | 線の実寸は 16px で 1.33px・14px で 1.17px（viewBox 24 に線幅 2 固定）、トラックは `currentColor` の 25%、reduced-motion では止まったまま（75% の進捗の円に見える）、ボタンの `loading` のスピナーは `fg-disabled`（ライトで地に 2.82:1）、RAG の状態バッジ 3 箇所は lucide の `Loader2` / `LoaderCircle` / `RefreshCw` を回していた → すべて共有 `Spinner`: 線は大きさによらず**実寸 2px**、トラックは `--color-spinner-track`（ライト 30% / ダーク 35%）、reduced-motion では回転を止めてアークの濃さを 1 ↔ 0.5 で変える、ボタンの `loading` のスピナーは `fg-muted`（4.39:1）。回転は等速のまま、ボタンのスピナーは sm / md / lg とも 16px のまま | 等倍の画面で線がかすれ、ダークでトラックが見えにくかった。アーク対トラックの境目 3:1 を保つ上限まで濃くした（§4「Spinner」、#395） |
| 37 | **RAG の原本プレビューの処理前 / 処理後と、抽出エクスポートの形式が下線のタブになる** | 枠（`border` + `bg-surface-sunken` + `p-0.5`）の中にボタンを並べた手書きのセグメント（外枠と選択中のボタンの枠が 1.75px の間隔で二重、ボタンの間 0px、角丸は外 5.25px・内 6px（形式は内 3.5px）、選択は塗りの違いだけ。処理前 / 処理後は `aria-pressed` も無く、矢印キーで動かない）→ 共有の `Tabs` + `TabPanel`（`tablist` / `aria-selected`、← → / Home / End）。処理前 / 処理後のタブの列は見出し（「原本プレビュー」とダウンロード）の下の行に移り、プレビューがその分（約 2.75rem）低くなる。形式のタブは件数の下に置き、375px で入りきらないときは横スクロールと端のフェード（折り返さない）。「処理後」が使えない理由は無効のタブの `title`（`disabledReason`）で、見た目は変えない | 同じ対象の別の見方は `Tabs`（§4「`Tabs`」）。枠線の二重・隙間 0・同心でない角丸・選択の読み上げの欠落を、部品を足さずに解消する（#396） |
| 38 | **開閉できる領域に Chevron が付き、見た目がそろう** | RAG の「処理の詳細(診断)」・Vision の読み取り内容・回答の根拠の構成 / 処理の手順・回答の評価の網羅 / 主張・プロンプトの工程・運用診断・チャットの回答の根拠、NL2SQL の技術詳細・主要 / 補助の概念・オントロジー構築の工程は Chevron なし（ブラウザの三角か、`flex` で三角も消えて何もなし）。RAG の抽出の区分は先頭の `ChevronRight`、分割の詳細設定・チャットの引用は右端の `ChevronDown` の 180° 回転 → 共有 `Disclosure`: 右端（`plain` は見出しの直後）の `DisclosureChevron`、見出しの行全体に hover の地、`card` は開くと見出しと内容の間に区切り線、見出しの高さ 40px（タッチ端末 44px）。先頭のアイコンは `--color-fg-muted`（「処理の詳細(診断)」の `Wrench` はアクセント色だった）。RAG の検索の「診断」「詳細条件」・「見出しで絞り込む」「文書の分類で絞り込む」（「＋ / −」の文字）・サービス管理の実行コマンド・ナレッジグラフ / パイプライン構成の表示・「処理設定を編集」（向きの変わらない `ChevronDown`）のボタンにも `DisclosureChevron` | 押せる見出しであること（signifier）と、いま開いているか（システムの状態）を見て分かるようにする。3 製品で同じ意匠にする（§4「`Disclosure`」、#397） |
| 39 | **Chevron の折りたたみの向きが右になる** | `DisclosureChevron`（NL2SQL の SQL 詳細・実行オプション・Show Prompt・全文表示・スキーマの表・評価の分析、system-settings のシステムテーブルの詳細、「その他の操作」メニューなど）: 閉じているとき**左向き** → **右向き**（開いているときの下向きは同じ）。共有 `Sidebar` のセクションは元から右向きで、同じ部品にした | 同じ「開閉」が画面ごとに逆を向いていた。ブラウザ標準の ▸ / ▾・APG の例と同じ向きにそろえる（#397） |
| 40 | **その場の実行と停止が 1 つのボタンになる**（RAG。#413） | RAG 検索: 質問欄の右に「検索」、実行中は押せない「検索」（スピナー）の右に「停止」（`X`）→ 検索のボタンはフォームの最後（「詳細条件」の下の区切り線の下、左寄せ。375px は全幅）に移り、実行中は**同じ位置・同じ要素のまま** `secondary` の「停止」（`Square`）になる。質問欄は隣の lg のボタンが無くなったため 40px → 既定の 36px。進捗の表示（「回答を生成しています」）がスピナーを出す（以前はボタンのスピナーだけ）。ナレッジベースの検索テストも同じ 1 つのボタン（文言「中止」→「停止」、幅は「検索テスト」の幅を保つ）で、処理中は今の工程と経過時間を出す。チャットの送信 / 停止は同じ要素になり、高さの手書き（38.5px / 31.5px）→ `md`（36px、タッチ 44px）、送信できない間は `aria-disabled`、生成中も入力欄に書ける。NL2SQL・Agent は変わらない（止められる操作はバックグラウンドの job で、開始と別の「中止」のまま） | 実行中に押せる操作は停止だけにし、押した位置にそのまま停止を出す。フォーカスを保つ。詳細条件を変えた後に上へ戻らずに検索できる（UX 契約 buttons.md §3.1） |
| 41 | **RAG の chunk・会話一覧、NL2SQL の評価・履歴・プロファイル・訓練データの一覧が、5 / 8 行と共通のページ送りになる** | RAG の文書詳細の chunk はページの高さで全件を表示、抽出セグメントは `bounded-scroll-area`（22rem）、チャットの会話一覧は 50 件で打ち切り（`max-h-56`）。NL2SQL の SQL生成評価は独自の「前へ / 次へ」だけ（件数なし、結果明細は 25 件/ページ）、読み込み中は文言だけ。実行履歴は 42rem、プロファイル一覧は 20rem / 30.5rem、質問分類の訓練データは 42rem の手書きの高さ → chunk（xl 未満）・抽出セグメント・会話一覧（lg 未満）・実行履歴は `INFORMATION_LIST_SCROLL_CLASS`（md 未満 17.5rem・md 以上 28rem）で中をスクロール。chunk は xl 以上ではプレビューと同じ高さのタブのパネルがスクロールし、選んだ chunk をそのスクロール領域の中だけで見せる。会話一覧は lg 以上で会話エリアの高さに合わせる。会話一覧は 10 件/ページの `Pagination`（offset / limit、ページは作業状態に残る）。プロファイル一覧・訓練データは `visibleRows`（実測で 5 / 8 行）。SQL生成評価は共通の `Pagination`（件数と「N / M ページ」、10 件/ページ）と、経過時間 + 形の Skeleton の読み込み中 | NL2SQL の基準にそろえる。基準から外す一覧（選択と連動する chunk、カーソル型の「さらに読み込む」、フィードバックの件数の切り替え、評価の結果明細の高さ）は UX 契約 `page-archetypes.md` に理由を書いた（#403） |
| 42 | **通知が右下ではなく上端の見出しの面に出る** | 画面の右下（下 1rem・右 1rem、幅 `min(92vw, 22rem)`）に積み、下から上がってくる → md 以上は `PageHeader` に重ね、ページの操作のすぐ左（上端は `PageHeader` の上端 + 1rem、幅 22rem まで）。`PageHeader` が見えなければ画面の右上。md 未満は上端の全幅（上端のバーに重ね、メニューのボタンは覆わない）。上から降りてくる。`Toaster` の `placement` プロップは削除 | ページの末尾の操作（NL2SQL の「SQL 生成」など）は画面の下端に来るため、右下の通知が覆い、一時停止（#351）と重なって押せなかった。`PageHeader` の下の右上は内容の面の右上の操作を覆う（§4「Toaster」、#411） |
| 43 | **一覧の行の題名のボタンが 3 製品で同じ見た目になる** | NL2SQL のプロファイル・DB オブジェクト（DB 管理の一覧と対象の選択）の名前は常にアクセント色（`text-accent-fg`）、フィードバック管理のエントリはホバーでアクセント色。ユーザー・ロール・ロール権限の一覧は選択中だけアクセント色。RAG / Agent は `--color-fg` + ホバーの下線。フォーカスの outline は画面ごとに offset が 0 / 2px、タッチ端末の当たり判定は NL2SQL の対象の選択が md 未満で `min-h-11`、ほかは文字の高さ → 共有 `RowTitleButton`: 題名は `--color-fg` / 500 + ホバーの下線、選択の見た目は行の淡アクセント面 + 左バーだけ、outline は 2px / offset 2px、タッチ端末（`pointer: coarse`）で当たり判定 44px 以上（見た目の大きさは変えない。NL2SQL の対象の選択は md 未満でも行の高さが 44px 固定でなくなる）。プロファイル名の太さは 600 → 500（DB オブジェクト名は等幅・600 のまま、色だけ `--color-fg`）。Agent のメモリの内容は 80 文字で「…」 → 2 行で切り詰め、Tooltip で先頭 120 文字まで（全文は詳細）。NL2SQL のフィードバックのエントリは 3 行の切り詰めのまま、全文を Tooltip で見せる | 3 製品で同じ役割の部品を 1 つにする。アクセントを「現在の項目」の印に限り、選択行と区別しやすくする（§4「`RowTitleButton`」、#421） |
| 44 | **「アニメーションを減らす」設定でもスピナーが回る** | reduced-motion では `Spinner` の回転を止め、アークの濃さを 1 ↔ 0.5 で変えるだけだった（処理中なのに止まって見えた）→ 設定によらず等速（linear 1s）で回る。他の reduced-motion 対応（Skeleton の点滅・通知・ドロワー・Chevron の動き）は変えない | 処理中を伝える本質的な動きのため（§4「Spinner」、#440） |
| 45 | **開いたメニュー・選択肢の一覧が通知の上に出る** | `--z-dropdown` は 100 で通知（`--z-toast` 800）の下だったため、375px で通知が 2 つ重なると PageHeader の「その他の操作」のメニュー項目が覆われて押せなかった → `--z-dropdown` を 850（通知の上・モーダルの暗幕の下）にした。モーダル・固定ヘッダーの中では従来どおりその層の 1 段上 | いま操作しているメニューを通知が塞がない（#431） |
| 46 | **権限管理の「利用できる対象」の候補の行が名前と説明だけになり、高さがそろう** | 各行に名前・説明・内部の ID（等幅、32 桁の hash など）を並べ、行の高さが内容で変わった。高さに上限のあるスクロール領域の grid で行が `min-h-11` まで縮み、375px の 1 列では次の行の名前が前の行の ID・説明に重なった → ID は出さない。名前は 1 行・説明は 2 行で省略し（全文は `title`、チェックボックスの名前は全文）、説明を持つ候補がある対象ではすべての行が説明の 2 行分を取って高さがそろう。行は内容の高さ（`auto-rows-max`）で、縮めて重ねない。名前の行はバッジの高さを常に取る。ホバーで行の地が `surface-hover` になる。NL2SQL の業務プロファイル・Agent のエージェント / 検索・回答プロファイルも同じ部品（説明を持たない対象は名前だけの行） | 利用者には ID が意味を持たず、何の対象かを名前と説明で見分ける。行の重なりを解消する（`RolePermissionTargetOption`、#521） |
| 47 | **必須の欄に「必須」のタグがそろい、「(任意)」が消える** | 必須の欄でも `requiredLabel` を渡し忘れた欄・素の `<label>` の欄はタグが無く、一部の欄だけにタグがあった。任意の欄はラベル・placeholder に「(任意)」「（任意）」を書く画面があった（RAG・NL2SQL・Agent で 14 か所）→ backend の検証か送信ガードで必須の欄すべてにタグ「必須」と `aria-required`、任意の欄には何も付けない。チェックボックスの群・ラジオの必須は legend の後ろに同じタグ。NL2SQL / system-settings の独自の `FieldLabel` は共有部品になる（見た目は同じ） | 必須だけを 1 通りの印で示し、凡例なしで見分けられるようにする。placeholder をラベルの代わりにしない（§4「必須の表示」、WCAG 3.3.2 / 1.4.1 / 4.1.2、#531） |
| 48 | **一覧の絞り込みの検索ボタンが無くなり、入力に合わせて絞り込む**（#535） | RAG の検索・回答プロファイルの一覧: 検索欄（固定 `w-56`・先頭アイコンなし）+「名前・説明で検索」のボタン → ナレッジベースの一覧と同じ `SearchField`（先頭アイコン・消去、`w-64`、375px は全幅）でボタンなし。NL2SQL の学習候補・アプリ内フィードバック: 条件の行の右端の「絞り込み」（44px）→ ボタンなしで、条件を変えるとすぐ読み込む（読込中は一覧の上に経過時間とスピナー）。アプリ内フィードバックの検索欄は手書きの入力欄 → `SearchField`（44px、虫眼鏡・消去付き）。Agent のメモリの検索欄は手書きの入力欄（40px、地が `surface-sunken`）→ `SearchField`（36px、地は `surface`）。0 件の空の状態に「検索語をクリア」（RAG のナレッジベース・検索・回答プロファイル・文書、ユーザー・ロール・権限管理） | 同じ種類の画面（一覧の絞り込み）の操作を 3 製品でそろえる。押し忘れ・押し直しを無くす。0 件から 1 操作で戻れる（UX 契約 page-archetypes.md「一覧の絞り込みの検索」） |
| 49 | **モデル設定の OCI Enterprise AI の接続がタブになり、エラーのあるタブに印が付く** | 「接続 1（既定）」「接続 2」の枠（`border` + `bg-surface-sunken`）を縦に並べ、各接続に自由入力の「表示名」があった → カードの中の共有の `Tabs`（「プライマリ接続」「セカンダリ接続」）で切り替え、表示名の欄は無い。未設定のセカンダリ接続は `EmptyState` と「セカンダリ接続を設定」、設定済みは入力欄と「セカンダリ接続を削除」（説明の行の右端、ghost の danger）。エラーのあるタブはラベルの後ろに danger 色の `CircleAlert`、未保存の入力があるタブは「未保存」のバッジ。Endpoint URL・Project OCID・API key の必須のタグは、プライマリ接続が 3 つとも「OCI 運用時必須」、セカンダリ接続が 3 つとも「必須」 | 同じ設定の別の枠はタブで切り替える（§4「`Tabs`」）。名前を画面・選択肢・保存値でそろえ、別のタブのエラーを見落とさない（#542） |
| 50 | **数百件から選ぶ選択が「検索 ＋ 候補の一覧 ＋ 選択済みの chip」になる**（#578） | RAG の文書インデックスの「ナレッジベース」の絞り込みは `SelectField`（検索できず、選んだ長い名前は「利用できるすべてのナ…」と省略）→ `SearchableSelectField`（ボタンは名前を折り返して全体を出す。押すと検索欄と候補の一覧を重ねて開き、右端に文書数、下に「51 / 300 件」）。同じ画面の状態の絞り込みは 11 個の `ToggleChip`（desktop でも 2 行、375px では 5 行に折り返した）→ 「状態」の `SelectField`（状態・ナレッジベース・検索欄が 1 行）。KB と検索・回答プロファイルの複数選択（RAG 固有の `MultiSelectCombobox`: 枠の中に chip と枠なしの入力欄・先頭に塗りの虫眼鏡のタイル）→ `SearchableMultiSelect`（上に `SearchField` の検索欄と開閉の iconOnly、開いている間だけ下に候補の一覧、その下に選択済みの chip。chip の名前は省略せず折り返す。「表示中をすべて選択」「クリア」は文字のリンク → ghost の `Button`）。KB の選択の見出しの先頭のデータベースのアイコンは無くなり、「空の KB を隠す」のチェックも無くなる（作ったばかりの空の KB へ登録するため） | 数百件でも検索して選べ、全件を読まない（201 件以上はサーバー側の検索）。選んだ名前を切らない。キーボード・IME・読み上げを 3 製品で共通の部品にそろえる |
| 51 | **狭い画面のページの操作が 2 つなら「その他の操作」に畳まない**（#582） | lg 未満の `PageHeader` は主操作 1 つ +「その他の操作」。操作が 2 つ（RAG の検索・回答プロファイル・Agent のエディタの「一覧へ戻る」+ 保存、NL2SQL・system-settings の一覧の「表示を更新」+「新規作成」など）でも、残りの 1 つだけのメニューになり、戻るのに 2 タップ要った → メニューに入るのが 1 つだけなら畳まず 2 つとも出す。読み上げ名は「その他の操作」→「その他の操作（ページ操作）」「その他の操作（〇〇の操作）」（見た目の文言は同じ） | 戻る操作を 1 タップにし、ページとカードの「その他の操作」を読み上げで区別する（§4「`PageHeader`」7・8） |
| 52 | **複数行の入力欄の見た目が 1 つにそろう**（#584） | 手書きの `<textarea>`: 地が `bg-surface-sunken`（RAG の検索・回答プロファイル・設定・評価など）/ `bg-surface`、角丸 `rounded-md`、disabled が `opacity-50` など、ラベル・文字数の位置も画面ごと → 共有 `TextareaField`: 地は `bg-surface`（read-only は `bg-surface-sunken`、disabled は `bg-surface-disabled`）、角丸 `--radius-control`、余白 `px-3 py-2`、枠線・フォーカスは `TextField` と同じ、文字数と補足は欄の下（RAG のドメインキーワードの補足は欄の上 → 下）。等幅の欄（SQL・JSON・論理構造など。NL2SQL の SQL の入力欄は 14px だった）は `--font-mono` の 12px。RAG の回答プロンプトの「既定値／カスタマイズ済み」のバッジはラベルの横 → 操作の行 | 1 行の入力欄と同じ部品に見せ、disabled を透過で薄めない（§4「`TextareaField`」） |
| 53 | **一覧の検索欄が左（先頭）に移り、大量の候補から選ぶ一覧が 1 つの見た目になる**（#600） | RAG のナレッジベース・検索・回答プロファイルの一覧: 左に状態のチップ、右端に検索欄（`sm:w-64`）、間が空く → `ListToolbar` で左に検索欄（残りを埋める）、その右に状態のチップ。文書の一覧: 右端の検索欄（`sm:ml-auto sm:w-64`）→ 左端に検索欄、その右に状態・ナレッジベース。ナレッジベースの「所属文書」: 検索なし、上に「追加する文書を検索」+ 選択欄 +「追加」（1 件ずつ・候補 100 件まで）→ 左に「所属文書を検索」、右に「文書を追加」。押すと下に `ListPicker`（候補は 100 件ずつ「さらに読み込む」、チェックで複数を選び「選択した N 件を追加」、追加済みは灰色で「追加済み」）。NL2SQL の業務プロファイルの許可する表・ビュー: 枠 392px 固定の中に手書きのチェックボックスの行（44px）と、スキーマごとの枠 → `ListPicker`（見出し・一覧・フッターの 3 段、一覧は 28rem / 375px では 17.5rem、行 3.5rem、スキーマの見出しは面の地、チェックは塗りの四角）。権限管理の「利用できる対象」: 2 列のチェックボックスの行（名前 + 説明 2 行）→ `ListPicker` の 1 列の行（名前 + 説明 1 行で省略、全文は title、状態のバッジは右端）。フッターの「さらに読み込む」は失敗中は出さず、Banner の「再試行」だけにする | 検索欄の位置を 3 製品でそろえる（UX 契約 page-archetypes.md「一覧のツールバー」）。数千〜数万件から検索して複数をまとめて選べる。キーボード（Tab 1 回で一覧に入り、矢印と Space）・読み上げを共通の部品にそろえる |
| 54 | **NL2SQL のメタデータ SQL・合成データの対象が `ListPicker` に、Deep Data Security の対象が検索できる選択欄になる**（#608） | メタデータ SQL（コメント・アノテーション・ドメイン）の対象: 一括選択の下に 3 列の `DataTable`（対象名・種類・所有者の並べ替え、行のチェックボックス、コメントは 2 行）→ `ListPicker`（選択の行、1 列の listbox、行は名前・コメント 1 行で省略・右端に種類のバッジ、28rem / 375px では 17.5rem、フッターの「さらに読み込む」）。合成データの対象の表: 手書きのチェックボックスの行 → `ListPicker`（左に候補の検索欄）。Deep Data Security の対象 table/view: 枠の中に検索欄・所有者の欄・手書きの listbox（max-h-52）・「さらに読み込む」のボタン → `SearchableSelectField`（ボタンに選んだ名前、開くと検索と候補の一覧。所有者の欄は無くなり、検索がスキーマ名も照合する）。権限管理の「利用できる対象」は見た目は同じで、候補を 50 件ずつ読む（フッターの「さらに読み込む」が出る） | 似た機能を同じ部品にそろえる（UX 契約 page-archetypes.md「大量の候補から選ぶ」の画面ごとの判断）。候補を全件読まない |
| 55 | **入力欄・選択欄・ボタンの高さが 3 段にそろい、選択欄が値の長さの幅になる**（#613） | 高さ: 入力欄 36px・その横のボタン 44px（`touchTarget`）、NL2SQL の選択欄 38.5px（`h-11`）/ 44px（`min-h-[44px]`）、Agent の入力欄 40px（`h-10`、地は `surface-sunken`）、ログインの入力欄 38.5px と送信 44px、確認語欄 44px、タッチ端末では入力欄 36px・ボタン 44px → 同じ行は同じ段（sm 32 / md 36 / lg 40px）、タッチ端末は入力欄も含めて 44px。ネイティブの select / input も共有の見た目（地は `surface`）。幅: 単独の選択欄が親の幅いっぱい（RAG のチャットの検索・回答プロファイルが 1,280px で約 560px）→ 値の長さの段（xs 8 / sm 12 / md 20 / lg 28rem、sm 未満は全幅）。チャットの入力欄と送信は下端をそろえ、送信は lg | README §4「操作部品の高さと幅」。Carbon・Material 3・Apple HIG・GOV.UK の指針。行の中で上端・下端がずれず、短い値の欄が長く伸びない |
| 56 | **詳細・作成・編集の画面の「一覧へ戻る」が左上に、保存が右端の primary にそろう**（#618） | RAG・Agent: ヘッダーの右に「一覧へ戻る」（secondary）+ 保存、タイトルの上にパンくず（一覧 › 対象）、RAG は「変更を元に戻す」を本文のカードの下。NL2SQL・system-settings: 本文の先頭に左寄せの「一覧に戻る」（ghost）、system-settings の保存はフォームの末尾の `FormActionBar`（キャンセル・危険な操作と同じ行） → すべて `PageHeader` の左上（タイトルの上）に「← 一覧へ戻る」、右端に保存・作成（primary）、その左に「変更を破棄」。パンくずは出さない。system-settings のパスワードのリセット・無効化・削除・復元・アーカイブはフォームのパネルの見出しの右の `ObjectActionBar`、保存の失敗はヘッダーの直下の `SaveErrorBanner`。文言は「一覧へ戻る」「保存」「作成」「変更を破棄」 | README §4「詳細・作成・編集の画面の操作」。Material 3・Apple HIG・Polaris・Atlassian・GitHub Primer の型 |
| 57 | **RAG 検索とチャットの「対象の検索・回答プロファイル」が同じ単一選択の欄になる**（#635） | RAG 検索: 検索欄＋候補の一覧＋選択済みの chip の複数選択（`SearchableMultiSelect`。先頭に「代表方針」）。チャット: 検索のない `SelectField`（ラベル「検索・回答プロファイル」、`width="lg"`、説明文なし） | 両画面とも `SearchableSelectField`（先頭に検索のアイコン `leadingIcon`、「検索・回答プロファイルを検索して選択…」、候補の右端に「参照 KB N 件」、ラベル「対象の検索・回答プロファイル」＋必須＋説明文、カードの幅いっぱい） |
| 58 | **製品のネイティブの選択欄・入力欄が共有の部品になり、選択欄に無効の見た目が付く**（#631） | NL2SQL・Agent・system-settings のフォームのネイティブの `<select>`（開くと OS / ブラウザの一覧。ダークテーマでも明るい面・typeahead や矢印の動きがブラウザごと）→ `SelectField`（トークンの面の一覧・チェックの印・typeahead・画面の下端で上に反転）。ネイティブの `<input>`（手書きのラベル・エラー）→ `TextField` / `SecretField` / `SearchField`（ラベル・必須・補足・エラーの位置と結び付きが共有）。`SelectField` の無効は、ネイティブの select の無効（ブラウザの灰色）→ `--color-surface-disabled` の地・`--color-fg-disabled` の文字とシェブロン・`not-allowed` のカーソル（`TextField` の無効と同じ）。NL2SQL のオントロジーの「概念の種類」（`<optgroup>` と選べない選択肢）など部品で表せない所だけネイティブのまま | 開いた一覧の見た目と操作、ラベル・エラーの関連付けを 3 製品で 1 つにする。無効な欄が押せそうに見えない |
| 59 | **任意の選択欄の一覧の先頭に「未選択」が付く**（#647） | #631 でネイティブの `<select>` を `SelectField` にした後、未選択を表す空の選択肢はボタンの placeholder だけになり、一度選ぶと未選択に戻せなかった（NL2SQL の Deep Data Security の「列を選択」・関連テーブル条件の Profile / 関連テーブル / 関連キーの列） | `SelectField` の `emptyOptionLabel`: 一覧の先頭に「未選択」（文字は `--color-fg-muted`。選択中・強調中は他の選択肢と同じ）。選ぶと空の値に戻り、ボタンは placeholder（控えめの色）。必須の欄には出さない | ネイティブの select の `<option value="">` と同じく、任意の欄を未選択へ戻せる。キー操作・typeahead・読み上げは他の選択肢と同じ |
| 60 | サイドナビの項目名の行の高さが上がる（#658） | 項目名 `leading-5`（17.5px）→ `leading-6`（21px）、ブランド名 `leading-5` / `leading-4` → `leading-6` / `leading-5` | `truncate` は行の高さで切り取るため、Noto Sans JP の字面（約 1.45em）より低いと「HuggingFace」の g などの下端が切れた。行（44px）とヘッダー（3.5rem）の高さは変えない |
| 61 | **RAG のチャットの会話の履歴が既定で閉じ、チャットが全幅になる。検索・回答プロファイルの欄の説明文が無くなる**（#664） | チャット: 左に会話の一覧のパネル（280px、見出し「会話」・「新しい会話」・一覧・ページ送り）を常に表示し、lg 未満では検索・回答プロファイルとチャットの間に縦に積んでいた。会話を選ぶまで入力欄は無効。RAG 検索・チャットの「対象の検索・回答プロファイル」の欄の下に説明文（「選んだ検索・回答プロファイルが参照するナレッジベースを検索し、…」）→ 会話の履歴は既定で閉じ、チャットの上端の行に開閉ボタン（`PanelLeftOpen` / `PanelLeftClose`、「会話の履歴」）・今の会話の名前・「新しい会話」を置く。lg 以上は開くと左に 280px のパネル（開閉は作業状態に残る）、lg 未満は左からの `SideSheet`（会話を選ぶ・Esc・外側で閉じる）。一覧はパネル・シートの高さまで伸ばして中でスクロールし、ページ送りは下に常に見える。会話を選ばなくても入力でき、最初の送信で会話を作る。検索・回答プロファイルの欄の説明文は出さない | 多くの利用者は会話の履歴を使わないので、チャットに面積を渡す（ChatGPT・Claude・Gemini・Microsoft Copilot と同じ型。§4「`SideSheet`」）。説明文は毎回読む情報ではなく、縦の面積を取っていた |
| 62 | サイドナビのセクションの見出しが大文字にならない | CSS の `uppercase` で英字を大文字（「Agent 構築」→「AGENT 構築」）→ i18n の文言のまま（太さ・大きさ・字間は同じ） | 日本語第一の UI で、英字の製品用語（Agent・AI・MCP）の綴りを変えない。日本語だけの見出しは変わらない（#801） |
| 63 | **Agent のチャットの送信が、回答の作成中は同じ位置で「停止」になる**（#805） | 回答の作成中・承認待ちは送信のボタンが `disabled`（`loading` のスピナー）になり、止める手段が無かった → RAG と同じ共有の `RunStopButton` で、同じボタンが `secondary` の「停止」（`Square`）になり、押すと Run を中止する。止めた回答は会話に停止のアイコン付きの文で出す。RAG の検索・チャット・検索テストの見た目と、回答の評価（RAG・Agent とも共有の `FeedbackControls` に置き換え）の見た目は変えない | UX 契約 buttons.md §3.1。ChatGPT・Copilot・Gemini と同じ、入力欄の送信と停止の 1 つのボタン |
| 64 | **生の色・型のスケールの外の文字・アイコンの寸法・`min-h-*` で決めた複数行の欄の高さを、トークンと `rows` にそろえる**（#800） | RAG の文書の原本プレビューの bbox の縁取りが `rgba(255,255,255,0.9)` の白（ダークテーマで白い線が浮く）。ナレッジベースの処理の流れ・関係情報の図のラベルが 10 / 11px、余白・角丸が数値の px。アイコンに 10 / 11 / 28px。NL2SQL の複数行の欄の高さを `rows` と `textareaClassName="min-h-*"` の両方で決め、多くは `min-h-*` が無効（`rows` の方が高い）、一部（`rows={2}` の欄など）は `min-h-*` が勝っていた。設定のプレビューは `h-44` / `h-56` の固定。NL2SQL の読込スケルトンが `h-[64px]` / `h-[40px]` / `h-[288px]` の px。ロールの詳細の「権限管理で設定」が枠の中の枠 | 縁取りは `ring-1 ring-surface/90`（面の色。ライトは白・ダークは面の暗い色）。図のラベルは `--font-size-xs`（12px）、余白・角丸は `--space-*` / `--radius-*`。アイコンは 14 / 24px。複数行の欄の高さは `rows` だけで決める（`rows={2}` の欄は 70px → 約 64px。プレビューは 7 / 9 行）。スケルトンは rem（`h-18` / `h-11.5` / `h-82`、約 63 / 40 / 287px）。権限の導線は詳細の区切り線の下に置き、`ButtonLink` で出す | トークンの外の値はテーマ・密度の変更に追従しない。adherence の lint が `<table>`・`PageHeader` の `actions` の JSX・`rgba()` などの色の関数・数値の文字サイズと余白・`textareaClassName` の高さを検出するようにした（#800） |
| 65 | **DB ゲートの案内が、状態（未設定・接続できない・初期化が必要・状態を確認できない）ごとに見出し・アイコン・導線を分け、設定を開けない利用者には導線を出さない**（#820） | 全状態で同じ `Database` のアイコンと「設定を開く」（初期化が必要はシステムテーブル）・再試行を出した。接続できない見出しは「データベースを起動してください」、DB に接続できないのに「データベース接続済み・初期化が必要です」と出ることがあった。権限の無い利用者にも押しても開けない設定へのリンクを出した。リンクは `<Link className={buttonVariants()}>` の子にアイコンを手書き | 見出しとアイコン: 未設定「データベースの接続情報が未設定です」（`Settings`）/ 接続できない「データベースに接続できません」（`Unplug`）、ADB の状態が分かれば「Autonomous Database が停止しています」（`PowerOff`）・「…を起動しています」（`Hourglass`、info）・「…を利用できない状態です」（`ServerOff`）と ADB の状態の `StatusBadge`（「Autonomous Database: 停止済み」）/ 初期化が必要「システムテーブルの作成・更新が必要です」（`Wrench`）/ 状態を確認できない（`ServerCrash`）。導線: 未設定 → データベース設定、接続できない → データベース設定の ADB 管理のカード（ADB が起動済みなら接続情報）、初期化が必要 → システムテーブル、状態を確認できない → 再試行だけ。設定を開けない利用者は「システム管理者に連絡して、…を依頼してください。」とフッターの説明だけで、リンクを出さない（再試行は残す）。リンクは `ButtonLink` | 不通と初期化の不足では直す人と直す場所が違う。押しても開けない導線を出さず、次の行動（管理者への連絡）を示す（UX 契約 messaging §3.4.1） |
| 66 | **処理中の表示（`ProcessingIndicator` / `TimedLoadingState`）が、10 秒後の遅延の案内で動かない**（#902） | 経過 10 秒で「通常より時間がかかっています。」の行を足し、領域が 1 行高くなっていた。中央寄せの DB ゲートではスピナーごとカードが 12.25px 上へ動き、流れの中では下の内容を押し下げた | 遅延の案内の行を処理の開始から置き、遅延するまでは同じ文言を `::before` の content で見えない状態に描いて高さだけ予約する（狭い画面で折り返しても同じ高さ。textContent・読み上げには入らない）。遅延したら文言を足し、`role="status"` を付けて読み上げる（予約の行は status にしない）。処理中は最初から 1 行ぶん高い | 後から出る内容の場所を先に取り、レイアウトシフト（CLS）を出さない。スピナーの回転そのもの（#395 の全周トラック）は揺れていなかった（角度・DPR・小数の位置ごとの実測で輪郭の中心のずれ 0.17px 以下） |
| 67 | **通知（Toast）が内容に合わせて広がり、メッセージの本文が文節で折り返す**（#899） | desktop の通知の幅は 22rem 固定で、「Oracle Profile の反映が完了しました。」も「…完了しま / した。」と 2 行に折り返した。Banner・FormStatus なども任意の文字の間で折り返した（「確認 / してから」）→ 通知は内容に合わせて 22〜32rem の間で広がり（短い通知は従来と同じ 22rem、375px は従来どおり画面の幅）、メッセージの部品の本文は `.pr-message-text` で文節で折り返す（Chrome / Edge。Firefox・Safari は従来どおり）。通知は PageHeader のタイトルの側へ最大 10rem 長く伸びる | 短い 1 文は 1 行で読め、折り返すときも語の途中で切らない（§4「`Toaster`」「メッセージの本文の折り返し」、UX 契約 messaging.md §3.1 / §4.1） |
| 68 | **操作の行の補足の説明が常設されず、ラベルの横の info アイコンから出る**（#901） | RAG のチャット・RAG 検索の「回答するモデル」のチップの後ろ（未選択のときだけ）、NL2SQL のチャットの「生成方法」の選択の右、NL2SQL の SQL 生成の「AI要件確認」の右、Agent の Run の作成の「下書きで実行」の右、Run の詳細の「ストリーム方式」のチップの下に、説明文を常に表示していた → ラベル（または操作）の横に 16px の `Info` アイコン（見た目 24px の円）を置き、ポインタを乗せる（150ms）・キーボードのフォーカス・押す（クリック・タップ・Enter / Space）で暗い吹き出し（12px / 400・最大幅 20rem）に出す。Escape・外側を押す・もう一度押すで閉じる。行の高さが説明の折り返しの分だけ低くなる。RAG の説明は選択の有無にかかわらず出せる | 操作の行を密に保ち、補足は必要なときだけ読む。タッチ端末・キーボード・読み上げでも読める（§4「`InfoTip`」） |

### API の非互換

| 対象 | 変更 |
|---|---|
| `Button` | `icon` / `trailingIcon` プロップ新設。子にアイコンを書く旧スタイルは動くが**非推奨** |
| `PageHeader`（#618） | `back`（`PageHeaderBack`: `{ label, onClick, ariaLabel?, disabled?, testId? }`）を追加し、`PageHeaderBack` を export。「一覧へ戻る」は `actions` の `id: "back"` ではなく `back` で渡す（タイトルの上の左端。`data-page-header-back`）。E2E で `getByTestId("<page>-actions")` の中から「一覧へ戻る」を探していたら、`getByRole("button", { name: /一覧へ戻る/ })` か `data-testid` で引く |
| 操作部品の高さと幅（#613） | **新規 export** `ControlSize` / `FieldWidth` / `CONTROL_HEIGHT_CLASS` / `CONTROL_MIN_HEIGHT_CLASS` / `FIELD_WIDTH_CLASS` / `fieldWidthClass` / `fieldControlClassName` / `FieldActionRow`。`TextField` / `SearchField` / `SecretField` / `SelectField` / `SearchableSelectField` に `size`（`"sm" \| "md" \| "lg"`）と `width`（`"xs" \| "sm" \| "md" \| "lg" \| "full"`）を追加。`TextField` の `touchTarget` と `ClearActionButton` の `matchButtonHeight` を**削除**（`ClearActionButton` はマウス環境でも 44px にしない。行と同じ `size` を渡す）。トークン `--control-height-sm` / `md` / `lg` と `--field-width-xs` / `sm` / `md` / `lg` を新設し、`--button-height-*` / `--field-height` は別名にした。`pointer: coarse` では入力欄・選択欄も 44px |
| `Button`（#355） | `loading` 中はネイティブの `disabled` ではなく `aria-disabled="true"`（CSS の `:disabled`・jest-dom の `toBeDisabled()` では判定できない）。`ButtonProps` は `interface` から `type`（`variant` と `tone` の組み合わせの union）に変わり、`variant="danger"` + `tone="danger"` は型エラー。組み合わせの型は `ButtonVariantToneProps` として export |
| `StatusBadge` | `icon` プロップ新設（既定 `true`）。`pending` は `warning` の別名で**非推奨** |
| `PageHeader` | `tabs` / `wide` プロップ新設。アクションの並び順が変わる |
| `.pr-icon-button` | **削除。** `<Button variant="ghost" iconOnly>` へ |
| `--font-size-lg` | 削除（未使用の孤児トークンだった） |
| `AppShell` | スキップリンクと `<main id="pr-main">` を出力 |
| `Button`（#372） | `tooltip`（`string \| false`）/ `tooltipPlacement` プロップ新設。`iconOnly` は既定で `aria-label` と同じ文言の Tooltip を出す。Tooltip を出すときは `title` を無視する |
| `Tooltip` | **新規 export。** `Tooltip` / `TooltipProps` / `TooltipPlacement` |
| `SideSheet`（#664） | **新規 export。** `SideSheet` / `SideSheetProps`。既存の部品の props は変えない |
| `AppShell`（#367） | `navDrawerLabels` プロップ新設（md 未満のドロワーの文言）。md 未満では `sidebar` をドロワーの中に描く。新規 export `useSidebarCollapsed`（サイドバーの `footer` の部品がドロワーの中で展開して描くためのフック）・`DEFAULT_NAV_DRAWER_LABELS`・`NAV_DRAWER_QUERY` |
| `PagedDataTable`（#265） | **新規 export。** `PagedDataTable` / `PagedDataTableProps` / `PaginationLabels`。クライアント側で全件を持つ一覧の標準形（`stickyHeader` + `visibleRows` + 10 件/ページの `Pagination`）。文言は `paginationLabels` で渡す |
| `@engchina/production-ready-system-settings`（#265） | `SECURITY_TABLE_VISIBLE_ROWS` / `SECURITY_TABLE_ROW_CLASS` / `SECURITY_LIST_SCROLL_CLASS` / `SECURITY_LIST_FOCUS_CLASS` の export を**削除。** 同じ値の `INFORMATION_TABLE_VISIBLE_ROWS` / `INFORMATION_TABLE_ROW_CLASS` / `INFORMATION_LIST_SCROLL_CLASS` / `INFORMATION_TABLE_FOCUS_CLASS`（`@engchina/production-ready-ui`）を使う |
| `RequiredBadge` | **新規 export。** `TextField` / `SelectField` の必須表示と同じタグ。アプリ独自の必須表示（`*` など）はこれに置き換える。#531 で `label` を省略可（既定「必須」）にし、`DEFAULT_REQUIRED_LABEL` を export |
| `FieldLabel` / `FieldLegend` / `Fieldset`（#531） | **新規 export**（`FieldsetProps` も）。TextField 以外の入力の必須表示。NL2SQL の `components/ui/required-field.tsx` と system-settings の `oci/required-field.tsx` は削除。`TextField` / `SelectField` / `SecretField` の `requiredLabel` は既定「必須」（省略可）になった |
| `ExecutionConfirmationField` | **新規 export（#379）。** `ExecutionConfirmationField` / `ExecutionConfirmationFieldProps` / `ExecutionConfirmationLabels` / `ExecutionConfirmationStatus` / `executionConfirmationStatus` / `DEFAULT_EXECUTION_CONFIRMATION_LABELS`。NL2SQL の `DbAdminShared` の `ExecutionConfirmationField` は削除 |
| `TextField`（#384） | `leadingIcon` / `trailing` / `onClear` / `clearLabel` / `labelHidden` / `size`（`"md" \| "lg"`）/ `touchTarget` プロップ新設。HTML の `size` 属性（文字数）は受け取らない。入力欄は `div.relative` に包まれる（label の直後の要素が input でなくなる。E2E で `label > svg` や `xpath=ancestor::label` を引いていたら、`getByRole` と入力欄の親で引く）。`type="search"` のブラウザ既定のクリアを出さない。`TextFieldProps` / `TextFieldSize` を export |
| `TextField`（#547） | `suggestions`（`readonly string[]`）プロップ新設。渡すとネイティブの `<datalist>` で候補を選べる自由入力（role=combobox）になり、既定で `autoComplete="off"`。`list` 属性は受け取らない（`TextFieldProps` から除いた）。既存の props・id・aria は変えない |
| `SearchField`（#535） | **新規 export。** `SearchField` / `SearchFieldProps` / `SEARCH_FIELD_DEBOUNCE_MS` / `trimSearchValue`、IME 対応の Enter の判定 `isImeComposing` / `isSubmitEnter` / `KeyboardEventLike`。`@engchina/production-ready-system-settings` の `SecuritySearchField` は `SearchField` で作り直し（`onChange` は確定した値で呼ぶ）、`resultCountLabel` と 0 件の「検索語をクリア」の `SecurityClearSearchAction` を追加 |
| `--radius-control`（#384） | **新規トークン**（utility `rounded-control`）。`--button-radius` / `--input-radius` はその別名 |
| `Tabs`（#396） | `TabItem` に `disabledReason`（無効のときだけ HTML の `title` として付ける）を追加。既存の props・id・aria・キー操作は変えない |
| `Tabs`（#542） | `TabItem` に `invalid` / `invalidLabel` を追加し、`DEFAULT_TAB_INVALID_LABEL` を export。`invalid` のタブは `data-invalid` と、`aria-describedby` に `<idPrefix>-tab-<id>-invalid` を持つ（バッジがあればバッジの id と並べる）。既存の props・id・キー操作は変えない |
| `Disclosure`（#397） | **新規 export。** `Disclosure` / `DisclosureProps` / `DisclosureVariant` / `DisclosureSurface` / `DisclosureTone` / `DisclosureSize`。`<details>` を包む開閉の標準形。adherence の lint が製品の JSX の `<details>` を検出する |
| `DisclosureChevron`（#397） | 折りたたみの向きが `rotate-90`（左向き）→ `-rotate-90`（右向き）。`getComputedStyle(icon).rotate` を検証している E2E は `"90deg"` → `"-90deg"`。`expanded="group"` は残すが、入れ子の `<details>` では外側の open に引きずられるため新規コードは `Disclosure` か boolean を使う |
| `Toaster`（#411） | `placement` プロップ（`"bottom-left" \| "bottom-right"`）を**削除。** 置き場所は `Toaster` が決める（md 以上は `PageHeader` に重ねてページの操作の左、md 未満は上端のバー）。`PageHeader` の `<header>` に `data-page-header`、ページの操作の並びに `data-page-header-actions` を付ける（`Toaster` が位置を読む）。通知の領域に `data-toast-placement`（`page-header` / `below-page-header` / `top-right` / `top-bar`） |
| `RowTitleButton`（#421） | **新規 export。** `RowTitleButton` / `RowTitleButtonProps` / `RowTitleButtonMaxLines`。RAG・Agent の `EntityLayout` の `RowTitleButton` は削除（RAG の `ariaLabel` / `dataAttributes` は、標準の `aria-label` / `data-*` をそのまま渡す）。行の中の要素として `data-row-title-button` を持つ |
| `Tooltip`（#421） | `describe?: boolean` を追加（既定 true）。false で説明として結び付けず、吹き出しを `aria-hidden` にする |
| `InfoTip` / `Tooltip`（#901） | **新規 export** `InfoTip` / `InfoTipProps` / `INFO_TIP_SHOW_DELAY_MS`。`Tooltip` の props・見た目・振る舞いは変えない（内部の開閉の状態機械に、押して開いたまま固定する `press` / `dismiss` と開いたきっかけ `press` を足し、吹き出しの描画を `InfoTip` と共有した） |
| `RowTitleButton`（#583） | `href?: string` を追加（渡すと `<a href>` のリンクの形。修飾キーの無いクリックは `onClick`、修飾キー付き・中クリックはブラウザの既定）。`ref` の型は `Ref<HTMLButtonElement \| HTMLAnchorElement>`。E2E で題名を `getByRole("button")` で探している一覧は、`href` を渡すと `getByRole("link")` になる |
| `TextareaField`（#584） | **新規 export。** `TextareaField` / `TextareaFieldProps` / `defaultTextareaCount`。adherence の lint（`design-system/restricted-syntax`）が製品の JSX の `<textarea>` を検出する |
| `@engchina/production-ready-system-settings`（#421） | **新規 export** `SecurityIdentityRowTitleButton`（ID と表示名の 2 段表示を `RowTitleButton` に載せたもの） |
| `@engchina/production-ready-system-settings` の `RolePermissionTargetSection`（#608） | `load`（全件を読む）を**削除**し、`query(query, { signal })` に置き換え。`query` は `RolePermissionTargetQuery`（`q` / `limit` / `offset` / `ids`）を受けて `RolePermissionTargetPage`（`items` / `total` / `warning`）を返す。クエリ文字列は `rolePermissionTargetSearchParams` で作る。`RolePermissionTargetLoadResult` / `targetLoadRows` は削除。新規 export `resolveTargetItems`（選択済みの ID の名前を 100 件ずつ `ids` で読む）/ `TARGET_PAGE_SIZE`。3 製品の backend は対象ごとの API（RAG / Agent: `GET /api/security/access-targets/{kind}`、NL2SQL: `GET /api/security/profile-access/profiles`）で `Page` を返す |
| `ListToolbar` / `ListPicker` / `LoadMoreFooter`（#600） | **新規 export。** `ListToolbar` / `ListToolbarProps` / `ListPicker` / `ListPickerProps` / `ListPickerItem` / `ListPickerGroup` / `ListPickerLabels` / `ListPickerSearch` / `DEFAULT_LIST_PICKER_LABELS` / `LoadMoreFooter` / `LoadMoreFooterProps`。NL2SQL の `profileVirtualList.ts`（仮想スクロールの計算）は削除し、`packages/ui` の `lib/list-window.ts` に移した。`DbObjectSelectorFooter` の props は変えない（中身は `LoadMoreFooter`） |
| `SelectField` / `SearchableSelectField` / `FieldActionRow`（#631） | 追加のみ（既存の props・id・aria は変えない）。`SelectField` に `disabled`（ネイティブの disabled。開かない・Tab で止まらない・typeahead も効かない）・`labelHidden`・`data-testid`（ボタン）を追加し、ボタンと選択肢に値の `data-value` を出す。`SearchableSelectField` は無効のときのホバー・シェブロン・文字の色と「開いているときに無効になったら閉じる」を `SelectField` にそろえた。`FieldActionRow` は `actions` が null / false なら操作の列を描かない |
| `SelectField` / `SearchableSelectField`（#647） | 追加のみ（既存の props・id・aria は変えない）。`SelectField` に `emptyOptionLabel`（任意の欄で未選択へ戻す、先頭の空の値の選択肢。`required` の欄・`options` に空の値がある欄では出さない。値の型 `T` が `""` を含むときだけ渡せる）を追加。`SelectField` / `SearchableSelectField` に `describedBy`（欄の外の説明の id。`helper`・`error` の id の後ろに足して `aria-describedby` に渡す）を追加 |
| `SearchableSelectField` / `SearchableMultiSelect`（#578） | **新規 export。** `SearchableSelectField` / `SearchableSelectFieldProps` / `SearchableMultiSelect` / `SearchableMultiSelectProps` / `SearchableSelectOption` / `SearchableSelectRemote` / `SearchableSelectLabels` / `SearchableMultiSelectLabels` / `DEFAULT_SEARCHABLE_SELECT_LABELS` / `filterSearchableOptions`。既存の部品の props は変えない |
| `SearchableSelectField`（#635） | `leadingIcon?: LucideIcon` を追加（任意。ボタンの先頭の 16px のアイコン、読み上げない）。渡さなければ見た目は変わらない |
| `RunStopButton` / `FeedbackControls`（#805） | **新規 export。** `RunStopButton` / `RunStopButtonProps` / `runStopClickAction` / `isRepeatedActivationKey` / `RunStopAction`（RAG の `components/RunStopButton.tsx` と `lib/run-stop.ts` から移した）と、`FeedbackControls` / `FeedbackControlsProps` / `FeedbackControlsLabels` / `FeedbackControlsValue` / `FeedbackControlsSubmission` / `FeedbackRating` / `FeedbackReasonOption` / `isSameFeedback`。既存の部品の props は変えない |
| `PageHeader`（#823） | `actions` の型を `PageHeaderAction[] \| ReactNode` から `PageHeaderAction[]` にした（JSX の `<Button>` 等を渡す後方互換を**削除**。3 製品は #800 で配列に移した）。空の配列は操作の群れを描かない |
| `@engchina/production-ready-system-settings` の `RoleManagementPage`（#823） | `renderRoleDetailExtra` を**削除。** ロールの詳細の末尾は `permissionSummary`（機能権限の件数と権限管理への導線。#800）だけにする |
| `PageHeader` / `ObjectActionBar` / `FormActionBar`（#582） | 「その他の操作」のボタンとメニューの読み上げ名が「その他の操作（<操作のグループの名前>）」になる（見た目の文言は同じ）。`getByRole("button", { name: "その他の操作", exact: true })` の E2E は一致しなくなるので、`exact` を外すか `data-testid`（`page-actions-more` / `<testId>-more` / `form-actions-more`）で探す。`PageHeader` の lg 未満は、メニューに入るのが 1 つだけなら畳まない |

---

## 8. 実装手順

### A. 素直な移植（推奨）

1. `css/tokens/palette.css` を `packages/ui/src/styles/palette.css` として新規追加
2. `css/tokens/colors.css` で既存 `src/styles/tokens.css` の色ブロック（`:root` と `.dark`）を**置換**。`.dark` の40値手書きブロックは**削除**
3. `css/tokens/colors-graph.css` を追加（グラフを使わない RAG / Agent では `@import` を外してよい）
4. `css/tokens/{typography,elevation,base,spacing,radius,motion,fonts}.css` を差分適用
5. `css/tokens/a11y.css` を追加し、`styles.css` の**`components.css` より後**に `@import`
6. `css/tokens/compat.css` を追加し、`styles.css` の**最後**に `@import`
7. `css/components/components.css` を `packages/ui` の同等ファイルに適用
8. `components-reference.md` の `Tabs` / `PageBody` を参照に、`packages/ui` の作法（`.tsx` + Tailwind）で新規実装
9. 同ファイルの `Button` / `StatusBadge` / `PageHeader` / `Pagination` / `DataTable` / `AppShell` / `Sidebar` を参照に既存コンポーネントを更新
10. `packages/ui` 内を §5 の表で機械置換 → 文脈判断3件を目視
11. `pnpm build` → 3アプリで `file:` リンクを更新して起動確認
12. 3アプリ側も §5 で機械置換
13. **全アプリの旧名参照が0になったら `compat.css` と その `@import` を削除**

`compat.css` は旧名 → 新名の `@deprecated` エイリアスです。**これがある間は既存コードが無改造で動く**ので、`packages/ui` と3アプリを別 PR に分割できます。

### B. `light-dark()` が使えない場合のフォールバック

対応ブラウザ下限が Chrome 123 / Safari 17.5 を下回る場合のみ。`tokens/colors.css` を次の形に展開します。

```css
:root, [data-theme="light"], .light { --color-surface: var(--neutral-0);   /* … */ }
[data-theme="dark"], .dark          { --color-surface: var(--neutral-900); /* … */ }
@media (prefers-color-scheme: dark) {
  [data-theme="auto"]               { --color-surface: var(--neutral-900); /* … */ }
}
```

**この場合もダーク値の記述が2箇所に増えるだけで、TIER 1 / TIER 2 の分離は維持されます**（旧構成との本質的な違いはそこ）。`[data-surface]` スコープは `color-scheme` に依存しているので、フォールバック時はスコープ内で全トークンを明示的に上書きしてください。

### C. Tailwind v4 の `@theme` に載せる場合

TIER 2 のトークンを `@theme inline` に登録すると `bg-surface` / `text-fg-muted` / `border-border-control` のようなユーティリティが生えます。**TIER 1 は `@theme` に入れない**こと（ユーティリティとして露出すると原色の直参照が起きます）。

---

## 9. 検収基準

**トークン**
- [ ] `packages/ui` と3アプリに旧トークン名の参照が0件
      （`grep -rn "var(--card)\|var(--muted)\|var(--background)\|var(--control-border)\|var(--button-border)"`）
- [ ] `compat.css` を外してもビルドと画面が壊れない
- [ ] `.dark` に色値の手書き宣言が1つも残っていない
- [ ] 入力欄・secondary ボタン・チップの枠線コントラストが **3:1 以上**（light / dark 両方）
- [ ] ダークで card / popover / dialog の背景色が**それぞれ異なる**
- [ ] サイドバーとコードブロックに `#ffffff` / `rgb(255 255 255 / …)` の直書きが0件
- [ ] `data-theme="auto"` で OS 設定に追従する
- [ ] `class="light"` / `class="dark"` の旧 API が引き続き動く

**タイポグラフィ**
- [ ] 本文が 14px、表・メタが 12px でレンダリングされる
- [ ] 余白が移行前と同一（ルート 14px を変えていない）
- [ ] 日本語の本文で行頭に `、。ー っ ゃ` が来ない
- [ ] アイコンサイズが 14 / 16 / 20 / 24 の4値のみ

**レイアウトとコンポーネント**
- [ ] **1920px で PageHeader のタイトル左端と本文カードの左端が一致する**（ずれ 0px）
- [ ] 全画面が `PageHeader` → `PageBody` の構成（手書きの padding div が0件）
- [ ] `PageHeader` と `PageBody` の `wide` が常に同値
- [ ] `wide` 画面で、カード内のフォーム・危険な操作区画・検索欄に max-width が無く、1280 / 1920 / 2560px で「左寄せ取り残し」が 0 件（例外: ダイアログ等の本体、長文段落の `max-w-prose`、バッジの truncate）
- [ ] 並べる要素がある検索欄・ファイル選択欄が、1 本で行全体に伸びていない（toolbar は比率で配分）
- [ ] ヘッダーのアクションの右端が primary
- [ ] 同じボタングループ内でアイコンの有無が混在していない
- [ ] `loading` にしてもボタンの幅とラベルが変わらない
- [ ] Enter で押したボタンが `loading` になってもフォーカスがボタンに残り（`aria-disabled` / `aria-busy`）、完了後もボタンのまま。`loading` 中の Enter / Space / クリック / 入力欄の Enter で二重に送信しない
- [ ] `loading` を渡す `Button` がすべて `icon` を持つ（adherence の lint の違反 0 件）
- [ ] 赤塗りの `danger` が破壊的な確定（確認ダイアログの確定・確認語つきの危険な操作）だけに使われている
- [ ] ヘッダーの操作のグループ（危険操作 / ページツール / 作業開始）の境界に区切り線がある
- [ ] 表ヘッダのソートがセル全体で押せる
- [ ] カード内の操作行で、主操作と破壊的操作が隣り合っていない（二者択一は反対の端、影響の大きい操作は危険な操作区画、行内はメニュー）
- [ ] 確認語欄が入力前に danger 色を使っていない

**アクセシビリティ**
- [ ] サイドバー内を Tab 移動してフォーカスリングが視認できる
- [ ] フォーカスの表示が outline 1 つ（ring との二重表示が 0 件。adherence の lint の違反 0 件）
- [ ] Tab キーで最初に「本文へスキップ」に到達する
- [ ] Windows ハイコントラストモードでボタン・入力・アクティブ nav が消えない
- [ ] `StatusBadge` をグレースケールにしても状態が判別できる
- [ ] 必須表示が状態色（warning / danger）を使わず、テキストで示されている（アプリ独自の `*` や色付きバッジが0件）
- [ ] 必須の欄だけに「必須」のタグがあり、入力（または radiogroup）に `aria-required`、任意の欄と placeholder に「(任意)」が無い（adherence の lint の違反 0 件）
- [ ] タブが ← → / Home / End で操作できる
- [ ] タッチ端末（`pointer: coarse`）で `ToggleChip` / `Switch` の当たり判定が 44px 以上、見た目の大きさとマウス環境の当たり判定は変わらない
- [ ] 375px でタブが入りきらないとき、スクロールできる方向の端だけがフェードし、キーボードで選んだタブがフェードに隠れない
- [ ] 強制カラーモードで、選んだタブだけに下線（`Highlight`）が出る
- [ ] 検索欄が `TextField`（手書きの検索欄の adherence の lint の違反 0 件）で、高さが隣の操作部品と同じトークン。クリアボタンに Tab で届き、押すと入力欄にフォーカスが戻る
- [ ] 一覧の絞り込みの検索欄が `SearchField`（`type="search"` の adherence の lint の違反 0 件）で、隣に検索・絞り込みのボタンが無い。入力に合わせて絞り込み、IME の変換中は絞り込まない。0 件に「検索語をクリア」がある（#535）

---

## 10. ファイル一覧

| パス | 内容 |
|---|---|
| `css/styles.css` | エントリポイント。層の順に `@import` |
| `css/tokens/palette.css` | **TIER 1** 原色 |
| `css/tokens/colors.css` | **TIER 2** 意味トークン + `[data-surface]` スコープ |
| `css/tokens/colors-graph.css` | グラフ配色（NL2SQL 固有の語彙） |
| `css/tokens/typography.css` | タイプスケール（px） |
| `css/tokens/spacing.css` | 余白 + アイコン寸法 + `--content-max-width` + `--tab-height` / `--tab-fade-width` |
| `css/tokens/elevation.css` | 影（両テーマ）+ z-index 7段 |
| `css/tokens/base.css` | 14px ルート、日本語行組版、フォーカス、スキップリンク |
| `css/tokens/a11y.css` | `forced-colors` / `prefers-contrast` 応答層 |
| `css/tokens/compat.css` | 旧名 → 新名の `@deprecated` エイリアス。**移行完了後に削除** |
| `css/components/components.css` | **TIER 3** 状態（button / tab / chip / input / nav / sort header） |
| `components-reference.md` | 参照実装 9 件。**新規** `Tabs` / `PageBody`（+`Section`）、**変更** `PageHeader` / `Button` / `StatusBadge` / `DataTable` / `Pagination` / `AppShell` / `Sidebar`。props の型も併記 |
| `reference/*.html` | 目視確認用。ブラウザで開くとライト／ダーク並びで見える |
| `ARCHITECTURE.md` | 依存の向き、責任の境界、アプリ別の作業、変更を入れたいときの手順 |
| `adherence.oxlintrc.json` | lint ルール（oxlint / ESLint 共通の純粋な JSON） |
| `design-system-plugin.mjs` | oxlint 用 JS プラグイン（`no-restricted-syntax` 相当） |

---

## 11. まだ決まっていないこと / 本移行に含まれないこと

**実装前に確認が必要**

1. **社内の対応ブラウザ下限。** `light-dark()` は Chrome 123+ / Safari 17.5+ / Firefox 120+。下回る場合は §8-B。
2. **Tailwind v4 の `@theme` に載せるか。** 載せる場合 TIER 1 は入れない（§8-C）。
3. **表の行高が増えることの是非。** 文字が 10.5→12px になるため、1画面に収まる行数が減ります。`DataTable` の `dense` を既定にするか検討してください。

**別タスク（このハンドオフの範囲外）**

- **`DropdownMenu`** — 破壊的アクションをヘッダーからオーバーフローに移すために必要。最優先
- ~~**`Tooltip`** — `iconOnly` ボタンが増えたので必須~~ → 実装済み（#372、§4「`Tooltip`」）。`iconOnly` の `Button` は既定で出す
- **`Checkbox`** — 不在のため `DataTable` の一括選択が組めない
- **`CommandPalette`** — 3 製品とも持たないと決めた（#308）。サイドバーの起動ボタンも削除した。`--shadow-palette` / `--z-palette` は未使用のトークンとして残っている
- **`html { font-size: 14px }` の撤去** — ルート上書きは利用者のブラウザ設定を無視します。撤去すると Tailwind の rem ユーティリティ経由で全余白が 14.3% 増えるため、単独のタスクとして計画が必要
- **空状態 / ローディングの使い分け規定** — `DataTable` の `emptyText`（表内1行）と `StateViews` の `EmptyState`（カードごと置換）の2系統、ローディングは4系統あり、どちらを使うかの規定が無い。推奨: 「行が0件 → 表内テキスト」「取得前・権限なし・前提未達 → StateViews」「200ms 未満は何も出さない / 200ms–1s は Skeleton / 1s 超は LoadingState」
