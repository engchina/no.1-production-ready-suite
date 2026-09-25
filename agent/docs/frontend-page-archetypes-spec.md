# ページの型の割り当て（Agent の差分）

> ページの型（A〜D）と共有プリミティブの共通規約は platform の [UX 契約 page-archetypes.md](../../platform/docs/ux-contracts/page-archetypes.md)、作業状態と離脱ガードは [workspace-state.md](../../platform/docs/ux-contracts/workspace-state.md) が正本。
> 本書には Agent の各ページがどの型に属するかと、Agent 固有の補足だけを書く。システム設定（OCI 認証 / アップロード保存先 / モデル / データベース / 外観）は共有パッケージの画面なので対象外。

## 1. 割り当て

ルートの正本は `frontend/src/lib/routes.ts`（`APP_ROUTES`）と `frontend/src/App.tsx`。

| 型 | ページ（ルート） | 補足 |
|---|---|---|
| A. 一覧 → 全画面エディタ | 業務 Agent（`/agents`）/ Skill（`/skills`）/ 外部 MCP（`/settings/external-mcp`）/ 連携機能（`/plugins`）/ マーケットプレイス（`/plugins/marketplaces`） | 現状は一覧と同じ画面に作成・編集のカードを出す。`?id=` を唯一の情報源にする全画面エディタへの載せ替えは未移行。 |
| B. マスタ詳細の閲覧 | Run（`/runs`）/ 承認（`/approvals`）/ メモリ（`/memory`）/ ツール（`/tools`） | Run は一覧と詳細を並べる参照実装。`FixedSplitPane` は `packages/ui` に入った後に移す。 |
| C. ツール / ワークフロー | 監査（`/audit`）/ Control Plane バックアップ（`/settings/runtime-snapshot`） | 監査は 絞り込み → 適用 → 結果の `DataTable`。バックアップは 入力 → 検証 → 置換。 |
| D. ダッシュボード / 状態 | ダッシュボード（`/`）/ Runtime（`/runtimes`）/ Agent 接続設定（`/settings/connection`）/ 外部 RAG（`/settings/external-rag`）/ 外部 NL2SQL（`/settings/external-nl2sql`）/ ツール権限（`/settings/tool-policy`）/ Command Policy（`/settings/command-policy`）/ Runtime Safety（`/settings/runtime-safety`） | 運用設定の単一フォームは「状態 + 最小の編集」として D 型に置く。 |

## 2. 未保存変更の離脱ガード

共有パッケージ `@engchina/production-ready-system-settings` の `useUnsavedChangesGuard` / `useSettingsDraftGuard` を `frontend/src/lib/leave-guard.ts` で Agent の i18n 文言に包んで使う。dirty のときだけ、サイドナビ・内部リンクの移動を確認ダイアログで止め、再読込・タブを閉じる操作を `beforeunload` で止める。dirty は保存済みの基準との比較で判定し、保存に成功したら基準を更新する。

| 画面 | フック | dirty の対象 |
|---|---|---|
| 業務 Agent | `useEditorLeaveGuard` + `useDirtySources` | 新規作成フォーム、各 Agent の編集フォーム（Skill は集合として比較）、各 Agent の Binding 追加フォーム |
| Skill / 外部 MCP | `useEditorLeaveGuard` | 開いている作成・編集フォーム（開いた時点の内容と比較）。画面内の「キャンセル」や別の対象の編集への切替でも破棄を確認する |
| 連携機能 / マーケットプレイス | `useEditorLeaveGuard` | manifest の入力 / 追加フォームの入力 |
| メモリ | `useEditorLeaveGuard` | 登録フォームの内容とメタデータ |
| 外部 RAG / 外部 NL2SQL / ツール権限 / Command Policy / Runtime Safety | `useSettingsLeaveGuard` | 取得した設定との差分（prefix は集合、ツール権限の「既定」は未指定として比較） |
| Control Plane バックアップ | `useSettingsLeaveGuard` | インポート JSON と理由。確認語（`REPLACE`）は対象外で、離脱で解除される |

ブラウザの戻る / 進む（`popstate`）は共有フックの制約で対象外（`<BrowserRouter>` のため）。

## 3. 作業状態の保持

`frontend/src/lib/workspace-state.ts` の `useWorkspaceState` が、allowlist（`WORKSPACE_FIELDS`）に登録した field だけを同じタブの `sessionStorage` に残す。namespace は `production-ready-agent.workspace.v1:`、期限は最終保存から 8 時間、1 field の JSON は 20,000 文字まで。既定値と同じ値は保存しない。読めない・型違い・期限切れの値は捨てる。

| 画面 | 保存する field | 戻ったときの検証 |
|---|---|---|
| Run | 目標の下書き（`runs.goal`）、選択中の Run（`runs.selectedRunId`）、イベント購読方式（`runs.streamMode`） | 選択中の Run が一覧に無ければ説明を出し、最新の Run を表示する。Agent / Binding は実行条件なので保存しない |
| 監査 | 入力中の絞り込み（`audit.filterForm`）、適用済みの絞り込み（`audit.appliedForm`） | 適用済みの条件で一覧を取り直す |
| メモリ | 検索語（`memory.query`） | 検索し直す |
| Skill | 詳細を開いている Skill（`skills.detailId`） | 一覧に無ければ説明を出し、選択を外す |
| マーケットプレイス | 連携機能を見ているマーケットプレイス（`marketplaces.browseId`） | 一覧に無ければ説明を出し、選択を外す |

- 秘密情報（MCP の OAuth client secret / session ID 等）、確認語、サーバー応答全体、未保存の編集フォームは保存しない。編集フォームは §2 の離脱ガードで守る。
- 確認語と破壊的操作の確認は画面の state にだけ置き、ページを離れると解除される。ページに戻っただけで mutation を送り直さない。
- Run の目標の一時保存に失敗した場合は警告を出し、離脱ガードを有効にする。
- Agent には現状ログインがないため、ユーザー / 接続先 context による分離は未実装。認証を入れるときは logout で `clearWorkspaceState()` を呼ぶ。

## 4. 検証

Playwright の `frontend/e2e/leave-guard-workspace-state.spec.ts` で、desktop / 375px のサイドナビ移動の確認・保存後の解除・未変更時の自由な移動・`beforeunload`・画面内キャンセル・監査の絞り込み / Skill の選択 / Run の目標 / メモリの検索語の往復と再読込・失効した選択の説明・確認語の解除を確認する。
