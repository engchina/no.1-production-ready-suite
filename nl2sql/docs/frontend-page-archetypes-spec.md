# ページの型の割り当て（NL2SQL の差分）

> ページの型（A〜D）と共有プリミティブの共通規約は platform の [UX 契約 page-archetypes.md](../../platform/docs/ux-contracts/page-archetypes.md) が正本。
> 本書には NL2SQL の各ページがどの型に属するかと、NL2SQL 固有の補足だけを書く。

## 1. 割り当て

### A. 一覧 → 全画面エディタ

`ProfileManagementPage`（業務プロファイル）を参照実装とする。URL の検索パラメータは `?id=` / `?profile=` など。

- **割当**: 業務プロファイル / 用語・同義語 / 検証用サンプルデータ / コメント管理 / アノテーション管理 / ドメイン管理 / 質問分類モデル管理。テーブルの管理・ビューの管理の作成・取込（`?` を持たない画面内の切り替え）もこの型の置き方（`PageHeader` の `back`）にする。
- 旧タブ（list/create/import 等）は **一覧上のアクション** か **エディタ内の節** へ平坦化。破壊的操作は `useConfirm` + `ADMIN_EXECUTE` ゲートへ集約。

### B. マスタ詳細の閲覧

- **割当**: テーブルの管理 / ビューの管理（共有の `ListDetailLayout`。#2281）。一覧の列は見出し・検索・所有者の接頭辞・件数（`DbObjectSearchOwnerFields`）→ 名前とコメントの 2 段 + 行数の表（並べ替えは列見出し）→「さらに読み込む」、詳細の列は対象の見出し・`ObjectActionBar`・列情報 / DDL のタブ。選択は URL の `?table=` / `?view=`（`useWorkspaceUrlSelection`。keep-alive の間はほかの画面の URL を読まず、サイドナビから戻ると最後の選択を URL に戻す）。先頭の行を自動で選ばない。選んでいる行をもう一度押すと詳細を取り直す（取消・失敗の後の読み直し）。部品は `components/DbObjectManagementShared.tsx` の `DbObjectListDetail` / `DbObjectDetailPanel`。フィードバック管理は #2283 で同じ型に移す（今は `FixedSplitPane`）。
- 実行履歴は 3 製品共通の型（一覧 → `?id=<run_id>` の全幅の詳細。UX 契約 `platform/docs/ux-contracts/run-history.md`。#1955）に移した。A 型の一覧内詳細は本規約を流用可。種類の絞り込みの既定は「品質評価以外」（Agent の品質評価の Run が MCP で呼んだ実行を隠す。選択肢は 品質評価以外 / すべて / チャット / SQL 生成 / MCP / 品質評価、「条件をクリア」は既定へ戻す）。品質評価の実行だけがあるときは、絞り込みを出したまま表の空の状態に隠している件数と出し方を出す（#1986。`security-rbac-deepsec.md`「実行履歴の種類」）。

### C. ツール / ワークフロー

- **割当**: SQL 生成 / SQL から質問を生成。入力と結果を左右に並べる画面（SQL から質問を生成・データの管理のデータの表示・サンプルデータ管理）は `FixedSplitPane`（§3）。

### D. ダッシュボード / 状態

- **割当**: SQL生成評価。

## 3. 一覧 + 詳細と分割ペイン

- `FixedSplitPane`（C 型の入力 | 結果と比較）は共有の `@production-ready/ui` の部品で、`DbObjectManagementPanelShell` の `splitId` に渡すと左右に並べる。保存 key の前置きは `FIXED_SPLIT_STORAGE_PREFIX`（`src/lib/ui-store.ts`）。
- `ListDetailLayout`（B 型の一覧 + 詳細）は `DbObjectListDetail` が包む。一覧の幅と開閉の保存 key の前置きは `LIST_DETAIL_STORAGE_PREFIX`（`production-ready-nl2sql.listDetail`）。一覧 + 詳細の画面は `DbObjectManagementPanelShell` に `framed={false}` を渡し、外側の枠を付けない（一覧と詳細の列がそれぞれ枠を持つ）。
- 共通のユーザー管理・ロール管理・権限管理（`@production-ready/system-settings` の `UserManagementPage` / `RoleManagementPage` / `RolePermissionsPage`）は共有の `ListDetailLayout` の一覧 + 詳細（#2282）。選択は URL の `?user=` / `?role=`（ロール管理の詳細の「権限管理で設定」は `?role=` を付けて権限管理を開く）、先頭の行を自動で選ばない。作成・編集は今までどおり一覧に代わる 1 枚のフォーム。製品のラッパー（`features/security/Security*Page.tsx`）は `listDetailStoragePrefix={LIST_DETAIL_STORAGE_PREFIX}` を渡す。

## 4. 色の移行

生のパレットの class をトークンへ置き換えるときは、重いファイルから着手する：共有 `DbObjectManagementShared`（テーブル / ビュー / SQL 分析の見た目の核）/ `DbAdminShared` / `DataManagementPage` / `EvaluationPage` / `QuestionLearningPage`。
