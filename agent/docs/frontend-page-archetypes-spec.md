# ページの型の割り当て（Agent の差分）

> ページの型（A〜D）と共有プリミティブの共通規約は platform の [UX 契約 page-archetypes.md](../../platform/docs/ux-contracts/page-archetypes.md)、作業状態と離脱ガードは [workspace-state.md](../../platform/docs/ux-contracts/workspace-state.md) が正本。
> 本書には Agent の各ページがどの型に属するかと、Agent 固有の補足だけを書く。システム設定（OCI 認証 / アップロード保存先 / モデル / データベース / 外観）は共有パッケージの画面なので対象外。

## 1. 割り当て

ルートの正本は `frontend/src/lib/routes.ts`（`APP_ROUTES`）と `frontend/src/App.tsx`。画面は `frontend/src/pages/<領域>/<画面>.tsx`（`agents` / `runs` / `approvals` / `audit` / `skills` / `plugins` / `marketplaces` / `settings` など）に置き、`App.tsx` が `React.lazy` で route ごとに読む（#818）。読み込みの間は `PageBody wide` の中に `TimedLoadingState`（「画面を読み込んでいます」、`placement="page"`）を出す。

| 型 | ページ（ルート） | 補足 |
|---|---|---|
| A. 一覧 → 全画面エディタ | 自動実行（`/automations`。#784）/ 業務 Agent（`/agents`）/ スキル（`/skills`）/ MCP 接続（`/settings/mcp-connections`）/ プラグイン（`/plugins`。ナビには出さず、マーケットプレイスから入れる）/ マーケットプレイス（`/plugins/marketplaces`） | `?id=` を唯一の情報源にする（§1.1）。 |
| B. マスタ詳細の閲覧 | ツール（`/tools`。ナビには出さない） | 一覧と詳細を `FixedSplitPane` で並べる（§1.2）。 |
| B の例外. 一覧 → 全幅の詳細 | 実行履歴（`/runs`。#875）/ 承認（`/approvals`。#877） | 実行は回答と過程、承認は目標・理由・引数と判断記録を全幅で確認する。実行の作成は `?id=new`、詳細は各対象 ID。承認には作成画面を設けない（§1.3 / §1.4）。 |
| C. ツール / ワークフロー | 監査ログ（`/audit`）/ フィードバック（`/feedback`。絞り込み → 集計と一覧 → 詳細の side sheet）/ 品質評価（`/evaluation`。評価セットの一覧 → 実行状況 → 評価概要（前回との比較）→ ケース別結果 → 最近の評価。評価セットは A 型の `?id=` の全画面エディタで、評価ケースを 1 件ずつ編集し Excel で取り込み・書き出しする）/ バックアップと復元（`/settings/runtime-snapshot`。検証の結果は「検証」の直下の `SettingsTestResultPanel` 1 つ。#814）/ API キー（`/settings/api-keys`。発行のフォーム → 発行したキーの 1 回だけの表示 → 一覧） | 監査ログは 絞り込み → 適用 → 結果の `DataTable`。バックアップは 入力 → 検証 → 置換。 |
| D. ダッシュボード / 状態 | 実行環境（`/runtimes`。組み込みの実行環境の状態）/ 利用状況（`/usage`）/ ツール権限（`/settings/tool-policy`。ナビには出さない。ツールの一覧は検索・ページングの表。#818） | 運用設定の単一フォームは「状態 + 最小の編集」として D 型に置く。ダッシュボード（`/`）は廃止した（#262）。チャット（`/chat`。#768）は会話の画面で、型の外（入力 → 回答を会話として積む。送信は「その場で結果を待つ操作」で、実行中は同じボタンが「停止」になる。buttons.md §3.1）。 |

### サイドナビの構成（#791）

上に一般の利用者が毎日使う画面、下に管理者が作る・運用する画面を置く（RAG・NL2SQL と同じ考え方。#658）。
正本は `frontend/src/components/layout/nav-config.ts`。権限管理の機能の一覧も同じ並び・名前になる（#567）。

| セクション | 項目 | 使う人 |
|---|---|---|
| AI 活用（NL2SQL と同じ名前） | チャット / 実行履歴（`/runs`）/ 承認 | 一般の利用者 |
| Agent 構築（RAG の「ナレッジ構築」・NL2SQL の「データ準備」に当たる） | 業務 Agent / スキル / 自動実行 / マーケットプレイス | 管理者（作る人） |
| 改善・運用 | 品質評価 / フィードバック / 利用状況 / 監査ログ | 管理者・監査担当 |
| セキュリティ設定 → ユーザーとロール | 権限管理 → ユーザー管理 / ロール管理 | 管理者 |
| 運用設定 | システムテーブル / 実行環境（`/runtimes`）/ MCP 接続 / API キー / バックアップと復元 | 管理者 |
| システム設定（3 製品共通） | OCI 認証 / アップロード保存先 / モデル / データベース / 外観 | 管理者 |

画面・ナビの文言に「Control Plane」「Runtime」のような基盤の用語を使わない（コードの識別子・URL・権限コードはそのまま）。用語は次にそろえる（#802 / #807）。

| 概念（コード） | 画面の文言 |
|---|---|
| Run（`run`） | 実行（一覧は「実行履歴」） |
| Skill（`skill`） | スキル |
| Runtime（組み込み Runtime） | 実行環境 |
| Plugin（`plugin`） | プラグイン |
| Approval / Audit | 承認 / 監査ログ |

`/` は画面を持たない入口で、NL2SQL と同じく次のように振り分ける（`frontend/src/lib/route-permissions.ts`）。

- `/` → ナビの並び順で最初に開ける画面（`firstAllowedRoute`。どれも開けなければ権限なしの画面）。
- 未知の URL・ログイン後・権限なしの画面の「利用可能な画面へ戻る」→ 既定入口（`defaultEntryRoute`）。ナビの先頭のチャット（`/chat`）を開ければチャット（RAG と同じ。#791）、開けなければ `/` を経て最初に開ける画面。
- どちらも `Navigate replace` で移し、履歴に `/` や未知の URL を残さない。

### 1.1 A 型（一覧 → 全画面エディタ）

`frontend/src/lib/editor-route.ts` の `useEditorRoute` が URL の検索パラメータ `id` を読み書きする。

| `?id=` | 表示 |
|---|---|
| なし | 一覧（`PagedDataTable`）。上に `ListToolbar`（左に `SearchField`、右に件数。#808）。ページ操作（新規作成・表示を更新）は `PageHeader` |
| `new` | 新規作成のエディタ |
| `<対象の ID>` | その対象のエディタ（プラグインとマーケットプレイスは変更できる項目がないため詳細の閲覧）。一覧に無い ID は「対象が見つかりません」と一覧へ戻る導線を出し、別の対象へ黙って置き換えない |

| 画面 | ID | エディタの節 | 対象の操作（`EntityAction`） |
|---|---|---|---|
| 業務 Agent | Agent ID | 概要（版の履歴。#770）/ 基本情報（`lg:grid-cols-2`）/ スキル（`ListPicker`）。新規は先頭に「テンプレートから始める」（#780） | 公開・有効にする・無効にする（版の履歴の行に「この版に戻す」） |
| スキル | Skill ID | 概要 / 基本情報 / 内部依存。ビルトイン・ファイル・env は読み取り専用の詳細 | 削除（実行時に追加したスキルだけ） |
| MCP 接続 | 接続 ID | 概要 / 接続 / 認証（秘密は `SecretField`）/ ツール（tools/list の取得。結果は `SettingsTestResultPanel`。#814） | 削除（RAG / NL2SQL・宣言・プラグインの接続は削除不可） |
| プラグイン | Plugin ID | `new` は manifest の入力。既存は概要 / 内容（スキル・MCP・resource） | 有効にする・無効にする・アンインストール |
| マーケットプレイス | Marketplace ID | `new` は追加フォーム。既存は配布元 / インストールできるプラグイン（検索付きの一覧。行メニューからインストール。#814） | 更新・削除 |

- 開く・閉じるは履歴に積む。再読込・戻る / 進むで同じ対象が開く。新規作成に成功したら作成した対象へ、エディタで削除したら一覧へ、どちらも `replace` で移る（戻るで空の新規フォームや消えた対象へ戻さない）。
- 「一覧へ戻る」は `PageHeader` の `back`（左上・タイトルの上。読み上げ名は「{一覧}の一覧へ戻る」）。2 階層のパンくずは出さない（#618）。`back` も §2 の離脱ガードの対象。
- 保存・作成・導入・追加の失敗は、欄に結び付くもの（未入力・JSON の形式）は欄の直下、それ以外は `PageBody` の先頭の共有 `SaveErrorBanner` の 1 か所だけに出す（UX 契約 messaging.md §3.3.1。#585）。概要の節より上に置き、Toast には重ねない。testId は `agent-save-error` / `skill-save-error` / `mcp-server-save-error` / `plugin-install-error` / `marketplace-add-error`。
- `PageHeader` の操作は右端の「保存 / 作成」（primary）と、その左の「変更を破棄」（secondary。変更が無いときは disabled。#618 / #808）。対象の操作は同じ `EntityAction[]` を一覧の行では `RowActionMenu`、エディタでは概要の `ObjectActionBar` に渡す。破壊的な操作（削除・アンインストール）は `useConfirm` で確認する。
- 一覧の行は、先頭セルの対象名のボタン（`RowTitleButton`）か行の操作以外の領域のクリックで開く。行の中のボタンは対象名と操作メニューの 2 つだけ。使える項目がない行のメニュー（既定の MCP サーバー）は disabled にする。
- 業務 Agent の有効 / 無効は対象の操作にし、フォームの下書きに含めない（切り替えで一覧を取り直しても、編集中の内容を上書きしない）。新規作成だけは初期状態をフォームで選ぶ。

### 1.2 B 型（一覧 + 詳細）

`frontend/src/components/EntityLayout.tsx` の `AgentSplitPane` が共有 `FixedSplitPane` に `storagePrefix="production-ready-agent.fixedSplitPane"` と Agent の i18n 文言を渡す。詳細側を既定で広くし、xl 未満は縦積み。

| 画面 | `splitId` | 左（一覧） | 右（詳細） | 対象の操作 |
|---|---|---|---|---|
| ツール | `tools-list` | ツール一覧 | schema と監査タグ | なし |

- 行の操作以外の領域のクリックで選び、選択は行の背景と `aria-current` で示す。行の操作は `RowActionMenu`、詳細は同じ定義を `ObjectActionBar` に渡す。
- 実行の詳細の操作は `ObjectActionBar` の 1 か所だけ（#814）。イベントを WebSocket で購読していて接続済みなら、操作は handler の中でその接続のコマンドとして送り、それ以外は REST を呼ぶ。押した操作だけを `loading` にし（WebSocket はコマンドの受付・拒否まで）、ほかは `disabled`。通信の指標（最後の応答確認・操作の受付・再接続の回数など）は「接続の詳細」の `Disclosure`（既定で閉じる）に畳む。

### 1.3 実行履歴の全幅の導線（#875）

- `/runs` は検索・状態の絞り込み・ページングを持つ一覧。見出しの「実行を作成」から `?id=new` を開く。作成フォームを一覧に重ねない。
- 題名のリンクで `?id=<Run ID>` の全幅の詳細を開く。見出しの「一覧へ戻る」で元の検索・状態・ページを保ち、選んだ行へフォーカスを戻す。URL の再読込・戻る / 進むでも同じ対象を開く。
- 詳細は概要と対象の操作の下に「結果」「実行の経過」「監査ログ」の `Tabs` を置く。既定は結果で回答・成果物を先に見せる。ステップ・イベント・購読方式は経過、監査は権限がある利用者だけが開いたときに取得する。承認待ちの案内と判断の操作はタブの外で常に見える。
- 作成後は作った Run の詳細へ移る。URL の ID が一覧に無い場合は説明を出し、別の実行へ置換しない。

### 1.4 承認の全幅の導線（#877）

- `/approvals` は全幅のキュー。既定は「保留中」で、検索・状態・ページを作業状態に残す。題名のリンクから `?id=<Approval ID>` の全幅の詳細を開く。
- 詳細の対象は URL が正本で、全承認から解決する。判断後に保留中のキューから消えても同じ承認を表示し、判断者・日時・状態を確認できる。別の承認へ暗黙に移動しない。
- 目標・ツール・承認理由・申請日時・引数を分けて示す。判断済みには判断記録を出し、日時は JST。関連する実行への導線は実行の閲覧権限がある利用者だけに出す。
- 判断は保留中で権限を持つ利用者だけ。行メニューと詳細の `ObjectActionBar` は同じ定義を使い、確認に対象の目標・ツール・ID を含める。処理中は押した対象だけを loading にし、重複判断を抑止する。確認中に別の操作者が判断した場合は再取得済みの状態を確かめてから送る。
- 戻ると検索・状態・ページ・選択行のフォーカスを保つ。選んだ承認が保留中から消えた場合は一覧の見出しへフォーカスを戻す。対象が存在しない URL は説明し、別の対象へ置換しない。

## 2. 未保存変更の離脱ガード

共有パッケージ `@engchina/production-ready-system-settings` の `useUnsavedChangesGuard` / `useSettingsDraftGuard` を `frontend/src/lib/leave-guard.ts` で Agent の i18n 文言に包んで使う。dirty のときだけ、サイドナビ・内部リンクの移動を確認ダイアログで止め、再読込・タブを閉じる操作を `beforeunload` で止める。dirty は保存済みの基準との比較で判定し、保存に成功したら基準を更新する。

| 画面 | フック | dirty の対象 |
|---|---|---|
| 業務 Agent | `useEditorLeaveGuard` + `useDirtySources` | エディタのフォーム（スキルは集合として比較）。画面内の「一覧へ戻る」（`back`）でも破棄を確認する |
| スキル / MCP 接続 | `useEditorLeaveGuard` | 全画面エディタのフォーム（開いた時点の内容と比較）。画面内の「一覧へ戻る」でも破棄を確認する |
| プラグイン / マーケットプレイス | `useEditorLeaveGuard` | `?id=new` の manifest の入力 / 追加フォームの入力 |
| 自動実行 / 評価セット | `useEditorLeaveGuard` | 全画面エディタのフォーム |
| ツール権限 | `useSettingsLeaveGuard` | 取得した設定との差分（ツール権限の「既定」は未指定として比較） |
| バックアップと復元 | `useSettingsLeaveGuard` | インポート JSON と理由。確認語（`REPLACE`）は対象外で、離脱で解除される |

ブラウザの戻る / 進む（`popstate`）も、data router の root（`main.tsx`）に 1 つだけ置いた共有の `UnsavedChangesBlocker` で確認する（#138 / #586）。A 型のエディタで未保存の編集があるときも、戻る / 進むで `?id=` が変わる前に破棄を確認する。

## 3. 作業状態の保持

`frontend/src/lib/workspace-state.ts` の `useWorkspaceState` が、allowlist（`WORKSPACE_FIELDS`）に登録した field だけを同じタブの `sessionStorage` に残す。namespace は `production-ready-agent.workspace.v1:`、期限は最終保存から 8 時間、1 field の JSON は 20,000 文字まで。既定値と同じ値は保存しない。読めない・型違い・期限切れの値は捨てる。

| 画面 | 保存する field | 戻ったときの検証 |
|---|---|---|
| チャット | 選んだ業務 Agent（`chat.agentId`）、会話（`chat.threadId`）、送る前の下書き（`chat.draft`）、デスクトップの履歴の開閉（`chat.historyOpen`。既定で閉じる） | 会話が無くなっていれば新しい会話にする |
| 実行履歴 | 目標の下書き（`runs.goal`）、選択中の実行（`runs.selectedRunId`）、イベント購読方式（`runs.streamMode`） | 詳細の対象は URL が正本。一覧へ戻ると最後に選んだ行へフォーカスを戻す。対象が無い場合は説明を出す。業務 Agent は実行条件なので保存しない |
| 承認 | 最後に開いた承認（`approvals.selectedId`） | 詳細の対象は URL が正本。一覧へ戻る時の行のフォーカスにだけ使い、判断は確認し直す |
| フィードバック / 品質評価 / 利用状況 | 絞り込み（`feedback.*`）、業務 Agent と表示中の評価（`evaluation.*`）、期間と内訳のタブ（`usage.*`） | 期間・対象で取り直す |
| 監査 | 入力中の絞り込み（`audit.filterForm`）、適用済みの絞り込み（`audit.appliedForm`）、ページ（`audit.page`） | 適用済みの条件とページ（API の offset）で一覧を取り直す。範囲外になったページは最後のページに寄せる。条件を適用し直すと 1 ページ目へ戻す |
| 一覧のページ（#265） | `lists.*`（業務 Agent・実行・承認・ツール・MCP 接続・スキル・プラグイン・マーケットプレイス・評価セット・評価の結果・最近の評価・自動実行・フィードバック・利用状況・API キー） | 一覧 → エディタ → 一覧の移動と再読込で同じページに戻る。行が減って範囲外なら表示だけ末尾のページに寄せる。定期的な再取得ではページを戻さない |
| 一覧の検索・絞り込み（#808） | `listSearch.*`（`SearchField` が確定した検索語）、`listFilter.runs` / `listFilter.approvals`（状態のチップ） | 検索語・絞り込みが変わったら 1 ページ目へ戻す |

- A 型の編集対象は URL の `?id=` が唯一の情報源なので `sessionStorage` に置かない（#87 で置いた `skills.detailId` / `marketplaces.browseId` は #137 で URL へ移した）。サイドナビから開くと一覧に戻る。
- 秘密情報（MCP の OAuth client secret / session ID 等）、確認語、サーバー応答全体、未保存の編集フォームは保存しない。編集フォームは §2 の離脱ガードで守る。
- 確認語と破壊的操作の確認は画面の state にだけ置き、ページを離れると解除される。ページに戻っただけで mutation を送り直さない。
- 実行の目標の一時保存に失敗した場合は警告を出し、離脱ガードを有効にする。
- #215 で backend は共通認証のログインに対応した（[security-rbac.md](security-rbac.md)）。画面をログインに切り替えるときは、logout とユーザーの切り替えで `clearWorkspaceState()` を呼ぶ。

## 4. 検証

Playwright は `desktop`（Desktop Chrome）と `mobile-375`（Pixel 5、375×812、タッチ）の 2 project（#823。NL2SQL と同じ名前）。spec の中で viewport を回しているテスト（題名に「(desktop」「(mobile-375」など）と、画面幅に関係しない spec（vite の設定・フォント・権限カタログ）は `mobile-375` では実行しない（`playwright.config.ts` の `grepInvert` / `testIgnore`）。PR の CI は smoke の spec だけを両方の project で実行し、全件は `e2e-nightly.yml`。

- `frontend/e2e/leave-guard-workspace-state.spec.ts`：desktop / 375px のサイドナビの移動の確認、保存後の解除、未変更時の自由な移動、`beforeunload`、画面内の「一覧へ戻る」、監査の絞り込み / 実行の目標の往復と再読込、URL の対象の再読込と一覧に無い ID の説明、確認語の解除。
- `frontend/e2e/list-search.spec.ts` / `list-loading-paging.spec.ts`：一覧の検索（300ms・IME・0 件の「検索語をクリア」・1 ページ目へ戻す）、読み込み中の経過時間と Skeleton、縦スクロールとページング。
- `frontend/e2e/entity-archetypes.spec.ts`：desktop / 375px の A 型（URL で開く・再読込・戻る / 進む・「一覧へ戻る」・`?id=new`、行のボタンは対象名とメニューだけ、行メニューの Enter / 矢印 / Esc とフォーカスの戻り、破壊的な操作の確認とキャンセル、`ObjectActionBar` からの削除と一覧への復帰）と B 型（ツールの `FixedSplitPane`、desktop は分割 / 375px は縦積み、divider のキー操作と保存 key、行の選択と詳細の更新、行メニューと詳細の操作の一致、承認の確認）。
- `frontend/e2e/runs-layout.spec.ts`：一覧 → 全幅の詳細、結果を先に表示するタブ、戻る / 進むと検索の保持、監査の遅延取得、作成・空・取得エラー・対象なし・権限、desktop / 375px のスクロール。
- `frontend/e2e/approvals-layout.spec.ts`：全幅のキューと詳細、判断の後の対象維持、URL と一覧の条件の復帰、同時更新・重複操作・判断エラー・権限、JST、desktop / 375px。
- `frontend/e2e/agent-settings.spec.ts` / `agent-runtime-flow.spec.ts`：各 A 型画面の作成・編集・削除の主な導線。

## 業種テンプレート（#780）

業務 Agent の新規作成（`/agents?id=new`）の先頭に「テンプレートから始める」を置く。テンプレート（`GET /api/agent-templates`。backend の `app/features/agent/templates.py`）を選ぶと、名前・説明・指示・スキルをフォームに入れる（入力済みなら確認してから置き換える）。テンプレートは業務 Agent を直接作らず、保存はいつもの「作成」で行う。使えない（登録されていない）スキルは外して知らせる。既存の業務 Agent の編集には出さない。

テンプレートを選ぶと「テンプレートの評価ケース（N 件）で評価セットを作る」（既定はオン、品質評価の権限を持つ利用者だけ）を出し、作成した後に評価セットを作る（#810）。作った業務 Agent は `template_id` を持ち、品質評価で評価セットが無ければ空の状態に「テンプレートの評価ケースで作る」を出す。

## 品質評価のフォローアップ（#810）

- **評価ケースに追加**: フィードバックの詳細（本人か管理者の評価が「役に立たなかった」）と実行の詳細（回答が出た実行。品質評価の実行は除く）に、`Disclosure`「評価ケースに追加」を置く（品質評価の権限を持つ利用者だけ）。開いたときに下書き（質問・管理者のコメント・呼んだツール）を取得し、読み込み中は `TimedLoadingState` + `FormSkeleton`、失敗は再試行付きの `ErrorState`。追加先の評価セットを選び（同じ質問を持たない最近の評価セットが既定。無ければ「新しい評価セットを作る」）、期待する回答の要点は必須。同じ質問を持つ評価セットを選ぶと欄の下に知らせる。成功は Toast（「評価セットを開く」）、失敗は操作の行の `FormStatus`。
- **評価する版**: 品質評価の業務 Agent の選択の横と、評価セットのエディタに「評価する版」（`SelectField`、`width="md"`）。既定は公開していない変更があれば下書き、なければ公開中の版。実行の意思なので作業状態には残さない。最近の評価の一覧に「版」の列、評価概要の説明に版、前回との比較の文に比べた版と日時を出す。
