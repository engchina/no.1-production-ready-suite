# Design QA — 文書処理レシピ管理

## 検証範囲

- 文書 workspace の Recipe 選択、成果物表示、段階別再処理、segment 再試行。
- 業務ビューの作成・編集と固定 `fused` payload。
- Playwright の `desktop` と `mobile`（375px）プロジェクト。

## 確認結果

- PASS: 選択中 Recipe の extraction、処理後ファイル、chunks だけを表示する。
- PASS: extraction pointer の無い Recipe は文書レベル成果物へ fallback せず、export API も呼ばない。
- PASS: Recipe job、承認、segment 再試行は `recipe_id` / `recipe_revision` を維持する。
- PASS: loading、error、empty、確認待ち、再処理中の各状態を既存の日本語 UI で表示する。
- PASS: キーボード操作、bbox 連動、内部スクロール、375px 幅での横崩れがない。
- PASS: 業務ビュー作成画面に配信モード操作を出さず、POST payload は `fused` を送る。

## 実行結果

```text
Playwright: 86 passed
対象: document-workspace-file-processing.spec.ts, business-views.spec.ts
プロジェクト: desktop, mobile
```

ブラウザテストは deterministic API mock を使用し、実 DB や業務データには書き込んでいない。

---

# Design QA — チャット会話一覧

- Source visual truth: `/tmp/production-ready-rag-chat-audit/01-current.png`
- Implementation screenshot (desktop): `/tmp/production-ready-rag-chat-audit/02-implemented-desktop.jpeg`
- Implementation screenshot (mobile): `/tmp/production-ready-rag-chat-audit/03-implemented-mobile.jpeg`
- Viewports: desktop 1440×900, mobile 375×812
- State: 業務ビュー選択済み。desktop は回答・引用表示後、mobile は会話名変更後の通常表示。

## Full-view comparison evidence

元画面のサイドナビ、ページ見出し、業務ビューカード、会話一覧と回答領域の二分割、白いカード、青い選択色を維持した。変更は会話行へ題名、件数・更新日時、名前変更操作を追加した範囲に限定されている。desktop と mobile のどちらにも横方向の欠けや重なりはない。

## Focused region comparison evidence

会話一覧は元画像でも十分な解像度があり、実装画像でも題名、`2件・01/01 09:00`、鉛筆ボタン、選択背景を判読できるため追加 crop は不要。元画像の全件同名状態に対し、実装は質問由来の固有題名と時刻を同じ二行密度で表示している。

## Findings

- P0/P1/P2: なし。
- Fonts and typography: 既存の `Noto Sans JP`, `Roboto`, system-ui と 14px 基準を維持。題名は一行省略、日時は tabular figures で安定している。
- Spacing and layout: 280px の一覧幅、カード余白、行高、選択背景を維持。編集時のみ入力と保存・取消を同じ行へ展開する。
- Colors and tokens: `primary`, `muted`, `border`, `destructive`, `ring` の既存意味トークンだけを使用している。
- Image quality: 対象画面に生成対象の画像アセットはない。アイコンは既存製品規約どおり Lucide に統一した。
- Copy and content: 日本語 i18n キーを追加し、ユーザー向け技術語を増やしていない。
- Accessibility and behavior: 選択と編集を兄弟ボタンへ分離。名前変更は Enter/Escape とタッチ用保存・取消に対応し、失敗は入力直下で通知する。
- Responsive: 375px では編集操作を常時表示し、desktop では hover/focus/選択時に表示する。Playwright の overflow 検査を通過した。

## Patches made since the previous QA pass

- 会話選択と名前変更操作を分離した。
- 初回質問由来の題名、件数・更新日時、インライン編集を追加した。
- mutation 失敗後のフォーカス復帰を pending 状態の解除後に行うよう修正した。
- 長い日本語題名と未送信会話再利用の responsive テストを追加した。

## Follow-up polish

- P3: 50件を超える履歴が必要になった場合のみ検索・日付グループを検討する。

final result: passed
