# ユーザーへのメッセージの提示（Messaging）

> 3製品共通の正本。通知・成功 / エラーの表示・フォームの検証・確認ダイアログ・空 / 読込 / エラーの状態を新しく作る・直すときは本規約に従う。
> 見た目（トークン・z-index・アニメーション・`Banner` / `Toast` / `FormStatus` などの部品仕様）は [デザインシステム](../design-system/README.md) が正本。
> 製品固有の通知の例・実装の場所・移行記録は、各製品の `docs/frontend-messaging-spec.md` に書く。
>
> 準拠：`ui-ux-pro-max` の §1 Accessibility / §2 Touch & Interaction / §7 Animation / §8 Forms & Feedback / §9 Navigation。

---

## 0. 設計原則

1. **チャネルは 6 種類だけ**（§1）。新しい提示方法を勝手に増やさない。
2. **意味（severity）は 4 トーンだけ**：`success` / `warning` / `danger` / `info`。生 hex を使わず semantic token を使う。
3. **色だけで意味を伝えない**（`color-not-only`）。トーンごとに必ずアイコンを並べる。
4. **文言はすべて i18n 経由**（製品の `t()`）。コンポーネントや各チャネルの API に日本語の文字列を直書きしない（`ApiError.message` などサーバー由来の文字列はよい）。
5. **メッセージは「原因 + 次の行動」**（`error-clarity` / `error-recovery`）。「失敗しました」だけで終えない。
6. **同じ失敗の「原因 + 次の行動」は 1 つの標準チャネルだけを正本にする。** 多項目フォームの「エラー概要 + FieldError」は例外として認めるが、同じ文を FieldError と FormStatus / Toast に重ねて出さない。

---

## 1. メッセージのチャネル（6 種）と使い分け

| # | チャネル | 役割 | 持続 | ブロッキング | 主な用途 |
|---|---|---|---|---|---|
| 1 | **Toast** | 一時通知 | 自動で消える（3–5s） | しない | 保存成功、コピー完了、バックグラウンド処理の結果、取り消せる操作 |
| 2 | **FieldError** | 入力欄の検証 | 入力を直すまで | しない | 入力欄ごとのバリデーション（必須・形式） |
| 3 | **FormStatus** | フォーム / 操作の結果 | 次の操作まで | しない | 送信ボタンの近くの成功 / 失敗（例：「保存しました」） |
| 4 | **Banner（Alert）** | ページ / セクションに常設 | ずっと、または手動で閉じる | しない | 「接続未設定」「縮退モード」「保存前の警告」などの状況 |
| 5 | **ConfirmDialog** | 確認ダイアログ | 応答まで | **する** | 削除・上書き・不可逆操作の確認 |
| 6 | **State views** | 領域の状態 | データ取得まで | 領域を占める | 読込（Skeleton）/ 空（Empty）/ エラー（Error + 再試行） |

### エラーの影響範囲ごとの選び方

| 影響範囲 | 標準チャネル | 配置・保持の規則 |
|---|---|---|
| 1 つの入力欄 | `FieldError` | 入力の直下。`aria-invalid` / `aria-describedby` を結び付け、送信後は最初の欄へフォーカスする。 |
| 利用者が直前に実行した保存・作成・実行 | `FormStatus` / `ActionResultRegion` | ボタン群または結果領域のすぐ近く。再試行または関連入力の変更まで残す。 |
| ページ・機能がずっと使えない | `Banner` / `PageNotice` | ページまたは最小の影響範囲の先頭。復旧の action を添える。 |
| データ領域の取得失敗 | `ErrorState` | その領域だけを占め、再試行を出す。ページ全体の障害へ格上げしない。 |
| 固定の表示面がない非同期の失敗 | danger Toast | 最後の手段。固定面と併用せず、利用者が閉じるまで残す。 |
| Job・step・CSV の行 | 状態 / 結果のデータ | Badge・stepper・表は状態を示す。具体的な理由は上の標準の面 1 つにだけ出す。 |

### 決め方

```
ユーザーに何かを伝えたい
│
├─ 続けてよいかの確認が必要？（削除・上書き・不可逆）
│     └─ YES → 5. ConfirmDialog
│
├─ 特定の入力欄のエラー？
│     └─ YES → 2. FieldError（欄の直下）
│
├─ 領域全体がデータ未取得 / 空 / 取得失敗？
│     └─ YES → 6. State views（Skeleton / EmptyState / ErrorState）
│
├─ ページ / セクション全体の状況を出し続けたい？（設定未完了など）
│     └─ YES → 4. Banner
│
├─ 送信 / 保存の直近の結果を、その場で見せたい？
│     └─ YES → 3. FormStatus（操作ボタンの近く）
│
└─ それ以外（操作の完了通知・非同期の結果・コピー成功など）
      └─ 1. Toast
```

**禁止事項**
- 破壊的操作の結果の通知に Toast を使うのはよい。ただし**確認そのものは ConfirmDialog** で行う（Toast に確認を載せない）。
- 入力欄のエラーを Toast やページ上部だけで出さない（`error-placement`：その欄の直下に出す）。
- `problem.field_errors` がすべて入力欄に結び付いた場合、同じ `problem.detail` を FormStatus に重ねて出さない。
- `window.alert` / `window.confirm` / `console.error` を利用者への通知に使わない。

---

## 2. トーン（severity）

**4 トーン固定。** 色・枠・背景のトークンは [デザインシステム README](../design-system/README.md) の semantic token を使う（`Banner` / `FormStatus` / `Toast` / `StatusBadge` が内部で持つ）。

| トーン | 意味 | Lucide アイコン | role |
|---|---|---|---|
| `success` | 完了・正常 | `CheckCircle2` | `status` |
| `info` | 補足・進行中 | `Info` | `status` |
| `warning` | 注意・要確認 | `AlertTriangle` | `status` |
| `danger` | 失敗・破壊的 | `AlertCircle` | `alert` |

- アイコンは本文内 16px、State views 24px を基準にし、`aria-hidden` を付ける（意味はテキストで伝える）。
- `danger` は `role="alert"`（すぐ読み上げる）、他は `role="status"`（`aria-live="polite"`）。
- Job / Workflow の大きな状態パネル全体に `role="alert"` を付けない。新しく起きたエラーの要約を `Banner severity="danger"` などの最小の live region で 1 回だけ知らせる。
- 破壊的操作のボタンは `danger` トーンで**主操作から視覚的に分ける**（`destructive-emphasis`）。

---

## 3. チャネル別の規約

### 3.1 Toast

- **配置**：画面の右下に積む。共有 UI の `<Toaster/>` を使い、重なり順は共有トークン `--z-toast`（暗幕とモーダルの下）に従う（§6）。通知が確認ボタンを覆ってはならない。`<Toaster/>` の `placement` は既定 `bottom-right`、`bottom-left` は明示した製品だけが使う。
- **a11y**：コンテナは `role="region"` + `aria-live="polite"`、フォーカスを奪わない（`toast-accessibility`）。`danger` は `role="alert"`。
- **自動で消える時間**：success / info / warning は既定 4 秒（`toast-dismiss`：3–5s）。`danger` は既定 `duration: 0`（利用者が閉じるまで残す）とし、閉じる × ボタンを必ず出す。
- **アニメーション**：enter 200ms ease-out / exit 130ms ease-in（`exit-faster-than-enter`）。`prefers-reduced-motion` で無効にする。
- **Undo**：削除・一括操作の成功 Toast には、できれば「元に戻す」action を付ける（`undo-support`）。
- **終了通知**：バックグラウンド処理の終了は、終端への遷移を観測したときだけ 1 回出す。初回取得・再読込・同じ状態の再取得では出し直さない。
- **API**：

```ts
toast.success(message, opts?)
toast.info(message, opts?)
toast.warning(message, opts?)
toastError(message, opts?)      // = danger。固定面がない場合だけ。既定で自動では消えない
// opts: { description?: string; action?: { label; onClick }; duration?: number }
```

- `message` / `description` / `action.label` には **i18n 済みの文字列**（`t(...)` の戻り値、または `ApiError.message`）を渡す。リテラルの直書きは禁止。`<Toaster/>` はアプリで一度だけ描く。

### 3.2 FieldError

- **配置**：必ずその入力欄の**直下**（`error-placement`）。
- **検証のタイミング**：blur 時または送信時（`inline-validation`、キー入力ごとに出さない）。
- **a11y**：`<input aria-invalid aria-describedby={errorId}>` ↔ `<p id={errorId} role="alert">`。送信に失敗したら**最初の不正な欄に自動でフォーカス**する（`focus-management`）。
- 共有 UI の `TextField` / `SelectField` と `<FieldError id message />` を使う。
- エラーが複数あるときはフォーム上部に**概要 + 各欄へのアンカー**を置いてよい（`error-summary`）。ただし欄の直下の表示は必須。
- サーバーの検証結果は `problem.field_errors[].pointer` の JSON Pointer を、画面が宣言する `pointer → 入力欄` の対応表で結び付ける。日本語の `detail` の部分一致や正規表現で欄を推測しない。
- 関連する入力を変えたときは、その欄のサーバーエラーだけを消す。他の欄のエラーは残し、再送信のときにサーバーで検証し直す。

### 3.3 FormStatus

- 操作ボタン（保存 / 接続テストなど）の**近く**に、直近の結果を 1 行で出す。
- 検索の実行・プレビュー・実行・保存など、利用者が明示的に押したボタンの失敗は、そのボタン群の**直下**に `FormStatus` または `ActionResultRegion` で出す。ページ先頭の `PageNotice` / Banner へ送らない。
- ページの初期読込、接続未設定、権限不足、対象データなしなど、特定のボタンではなくページ / セクション全体の状態を説明するものは Banner / State views を使う。
- 一時パスワード・recovery code など**一度しか表示できない資格情報**は、発行操作のすぐ近くに置く。複数項目の結果は `ActionResultRegion`、既存のフォーム項目に対応する単一の値は**読み取り専用の入力欄 + コピーの action** で出す。Toast やページ先頭の Banner に資格情報を載せず、別の対象へ移る・一覧に戻る操作まで残し、コピーに失敗したときの復旧方法を欄の直下に出す。
- 一度しか表示できない資格情報はコンポーネントのローカル state だけに持ち、URL・Query / Zustand の store・local / session storage・ログに保存しない。`aria-live` は発行の完了だけを知らせ、資格情報そのものを自動で読み上げない。
- TanStack Query の `mutation.isSuccess` / `isError` と連動させる。`isError` の文言は `error instanceof ApiError ? error.message : t("...loadError")` を基本形にする。
- 成功の表示は数秒後に消してよいが、エラーは次の操作まで残す。

### 3.4 Banner（Alert）

- セクション / ページの先頭に置く横長の通知。トーンは §2、左にアイコン、必要なら閉じる × と action ボタン。共有 UI の `<Banner>` を使う。
- 主に「設定が終わっていないので機能が使えない」「縮退モード」などの**状況**を出す。一時的な成功には使わない（→ Toast）。

### 3.5 ConfirmDialog

- **破壊的・不可逆な操作は必ず確認する**（`confirmation-dialogs`）。対象：削除、一括削除、上書き、未保存の破棄。
- 共有 UI の `useConfirm()` / `<ConfirmProvider>` を使う。
- **背景と面の使い分け**：
  - ConfirmDialog、専用の確認ダイアログ、確認語の入力領域は**中立の面**を基本にする。
  - 削除・DROP・無効化・破棄などの danger 操作でも、ダイアログのヘッダー / 本文 / 対象欄を広い danger の背景で塗らない。
  - danger は左のアクセント、タイトル / 補助文、状態 badge、確定ボタンで表す。
  - danger の背景は小さな badge、icon chip、インラインの警告 / エラーの補助面だけに使い、確認のコンテナ全体の背景に使わない。
- **a11y / 操作**：
  - フォーカストラップ。開いたら確認ボタンへフォーカスし、閉じたら trigger に戻す。
  - `Esc` とオーバーレイのクリックでキャンセル（`escape-routes`）。破棄系は誤操作を防ぐためオーバーレイのクリックを無効にしてよい。
  - scrim は共有トークン `--scrim`、overlay は `--z-dialog` で Toast（`--z-toast`）より上。
  - メニュー（`role="menu"`）の項目から開いた場合は、閉じたあとメニューの trigger にフォーカスを戻す。ルートが変わったら開いている確認はキャンセルする（`useLocation().key` を `navigationKey` に渡す）。
  - 確認ボタンは操作のトーンに合わせる（削除なら `danger`）。キャンセルを既定のフォーカスにしてもよい。
  - enter は trigger を起点にした scale + fade（`modal-motion`）、`prefers-reduced-motion` ではフェードだけ。
- **API**：

```ts
const ok = await confirm({
  title, description,
  confirmLabel, cancelLabel,
  tone: "danger" | "warning" | "info",
});
if (ok) { /* 実行 */ }
```

- 確認のあとの結果は **Toast**（成功）または **FormStatus / Banner**（失敗）で返す。

### 3.6 State views

- `LoadingState` / `ErrorState`（再試行付き）/ `EmptyState` を使う。
- 1 秒を超える取得は `TimedLoadingState` + `Skeleton` にする（`progressive-loading`、画面を塞ぐスピナーは禁止）。CLS を出さないよう領域の寸法を予約する。
- TanStack Query と連動した標準の分岐：

```tsx
if (query.isPending) return <SkeletonXxx />;          // 読込
if (query.isError)   return <ErrorState message={…} onRetry={query.refetch} />; // 失敗
if (!query.data?.length) return <EmptyState title={…} hint={…} />;             // 空
```

- `ErrorState` のメッセージには原因と、再試行 / 設定への誘導を含める（`error-recovery`）。

### 3.7 処理中・経過時間

- 利用者が直接見ている取得・送信・実行と durable job は、対象の領域の中に `ProcessingIndicator` または `TimedLoadingState` を出す。静かな polling、prefetch、background refresh では出さない。
- **配置は「影響を受ける最小の領域の先頭」**。座標やページごとの見た目ではなく、`ProcessingPlacement = page | workspace | panel | tab | result | action | job` で意味を示し、`data-processing-placement` に反映する。
  - `page`：認証・DB gate など、ページ全体が使えない初期処理。
  - `workspace`：一覧の再取得など、複数のパネルに影響する処理。
  - `panel`：一覧、設定カード、詳細ペインなど 1 つのパネルの取得。
  - `tab`：選択中のタブの内容だけを取得する処理。
  - `result`：実行結果を作る処理（生成・分析・プレビューなど）。
  - `action`：ログインや送信など、フォーム操作に直接属する処理。
  - `job`：durable job の進行表示。
- 領域内の順序は **固定の見出し / 検索 / タブ → 処理中の表示 → Skeleton または保持中の内容**。処理と関係のないタイトル・フィルター・タブは隠さず、利用者が現在地を見失わないようにする。
- `PageHeader` はボタンのスピナーだけを担い、経過時間などの詳細表示を自動で作らない。詳細表示をヘッダーの直下に置くのは `placement="page"` の処理だけ。
- 初回の取得は `TimedLoadingState` + Skeleton で寸法を予約する。明示的な再取得は今の内容を残し、対象領域の先頭に compact な `ProcessingIndicator` を置く。
- 同じ処理の詳細表示は 1 つだけ。**動くスピナーは同じ処理に 1 つだけ**にし、起点のボタンが `loading` を出している場合、詳細表示は `activityIcon="none"` で静的なラベル・経過時間・slow hint だけにする。同じ timer を複数のパネルに重ねない。
- 実行中は `経過時間 00:00`、durable job や結果カードの完了後は `処理時間 00:00`。1 時間未満は `mm:ss`、1 時間以上は `h:mm:ss`。数字は `tabular-nums` で幅を固定する。
- 10 秒を超えたら控えめな slow hint を足す。取り消せる処理は同じ領域に取消の action を置く。進捗が不明なら progress bar を出さず、総量が分かる場合だけ進捗率を並べる。
- timer は `role="timer"` + `aria-live="off"` とし、1 秒ごとに読み上げない。アニメーションは `prefers-reduced-motion` に従う。
- 操作の key が変わったら client 側の開始時刻をリセットする。durable job はサーバーの `started_at` / `finished_at` / `elapsed_ms` を優先する。
- timeout と取消は別の状態にする。timeout は今の選択を残した `ErrorState` に置き換えて再試行を示し、取消は timeout のエラーとして知らせない。
- request の時間予算は製品の request policy に 1 か所で定義し、秒数をページの文言に直書きしない。予算を超える処理は durable job にする。

---

## 4. i18n（文言）の規約

- すべての文言は製品の i18n 辞書に定義し、key で参照する。
- **key の命名**：`<domain>.<feature>.<channel>.<state>`。
  - 例：`settings.model.toast.saved` / `settings.database.field.host.error.required` / `documents.confirm.delete.title`。
- 既存の流儀に合わせる（`...saved` / `...loadError` / `...saveError` など）。
- エラーの文言には**原因と対処**を含める。
  - ✗ 「保存に失敗しました。」
  - ✓ 「保存に失敗しました。接続情報を確認して再試行してください。」
- 数値・日時はロケールに合わせて整形する。データの列は等幅数字（`tnum`）。

### 4.1 文末を優先した折り返し

- Toast / Banner / FormStatus / FieldError / ConfirmDialog / ErrorState / EmptyState の文字列は、共有 UI の `<MessageText>` で描く。
- 改行・タブ・連続した空白は 1 つの空白にし、`。！？；` と英語の `.?!` を文末の候補にする。小数・version・URL の中の `.` では分けない。
- 文ごとに atomic inline box で描き、**幅が足りないときだけ文末を優先して折り返す**。文ごとに必ず改行してはならない。
- 1 つの長い文・URL はコンテナ幅の中で `break-words` し、375px でも横スクロールを出さない。
- 複数行の表・リストなどは文字列に `\n` を埋め込まず、ReactNode と意味のある要素で組む。

### 4.2 操作の結果のフィードバック

| 操作 | 実行中 | 成功 | 失敗 |
|---|---|---|---|
| 保存 / 作成 / 更新 / 削除 / 取込 / 出力 / コピー / 起動 / 停止 / 再作成 | `<Button loading>` + 二重実行の防止 | 原則 `toast.success` | FieldError / FormStatus / PageNotice。固定面がない場合だけ danger Toast |
| 利用者が押した更新 / 再試行 / 接続確認 | `<Button loading>` | 差分がなくても完了の Toast | 原因と復旧方法を近くに残す |
| 長時間の job | 開始の info Toast + 常設の状態表示 | 終端への遷移で 1 回だけ success Toast | 終端への遷移で 1 回だけ danger の通知 |
| 生成 / 実行 / 評価などの主な結果 | Skeleton / 進行状態 | 結果の領域そのものを完了の通知にする | FormStatus / Banner / ErrorState |
| ナビゲーション / タブ / フィルター / 展開 / 選択 | 見える状態 + ARIA | Toast を出さない | — |

- 初期の読込と background polling は静かに行い、利用者の明示的な操作だけを知らせる。
- 同じ結果を Toast と Banner / FormStatus に重ねて出さない。成功の瞬間は Toast、続く警告・回復できる失敗は固定面を正本にする。
- clipboard / download / file import は、Promise / HTTP の成功と失敗の両方を必ず扱う。

### 4.3 API problem 契約（段階的な互換）

失敗の応答は既存の envelope の `error_messages` / `error_code` を保ち、機械判定用の `problem` を足す。移行の間は `application/json` のままとし、`error_messages[0]` と `problem.detail` を一致させる。

```json
{
  "data": null,
  "error_messages": ["入力内容に誤りがあります。該当項目を確認してください。"],
  "warning_messages": [],
  "error_code": "REQUEST_VALIDATION_FAILED",
  "problem": {
    "type": "urn:<product>:problem:request-validation-failed",
    "title": "入力内容を確認してください",
    "status": 422,
    "detail": "入力内容に誤りがあります。該当項目を確認してください。",
    "code": "REQUEST_VALIDATION_FAILED",
    "request_id": "...",
    "retryable": false,
    "field_errors": [
      { "pointer": "/login_user_id", "code": "already_exists", "message": "別のIDを入力してください。" }
    ]
  }
}
```

- FastAPI の 422、認証 / 権限、競合、429、503、処理されない 500 は、backend の problem factory 1 か所で作る。
- frontend の `ApiError` は `problem` / `fieldErrors` / `requestId` / `retryable` を持ち、新旧の envelope の両方を読む。
- 分岐は `problem.code` または HTTP status だけで行う。説明用の `detail` / `message` を解析してロジックを決めない。
- 401 / 403 / 500 と Oracle の例外は、安全な日本語の概要・安定した code・request ID だけを返す。raw exception、SQL、資格情報は構造化ログだけに記録し、画面や API の `error_details` に返さない。

---

## 5. アクセシビリティ チェックリスト（全チャネル共通・必須）

- [ ] トーンに**アイコンを並べる**（色だけで意味を伝えない）。
- [ ] `danger` = `role="alert"`、他 = `role="status"` / `aria-live="polite"`。
- [ ] Toast はフォーカスを奪わない。ConfirmDialog はフォーカストラップ + `Esc` で閉じる。
- [ ] フォーム送信のエラーで最初の不正な欄へフォーカス。`aria-invalid` / `aria-describedby` を付ける。
- [ ] テキストのコントラスト 4.5:1 以上（semantic token は準拠済み）。
- [ ] `prefers-reduced-motion` で出現 / 消滅のアニメーションを無効にする。
- [ ] 閉じる / キャンセル / 再試行のボタンの操作領域 ≥ 44×44px、`cursor-pointer`。

---

## 6. z-index / アニメーション

- z-index は [デザインシステム README §6](../design-system/README.md) のスケール（`--z-toast` / `--z-scrim` / `--z-dialog` など）を正本とし、アプリで数値を直書きしない。Toast は暗幕とモーダルの下、ConfirmDialog と画面固有のモーダルの overlay・scrim は `--z-dialog`。
- アニメーション：micro 150–300ms、exit は enter の 60–70%、`transform` / `opacity` だけ、`ease-out`（enter）/ `ease-in`（exit）。すべて `prefers-reduced-motion` に対応する。

---

## 7. 既存コードの移行の方向

| 今の実装 | 移行先 |
|---|---|
| 手書きの `role="alert"` のインラインエラー | `FormStatus` / `FieldError` / `Banner` に振り分ける |
| 画面ごとの tone 付きの手書きの箱 | 共通 `<Banner>` |
| 確認なしですぐ実行する削除 | `ConfirmDialog` の確認を挟む |
| 散らばった保存成功の表示（「保存しました」） | `FormStatus`（その場）+ 必要なら `toast.success` |
| `console.error` / `window.confirm` | Toast / ConfirmDialog |
| 個別の `useState(errorText)` | mutation と連動した `FormStatus` |

意図的に対象外とするもの：状態の可視化（`StatusBadge` / `FlowStepper` のステップ表示。通知ではなくデータの表示）、複合の結果パネル（タイトル + 詳細リスト + チップ）、中立の `role="status"` の軽いテキスト。

---

## 8. 新規 / 改修のときの完了条件

1. チャネル / トーン / i18n の規約に従って作る。
2. 文言を i18n 辞書に足す（原因 + 対処）。
3. 製品の lint / build / Vitest を通す。
4. **Playwright** で実画面を確認する（成功 / エラー / 空 / 読込、375px・desktop、キーボード操作・`Esc`・フォーカスの戻り）。
5. UI/UX の変更は `ui-ux-pro-max` skill のチェックリストで自己レビューする。

---

## 9. 失敗状態の情報設計（error-state IA）

> §1–§3 が「どのチャネルで出すか」を決めるのに対し、本節は **1 つの失敗を画面のどこに何回出すか** を決める。
> 同じ失敗を複数のチャネルに重ね、肝心の「原因 + 対処」を最下部に埋もれさせることを禁止する。
> 対象は、1 つのエンティティ（文書・KB・ジョブなど）が複数の状態の面（バッジ / バナー / ステッパー / 診断パネル）を同時に持つ画面。

| # | 原則 | 規約 |
|---|---|---|
| P1 | **状態の出所は 1 つ** | あるエンティティの「失敗した」という*状態*は、各階層で **1 か所**だけ示す。エンティティの状態は header の `StatusBadge` が正本。同じ階層に「エラー」とだけ書いた帯 / バナー / ラベルを重ねない。ジョブ単位・セグメント単位など*別の階層*のバッジはよい（データの表示）。 |
| P2 | **原因は最も具体的な層で 1 回** | 「原因 + 対処」の本文は **1 か所**だけ出す。同じ文字列をエンティティ / 設定 / バナー / ジョブ / セグメントに重ねない。最も具体的な層（job → segment → entity の順で最初に得られたもの）を採り、上位の要約バナーに一度だけ上げる。 |
| P3 | **優先順位 + 段階的な開示** | 最も重大で対処できる「原因 + 対処」を**上に目立たせる**。ジョブ ID・試行回数・時刻・セグメントの code などの技術詳細は折りたたみ（診断の詳細）へ下げ、エラーのときだけ自動で開く（`progressive-disclosure`）。 |
| P4 | **空のメッセージ面を描かない** | トーンのラベルだけで本文（原因 + 対処）のないバナー / 帯 / バッジを出さない。本文がないなら面ごと描かず、`StatusBadge` に任せる。`<Banner>` は `title` / `children` がどちらも空なら描かない。 |
| P5 | **進行の表示はエラーでも残す** | 工程のステッパーはエラーのときも**ステップ列を保ち**、失敗したステップを `danger` で強調する（どの工程で落ちたかを残す）。全体を「エラー」一語の帯に置き換えない。色だけに頼らずアイコン + テキストを並べる。 |

### 適用のパターン

```
header           : StatusBadge（エンティティの状態の正本 = P1）
└ 重複 / 設定ドリフトなどの警告 Banner（状況の提示。失敗とは別）
└ 工程ステップの表示：工程列を保ち、失敗したステップを danger で強調（P5）
└ 状態メッセージの枠（**常に 1 本だけ**。優先順：失敗の原因 danger > 実行中 info > 承認待ちの案内 info）
   └ 失敗の原因：「{工程}で失敗しました」+ 原因 + 対処（P2 / P3 の要約）
└ 診断の詳細（折りたたみ・エラー時に自動で開く = P3）
   └ ジョブ / セグメント：技術詳細。要約バナーと同じ文字列は出さない（P2）
```

- 「失敗した」状態の*存在*（バッジ）、「なぜ失敗したか」の*本文*（バナー 1 本）、「技術詳細」（折りたたみ）を**役割で分け**、同じ文を場所を変えて繰り返さない。
- **完了の状態を示す常設の success バナーは出さない**（P1 の系）。完了という*状態*は `StatusBadge` / 工程ステップ / 日時が担い、完了の*瞬間*は遷移を観測したときだけ `toast.success` で 1 回知らせる。
- 実装の例：RAG の文書詳細（`documents/ingestion-error-display.ts` の `resolveDocumentFailureView()`）。RAG の `docs/frontend-messaging-spec.md` を参照。
