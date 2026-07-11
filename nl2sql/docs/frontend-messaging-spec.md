# フロントエンド メッセージ機構 仕様書（Messaging Spec）

> **このファイルはシステム全体の「ユーザーへのメッセージ提示」を統一する正本(spec)です。**
> 通知・成功/エラー表示・フォーム検証・確認ダイアログ・空/読込/エラー状態を**新規実装・改修するときは必ず本仕様に従う**こと。
> 逸脱が必要な場合は AGENTS.md §コーディング規約 8/9 に従い理由を添えて確認する。
>
> 対象スタック: Vite + React Router + TypeScript + Tailwind v4 + shadcn/ui + TanStack Query + Zustand。
> 準拠: `ui-ux-pro-max` の §1 Accessibility / §2 Touch & Interaction / §7 Animation / §8 Forms & Feedback / §9 Navigation。

---

## 0. 設計原則（5 原則)

1. **チャネルは 6 種類のみ**(下記 §1)。新しい提示方法を勝手に追加しない。
2. **意味(severity)は 4 トーンのみ**: `success` / `warning` / `danger` / `info`。生 hex を使わず semantic token を使う。
3. **色だけで意味を伝えない**(`color-not-only`)。トーンごとに**必ずアイコン**を併置する。
4. **文言はすべて i18n 経由**(`src/lib/i18n.ts` の `t()`)。コンポーネントや各チャネル API に日本語文字列を直書きしない(`ApiError.message` 等のサーバ由来文字列は可)。
5. **メッセージは「原因 + 次の行動」**(`error-clarity` / `error-recovery`)。「失敗しました」だけで終えない。

---

## 1. メッセージチャネル(6 種)と使い分け

| # | チャネル | 役割 | 永続性 | ブロッキング | 主用途 |
|---|---|---|---|---|---|
| 1 | **Toast** | 一時通知 | 自動消滅(3–5s) | 非ブロッキング | 保存成功、コピー完了、バックグラウンド処理結果、取り消し可能操作 |
| 2 | **FieldError** | フィールド検証 | 入力修正まで | 非ブロッキング | 入力欄単位のバリデーション(必須・形式) |
| 3 | **FormStatus** | フォーム/アクション結果 | 次操作まで | 非ブロッキング | 送信ボタン近傍の成功/失敗(例: 「保存しました」) |
| 4 | **Banner(Alert)** | ページ/セクション常設 | 永続 or 手動閉じ | 非ブロッキング | 「接続未設定」「縮退モード」「保存前の警告」等の状況提示 |
| 5 | **ConfirmDialog** | 確認ダイアログ | 応答まで | **ブロッキング** | 削除・上書き・不可逆操作の確認ゲート |
| 6 | **State views** | 領域状態 | データ取得まで | 領域占有 | 読込(Skeleton)/空(Empty)/エラー(Error+再試行) |

### 決定フロー

```
ユーザーに何かを伝えたい
│
├─ 操作を続行してよいか確認が必要？(削除・上書き・不可逆)
│     └─ YES → 5. ConfirmDialog
│
├─ 特定の入力欄に紐づくエラー？
│     └─ YES → 2. FieldError（欄の直下）
│
├─ 領域全体がデータ未取得/空/取得失敗？
│     └─ YES → 6. State views（Skeleton / EmptyState / ErrorState）
│
├─ ページ/セクション全体に関わる状況を“出し続けたい”？(設定未完了など)
│     └─ YES → 4. Banner
│
├─ 送信/保存アクションの直近結果を、その場で見せたい？
│     └─ YES → 3. FormStatus（アクションボタン近傍）
│
└─ それ以外（操作の完了通知・非同期結果・コピー成功など）
      └─ 1. Toast
```

**重要な禁止事項**
- 破壊的操作の結果通知に Toast を使うのは可。ただし**確認自体は ConfirmDialog**で行う(Toast に確認を載せない)。
- フィールドエラーを Toast やページ上部のみで出さない(`error-placement`: 該当欄の直下に出す)。
- `window.alert` / `window.confirm` / `console.error` をユーザー向け通知に使わない。

---

## 2. トーン(severity)と視覚仕様

トークンは `frontend/src/globals.css` 既定の semantic token を使用する。**4 トーン固定**。

| トーン | 意味 | テキスト/枠 | 背景 | Lucide アイコン | role |
|---|---|---|---|---|---|
| `success` | 完了・正常 | `text-success` / `border-success/30` | `bg-success-bg` | `CheckCircle2` | `status` |
| `info` | 補足・進行中 | `text-info` / `border-info/30` | `bg-info-bg` | `Info` | `status` |
| `warning` | 注意・要確認 | `text-warning` / `border-warning/30` | `bg-warning-bg` | `AlertTriangle` | `status` |
| `danger` | 失敗・破壊的 | `text-danger` / `border-danger/30` | `bg-danger-bg` | `AlertCircle` | `alert` |

ルール:
- アイコンは `size={16}`(本文内)/ `size={24}`(State views) を基準、`aria-hidden` を付ける(意味はテキストで担保)。
- `danger` は `role="alert"`(即時読み上げ)、他は `role="status"`(`aria-live="polite"`)。
- 破壊的アクションのボタンは `danger` トーンで**主アクションから視覚的に分離**する(`destructive-emphasis`)。

---

## 3. チャネル別 実装仕様

### 3.1 Toast

- **配置**: 画面右下にスタック。`z-index` は `1000`(§6 参照)。
- **a11y**: コンテナは `role="region"` + `aria-live="polite"`、フォーカスを奪わない(`toast-accessibility`)。`danger` は `role="alert"`。
- **自動消滅**: 既定 4 秒(`toast-dismiss`: 3–5s)。`danger` と action 付きは手動 + 8 秒に延長可。閉じる × ボタン必須。
- **アニメーション**: enter 200ms ease-out / exit 130ms ease-in(`exit-faster-than-enter`)。`prefers-reduced-motion` で無効化。
- **Undo**: 削除・一括操作の成功 Toast には可能なら「元に戻す」action を付ける(`undo-support`)。
- **API(実装規約)**:

```ts
// src/lib/toast.ts（Zustand ストア）+ src/components/ui/toast.tsx（<Toaster/>）
toast.success(message, opts?)
toast.info(message, opts?)
toast.warning(message, opts?)
toast.error(message, opts?)      // = danger トーン
// opts: { description?: string; action?: { label; onClick }; duration?: number }
```

- `message` / `description` / `action.label` には **i18n 済み文字列**(`t(...)` の戻り値、または `ApiError.message`)を渡す。**リテラル直書きは禁止**。`<Toaster/>` は `src/components/providers.tsx` で一度だけ描画する(導入済み)。

### 3.2 FieldError

- **配置**: 必ず該当入力欄の**直下**(`error-placement`)。
- **検証タイミング**: blur 時または送信時(`inline-validation`、キーストロークごとに出さない)。
- **a11y**: `<input aria-invalid aria-describedby={errorId}>` ↔ `<p id={errorId} role="alert">`。送信失敗時は**最初の不正欄に自動フォーカス**(`focus-management`)。
- 既存実装(`src/components/ui/select-field.tsx`)のパターンを正とし、共通 `<FieldError id message />` に集約する。
- 複数エラー時はフォーム上部に**サマリ + 各欄へのアンカー**を併設してよい(`error-summary`)。ただし欄直下表示は必須。

### 3.3 FormStatus

- アクションボタン(保存/接続テスト等)の**近傍**に、直近結果を 1 行で表示。
- TanStack Query の `mutation.isSuccess` / `isError` と連動させる。`isError` の文言は `error instanceof ApiError ? error.message : t("...loadError")` を基本形にする。
- 成功表示は数秒後にフェードしてよいが、エラーは次操作まで残す。

### 3.4 Banner(Alert)

- セクション/ページ先頭に常設する横長の通知。トーン §2、左にアイコン、任意で閉じる ×・action ボタン。
- 既存 `OciSettingsClient` / `ModelSettingsClient` のインラインバナー実装を**共通 `<Banner severity title? message action? dismissible? />`** に統一する(2 箇所の重複を解消)。
- 主に「設定未完了で機能が使えない」「縮退モード」等の**状況**提示。一時的成功には使わない(→ Toast)。

### 3.5 ConfirmDialog（新規導入)

- **破壊的・不可逆操作は必ず確認**(`confirmation-dialogs`)。対象: 文書/モデル/設定の削除、一括削除、上書き、未保存破棄。
- shadcn/ui の Dialog 準拠で新規作成(現状ライブラリ未導入のため `createPortal` ベースで実装)。
- **a11y / 操作**:
  - フォーカストラップ + 開いたら確認ボタンへフォーカス、閉じたらトリガーへ復帰。
  - `Esc` とオーバーレイクリックでキャンセル(`escape-routes` / `modal-escape`)。破棄系は誤操作防止のためオーバーレイクリック無効可。
  - scrim は 40–60% black。`z-index` 1000。
  - 確認ボタンは操作トーンに合わせる(削除なら `danger`)。キャンセルが既定フォーカスでもよい。
  - enter は trigger 起点の scale+fade(`modal-motion`)、`prefers-reduced-motion` でフェードのみ。
- **API(実装規約)**:

```ts
// useConfirm() フック or <ConfirmDialog/> 制御コンポーネント
const ok = await confirm({
  titleKey, descriptionKey,
  confirmLabelKey, cancelLabelKey,
  tone: "danger" | "warning" | "info",
});
if (ok) { /* 実行 */ }
```

- 確認後の結果は **Toast**(成功)または **FormStatus/Banner**(失敗)で返す。

### 3.6 State views

- `src/components/StateViews.tsx` の `ErrorState`(再試行付き)/ `EmptyState` を正本とする。
- **Loading を追加**: 1 秒超の取得は `Skeleton`(`src/components/ui/skeleton.tsx`)で行う(`progressive-loading`、ブロッキングスピナー禁止)。CLS を出さないよう領域寸法を予約する。
- TanStack Query 連動の標準分岐:

```tsx
if (query.isPending) return <SkeletonXxx />;          // 読込
if (query.isError)   return <ErrorState message={…} onRetry={query.refetch} />; // 失敗
if (!query.data?.length) return <EmptyState title={…} hint={…} />;             // 空
```

- `ErrorState` のメッセージは原因 + 再試行/設定誘導を含める(`error-recovery`)。

---

## 4. i18n（文言)規約

- すべての文言は `src/lib/i18n.ts` の `ja` に定義し、キー経由で参照する。
- **キー命名**: `<domain>.<feature>.<channel>.<state>`。
  - 例: `settings.model.toast.saved` / `settings.database.field.host.error.required` / `documents.confirm.delete.title`。
- 既存の流儀に合わせる(`...saved` / `...loadError` / `...saveError` 等)。
- エラー文言は**原因 + 対処**を含める。
  - ✗ 「保存に失敗しました。」
  - ✓ 「保存に失敗しました。接続情報を確認して再試行してください。」
- 数値・日時はロケール対応で整形(`src/lib/format.ts`)。データ列は等幅数字(`tnum`)。

---

## 5. アクセシビリティ チェックリスト(各チャネル共通・必須)

- [ ] トーンに**アイコン併置**(色のみで意味を伝えない)。
- [ ] `danger` = `role="alert"`、その他 = `role="status"` / `aria-live="polite"`。
- [ ] Toast はフォーカスを奪わない。ConfirmDialog はフォーカストラップ + `Esc` で閉じる。
- [ ] フォーム送信エラー時に最初の不正欄へフォーカス。`aria-invalid` / `aria-describedby` を設定。
- [ ] テキストコントラスト 4.5:1 以上(semantic token は準拠済み)。
- [ ] `prefers-reduced-motion` で出現/消滅アニメを無効化。
- [ ] 閉じる/キャンセル/再試行ボタンの hit area ≥ 44×44px、`cursor-pointer`。

---

## 6. z-index / アニメーション トークン

| レイヤ | z-index |
|---|---|
| 通常コンテンツ | 0 |
| sticky header / sideTabBar | 20–40 |
| Banner(sticky 時) | 40 |
| ConfirmDialog overlay/scrim | 1000 |
| Toast スタック | 1000 |

- アニメーション: micro 150–300ms、exit は enter の 60–70%、`transform`/`opacity` のみ、`ease-out`(enter)/`ease-in`(exit)。すべて `prefers-reduced-motion` 対応。

---

## 7. 既存コードの移行マップ（统一对象）

| 現状 | 移行先 |
|---|---|
| 各所の手書き `role="alert"` インラインエラー(SearchClient/Model/Database 等) | `FormStatus` / `FieldError` / `Banner` に振り分け |
| `OciSettingsClient` `ModelSettingsClient` の重複バナー(tone/kind) | 共通 `<Banner>` |
| モデル削除 `onRemove`(確認なし即実行) | `ConfirmDialog` ゲートを挟む |
| 保存成功の散在表示(「保存しました」) | `FormStatus`(その場)+ 必要に応じ `toast.success` |
| `console.error` / 想定の `window.confirm` | Toast / ConfirmDialog へ置換 |
| 個別 `useState(errorText)` ボイラープレート | mutation 連動 + `FormStatus` |
| `StateViews`(良好) | Loading(`Skeleton`)を追加して 3 状態を標準化 |

---

## 8. 新規/改修時の必須手順(Definition of Done)

1. 上記チャネル/トーン/i18n 規約に従って実装。
2. 文言を `i18n.ts` に追加(原因 + 対処)。
3. `npm run lint && npm run build` と Vitest を通す。
4. **Playwright** で実画面確認(成功/エラー/空/読込、375px・デスクトップ、キーボード操作・`Esc`・フォーカス復帰)。
5. UI/UX 変更時は `ui-ux-pro-max` skill のチェックリストで自己レビュー。

---

## 9. 失敗状態の情報設計（error-state IA）

> §1–§3 が「どのチャネルで出すか」を定めるのに対し、本節は **1 つの失敗を画面のどこに何回出すか**を定める。
> 同じ失敗を複数チャネルへ多重表示し、肝心の「原因 + 対処」を最下部に埋もれさせる事故（RAGFlow/PowerRAG 比較で頻出のアンチパターン）を禁止する。
> 対象は 1 つのエンティティ（文書・KB・ジョブ等)が複数の状態面（バッジ/バナー/ステッパー/診断パネル)を同時に持つ画面。

### 原則（P1–P5）

| # | 原則 | 規約 |
|---|---|---|
| P1 | **単一状態源** | あるエンティティの「失敗した」という*状態*は、各 altitude で **1 箇所**だけ示す。文書レベルの状態は header の `StatusBadge` が正本。同じ altitude に「エラー」とだけ書いた帯/バナー/ラベルを重ねない。ジョブ単位・セグメント単位など*異なる altitude* のバッジは可（データ表示）。 |
| P2 | **原因は最も具体的なレイヤに1回** | 「原因 + 対処」の本文は **1 箇所**だけ出す。文書/KB 構築設定/バナー/ジョブ/セグメントへ同一文字列を多重表示しない。最具体レイヤ（job → segment → document の順で最初に得られたもの）を採用し、上位の要約バナーに1度だけ昇格させる。 |
| P3 | **優先順位 + 段階開示** | 最も重大で actionable な「原因 + 対処」を**上部に目立たせる**。ジョブ ID・試行回数・タイムスタンプ・セグメント code 等の技術詳細は折りたたみ（取込・診断の詳細）へ降格し、エラー時のみ自動展開する（`progressive-disclosure`）。 |
| P4 | **空のメッセージ面を描画しない** | トーンラベルだけで本文（原因 + 対処）を持たない バナー/帯/バッジを出さない。本文が無いなら面ごと描画せず、`StatusBadge` に委ねる。`<Banner>` は `title`/`children` 双方空なら描画しない（アイコンだけの空箱を作らない)。 |
| P5 | **進行可視化はエラーでも保持** | 工程ステッパーはエラー時も**ステップ列を維持**し、失敗したステップを `danger` で強調する（どの工程で落ちたかを残す)。全体を「エラー」一語の帯に置き換えない。色だけに頼らずアイコン + テキストを併置（`color-not-only`)。 |

### 適用パターン（文書詳細を例に）

```
header           : StatusBadge（文書状態の正本 = P1）
└ 重複/設定ドリフト等の警告 Banner（状況提示・失敗とは別概念）
└ レシピカード    : 工程ステップ表示（RecipeSteps）。工程列を維持し失敗ステップを danger 強調（P5）
└ 状態メッセージスロット（レシピ選択直下・**常に 1 本だけ**。優先順: 失敗原因 danger > 実行中 info > 承認待ちゲート案内 info）
   └ 失敗原因    : 「{工程}で失敗しました」+ 原因 + 対処（P2/P3 の要約）
└ 取込・診断の詳細（折りたたみ・error 時自動展開 = P3）
   └ ジョブ/セグメント: 技術詳細。要約バナーと同一文字列は再掲しない（P2）
```

### 実装の指針

- 原因の一本化は `documents/ingestion-error-display.ts` の `resolveDocumentFailureView()`（最具体レイヤ採用 + 失敗ステップ導出）に集約する。要約バナーに昇格した文字列は `resolveIngestionErrorDisplayPlan({ suppressMessages })` と各詳細パネルの `suppressMessage` で**二重表示を抑止**する。
- 「失敗した」状態の*存在*（バッジ）と「なぜ失敗したか」の*本文*（バナー1本）と「技術詳細」（折りたたみ）を**役割で分離**し、同じ文を場所を変えて繰り返さない。
- **完了状態の常設 success バナーは出さない**（P1 の系）。完了という*状態*は `StatusBadge` / 工程ステップ表示 / 日時メタデータが担い、完了の*瞬間*の通知は遷移を観測した時のみ `toast.success` で 1 回出す（§3.4）。

---

## 付録 A: 共通モジュール（実装済み）

```
src/components/ui/feedback-tone.ts   FeedbackTone(4 トーン)+ アイコン/色/role マップ
src/lib/toast.ts                     Toast ストア(Zustand) + toast.* API
src/components/ui/toast.tsx          <Toaster/>（右下スタック・aria-live）
src/components/ui/banner.tsx         <Banner severity title? action? onDismiss? />
src/components/ui/confirm-dialog.tsx <ConfirmProvider> / useConfirm()（focus trap・Esc）
src/components/ui/field-error.tsx    <FieldError id message />
src/components/ui/form-status.tsx    <FormStatus tone message />
src/components/StateViews.tsx        LoadingState / ErrorState / EmptyState
src/components/providers.tsx         <ConfirmProvider> + <Toaster/> を配線済み
```

- 4 トーンの視覚定義は **`feedback-tone.ts` を単一の正**とする(各チャネルはこれを参照)。
- これらは shadcn/ui 流儀(token 駆動・variant は `class-variance-authority`)で実装する。

## 付録 B: 移行状況

- ✅ モデル設定のモデル削除に `useConfirm()` の確認ゲートを導入(`ModelSettingsClient`)。
- ✅ `ModelSettingsClient` のローカル `Notice` を撤廃し、通知/エラー/検証を共通 `<Banner>` へ統合。
- ✅ `OciSettingsClient` / `DatabaseSettingsClient` / `UploadStorageSettingsClient` の保存結果インライン表示を `<FormStatus>` へ統一。
- ✅ `SearchClient`(エラー・ガードレール警告)・`DocumentWorkspace`(重複/エラー/索引完了)の手書き tone ボックスを `<Banner>` へ統一。
- ✅ spec 準拠監査(全画面)を実施し、手書き tone ボックスを統一:
  - `<Banner>` 化: ログインエラー、文書抽出の警告、評価の閾値失敗/検証/実行エラー。
  - `<FieldError>` 化: `SelectField` 本体および OCI/アップロード保存先の各フィールドエラー(`aria-describedby` 連携を維持)。
  - `<FormStatus>` 化: コピー失敗などのアクション結果。
- ⏳ 今後の新規画面・機能は本 spec の 6 チャネルに従う。以下は **意図的に対象外**(spec の例外):
  - **状態可視化**(`StatusBadge` / `StatusPill` / `FlowStepper` のステップ表示)— 通知ではなくデータ表示。
  - **OCI 構成テストの結果パネル**(タイトル + 詳細リスト + モードチップの複合)— 専用パネルとして維持。
  - **中立の `role="status"` 軽量テキスト**(検索キャンセル通知など)。
