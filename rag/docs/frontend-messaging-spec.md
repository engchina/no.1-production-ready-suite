# フロントエンド メッセージ機構（RAG の差分）

> 通知・成功 / エラー表示・フォーム検証・確認ダイアログ・空 / 読込 / エラー状態の共通規約は platform の [UX 契約 messaging.md](../../platform/docs/ux-contracts/messaging.md) が正本。ボタンは [UX 契約 buttons.md](../../platform/docs/ux-contracts/buttons.md)。
> 本書には RAG 固有の適用だけを書く。節番号は共通規約にそろえる。

## 9. 失敗状態の情報設計（文書詳細への適用）

原則 P1–P5 は [UX 契約 messaging.md §9](../../platform/docs/ux-contracts/messaging.md#9-失敗状態の情報設計error-state-ia) を参照。

### 適用パターン（文書詳細）

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
- **完了状態の常設 success バナーは出さない**（P1 の系）。完了という*状態*は `StatusBadge` / 工程ステップ表示 / 日時メタデータが担い、完了の*瞬間*の通知は遷移を観測した時のみ `toast.success` で 1 回出す（UX 契約 messaging.md §3.1）。
