# SQL 生成のチャット

「AI 活用」の先頭の「チャット」（`/chat`）では、業務プロファイルを選び、自然言語の質問で SQL を生成して実行する（SQL 生成の画面と同じ。#1176）。続けて「多い順にして」「先月だけに絞って」のような条件を送ると、前の質問と生成 SQL を踏まえて完全な SQL を再生成する。

画面の構成は RAG のチャットにそろえる（#889 / #890 / #891）。

- ページ上部のカードに「業務プロファイル」の欄だけを置く。RAG の「検索・回答プロファイル」と同じ `SearchableSelectField`（閉じたボタンは選んだ値と開閉の印だけで虫眼鏡は開いた検索欄だけ（#1771）、必須、全幅、候補の右端に「表・ビュー N 件」）で、候補はサーバー側で検索する。読み込み中はラベルと欄の形の Skeleton と経過時間、使える業務プロファイルが無いときはカードの中の空の状態だけを出し、会話の欄は出さない。
- 会話は既定で全幅。会話の欄の上端の行の左端に、アイコンだけの「会話の履歴」の開閉ボタン（`PanelLeftOpen` / `PanelLeftClose`）を置き、デスクトップ（lg 以上）は左のパネル、狭い画面は `SideSheet` を開く。デスクトップの開閉は作業状態に残す。その右に今の会話の名前（会話を選んでいるときだけ）、右端に「新しい会話」。
- 「生成方法」は入力欄の直上の行に「ラベル・選択・説明」で置く（RAG のチャットの「回答するモデル」と同じ位置）。
- 送信は Enter、改行は Shift+Enter。IME の変換確定では送信しない。生成中のボタンは停止になり、入力行の高さを変える補足メッセージは追加しない。
- 送った質問はジョブの投入の応答を待たずに会話の欄の末尾へ出し、入力欄を空にする（3 製品で同じ楽観的な表示。platform の UX 契約 messaging.md §11、#907）。投入できたら同じ位置のジョブ（`client_job_id`）の表示に置き換える。投入できなかったときは質問を残し、その直下に原因と「再送信」を出す（入力欄には戻さない）。

## 生成と実行（#1176）

利用者の方針「NL2SQL のチャットの本質は SQL の生成と SQL の実行で、SQL 生成の画面と同じ」に合わせ、送信のジョブは SQL 生成の画面と同じく、生成 → 安全性の確認 → **実行** → 結果の整形を 1 つのジョブで行う（#1154 では送信は生成だけで、実行は「実行」を押したときだけだった）。

- 実行の規則は SQL 生成の画面の自動実行と同じ: 安全検査（SELECT だけ。DML・DDL などは `SQL_BLOCKED` で遮断し、ジョブは失敗。実行しない。SQL 生成の画面にも DML の確認の流れは無い。Select AI / Select AI Agent の SQL は省略する。下の「生成の方式ごとの安全確認」）、業務プロファイルの範囲、権限（`nl2sql.sql.execute`）、DeepSec・接続の分離（#904。worker の `actor_scope`）、上限、監査（実行履歴）。SELECT だけを実行するので確認語は使わない（確認語は管理 SQL の書き込み・削除だけ）。
- 実行の権限が無い利用者（`menu.chat` だけ）の送信は、拒否せず生成だけにする（route が `generation_only` にする）。バッジは「安全検査済み・未実行」、吹き出しに理由（「SQL を実行するには…」）。
- 実行の失敗（Oracle のエラー）はジョブを失敗にしない。生成した SQL を残し、実行の段階を失敗、吹き出しに失敗の `Banner`（1 文目は SQL 生成のジョブと同じ利用者向けの文、ORA は「詳細」）と「もう一度実行」を出す。
- 処理の段階（`ChatProgress`）は、ジョブの全段階（開始待ち・準備・生成・安全性の確認・実行・結果の整形）を出し、終端まで今の段階の行が必ずある（#1176）。経過時間は送信から数え続ける。
  - 段階は backend がジョブの処理の段階のイベント（3 製品共通の契約。`pr_backend_core.chat_progress`。ジョブの `progress_events`）として記録する（#1359）。記録の位置は `service.py` の `_record_job_progress_*` だけ: 作成で開始待ちと 5 段階を待機中で出して開始待ちを実行中（ジョブの作成時刻から）、worker の開始で開始待ちを完了・準備を実行中、`_transition_job_steps` で段階の終了（完了・失敗・未実行）と次の開始、完了は結果の整形を完了にして終端（SQL の遮断・実行の失敗は `failed`）、例外の失敗は実行中の段階を失敗、停止（`JOB_CANCELLED`）は実行中の段階に停止の印（`params.stopped`）を付けて未実行にして終端 `cancelled`。worker が始める前の失敗・停止は開始待ちが失敗・停止になる。最後の段階（結果の整形）は結果の保存まで実行中のまま（終端と同じ保存で記録する）。
  - 補足は `params` に値だけを入れ、文言は画面が付ける: 生成の段階に生成方法（`engine`）、安全性の確認の完了に参照した表（先頭 3 件の `tables` と `table_count`）、実行の完了に取得した行数（`rows`）。Select AI / Select AI Agent の SQL で安全性の確認を省略したときは、その段階を未実行（`skipped`）にして省略の印（`safety_check_skipped`）と参照した表を付け、画面は「Select AI が生成した SELECT 文のため省略しました」を出す（#1523）。
  - 試行（`attempt`）はジョブの `attempt` より 1 小さい（最初の実行が 0）。lease の切れたジョブを引き継いだ実行（2 回目以降の claim）は新しい試行で段階を出し直し、画面は段階の一覧を作り直す（#1358）。番号（`seq`）は続ける。
  - 画面（`SqlChatPage.tsx` の `SqlChatTurn`）は共有の `useChatProgressStream` で、処理中は `GET /api/nl2sql/jobs/{job_id}/progress/stream`（SSE）を受け取り、使えなければ `GET …/progress?since=` の polling に縮退する。会話の取り直し（`GET /api/nl2sql/chats/{id}`。結果の取得）に入っている `progress_events` も積む。投入の応答（`POST /api/nl2sql/jobs`）にも作成時のイベントが入り、会話を取り直す前から開始待ちの段階を出す（取り直しで段階の行が増えて会話の欄の末尾がずれない）。`features/nl2sql/chatProgress.ts` は段階の定義（名前の i18n と補足）だけを持つ。
  - SQL 生成の画面の工程の表示（`WorkflowProgressStrip`）はジョブの `steps` のまま。
- #1176 より前のチャットのターン（`generation_only` だけを持つ）は、今までどおり会話として開け、続けられる（実行は「未実行」のまま。「実行」で実行できる）。

## 生成した SQL の実行の表示（#1154 / #1176）

- 回答の吹き出しの SQL の行の右に「もう一度実行」（secondary・`Play`。実行中はラベルを変えずアイコンがスピナー）。生成だけのターン（権限が無い・#1176 より前）は「実行」。バッジは「安全検査済み・実行済み」。安全検査を通っていない SQL（DML など）には出さない。実行の権限（`nl2sql.sql.execute`。`menu.query` / `menu.direct_sql` が含む。`menu.chat` は含まない）が無い利用者には出さず、理由を吹き出しの中に出す。SELECT だけを実行するので確認語は使わない（確認語は管理 SQL の書き込み・削除だけ）。
- 結果は同じ吹き出しの SQL の下に、3 製品で共通の `ResultTable`（旧名 `ChatResultTable`。`@production-ready/ui`）で出す: 1 行目に要約（「12 行・5 列・0.8 秒」）、先頭 50 行のプレビュー（表頭固定・表の中で縦横スクロール・md 未満 5 行・md 以上 8 行）、「すべての行を見る」（広い side sheet・10 / 50 / 100 行/ページ）、「CSV をダウンロード」（取得した行だけ）。NULL は「NULL」、数値の列は右寄せ（SQL 生成・SELECT SQL の実行の画面の結果の表も同じ `ResultCell`）。
- 送信のジョブの中で実行した結果の行は、ジョブが終わったら画面が 1 回だけ受け取る（受け取る間は結果の位置に「実行結果を読み込んでいます」と表の形の Skeleton）。「もう一度実行」の実行中は結果の位置に経過時間（`ProcessingIndicator`。スピナーは「実行」のボタンだけ）。実行の失敗は danger の `Banner` で、1 文目は SQL 生成のジョブと同じ利用者向けの文、ORA のコード・元の文は「詳細」。要求の失敗（権限・通信）は `ApiErrorBanner`。
- 上限: 1 回の取得は `NL2SQL_CHAT_RESULT_MAX_ROWS`（既定 1,000 行）、セルの文字数は `NL2SQL_CHAT_RESULT_MAX_CELL_CHARS`（既定 2,000 文字）、応答の行の大きさは `NL2SQL_CHAT_RESULT_MAX_BYTES`（既定 2,000,000 バイト）、時間は SQL 生成のジョブと同じ Oracle の call timeout（`NL2SQL_ORACLE_CALL_TIMEOUT_SECONDS`）。総件数の COUNT は別に取らない（SQL 生成の画面と同じく「さらに行があります」）。打ち切ったら要約と案内で明示し、すべての行は「SELECT SQL を実行」（SQL を履歴の state で渡し、URL に載せない）で取得件数上限を指定して実行する。

### API と保存

- 送信のジョブ（`POST /api/nl2sql/jobs` の `chat: true`）の実行の段階は、SQL 生成のジョブと同じ `_run_job` の中で、チャットの上限で実行する（`Nl2SqlService._run_chat_sql`。「もう一度実行」と同じ関数）。
- 結果の行の受け渡し（#1176）: 行はターンのジョブ（会話）に残さず、`NL2SQL_STATE_DOCUMENTS` の `chat_results`（ジョブ ID ごと。ジョブの結果と同じトランザクションで保存）に、画面が受け取るまでの間だけ置く。画面は `POST /api/nl2sql/jobs/{job_id}/execution-result`（権限 `nl2sql.sql.execute`・本人の会話・業務プロファイルの利用権限）で 1 回だけ受け取り、backend は受け取ったら消す。期限（`NL2SQL_CHAT_RESULT_RETENTION_SECONDS`、既定 600 秒）を過ぎた・受け取り済みの結果は 404（画面は要約と「もう一度実行」）。受け取られなかった行は、次のチャットの実行のときに期限を過ぎたものをまとめて消す。「もう一度実行」は応答で行を返し、受け取られていない行を消す。
- `POST /api/nl2sql/jobs/{job_id}/execute`（権限 `nl2sql.sql.execute`。「もう一度実行」）。SQL は画面から受け取らず、ターンのジョブに保存した生成 SQL を使う。本人の会話のターン（管理の権限でも他の利用者の会話は不可）、ターンの業務プロファイルの利用権限を確かめる。
- 実行は SQL 生成のジョブの実行の段階と同じ経路（`Nl2SqlService._execute_generated_sql`）: 業務プロファイルの範囲（`_resolve_allowed_objects`）→ 安全検査（`analyze_sql`。生成の後に範囲が変わっていれば `SQL_BLOCKED`）→ `execute_sql`（`OracleNl2SqlAdapter.user_data_connection(read_only=True)`）。actor は要求の利用者を `actor_scope` で明示し、system_admin と非 system_admin で別の接続 pool、DeepSec 有効時の非 system_admin は DATA USER の pool（借りるたびに利用者の context を設定・消去。#904）。
- 監査: 実行ごとに実行履歴（`HistoryItem`。`generation_only=false`・`session_id` は会話 ID・件数と列）を残し、構造化ログ `nl2sql_chat_sql_executed`（SQL の本文・行の値は出さない）を出す。
- 結果の行は会話に保存しない。ターンのジョブに要約（`last_execution`: 状態・時刻・行数・列数・打ち切り・実行履歴の ID・受け取りの期限）だけを残し、会話を開き直したら「前回の実行（…）: 12 行・5 列。…」と「もう一度実行」を出す。理由: SQL 生成のジョブの永続の記録である実行履歴も件数と列だけで、会話は長く残るため業務データの行を会話に残さない（後で DeepSec・業務プロファイルの権限が変わっても古い行を見せない）。画面の中では結果をメモリ（TanStack Query のキャッシュ）にだけ持つ。

生成方法は SQL 生成画面の実行エンジンと同じ Select AI / Select AI Agent / OCI Enterprise AI（#890）。Select AI Agent は業務プロファイルの Agent の資産（SQL ツール・Agent・Task・Team）を使い、SQL ツールは `SHOWSQL` で SQL を作るだけで実行しない（`enable_human_tool` も無効）。返った SQL は Select AI と同じく安全検査を省略し（#1523。下の「生成の方式ごとの安全確認」）、SQL 生成の画面と同じ backend の経路で実行する（#1176）。Agent の資産が未同期・Oracle の接続が無いなど前提が整っていないときは、SQL 生成画面と同じくそのターンが失敗し、理由を会話の中に出す。従来の SQL 生成画面と MCP のジョブは既定の生成・実行を維持する。

### 生成の方式ごとの安全確認（#1523）

ユーザーの決定（2026-10-12）: Select AI と Select AI Agent が生成する SQL は既定で SELECT 文なので、安全確認をしない。LLM（OCI Enterprise AI。`enterprise_ai_direct`）で生成するときだけ安全確認をする。チャット・SQL 生成の画面・MCP（`nl2sql_query`）・SQL 生成評価で同じ（`Nl2SqlService.analyze_sql` の `engine`）。

| 生成の方式 | 安全確認 | ジョブの工程 `safety_check` |
|---|---|---|
| Select AI / Select AI Agent | しない（SELECT だけか・危険な関数・bind の placeholder・未承認の関数・他製品のシステムの表・業務プロファイルの表と列の範囲・LLM の解析の補強のすべて） | 「省略」（`skipped`。`SafetyReport.safety_checked=false`） |
| OCI Enterprise AI（`enterprise_ai_direct`） | 従来どおりすべて | 完了 / 失敗 |
| 生成の方式が無い経路（利用者が編集・入力した SQL の実行、SQL の解析の画面、SQL の論理構造からの再生成） | 従来どおりすべて（画面から受け取った SQL は、誰が作ったかを信頼できない） | — |

- **制約: 業務プロファイルの列の制限（許可した列）は、Select AI / Select AI Agent では効かない。** Select AI の profile の `object_list` は表の単位で、列の制限を表せない。業務プロファイルの表の範囲も、Select AI に渡す `object_list` で生成の対象を絞るだけで、生成した SQL を照合しない。列・行の単位で業務データを守るときは、DeepSec の Data Grant（非 system_admin の実行は DeepSec の context 付きの接続）か、OCI Enterprise AI の生成方法を使う。
- 変えないもの: 実行の接続（読み取り専用。system_admin と非 system_admin で別の接続。#904）、行数の制限、空の条件の質問の遮断（`_apply_empty_filter_generation_guard`。質問の側の確認）、実行履歴の監査。
- チャットの「もう一度実行」は、ターンのジョブに保存した生成の方式で判定する（画面から方式を受け取らない）。
- MCP（`nl2sql_query`）の応答の `safety_check` は `checked` / `skipped`。SQL 生成評価の Excel の「SELECTのみ」は、省略したとき「省略」。

## API と永続化

- `POST /api/nl2sql/jobs`: `chat: true`（#1176。それより前のチャットは `generation_only: true`）と質問・業務プロファイル・生成方法を渡す。実行の権限が無い利用者は、route が `generation_only: true`（生成だけ）にする。継続時は直前の `previous_job_id` を渡す。クライアントから会話本文や SQL を信頼して受け取らない。
- オントロジー: チャットは `use_ontology_context: true`（SQL の生成の prompt に業務プロファイルの公開版のオントロジーの文脈を入れる）と `include_ontology_grounding: false`（生成後の接地確認をしない）を送る。チャットの画面は接地確認のグラフを表示しないため、結果の整形の段階でグラフを読まない（解釈の artifact `interpretation` も作らない。段階のログ `nl2sql_job_stage_step_finished` の `format_results` / `ontology_graph` は `skipped: true`・`skip_reason: "grounding_not_requested"`）。経路は SQL 生成と同じ `_run_job` で、要求の項目で接地確認を省くだけ（#1172）。`include_ontology_grounding` の未指定（SQL 生成の画面・MCP・API の既定）は `use_ontology_context` に従い、今までどおり公開版があれば接地確認する。接地確認は公開版の確定が前提のため、`use_ontology_context: false` なら指定にかかわらず行わない。
- `GET /api/nl2sql/chats`: 本人の会話の先頭ジョブを、現在利用可能な業務プロファイルで絞って 50 件ずつ返す。続きは `next_cursor`。
- `GET /api/nl2sql/chats/{conversation_id}`: 本人の会話と各ターンの永続ジョブを返す。ID は先頭のジョブ ID。
- `PATCH /api/nl2sql/chats/{conversation_id}`（`{"title"}`。前後の空白を除いて 1〜80 文字）: 本人の会話の名前を変える（#1817）。名前は先頭のジョブの `chat_title` に置く（無ければ先頭の質問）。先頭のジョブの生成中は 409。
- `DELETE /api/nl2sql/chats/{conversation_id}`: 本人の会話を消す（#1817）。ジョブは監査・フィードバック・実行履歴の元なので消さず、会話の全ジョブに `chat_deleted` の印を付けて会話の一覧・詳細・続きから外す。SQL の生成中のターンがあれば 409。他の利用者の会話は 403（管理者も変えない）。
- 停止は既存の `POST /api/nl2sql/jobs/{job_id}/cancel`。既存の worker・lease・fence・再起動時の復旧を使う。
- 処理の段階（#1359）: `GET /api/nl2sql/jobs/{job_id}/progress?since=<seq>`（polling。`ChatProgressPage`）と `GET /api/nl2sql/jobs/{job_id}/progress/stream?since=<seq>`（SSE。`Last-Event-ID` も受ける。`id:` に `seq`、イベントの無い間は 10 秒ごとに heartbeat、終端を送ったら閉じ、終わったジョブを続きから求めたら 204）。権限・所有者・業務プロファイルの判定は `GET /api/nl2sql/jobs/{job_id}` と同じ。SSE はジョブの文書を `run_sync_io` で 1 秒ごとに読み直すので、worker が別のプロセスでも届く。

Oracle の既存 `NL2SQL_STATE_DOCUMENTS` の `jobs` を正本にする。新しい表や製品間コード依存は増やさない。ジョブに会話 ID・親ジョブと先頭マーカーを保存する。`sessionStorage` はユーザー・DB context ごとの選択 ID と未送信草稿だけに使い、会話の本文は保存しない。

## 文脈とアクセス

`menu.chat` は生成・業務プロファイル参照・スキーマ参照を含む。SQL 実行（`nl2sql.sql.execute`）とプロファイル管理は含まない（`menu.chat` だけの利用者のチャットは生成だけ）。会話の参照・継続は作成者に限り、現在のプロファイル利用権限を照合する。別のプロファイルに変えたら新しい会話にする。前のターンが未完了、会話が既に更新済み、存在しない親ジョブの場合は継続を拒否する。

1 会話は 50 往復まで。モデルに渡す前の往復（質問・生成した SQL・状態）は 3 製品共通の予算（`PLATFORM_CHAT_HISTORY_*`。既定 8 往復・1 往復 4,000 文字・合計 16,000 文字。新しい往復から詰め、入らない古い往復を落とし、長い SQL は切り詰める。#1818）で決め、SQL の生成には `<conversation_history untrusted="true">` の未信頼のデータとして渡す（回答前の確認は JSON の `conversation_history`）。質問は 10,000 文字まで。結果行・credential・権限設定は文脈に入れない。過去の発言は参照データとして扱い、現在のスキーマ・対象範囲・安全規則は既存の生成パイプラインを正本にする。

## 検証

`backend/tests/test_sql_chat_job_execution.py` が送信のジョブの中の実行（#1176: 同じジョブで実行・行は 1 回だけ受け取る・期限と掃除・DML の遮断・実行の失敗・権限が無いと生成だけ・#1176 より前のターンの継続）を、`test_nl2sql_db_roundtrips.py` の `test_business_sql_connection_follows_job_actor`（`chat_turn`）がその接続の種類を、`backend/tests/test_nl2sql_job_progress.py` が処理の段階のイベントの記録の位置（作成・開始・各段階・完了・失敗・停止・引き継いだ試行・終端まで今の段階があること）と配信（polling・SSE の続き・204・権限）を、`frontend/tests/sql-chat-progress.test.ts` が段階の名前と補足を確認する。`backend/tests/test_sql_chat_execute.py`・`test_nl2sql_db_roundtrips.py` の `test_chat_execution_connection_follows_requesting_actor` がチャットの実行（同じ経路・権限・上限・失敗・接続の種類・要約の保存）を、`frontend/tests/e2e/sql-chat-execution.spec.ts` が実行の画面（実行中・成功・0 行・打ち切り・失敗・DML・権限・開き直し）を確認する。`backend/tests/test_sql_chat.py` が生成のみ（Select AI Agent を含む）、文脈、別 worker での復元、所有者・プロファイル、古い会話、元の実行動作と安全検査を確認する。`frontend/tests/e2e/sql-chat.spec.ts` が desktop / 375px の多輪送信・履歴開閉・復元・停止・失敗・生成だけの権限・生成方法の選択と送る値・業務プロファイルの欄の形を確認する。実 Oracle / OCI 接続の動作確認と、決定論スタブによる CI の確認を区別する。
