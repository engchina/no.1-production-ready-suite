# ページの型の割り当て（RAG の差分）

> ページの型（A〜D）と共有プリミティブの共通規約は platform の [UX 契約 page-archetypes.md](../../platform/docs/ux-contracts/page-archetypes.md)、対象の操作の置き場所は [buttons.md §5.1](../../platform/docs/ux-contracts/buttons.md#51-オブジェクト操作一覧行--詳細)が正本。RAG の離脱ガードと作業状態の保持の割り当ては [frontend-workspace-state-spec.md](./frontend-workspace-state-spec.md) に書く。
> 本書には RAG の各ページがどの型に属するかと、RAG 固有の補足・例外だけを書く（Issue #131）。システム設定（OCI 認証 / アップロード保存先 / モデル / データベース / 外観）は共有パッケージの画面なので対象外。

## 1. 割り当て

ルートの正本は `frontend/src/lib/routes.ts`（`APP_ROUTES`）と `frontend/src/App.tsx`。

| 型 | ページ（ルート） | 補足 |
|---|---|---|
| A. 一覧 → 全画面エディタ | 文書インデックス（`/file-list`）→ 文書詳細（`/documents/:id`）/ ナレッジベース（`/knowledge-bases`）→ ナレッジベース詳細（`/knowledge-bases/:id`）/ 業務ビュー（`/business-views`） | 文書とナレッジベースは一覧の名前のリンクから全画面の詳細へ移る（対象はパスの `:id`）。業務ビューは同じルートの `?id=` を唯一の情報源にする全画面エディタ（§1.1）。 |
| B. マスタ詳細の閲覧 | フィードバック（`/feedback`） | 一覧と詳細を `FixedSplitPane` で並べる（§4）。絞り込み・ページ・選んだ行は URL の検索パラメータ（選んだ行は `feedback`）に持つ。 |
| C. ツール / ワークフロー | 文書アップロード（`/upload`）/ RAG 検索（`/search`）/ チャット（`/chat`）/ 品質評価（`/evaluation`） | 入力 → 実行 → 結果。チャットの会話一覧は作業の切替のためのサイドバーで、マスタ詳細の一覧としては扱わない。 |
| D. ダッシュボード / 状態 | ダッシュボード（`/dashboard`）/ 設定の概要（`/settings/pipeline`）/ 検索・回答設定の各ページ（`/settings/preprocess` `/settings/parser-adapters` `/settings/chunking` `/settings/vector-index` `/settings/retrieval` `/settings/grounding` `/settings/generation` `/settings/prompts` `/settings/guardrail` `/settings/evaluation` `/settings/graph` `/settings/agentic`）/ 運用設定（`/settings/huggingface` `/settings/services`） | 設定の単一フォームは「状態 + 最小の編集」として D 型に置く。 |

### 1.1 業務ビューの全画面エディタ（`?id=`）

- `src/lib/editor-route.ts` の `useEditorRoute` が `?id=` を読む。なし = 一覧 / `new` = 新規 / `<id>` = その業務ビューの編集。ほかの検索パラメータは残す。
- 一覧は `DataTable`。行の操作以外の領域のクリックと、先頭セルの名前のボタン（`{name} を編集`）でエディタを開く。アーカイブ済みの行は開かない。「新規作成」は `PageHeader` の primary。
- 開く・一覧へ戻るは履歴に積む（再読込・ブラウザの戻る / 進むで同じ対象が開く）。作成に成功したら作成した業務ビューの `?id=` へ、エディタからアーカイブしたら一覧へ、どちらも `replace` で移る（戻るで空の新規フォームや消えた対象へ戻さない）。
- エディタの上部は パンくず（`業務ビュー › 名前`、共有 `Breadcrumbs`）+ `一覧へ戻る`（secondary）+ `保存する` / `作成する`（primary）。375px では共有 `PageHeader` の規則で `一覧へ戻る` が「その他の操作」に入る。対象の操作（アーカイブ）は「基本情報と検索・回答設定」の見出しの `ObjectActionBar`、知識パネル（キーワード / FAQ / 用語・ルール）はその下に積む。
- `?id=` の業務ビューが無い（404）ときは「対象が見つかりません」と `一覧へ戻る` を出し、別の対象へ置き換えない。取得の失敗（404 以外）は再試行を出す。直接 URL で開いたアーカイブ済みの業務ビューは警告を出し、保存を無効にする。
- 共通の部品（`EditorBreadcrumbs` / `RowTitleButton` / `MissingEditorTarget` / `RagSplitPane`）は `src/components/layout/EntityLayout.tsx`。

## 2. 対象の操作（`EntityAction` / `RowActionMenu` / `ObjectActionBar`）

対象オブジェクトの操作は画面ごとに `EntityAction[]` を 1 回だけ定義し、一覧の行は `RowActionMenu`（1 行に 1 個）、詳細は `ObjectActionBar` に渡す。危険な操作は `tone: "danger"` にして、確定は `useConfirm` の確認ダイアログで行う。メニューのトリガーと詳細の操作のバーの名前は `common.objectActions.aria`（`{name} の操作`）、「その他の操作」は `common.objectActions.more`。

| 画面 | 対象 | 行（`RowActionMenu`） | 詳細（`ObjectActionBar`） | 定義の場所 |
|---|---|---|---|---|
| 文書インデックス | 文書 | ファイル準備を実行 / 再実行（`UPLOADED` / `ERROR` のみ）、削除（danger） | —（文書詳細は工程の操作が中心。§3） | `FileListClient.tsx` の `documentActions` |
| ナレッジベース / 詳細 | ナレッジベース | アーカイブ（danger。DEFAULT は理由付きで無効） | 同じ定義。危険な操作だけなので「その他の操作」に入る | `knowledge-bases/knowledge-base-actions.ts` の `useKnowledgeBaseActions` |
| ナレッジベース詳細 | 所属文書 | 外す（確認は warning。所属の行だけを消し、chunk へ波及しない） | — | `KnowledgeBaseDetailClient.tsx` |
| 業務ビュー | 業務ビュー | アーカイブ（danger。DEFAULT は理由付きで無効） | エディタの「基本情報と検索・回答設定」の見出しに同じ定義 | `BusinessViewManagementClient.tsx` の `useBusinessViewActions` |
| 業務ビュー（知識パネル） | 承認済み FAQ | 削除（danger。確認ダイアログを通す） | — | `ApprovedFaqManager.tsx` |
| 業務ビュー（用語・ルール） | 用語 / ルール | —（行は選択専用。名前のボタンと行のクリックで編集フォームへ読み込む） | フォームの 保存 / 削除（danger。確認ダイアログを通す） | `RuntimeKnowledgeManager.tsx` |
| 回答プロンプト | 版 | 有効化（有効な版は理由付きで無効） | — | `PromptVersionsClient.tsx` の `versionActions` |
| フィードバック | 回答のフィードバック | —（行は選択専用） | Approved FAQ に登録（確認ダイアログを通す。同じ質問の FAQ は置き換える）、品質評価のケースに追加（品質評価の要求 JSON に追記して品質評価へ移る）。詳細の見出しの下の行に置く。引用のフィードバックには出さない | `FeedbackClient.tsx` の `FeedbackPromotionActions` |
| RAG 検索 / チャット | 保存された回答 | — | この回答を削除（danger。確認ダイアログを通す。危険な操作だけなので「その他の操作」に入る） | `DocragAnswerHistory.tsx` の `SavedDocragAnswer` |
| サービス管理 | サービス（`deployable` の行） | ログを表示 / 閉じる、起動（一部異常のときだけ）、ビルド、削除（danger。確認ダイアログを通す）。行には別に状態に応じた起動 / 停止を 1 つだけ出す（§3.1） | — | `ServicesManagementClient.tsx` の `ServiceRow`（`servicePrimaryAction`） |
| 文書詳細（処理レシピ） | 選択中のレシピ | —（レシピのカードは選択専用） | 処理を開始 / 再開 / 再試行 / 再処理、レシピを削除（danger。最後の 1 件と処理中は無効） | `DocumentRecipeManager.tsx` の `recipeActions` |

- **選択の導線**：`編集` / `詳細` のような選ぶだけの操作はメニューに入れない。業務ビュー・用語 / ルール・フィードバックは、行の操作以外の領域のクリックと先頭セルの名前のボタンで開く / 選ぶ。同じ画面で一覧と選んだ対象を並べる場合（用語 / ルール・フィードバック）は、選んだ行を `aria-current` と背景で示す。
- **一括操作**：文書インデックスで行を 1 件以上選んでいる間と一括処理の間は、行の `RowActionMenu` を disabled にし、一括操作のバーへ集める。
- **3 層モデル**：操作の置き場所を変えただけで、文書レシピ / KB スコープ / Business View の責務と API は変えない。

## 3. 例外（行に `RowActionMenu` 以外のボタンを残す画面）

理由のある例外だけを残す（#147 で業務ビュー・フィードバック・ランタイム知識・回答プロンプト・保存された回答は規約に揃えた。#158 でサービス管理を §3.1 の規則に揃え、例外から外した）。

| 画面 | 残しているボタン | 理由 |
|---|---|---|
| 文書詳細の工程操作 | 承認・ファイル準備の実行・工程の再試行・再処理 | 対象の操作ではなく工程を進める操作（buttons.md §3 の主操作）。C 型の工程の段として扱う。 |
| チャットの会話一覧 | 名前を変更（icon-only 1 個） | 会話一覧は作業の切替のサイドバーで、行に 1 個だけのその場の名前の編集。`RowActionMenu` へ移すと 1 手増えるため据え置く。 |

### 3.1 運用のコントロールパネル（サービス管理）：状態に応じた主操作 1 つ + `RowActionMenu`

サービス管理は、サービスごとの起動 / 停止をすぐ押せることが要件の運用のパネルなので、行の操作を次の形にそろえる（#158 で決定）。規約（buttons.md §5.1）の例外ではなく、この画面の型として扱う。

- **主操作は 1 つだけ**：行に常に表示するのは、状態から決まる起動 / 停止のどちらか 1 つのボタン（`servicePrimaryAction`）。稼働中・一部異常は「停止」、停止中・未設定・状態の取得中 / 失敗は「起動」。起動と停止を同時に並べない。見た目は `secondary` の `sm`（停止は `tone="danger"`）で、ページの `primary` は使わない。
- **副操作はメニュー 1 個**：ログ（表示 / 閉じる）・ビルド・削除と、一部異常のときの起動（再作成）は、`EntityAction[]` にして行に 1 個の `RowActionMenu` へ入れる。削除は `tone: "danger"` で区切り線の下に置く。
- **確認と無効**：停止と削除は従来どおり `useConfirm` の確認ダイアログ（danger）を通し、起動とビルドは確認なしで実行する。制御が無効（prod の可視化のみ）・状態の取得中 / 失敗のときは主操作を無効にし、理由をツールチップで示す。同じサービスの操作中は、そのサービスの他の操作を無効にする。操作中は主操作のボタン、またはメニューのトリガーにスピナーを出す。
- **対象外の行**：backend 内処理の段（`deployable=false`）は状態の badge だけを出し、主操作もメニューも出さない。
- **推論サーバー（vLLM / SGLang）**：各 parser のイメージに内包され、親の parser の起動 / 停止で一緒に制御される（backend / compose の責務）。独立した行として出さず、この変更でも扱いを変えない。
- **レスポンシブ**：375px では状態の badge・主操作・メニューを名前の下に折り返し、ページの横スクロールを出さない。

## 4. 分割ペイン

一覧と詳細を常に並べる画面はフィードバックだけ。`src/components/layout/EntityLayout.tsx` の `RagSplitPane` が `@engchina/production-ready-ui` の `FixedSplitPane` を `storagePrefix="production-ready-rag.fixedSplitPane"` と RAG の文言（`split.*`）で包む。`splitId` は `<feature>-<view>`。

| 画面 | `splitId` | 既定で広い側 | 補足 |
|---|---|---|---|
| フィードバック | `feedback-list` | 左（一覧） | 一覧は評価・問題の概要・理由・対象 / 送信元の密な表なので一覧側を広くし、詳細は divider で広げる。モデル名と実行情報は詳細の「実行情報」タブに置く。`xl` 未満は縦積み（一覧 → 詳細）で、行を選ぶと詳細の見出しへフォーカスを移す。`md` 未満の一覧はカード。詳細の `詳細を閉じる` で選択を外し、その行の名前のボタンへフォーカスを戻す。 |

チャットの会話一覧は作業の切替のサイドバーで、分割ペインとしては扱わない。

## 5. 検証

- Playwright：`e2e/document-delete.spec.ts`（行のメニューの Enter で開く / Esc で閉じてトリガーへフォーカスを戻す、矢印キー、削除の確認のキャンセルと確定、一括選択中の disabled）、`e2e/knowledge-bases.spec.ts`（行のメニューからのアーカイブ・所属から外す、詳細の `ObjectActionBar` の「その他の操作」とフォーカスの戻り、DEFAULT の無効理由）、`e2e/business-views.spec.ts`（行のクリック / 名前のボタンで `?id=` のエディタを開く、再読込・戻る / 進む、パンくず、未保存の変更の確認と対象ごとの下書き、存在しない `?id=`、作成と アーカイブ の `replace`、DEFAULT の無効理由）、`e2e/feedback.spec.ts`（行のクリック / 名前のボタンでの選択と `aria-current`、分割ペインの divider と保存 key、375px の縦積み・カードの選択・詳細へのフォーカス、URL からの復元）、`e2e/docrag-knowledge.spec.ts`（用語・ルールの行の選択と削除の確認、保存された回答の `ObjectActionBar` からの削除の確認のキャンセルと確定）、`e2e/prompt-versions.spec.ts`（版の `RowActionMenu` の有効化と有効な版の無効理由）、`e2e/services-management.spec.ts`（各行が状態に応じた主操作 1 つ + メニューのトリガー 1 つだけ、起動 / 停止で主操作が切り替わる、メニューからのログ・ビルド・削除の確認、制御無効時の無効、Esc でトリガーへフォーカスが戻る、375px で横スクロールなし）。いずれも desktop / mobile-375 の両 project で動く。
