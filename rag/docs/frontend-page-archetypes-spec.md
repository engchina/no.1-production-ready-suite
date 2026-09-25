# ページの型の割り当て（RAG の差分）

> ページの型（A〜D）と共有プリミティブの共通規約は platform の [UX 契約 page-archetypes.md](../../platform/docs/ux-contracts/page-archetypes.md)、対象の操作の置き場所は [buttons.md §5.1](../../platform/docs/ux-contracts/buttons.md#51-オブジェクト操作一覧行--詳細)が正本。RAG の離脱ガードと作業状態の保持の割り当ては [frontend-workspace-state-spec.md](./frontend-workspace-state-spec.md) に書く。
> 本書には RAG の各ページがどの型に属するかと、RAG 固有の補足・例外だけを書く（Issue #131）。システム設定（OCI 認証 / アップロード保存先 / モデル / データベース / 外観）は共有パッケージの画面なので対象外。

## 1. 割り当て

ルートの正本は `frontend/src/lib/routes.ts`（`APP_ROUTES`）と `frontend/src/App.tsx`。

| 型 | ページ（ルート） | 補足 |
|---|---|---|
| A. 一覧 → 全画面エディタ | 文書インデックス（`/file-list`）→ 文書詳細（`/documents/:id`）/ ナレッジベース（`/knowledge-bases`）→ ナレッジベース詳細（`/knowledge-bases/:id`）/ 業務ビュー（`/business-views`） | 文書とナレッジベースは一覧の名前のリンクから全画面の詳細へ移る（対象はパスの `:id`）。業務ビューは一覧と同じ画面の上部に編集フォームを出し、編集対象は `sessionStorage` の `businessViews.view.editingId` に残す。`?id=` を唯一の情報源にする全画面エディタへの載せ替えは未移行。 |
| B. マスタ詳細の閲覧 | フィードバック（`/feedback`） | 一覧から詳細をダイアログ（右側のパネル）で開く。絞り込みと開いている詳細は URL の検索パラメータに持つ。`FixedSplitPane` への載せ替えは未移行（§4）。 |
| C. ツール / ワークフロー | 文書アップロード（`/upload`）/ RAG 検索（`/search`）/ チャット（`/chat`）/ 品質評価（`/evaluation`） | 入力 → 実行 → 結果。チャットの会話一覧は作業の切替のためのサイドバーで、マスタ詳細の一覧としては扱わない。 |
| D. ダッシュボード / 状態 | ダッシュボード（`/dashboard`）/ 設定の概要（`/settings/pipeline`）/ 検索・回答設定の各ページ（`/settings/preprocess` `/settings/parser-adapters` `/settings/chunking` `/settings/vector-index` `/settings/retrieval` `/settings/grounding` `/settings/generation` `/settings/prompts` `/settings/guardrail` `/settings/evaluation` `/settings/graph` `/settings/agentic`）/ 運用設定（`/settings/huggingface` `/settings/services`） | 設定の単一フォームは「状態 + 最小の編集」として D 型に置く。 |

## 2. 対象の操作（`EntityAction` / `RowActionMenu` / `ObjectActionBar`）

対象オブジェクトの操作は画面ごとに `EntityAction[]` を 1 回だけ定義し、一覧の行は `RowActionMenu`（1 行に 1 個）、詳細は `ObjectActionBar` に渡す。危険な操作は `tone: "danger"` にして、確定は `useConfirm` の確認ダイアログで行う。メニューのトリガーと詳細の操作のバーの名前は `common.objectActions.aria`（`{name} の操作`）、「その他の操作」は `common.objectActions.more`。

| 画面 | 対象 | 行（`RowActionMenu`） | 詳細（`ObjectActionBar`） | 定義の場所 |
|---|---|---|---|---|
| 文書インデックス | 文書 | ファイル準備を実行 / 再実行（`UPLOADED` / `ERROR` のみ）、削除（danger） | —（文書詳細は工程の操作が中心。§3） | `FileListClient.tsx` の `documentActions` |
| ナレッジベース / 詳細 | ナレッジベース | アーカイブ（danger。DEFAULT は理由付きで無効） | 同じ定義。危険な操作だけなので「その他の操作」に入る | `knowledge-bases/knowledge-base-actions.ts` の `useKnowledgeBaseActions` |
| ナレッジベース詳細 | 所属文書 | 外す（確認は warning。所属の行だけを消し、chunk へ波及しない） | — | `KnowledgeBaseDetailClient.tsx` |
| 業務ビュー | 業務ビュー | アーカイブ（danger。DEFAULT は理由付きで無効） | 編集フォームの見出しに同じ定義 | `BusinessViewManagementClient.tsx` の `businessViewActions` |
| 業務ビュー（知識パネル） | 承認済み FAQ | 削除（danger。確認ダイアログを通す） | — | `ApprovedFaqManager.tsx` |
| 文書詳細（処理レシピ） | 選択中のレシピ | —（レシピのカードは選択専用） | 処理を開始 / 再開 / 再試行 / 再処理、レシピを削除（danger。最後の 1 件と処理中は無効） | `DocumentRecipeManager.tsx` の `recipeActions` |

- **選択の導線**：`編集` / `詳細` のような選ぶだけの操作はメニューに入れない。業務ビューはカードの操作以外の領域のクリックと、名前のボタン（`{name} を編集`）で編集対象に選び、選んだカードを `aria-current` と背景で示す。
- **一括操作**：文書インデックスで行を 1 件以上選んでいる間と一括処理の間は、行の `RowActionMenu` を disabled にし、一括操作のバーへ集める。
- **3 層モデル**：操作の置き場所を変えただけで、文書レシピ / KB スコープ / Business View の責務と API は変えない。

## 3. 例外（行に `RowActionMenu` 以外のボタンを残す画面）

| 画面 | 残しているボタン | 理由 / 今後 |
|---|---|---|
| 文書詳細の工程操作 | 承認・ファイル準備の実行・工程の再試行・再処理 | 対象の操作ではなく工程を進める操作（buttons.md §3 の主操作）。C 型の工程の段として扱う。 |
| チャットの会話一覧 | 名前を変更（icon-only 1 個） | 行に 1 個だけで、その場で名前を編集する。`RowActionMenu` へ移すと 1 手増えるため据え置く（follow-up 候補）。 |
| 業務ビューのランタイム知識（用語・ルール） | 編集（1 個） | 選択の導線（フォームへ読み込む）。名前のボタン + 行のクリックへの載せ替えは follow-up 候補。 |
| 回答プロンプトの版 | 有効化（1 個） | 行に 1 個の非破壊の操作で、有効な版は badge と同じ文言で無効になる。 |
| フィードバック | 詳細を表示（1 個） | 詳細をダイアログで開くためのキーボードの導線とフォーカスの戻り先。B 型の分割ペインへ載せ替えるときに行のクリックへ移す（follow-up 候補）。 |
| サービス管理 | ログ・ビルド・起動・停止・削除 | サービスごとの運用のコントロールパネルで、起動 / 停止を常に見せる必要がある。`RowActionMenu` + `ObjectActionBar` への整理は follow-up 候補。 |
| RAG 検索 / チャットの保存済み回答 | この回答を削除（1 個） | 回答の表示の末尾に置く単独の危険な操作（確認ダイアログあり）。`ObjectActionBar` への移設は follow-up 候補。 |

## 4. 分割ペイン

現状、一覧と詳細を常に並べる画面はない（フィードバックはダイアログ、チャットは作業の切替のサイドバー）ため `FixedSplitPane` は使っていない。導入するときは `@engchina/production-ready-ui` の `FixedSplitPane` を `storagePrefix="production-ready-rag.fixedSplitPane"` で使い、`splitId` は `<feature>-<view>` にする。

## 5. 検証

- Playwright：`e2e/document-delete.spec.ts`（行のメニューの Enter で開く / Esc で閉じてトリガーへフォーカスを戻す、矢印キー、削除の確認のキャンセルと確定、一括選択中の disabled）、`e2e/knowledge-bases.spec.ts`（行のメニューからのアーカイブ・所属から外す、詳細の `ObjectActionBar` の「その他の操作」とフォーカスの戻り、DEFAULT の無効理由）、`e2e/business-views.spec.ts`（カードの選択と `aria-current`、DEFAULT の無効理由）。いずれも desktop / mobile-375 の両 project で動く。
