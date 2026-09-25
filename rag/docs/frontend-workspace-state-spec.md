# ページ遷移と作業状態の仕様（RAG）

正本の原則は platform の [UX 契約 workspace-state.md](../../platform/docs/ux-contracts/workspace-state.md)。ここには RAG での割り当て（どの画面・どの field を保存するか、namespace・期限、検証の spec）だけを書く（Issue #132）。

## 未保存変更の離脱ガード

共有パッケージ `@engchina/production-ready-system-settings` の `useSettingsDraftGuard` / `useUnsavedChangesGuard` を、`src/lib/leave-guard.ts` の `useLeaveGuard` / `useCustomLeaveGuard` 経由で使う。dirty のときだけ、内部リンク（サイドナビを含む）・再読込・タブを閉じる操作を確認する。`navigate()` で移動するコマンドパレットとログアウトは、先に `confirmPendingLeave()` で同じ確認を通す。保存に成功したら、サーバーの値（または保存後の入力）を基準に dirty を判定し直す。

| 画面 | dirty の判定 |
|---|---|
| 検索・回答設定（ファイル準備 / 文書解析 / 文書分割 / 検索インデックス / 検索方法 / 根拠確認 / 回答スタイル / 回答記録の保存期間 / 回答プロンプト / 安全チェック / 品質評価 / GraphRAG / エージェント計画） | フォームとサーバーの設定値の差分。検索方法の legacy 形式の移行保存だけでは確認しない |
| HuggingFace 設定 | endpoint の差分、token の入力、token の削除指定（token は保存しない） |
| システム設定（OCI 認証 / アップロード保存先 / モデル / データベース） | 共有パッケージの画面が持つ判定。RAG は `draftGuardMessages` で文言を渡す |
| 業務ビュー（作成・編集フォーム） | 名前・説明・設定の差分（KB は集合として比べる）。下書きはこのタブに残るため、確認は「移動する」 |
| 業務ビュー（ドメインキーワード / 承認済み FAQ の追加入力 / ランタイム知識） | 保存済みの値、または読み込んだ行との差分 |
| ナレッジベースの作成 | 名前・説明の入力 |
| 文書詳細（KB 所属 / レシピの処理設定 / 抽出確認の編集） | 集合としての所属の差分、レシピ設定の差分、抽出確認の未保存編集（抽出確認は画面固有の文言） |

ブラウザの戻る / 進む（`popstate`）は `<BrowserRouter>` では安全に差し戻せないため対象外（UX 契約のとおり）。

## 作業状態の保持

`src/lib/workspace-state.ts` の `useWorkspaceState` が、同じタブの `sessionStorage` に allowlist（`WorkspaceField`）の field だけを保存する。

- namespace：`production-ready-rag.workspace.v1:`（field ごとに `<namespace><field>[:<scope>]`）
- 期限：最終保存から 8 時間。1 field の JSON は 200,000 文字まで
- ユーザーの結び付け：`AuthProvider` が今のユーザー ID を `…owner` に記録し、変わったら namespace 配下を消す。logout でも消す
- storage が使えない・上限を超えた場合は初期値で動き、初期値から変わった入力は `beforeunload` で保護する

| 画面 | 保存する内容 | 保存方法 |
|---|---|---|
| RAG 検索 | 質問、対象の業務ビュー、検索方式、詳細条件（内容種別・見出し・候補取得数・Rerank 採用数・開閉） | sessionStorage |
| チャット | 選択中の業務ビュー、選択中の会話、入力中のメッセージ | sessionStorage（URL の `business_view_id` / `conversation_id` を優先） |
| 業務ビュー | 絞り込み・検索・ページ・編集対象、作成 / 編集フォームの下書き（業務ビューごと） | sessionStorage |
| 文書インデックス | 状態の絞り込み・KB の絞り込み・検索・ページ | sessionStorage |
| ナレッジベース | 状態の絞り込み・検索・ページ | sessionStorage |
| 品質評価 | 評価 JSON、比較 JSON、ランキング指標、KB スコープ、評価スイート | sessionStorage |
| フィードバック | 期間・絞り込み・検索・並べ替え・ページ・開いている詳細 | URL の検索パラメータ。サイドナビでパラメータなしの URL に戻ったときは `RememberedSearchParams` が直前の URL を復元 |

保存しないもの：回答・引用・評価結果などのサーバー応答、結果行、秘密情報（token・API key）、文書インデックスの行の選択（一括削除につながるため、ページを離れたら解除）、確認ダイアログと確認語。戻っただけで検索・チャット・評価を送り直さない。

復元した対象（業務ビュー・KB の絞り込み・編集中の業務ビュー）が一覧に無くなった場合は、その選択だけを外し、別の対象へ置き換えない。チャットの会話が取得できない場合は、選択を残してエラーと再試行を示す。

## 検証

- Playwright：`e2e/workspace-state.spec.ts`（desktop / mobile-375）。離脱の確認とキャンセル・破棄、dirty でないときの自由な移動、`beforeunload`、保存後の基準更新、業務ビューの下書きの復元、検索・チャット・文書インデックスのページ往復と再読込、フィードバックの URL の復元
- Vitest：`src/lib/workspace-state.test.ts`（期限・型不一致・上限・storage 無効・ユーザー切替・namespace 外を消さないこと）
