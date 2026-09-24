# Handoff: 基盤トークン再設計 + タイプスケール + 新コンポーネント

**対象リポジトリ:** `engchina/no.1-production-ready-platform` / `packages/ui` (`@engchina/production-ready-ui`)
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
| `adherence.oxlintrc.json` + `design-system-plugin.mjs` | 生の hex / inline style の生の px / 書体 / 型・角丸の任意値 / 旧トークン名 / 内部パス import / loading 中のラベル差し替えを検出する lint ルール | 各 repo の lint 設定から sibling パスで参照する（`AGENTS.md`「lint」節） |
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

> **タブ＝同じ対象の別の見方に切り替える。チップ（`ToggleChip`）＝データを絞り込む。**
> 意味が違うので、下線（タブ）と pill（チップ）で見た目を明確に分けています。**流用しないこと。**
> 旧実装は `ToggleChip` をタブ代わりに使っており、「押したら内容が入れ替わるのか、減るだけなのか」が判別できませんでした。

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
| 入力欄に隣接する操作 | 操作の役割で決定 | `touchTarget`（44px） | 操作を示すアイコン |

- **タッチ端末は全サイズ44px**。32/36/40pxは位置に応じた密度の違いであり、primaryだけを大きくする規則ではない。同じ操作行のprimary/secondary/ghostは同じ高さにする。
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
- アイコンと同じ 16px 枠なので **幅が変わらない**
- `aria-busy="true"` と `disabled` が自動で付く
- **旧実装のバグ:** `loading` でスピナーを**追加**していたため、スピナー＋アイコン＋ラベルの三重表示で幅が跳ねていました
- 1秒を超えて領域全体が待ちになる処理は、ボタンではなく領域側で `LoadingState`。ボタンのスピナーは「この操作が進行中」だけを表す

### カード内の操作行（新設）— ★ 主操作と破壊的操作の配置

ページヘッダー（右寄せグループ、右端が primary）とダイアログ以外で、カード・フォーム・パネルの中の操作ボタンをどこに置くかの規約です。**主操作と破壊的な操作を隣に並べない**（誤操作防止。NN/g、Apple HIG）ことが、すべての型に共通する原則です。

| 場面 | 置き方 | 例 |
|---|---|---|
| カード末尾の主操作 | 区切り線（`border-t`）の下の操作行に**左寄せ**。並びは primary → secondary（読む順） | 保存 / 評価を開始 |
| 同じ対象について「確定するか、捨てるか」の二者択一の破壊的操作 | **同じ操作行の反対の端（右端、`sm:ml-auto`）**。`ghost` + `tone="danger"`。確認語が要らない操作は確認語欄の中に置かない | 合成データの「確認したデータを適用」（左）と「確認用データを破棄」（右端） |
| めったに使わない影響の大きい操作 | カード末尾の**「危険な操作」区画**に分ける（見出し + 影響の説明 + 確認語 + danger ボタン）| すべて再作成 / 構成を解除（GitHub の Danger Zone と同じ型） |
| 一覧の行・詳細ヘッダーの破壊的操作 | 行内に文字ボタンを並べず、**「その他の操作」メニュー**に入れ、区切り線の下に置く | 削除 / アーカイブ / 無効化 |
| 実行中の処理を止める「中止」 | **進捗表示のそば**（状態の見出しや進捗ストリップの右側）| 評価の中止 / 構築の中止 |

- **狭い画面（640px 未満）** では操作行を縦に並べ、**主操作を上、破壊的操作を下**にする（`flex-col` → `sm:flex-row`）。各ボタンは `w-full sm:w-auto`。
- 破壊的操作の確定は、必ず `ConfirmDialog`（実際の動詞のラベル）または確認語で行う。配置だけで安全を担保しない。
- ページヘッダーは右寄せのため並びが逆（左端 danger → 右端 primary）になるが、「primary を最も押しやすい位置、danger を primary から最も遠い位置」という考え方は同じ。

**確認語欄（実行確認語）の色**
- 入力前は danger 色を使わない。ラベルは `--color-fg`、説明文は `--color-fg-muted`。操作前から赤いと、エラーが起きているように見えるため。
- 一致しない語を入力したとき（`aria-invalid="true"`）だけ、説明文と状態バッジを danger 色にする。
- 確認語欄であることは、見出し・状態バッジ（未入力 / 不一致 / 確認済み）・入力欄のフォーカス色で示す。

### `StatusBadge`（変更）

- **アイコンを必須化**（`icon` 既定 `true`）。success / danger の輝度がほぼ同じで色覚型によって見分けられないため、**形で冗長に符号化**します。強制カラーモードでも意味が残ります
- `pending` バリアントは **非推奨**（旧実装で `warning` と**完全に同値**でした）。互換のため `VARIANTS.pending = VARIANTS.warning` を残していますが、呼び出し側は `warning` に置換してください

### `TextField` / `SelectField` の必須表示（変更）— ★ 必須は「情報」であり「状態」ではない

`required` + `requiredLabel` で出す必須表示を、**中立色のテキストタグ**に統一しました。単体の `RequiredBadge` も export します。

```jsx
<TextField id="user" label="ユーザー名" required requiredLabel="必須" />
// TextField で表せない入力（ファイル選択・fieldset の legend・複合入力）
<legend>対象の業務ビュー <RequiredBadge label="必須" /></legend>
```

| 決めたこと | 理由 |
|---|---|
| **状態色（warning / danger）を使わない。** 文字 `--color-fg-muted`、輪郭 `--color-border-strong`（装飾）、地は塗らない | 状態色は**利用者の対応が要る状態**の信号です。必須は操作前から決まっている項目の属性なので、状態色で出すと、フォームを開いた瞬間に注意表示が並んでしまいます。そうなると、本物の警告やエラーが埋もれます。未入力で送信したときの `FieldError`（danger）だけが状態色を使います |
| **記号 `*` ではなくテキスト（翻訳済みの「必須」）** | `*` は意味を伝える凡例文（「* は必須入力項目です」）が要ります。しかし利用者は凡例を読みません（NN/g）。GOV.UK はアスタリスクを使わず、デジタル庁デザインシステムもテキストの「※必須」を使います。テキストなら色にも記号の学習にも頼りません（WCAG 1.4.1 / 3.3.2） |
| 文字コントラスト 4.5:1 以上 | 実測: light は surface 上 4.83:1、sunken 上 4.55:1。dark は surface 上 8.72:1、overlay 上 7.06:1。淡い灰色の必須表示は弱視の利用者が見落とします（NN/g） |
| 形は pill（`--radius-pill`）、`ring-inset` の輪郭で行の高さを変えない | バッジの角丸トークンに合わせます。アイコンは付けません。`StatusBadge`（状態 = アイコン必須）と区別するためです |
| 読み上げ: `TextField` / `SelectField` は `aria-required` で伝え、タグは `aria-hidden` | 「必須、必須」の二重読み上げを防ぎます。`RequiredBadge` を単体で使う場合、既定では読み上げます |
| 条件付きの必須も同じタグで、文言で区別する（例:「OCI 運用時必須」） | info 色のバッジで別扱いすると、必須表示が 2 種類になります |

- **`required` のときは `requiredLabel` を必ず渡してください。** 渡さないと見た目で必須が分からず、WCAG 3.3.2 を満たしません（`packages/ui` は日本語を持たないため既定文言がありません）
- 必須表示を**アプリで再実装しない**でください（赤い `*`・warning バッジ・info バッジが3アプリに混在していました）。`TextField` で表せない入力には `RequiredBadge` を置きます
- ネイティブの `required` 検証は付けません。未入力の検出と `FieldError` の表示はアプリ側で行います（従来どおり）

### `DataTable`（変更）

- **ソートの当たり判定を `<th>` 全体に**（`.pr-sort-header`）。旧実装は `<button>` が文字高（約15px）しかなく、**24px 最小タップ領域も割っていました**。hover も無く押せると分かりませんでした
- `wordBreak: "break-word"` を廃止 → 日本語が任意の文字で分断される問題を解消
- ヘッダーの地を `--color-surface-sunken` に
- 見出しセルは折り返さない。並べ替えボタンの高さは `--button-height-sm`（タッチ端末 44px）
- 一覧用の optional props（platform #56）: `stickyHeader`、`visibleRows`（表頭 + 先頭 N 行の実測高さで内部スクロール）、`scrollAriaLabel`（キーボードでスクロールできる region）、`selectedRowKey` / `isRowSelected`、`rowProps`、`renderRowDetail`、列の `rowHeader`。詳細は `components-reference.md`。**アプリで `<table>` を手書きしない**

### `AppShell`（変更）

- **`.pr-skip-link`（本文へスキップ）と `<main id="pr-main" tabIndex={-1}>` を出力。** サイドバーが20項目を超えるため、キーボード利用者が毎ページ全 nav を Tab 通過していました

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
--z-dropdown: 100    SelectField のリスト、コンボボックス
--z-popover:  200    ツールチップ、日付ピッカー
--z-sticky:   300    PageHeader、スキップリンク
--z-toast:    800    ToastRegion（モーダルの下）
--z-scrim:    900    モーダルの暗幕
--z-dialog:  1000    ConfirmDialog
--z-palette: 1200    コマンドパレット
```

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
```

旧構成は規定が無く、`DataTable` のセルが `word-break: break-word` だったため **「デー／タベース」のように任意の文字で分断**され、禁則処理も効かず **行頭に 、。ー っ ゃ** が来ていました。Japanese-first を掲げるシステムとしては致命的な欠落でした。

### アクセシビリティモード（`tokens/a11y.css`）

- `@media (forced-colors: active)` — システム色（`Canvas` / `CanvasText` / `Highlight` / `HighlightText` / `GrayText` / `LinkText`）に総入れ替え。**強制カラーモードでは塗りだけの要素が消えるため、境界線を明示的に与えています。** hover 行は `Highlight` と `HighlightText` を**必ず対で**指定（背景だけ変えると文字が読めなくなる）
- `@media (prefers-contrast: more)` — 罫線と副次テキストを強め、フォーカスリングを 3px に

---

## 7. 意図的な見た目の変更（回帰ではありません）

QA に事前共有してください。**16点あります。**

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

### API の非互換

| 対象 | 変更 |
|---|---|
| `Button` | `icon` / `trailingIcon` プロップ新設。子にアイコンを書く旧スタイルは動くが**非推奨** |
| `StatusBadge` | `icon` プロップ新設（既定 `true`）。`pending` は `warning` の別名で**非推奨** |
| `PageHeader` | `tabs` / `wide` プロップ新設。アクションの並び順が変わる |
| `.pr-icon-button` | **削除。** `<Button variant="ghost" iconOnly>` へ |
| `--font-size-lg` | 削除（未使用の孤児トークンだった） |
| `AppShell` | スキップリンクと `<main id="pr-main">` を出力 |
| `RequiredBadge` | **新規 export。** `TextField` / `SelectField` の必須表示と同じタグ。アプリ独自の必須表示（`*` など）はこれに置き換える |

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
- [ ] 表ヘッダのソートがセル全体で押せる
- [ ] カード内の操作行で、主操作と破壊的操作が隣り合っていない（二者択一は反対の端、影響の大きい操作は危険な操作区画、行内はメニュー）
- [ ] 確認語欄が入力前に danger 色を使っていない

**アクセシビリティ**
- [ ] サイドバー内を Tab 移動してフォーカスリングが視認できる
- [ ] Tab キーで最初に「本文へスキップ」に到達する
- [ ] Windows ハイコントラストモードでボタン・入力・アクティブ nav が消えない
- [ ] `StatusBadge` をグレースケールにしても状態が判別できる
- [ ] 必須表示が状態色（warning / danger）を使わず、テキストで示されている（アプリ独自の `*` や色付きバッジが0件）
- [ ] タブが ← → / Home / End で操作できる

---

## 10. ファイル一覧

| パス | 内容 |
|---|---|
| `css/styles.css` | エントリポイント。層の順に `@import` |
| `css/tokens/palette.css` | **TIER 1** 原色 |
| `css/tokens/colors.css` | **TIER 2** 意味トークン + `[data-surface]` スコープ |
| `css/tokens/colors-graph.css` | グラフ配色（NL2SQL 固有の語彙） |
| `css/tokens/typography.css` | タイプスケール（px） |
| `css/tokens/spacing.css` | 余白 + アイコン寸法 + `--content-max-width` + `--tab-height` |
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
- **`Tooltip`** — `iconOnly` ボタンが増えたので必須
- **`Checkbox`** — 不在のため `DataTable` の一括選択が組めない
- **`CommandPalette`** — `--shadow-palette` と `--z-palette` は定義済みなのに本体が無い（孤児トークン）。サイドバーに起動ボタンだけある
- **モバイル対応の方針決定** — `.pr-button` に `@media (max-width: 639px)` の 44px 強制があるが、`AppShell` は `height: 100vh` + 固定 15rem サイドバーで 639px では成立しない。**到達しない死にコード**。desktop-only と明記して coarse-pointer ルールを削除するか、レスポンシブシェルを作るか、どちらかに倒すべき
- **`html { font-size: 14px }` の撤去** — ルート上書きは利用者のブラウザ設定を無視します。撤去すると Tailwind の rem ユーティリティ経由で全余白が 14.3% 増えるため、単独のタスクとして計画が必要
- **空状態 / ローディングの使い分け規定** — `DataTable` の `emptyText`（表内1行）と `StateViews` の `EmptyState`（カードごと置換）の2系統、ローディングは4系統あり、どちらを使うかの規定が無い。推奨: 「行が0件 → 表内テキスト」「取得前・権限なし・前提未達 → StateViews」「200ms 未満は何も出さない / 200ms–1s は Skeleton / 1s 超は LoadingState」
