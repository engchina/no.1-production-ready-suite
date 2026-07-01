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
