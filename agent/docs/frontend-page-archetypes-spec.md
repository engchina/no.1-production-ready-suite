# ページの型の割り当て（Agent の差分）

> ページの型（A〜D）と共有プリミティブの共通規約は platform の [UX 契約 page-archetypes.md](../../platform/docs/ux-contracts/page-archetypes.md)、作業状態と離脱ガードは [workspace-state.md](../../platform/docs/ux-contracts/workspace-state.md) が正本。
> 本書には Agent の各ページがどの型に属するかと、Agent 固有の補足だけを書く。システム設定（OCI 認証 / アップロード保存先 / モデル / データベース / 外観）は共有パッケージの画面なので対象外。

## 1. 割り当て

ルートの正本は `frontend/src/lib/routes.ts`（`APP_ROUTES`）と `frontend/src/App.tsx`。

| 型 | ページ（ルート） | 補足 |
|---|---|---|
| A. 一覧 → 全画面エディタ | 業務 Agent（`/agents`）/ Skill（`/skills`）/ 外部 MCP（`/settings/external-mcp`）/ 連携機能（`/plugins`）/ マーケットプレイス（`/plugins/marketplaces`） | `?id=` を唯一の情報源にする（§1.1）。 |
| B. マスタ詳細の閲覧 | Run（`/runs`）/ 承認（`/approvals`）/ メモリ（`/memory`）/ ツール（`/tools`） | 一覧と詳細を `FixedSplitPane` で並べる（§1.2）。 |
| C. ツール / ワークフロー | 監査（`/audit`）/ Control Plane バックアップ（`/settings/runtime-snapshot`） | 監査は 絞り込み → 適用 → 結果の `DataTable`。バックアップは 入力 → 検証 → 置換。 |
| D. ダッシュボード / 状態 | ダッシュボード（`/`）/ Runtime（`/runtimes`）/ Agent 接続設定（`/settings/connection`）/ 外部 RAG（`/settings/external-rag`）/ 外部 NL2SQL（`/settings/external-nl2sql`）/ ツール権限（`/settings/tool-policy`）/ Command Policy（`/settings/command-policy`）/ Runtime Safety（`/settings/runtime-safety`） | 運用設定の単一フォームは「状態 + 最小の編集」として D 型に置く。 |

### 1.1 A 型（一覧 → 全画面エディタ）

`frontend/src/lib/editor-route.ts` の `useEditorRoute` が URL の検索パラメータ `id` を読み書きする。

| `?id=` | 表示 |
|---|---|
| なし | 一覧（`DataTable`）。ページ操作（新規作成・再読込）は `PageHeader` |
| `new` | 新規作成のエディタ |
| `<対象の ID>` | その対象のエディタ（連携機能とマーケットプレイスは変更できる項目がないため詳細の閲覧）。一覧に無い ID は「対象が見つかりません」と一覧へ戻る導線を出し、別の対象へ黙って置き換えない |

| 画面 | ID | エディタの節 | 対象の操作（`EntityAction`） |
|---|---|---|---|
| 業務 Agent | Agent ID | 概要 / 基本情報 / Skill / 実行先（既存のみ） | 有効にする・無効にする |
| Skill | Skill ID | 概要 / 基本情報 / 内部依存。ビルトイン・ファイル・env は読み取り専用の詳細 | 削除（実行時に追加した Skill だけ） |
| 外部 MCP | Server ID | 概要 / 接続 / OAuth。一覧の下に MCP tools/list | 既定にする・削除（`default` は削除不可） |
| 連携機能 | Plugin ID | `new` は manifest の入力。既存は概要 / 内容（Skill・MCP・resource） | 有効にする・無効にする・アンインストール |
| マーケットプレイス | Marketplace ID | `new` は追加フォーム。既存は概要 / 利用可能な連携機能（行メニューから install） | 更新・削除 |

- 開く・閉じるは履歴に積む。再読込・戻る / 進むで同じ対象が開く。新規作成に成功したら作成した対象へ、エディタで削除したら一覧へ、どちらも `replace` で移る（戻るで空の新規フォームや消えた対象へ戻さない）。
- パンくずは共有 `Breadcrumbs`（一覧 › 対象名）を `PageHeader` の `breadcrumbs` に渡す。一覧へのリンクも §2 の離脱ガードの対象。
- `PageHeader` の操作は「一覧に戻る」（secondary）と「保存 / 作成」（primary）。対象の操作は同じ `EntityAction[]` を一覧の行では `RowActionMenu`、エディタでは概要の `ObjectActionBar` に渡す。破壊的な操作（削除・アンインストール・実行先の削除）は `useConfirm` で確認する。
- 一覧の行は、先頭セルの対象名のボタン（`RowTitleButton`）か行の操作以外の領域のクリックで開く。行の中のボタンは対象名と操作メニューの 2 つだけ。使える項目がない行のメニュー（既定の MCP サーバー）は disabled にする。
- 業務 Agent の有効 / 無効は対象の操作にし、フォームの下書きに含めない（切り替えで一覧を取り直しても、編集中の内容を上書きしない）。新規作成だけは初期状態をフォームで選ぶ。

### 1.2 B 型（一覧 + 詳細）

`frontend/src/components/EntityLayout.tsx` の `AgentSplitPane` が共有 `FixedSplitPane` に `storagePrefix="production-ready-agent.fixedSplitPane"` と Agent の i18n 文言を渡す。詳細側を既定で広くし、xl 未満は縦積み。

| 画面 | `splitId` | 左（一覧） | 右（詳細） | 対象の操作 |
|---|---|---|---|---|
| Run | `runs-list` | 実行の作成フォーム + 実行履歴 | 実行詳細 | 再開・再実行・キャンセル（確認あり） |
| 承認 | `approvals-list` | 承認一覧 | 引数と Run | 承認・拒否（確認あり。保留中だけ） |
| メモリ | `memory-list` | 検索 + メモリ一覧（登録フォームは分割ペインの上） | 内容とメタデータ | なし |
| ツール | `tools-list` | ツール一覧 | schema と監査タグ | なし |

- 行の操作以外の領域のクリックで選び、選択は行の背景と `aria-current` で示す。行の操作は `RowActionMenu`、詳細は同じ定義を `ObjectActionBar` に渡す。

## 2. 未保存変更の離脱ガード

共有パッケージ `@engchina/production-ready-system-settings` の `useUnsavedChangesGuard` / `useSettingsDraftGuard` を `frontend/src/lib/leave-guard.ts` で Agent の i18n 文言に包んで使う。dirty のときだけ、サイドナビ・内部リンクの移動を確認ダイアログで止め、再読込・タブを閉じる操作を `beforeunload` で止める。dirty は保存済みの基準との比較で判定し、保存に成功したら基準を更新する。

| 画面 | フック | dirty の対象 |
|---|---|---|
| 業務 Agent | `useEditorLeaveGuard` + `useDirtySources` | エディタのフォーム（Skill は集合として比較）と、実行先の追加フォーム。画面内の「一覧に戻る」とパンくずでも破棄を確認する |
| Skill / 外部 MCP | `useEditorLeaveGuard` | 全画面エディタのフォーム（開いた時点の内容と比較）。画面内の「一覧に戻る」とパンくずでも破棄を確認する |
| 連携機能 / マーケットプレイス | `useEditorLeaveGuard` | `?id=new` の manifest の入力 / 追加フォームの入力 |
| メモリ | `useEditorLeaveGuard` | 登録フォームの内容とメタデータ |
| 外部 RAG / 外部 NL2SQL / ツール権限 / Command Policy / Runtime Safety | `useSettingsLeaveGuard` | 取得した設定との差分（prefix は集合、ツール権限の「既定」は未指定として比較） |
| Control Plane バックアップ | `useSettingsLeaveGuard` | インポート JSON と理由。確認語（`REPLACE`）は対象外で、離脱で解除される |

ブラウザの戻る / 進む（`popstate`）は共有フックの制約で対象外（`<BrowserRouter>` のため）。A 型のエディタでも、戻る / 進むは URL の対象を開き直すだけで、未保存の編集は守らない。

## 3. 作業状態の保持

`frontend/src/lib/workspace-state.ts` の `useWorkspaceState` が、allowlist（`WORKSPACE_FIELDS`）に登録した field だけを同じタブの `sessionStorage` に残す。namespace は `production-ready-agent.workspace.v1:`、期限は最終保存から 8 時間、1 field の JSON は 20,000 文字まで。既定値と同じ値は保存しない。読めない・型違い・期限切れの値は捨てる。

| 画面 | 保存する field | 戻ったときの検証 |
|---|---|---|
| Run | 目標の下書き（`runs.goal`）、選択中の Run（`runs.selectedRunId`）、イベント購読方式（`runs.streamMode`） | 選択中の Run が一覧に無ければ説明を出し、最新の Run を表示する。Agent / Binding は実行条件なので保存しない |
| 監査 | 入力中の絞り込み（`audit.filterForm`）、適用済みの絞り込み（`audit.appliedForm`） | 適用済みの条件で一覧を取り直す |
| メモリ | 検索語（`memory.query`） | 検索し直す |

- A 型の編集対象は URL の `?id=` が唯一の情報源なので `sessionStorage` に置かない（#87 で置いた `skills.detailId` / `marketplaces.browseId` は #137 で URL へ移した）。サイドナビから開くと一覧に戻る。
- 秘密情報（MCP の OAuth client secret / session ID 等）、確認語、サーバー応答全体、未保存の編集フォームは保存しない。編集フォームは §2 の離脱ガードで守る。
- 確認語と破壊的操作の確認は画面の state にだけ置き、ページを離れると解除される。ページに戻っただけで mutation を送り直さない。
- Run の目標の一時保存に失敗した場合は警告を出し、離脱ガードを有効にする。
- Agent には現状ログインがないため、ユーザー / 接続先 context による分離は未実装。認証を入れるときは logout で `clearWorkspaceState()` を呼ぶ。

## 4. 検証

- `frontend/e2e/leave-guard-workspace-state.spec.ts`：desktop / 375px のサイドナビ・パンくずの移動の確認、保存後の解除、未変更時の自由な移動、`beforeunload`、画面内の「一覧に戻る」、監査の絞り込み / Run の目標 / メモリの検索語の往復と再読込、URL の対象の再読込と一覧に無い ID の説明、確認語の解除。
- `frontend/e2e/entity-archetypes.spec.ts`：desktop / 375px の A 型（URL で開く・再読込・戻る / 進む・パンくず・`?id=new`、行のボタンは対象名とメニューだけ、行メニューの Enter / 矢印 / Esc とフォーカスの戻り、破壊的な操作の確認とキャンセル、`ObjectActionBar` からの削除と一覧への復帰）と B 型（4 画面の `FixedSplitPane`、desktop は分割 / 375px は縦積み、divider のキー操作と保存 key、行の選択と詳細の更新、行メニューと詳細の操作の一致、承認の確認）。
- `frontend/e2e/agent-settings.spec.ts` / `agent-runtime-flow.spec.ts`：各 A 型画面の作成・編集・削除の主な導線。
