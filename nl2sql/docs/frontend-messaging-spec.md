# フロントエンド メッセージ機構（NL2SQL の差分）

> 通知・成功 / エラー表示・フォーム検証・確認ダイアログ・空 / 読込 / エラー状態の共通規約は platform の [UX 契約 messaging.md](../../platform/docs/ux-contracts/messaging.md) が正本。
> 本書には NL2SQL 固有の通知の例・実装の場所・移行記録だけを書く。節番号は共通規約にそろえる。

---

## 3.1 Toast（NL2SQL 固有）

- **合成データ生成の終了通知**: 終端遷移を観測したときだけ、終了状態と対象表名を共通 Toast に表示する。結果画面へ遷移する action は付けず、4 秒で自動消去し、閉じるボタンも利用できる。ページ上部に終了履歴の Banner を表示せず、初回取得・再読込・同一状態の再取得では通知を再送しない。履歴と詳細はデータ管理画面で確認する。
- `danger` の Toast は共有 UI の `toast.error()` をそのまま使う（既定で利用者が閉じるまで残る。自動消滅の時間を上書きしない）。以前の `src/lib/toast.ts` の `toastError()` は、共有 UI の既定が UX 契約に合ったため削除した（#351）。`<Toaster/>` は `src/main.tsx` で一度だけ描画する。

## 3.3 FormStatus（NL2SQL 固有）

### SQL 実行エラーの具体的な対応例

- `DbAdminErrorNotice` は概要・原因候補・次の対応に加え、既知のエラーにはコピー可能な構文例を同じ結果領域に表示する。分類は statement の `error_code` を優先し、従来の Oracle メッセージは `ORA-xxxxx` を補助的に使う。日本語メッセージの部分一致で分類しない。
- `DB_ADMIN_COMMENT_SQL_POLICY_VIOLATION` は `COMMENT ON TABLE`（ビューも同じ構文）/ `COLUMN` / `MATERIALIZED VIEW`、`DB_ADMIN_ANNOTATION_SQL_POLICY_VIOLATION` と `ORA-11548` は表・列の `ANNOTATIONS` 例を共通の `dbAdminErrorRecovery` で管理する。未知のエラーには修正 SQL を推測表示しない。
- 構文例と対象に適用済みの修正 SQL を区別する。例の所有者・対象・列・説明文は利用者が実際の値へ置換する必要があることを明記し、コピーで入力変更・自動実行・確認ゲートの省略を行わない。
- コピーには共通 `Button` の `secondary` / `sm` を使い、例の名前を含む `aria-label` を付ける。SQL はモバイル幅で折り返して表示し、コピー成功・失敗は既存 Toast で通知する。

### 3.3.1 業務プロファイルのエディタの保存の失敗（#585）

共通規約は [UX 契約 messaging.md §3.3.1](../../platform/docs/ux-contracts/messaging.md#331-全画面のエディタの保存の失敗585)。業務プロファイルのエディタ（`/profiles?profile=`）は、保存ボタンがヘッダーではなくフォームの中（実行確認語の欄と並ぶ）にあるため、欄に結び付かない保存（PATCH / POST）の失敗は保存ボタンの直下の `FormStatus`（`profile-save-error`）の 1 か所だけに出す。`toast.error` は出さない。名前の重複（409）は名前の欄の直下に出してフォーカスし、Oracle 同期の投入の失敗は今までどおりその下の保存結果の領域（`ProfileSaveResultRegion`）に出す。

## 3.7 処理中・経過時間（NL2SQL 固有）

- request budget は `src/lib/requestPolicy.ts` を正本とする。`interactive-list = 60 秒`、`interactive-detail = 30 秒`、`job-control = 5 秒`。それ以上の処理は durable job とする。

## 11. AI要件確認（SQL 生成の画面とチャットで共通。NL2SQL 固有。#1439 / #1442 / #1715）

共通の送信の規則は [UX 契約 messaging.md §11](../../platform/docs/ux-contracts/messaging.md#11-チャットの送信楽観的な表示907)。AI要件確認は **SQL 生成の画面の AI要件確認を基準**にし、チャットも同じ API・同じ hook（`useRequirementSession`・`useRequirementCheck`・`useRequirementProceed`）・同じ部品（`components/RequirementClarificationView.tsx`）で行う（#1715）。画面ごとに違うのは次の 2 つだけ。

| | SQL 生成の画面 | チャット |
|---|---|---|
| 入口 | 「AI要件確認」のボタンで開く。業務プロファイルの推薦と確定の後に session を作る（「ALL」は推薦から選ぶ） | 送信のときに自動で始める（`POST /query-sessions` の `chat: true`・`previous_job_id`。会話の前の質問と SQL を検査に渡す）。送信のボタンは同じ位置で「停止」になり、押すと確認を中止する |
| 確定した要件の行き先 | 質問の欄へ反映（「要件を SQL の生成に反映」） | SQL の生成（「この要件で SQL を生成して実行」。実行の権限が無ければ「この要件で SQL を生成」） |

- **最低限だけを確かめる（#1715）**: 確認する（不足 / あいまいにする）のは、それが無いと SQL を 1 つに決められないものだけ（対象の表が特定できない・主な指標や列が複数の候補で決まらない・期間の基準の日付の列が複数ありうる・年度の開始月が分からない「今年度」・質問に書かれた条件の列や値が決まらない・同じ表の間の承認済みの関係が複数ある・結合の条件の無い表）。質問に書かれていない絞り込み・期間・集計・並び順・件数の上限・結合・サブクエリは「指定なし（使わない）」として確認しない（件数の上限は画面の既定の上限）。実行日から 1 つに決まる期間（「先月」など）と、承認済みの関係で 1 つに決まる結合の経路も聞かない。
- **確認が要らない**（最低限が決まり、聞くことが無い）: 利用者に聞かずに反映（`/apply`）して次へ進む（SQL 生成の画面は質問の欄へ反映して閉じ、「AI要件確認の要件を SQL の生成に渡します」を残す。チャットは `requirement_session_id` を付けたジョブで SQL を作り、回答に「AI要件確認で確かめた質問」を出す）。
- **SQL の文の質問は確かめない（#1715）**: 質問が SQL の文（SELECT / WITH で始まる 1 文。コメント・前後の空白・末尾の `;` を許す。文字列の外に日本語があるものは除く）なら、LLM を呼ばない。判定は `requirementCheck.ts` の `requirementCheckSkipReason`（backend の MCP は `sql_input.py`。同じ規則）の 1 か所。SQL 生成の画面は「AI要件確認」を押せなくし、ボタンの下に「質問が SQL の文なので、AI要件確認は要りません。そのまま SQL を生成できます。」を出す。チャットは確かめずに SQL を作る。
- **確認が要る**: 1 問ずつ（選択肢・複数選択・自由入力）聞く。SQL の要件（物理名の欄）・まとめ（書き直した質問・SQL の骨組み）は両方の画面で同じ表示。答えるまでチャットは次の質問を送らない。
- **確認を中止**: 「確認を中止して閉じる」。サーバーの session を中止する（チャットは送った質問を入力欄に戻す。入力欄に書き始めた文字があれば上書きしない。ジョブは作らない）。
- **検査の失敗**: 両方の画面で同じ `RequirementCheckFailure`（`ApiErrorBanner`）に、backend の 1 行の理由（タイムアウト・呼び出しの上限 429・サービスの 5xx・要求の誤り 400・応答が出力の上限で途中で終わった・応答の形式の誤り・接続の失敗・設定の誤り）と、開いた「詳細」（失敗の種類・OCI Enterprise AI の HTTP ステータス・モデル・所要時間・試行回数・待ち時間の上限・出力の上限・応答の理由・OCI のリクエスト ID・エラーコード・リクエストID）を出し、「もう一度確認」「確認せずに SQL を生成」「確認を中止して閉じる」を並べる。secret・prompt の全文は出さない。OCI Enterprise AI が設定されていない（`REQUIREMENT_AI_NOT_CONFIGURED`）ときだけは、確認せずにそのまま SQL を作る（チャット）。
- チャットの送った質問と session の ID は作業状態（`requirementCheck`）に残し、再読込・画面の移動の後も同じ確認を続ける（session を作り直さない・中止しない）。SQL 生成の画面は、パネルを閉じずに画面を離れたら session を中止する（作業状態に残すのはチャットの送信だけ）。
- チャットで作ったターンの吹き出しは利用者の質問のまま、生成は確かめた質問の文で行い、回答に「AI要件確認で確かめた質問」（畳んだ詳細）を SQL の前に出す。

## 4.3 API problem 契約（NL2SQL 固有）

- problem の factory は `backend/app/api/problems.py`。`type` は `urn:nl2sql:problem:<code>`。

---

## 付録 A: 共通モジュール（実装済み）

```
src/components/ui/feedback-tone.ts   FeedbackTone(4 トーン)+ アイコン/色/role マップ
@production-ready/ui        <Toaster/> / toast / <ConfirmProvider> / useConfirm() / <SelectField/>
src/components/ui/banner.tsx         <Banner severity title? action? onDismiss? />
src/components/ui/field-error.tsx    <FieldError id message />
src/components/ui/form-status.tsx    <FormStatus tone message />
src/components/StateViews.tsx        LoadingState / ErrorState / EmptyState
src/main.tsx                         <ConfirmProvider labels navigationKey> + <Toaster/> を配線済み
```

- 4 トーンの視覚定義は **`feedback-tone.ts` を単一の正**とする(各チャネルはこれを参照)。
- これらは shadcn/ui 流儀(token 駆動・variant は `class-variance-authority`)で実装する。

## 付録 B: 移行状況

- ✅ モデル設定のモデル削除に `useConfirm()` の確認ゲートを導入(`ModelSettingsClient`)。
- ✅ `ModelSettingsClient` のローカル `Notice` を撤廃し、通知/エラー/検証を共通 `<Banner>` へ統合。
- ✅ `OciSettingsClient` / `DatabaseSettingsClient` / `UploadStorageSettingsClient` の保存結果インライン表示を `<FormStatus>` へ統一。
- ✅ `SearchClient`(エラー・ガードレール警告)・`DocumentWorkspace`(重複/エラー/索引完了)の手書き tone ボックスを `<Banner>` へ統一。
- ✅ spec 準拠監査(全画面)を実施し、手書き tone ボックスを統一:
  - `<Banner>` 化: ログインエラー、文書抽出の警告、評価の閾値失敗/検証/実行エラー。
  - `<FieldError>` 化: `SelectField` 本体および OCI/アップロード保存先の各フィールドエラー(`aria-describedby` 連携を維持)。
  - `<FormStatus>` 化: コピー失敗などのアクション結果。
- ✅ `problem` 契約と JSON Pointer field binding を導入。ユーザーID/ロールコード競合は FieldError へ結び付け、
  入力変更時の局所クリア、最初のエラーへのフォーカス、FormStatus との重複抑止を実装。
- ✅ 業務 TSX の直接 `role="alert"` を撤廃。Job/Workflow はパネル全体を live region にせず、標準 Banner の
  エラー要約だけを通知する。設定診断は標準 Banner + Badge に統合し、raw backend/Oracle error を表示しない。
- ✅ `tests/messaging-source-contract.test.ts` で直接 alert と手書き danger surface の増加を検知する。
- ⏳ 今後の新規画面・機能は本 spec の 6 チャネルに従う。以下は **意図的に対象外**(spec の例外):
  - **状態可視化**(`StatusBadge` / `StatusPill` / `FlowStepper` のステップ表示)— 通知ではなくデータ表示。
  - **OCI 構成テストの結果パネル**(タイトル + 詳細リスト + モードチップの複合)— 専用パネルとして維持。
  - **中立の `role="status"` 軽量テキスト**(SQL の生成と実行のキャンセル通知など)。

### Markdown オントロジーの公開（Issue #491）

- 初回取得は `LoadingState`、空結果は `EmptyState`、再取得失敗は再試行を伴う `ErrorState`。以前の内容を残す場合は前回取得の情報であることを明記する。
- バージョンは読み取り DTO の `display_version` に由来する `vN` を表示する。解決できない場合は「版番号を取得できません」と取得可能な日時を示す。ID や一覧順から推測しない。
- 一般状態・操作は日本語のみ。Ontology の概念・契約には専門用語の英日併記を維持する。
- 定義検証と実データ検証はそれぞれの実施状況・日時・範囲・阻害事項を表示する。原始 JSON は初期状態で閉じた技術詳細へ置く。
- 受入テストの JSON エラーは入力に関連付ける。Markdown 解析の未対応行・競合・阻害事項を公開前に示す。移行と公開の確認は共通ダイアログで行い、公開の対象 Profile と実際の版を明記する。
- 公開成功はトースト、公開前チェックと取得失敗は対応する操作の近くに表示する。Markdown の編集で準備済み確認を失効させ、草稿は保持する。
- 公開成功後の再取得は本文が同じでも新しい草稿 ETag を受理する。公開 snapshot と草稿 revision の ID が異なる場合も、次の編集を保存可能にし、未保存入力は保持する。
- 応答喪失時は保存した冪等キーで公開結果を照会し、自動再実行しない。構築結果・公開能力の独立パネルは使用しない。

### 合成データの事前確認（Issue #510）

新規生成の完了は「準備が完了しました（未適用）」と通知する。確認用データの読込は結果領域内で示し、表切替のたびに成功 Toast を積み重ねない。適用・破棄は画面内で独立した状態を表示し、適用には確認語と共通 ConfirmDialog を使う。適用エラーは確認領域の FormStatus に表示して確認語を解除する。詳細は [合成データの事前確認と適用](./synthetic-data-preview.md) を参照。
