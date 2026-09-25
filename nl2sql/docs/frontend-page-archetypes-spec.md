# ページの型の割り当て（NL2SQL の差分）

> ページの型（A〜D）と共有プリミティブの共通規約は platform の [UX 契約 page-archetypes.md](../../platform/docs/ux-contracts/page-archetypes.md) が正本。
> 本書には NL2SQL の各ページがどの型に属するかと、NL2SQL 固有の補足だけを書く。

## 1. 割り当て

### A. 一覧 → 全画面エディタ

`ProfileManagementPage`（業務プロファイル）を参照実装とする。URL の検索パラメータは `?id=` / `?profile=` など。

- **割当**: テーブルの管理 / ビューの管理 / 業務プロファイル / 用語・同義語 / 検証用サンプルデータ / コメント管理 / アノテーション管理 / ドメイン管理 / 質問分類モデル管理 / フィードバック管理。
- 旧タブ（list/create/import 等）は **一覧上のアクション** か **エディタ内の節** へ平坦化。破壊的操作は `useConfirm` + `ADMIN_EXECUTE` ゲートへ集約。

### B. マスタ詳細の閲覧

- **割当**: 実行履歴。A 型の一覧内詳細も本規約を流用可。

### C. ツール / ワークフロー

- **割当**: SQL 生成 / SQL から質問を生成。分割ペインを使う場合は §3 準拠。

### D. ダッシュボード / 状態

- **割当**: SQL生成評価。

## 3. 分割ペイン

`FixedSplitPane` の実装は `frontend/src/components/layout/FixedSplitPane.tsx`。左右の枠の class は `DbObjectManagementPanelShell` を一般化した `PanelShell` にまとめる。

## 4. 色の移行

生のパレットの class をトークンへ置き換えるときは、重いファイルから着手する：共有 `DbObjectManagementShared`（テーブル / ビュー / SQL 分析の見た目の核）/ `DbAdminShared` / `DataManagementPage` / `EvaluationPage` / `QuestionLearningPage`。
