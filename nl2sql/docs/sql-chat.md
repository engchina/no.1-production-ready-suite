# SQL 生成のチャット

「AI 活用」の先頭の「チャット」（`/chat`）では、業務プロファイルを選び、自然言語の質問で SQL を生成する。続けて「多い順にして」「先月だけに絞って」のような条件を送ると、前の質問と生成 SQL を踏まえて完全な SQL を再生成する。

画面の構成は RAG のチャットにそろえる（#889 / #890 / #891）。

- ページ上部のカードに「業務プロファイル」の欄だけを置く。RAG の「検索・回答プロファイル」と同じ `SearchableSelectField`（先頭に検索のアイコン、必須、全幅、候補の右端に「表・ビュー N 件」）で、候補はサーバー側で検索する。読み込み中はラベルと欄の形の Skeleton と経過時間、使える業務プロファイルが無いときはカードの中の空の状態だけを出し、会話の欄は出さない。
- 会話は既定で全幅。会話の欄の上端の行の左端に、アイコンだけの「会話の履歴」の開閉ボタン（`PanelLeftOpen` / `PanelLeftClose`）を置き、デスクトップ（lg 以上）は左のパネル、狭い画面は `SideSheet` を開く。デスクトップの開閉は作業状態に残す。その右に今の会話の名前（会話を選んでいるときだけ）、右端に「新しい会話」。
- 「生成方法」は入力欄の直上の行に「ラベル・選択・説明」で置く（RAG のチャットの「回答するモデル」と同じ位置）。
- 送信は Enter、改行は Shift+Enter。IME の変換確定では送信しない。生成中のボタンは停止になり、入力行の高さを変える補足メッセージは追加しない。
- 送った質問はジョブの投入の応答を待たずに会話の欄の末尾へ出し、入力欄を空にする（3 製品で同じ楽観的な表示。platform の UX 契約 messaging.md §11、#907）。投入できたら同じ位置のジョブ（`client_job_id`）の表示に置き換える。投入できなかったときは質問を残し、その直下に原因と「再送信」を出す（入力欄には戻さない）。

## 生成と実行の境界

送信は SQL の生成と既存の AST・スキーマ・対象範囲の安全検査だけを行い、生成した SQL は実行しない。成功結果は「安全検査済み・未実行」と表示し、実行履歴でも取得件数を「未実行」とする。空の状態の案内（「SQL は自動で実行されません」）もそのまま。

## 生成した SQL の実行（#1154）

実行は明示の操作にする（ChatGPT の Advanced Data Analysis・Databricks Genie・Snowflake Cortex Analyst・Amazon Q in QuickSight と同じく、利用者が SQL を確かめてから実行する）。

- 回答の吹き出しの SQL の行の右に「実行」（secondary・`Play`。実行中はラベルを変えずアイコンがスピナー）。実行した後は「もう一度実行」、バッジは「安全検査済み・実行済み」。安全検査を通っていない SQL（DML など）には出さない。実行の権限（`nl2sql.sql.execute`。`menu.query` / `menu.direct_sql` が含む。`menu.chat` は含まない）が無い利用者には出さず、理由を吹き出しの中に出す。SELECT だけを実行するので確認語は使わない（確認語は管理 SQL の書き込み・削除だけ）。
- 結果は同じ吹き出しの SQL の下に、3 製品で共通の `ChatResultTable`（`@engchina/production-ready-ui`）で出す: 1 行目に要約（「12 行・5 列・0.8 秒」）、先頭 50 行のプレビュー（表頭固定・表の中で縦横スクロール・md 未満 5 行・md 以上 8 行）、「すべての行を見る」（広い side sheet・10 / 50 / 100 行/ページ）、「CSV をダウンロード」（取得した行だけ）。NULL は「NULL」、数値の列は右寄せ（SQL 生成・SELECT SQL の実行の画面の結果の表も同じ `ResultCell`）。
- 実行中は結果の位置に経過時間（`ProcessingIndicator`。スピナーは「実行」のボタンだけ）。実行の失敗は danger の `Banner` で、1 文目は SQL 生成のジョブと同じ利用者向けの文、ORA のコード・元の文は「詳細」。要求の失敗（権限・通信）は `ApiErrorBanner`。
- 上限: 1 回の取得は `NL2SQL_CHAT_RESULT_MAX_ROWS`（既定 1,000 行）、セルの文字数は `NL2SQL_CHAT_RESULT_MAX_CELL_CHARS`（既定 2,000 文字）、応答の行の大きさは `NL2SQL_CHAT_RESULT_MAX_BYTES`（既定 2,000,000 バイト）、時間は SQL 生成のジョブと同じ Oracle の call timeout（`NL2SQL_ORACLE_CALL_TIMEOUT_SECONDS`）。総件数の COUNT は別に取らない（SQL 生成の画面と同じく「さらに行があります」）。打ち切ったら要約と案内で明示し、すべての行は「SELECT SQL を実行」（SQL を履歴の state で渡し、URL に載せない）で取得件数上限を指定して実行する。

### API と保存

- `POST /api/nl2sql/jobs/{job_id}/execute`（権限 `nl2sql.sql.execute`）。SQL は画面から受け取らず、ターンのジョブに保存した生成 SQL を使う。本人の会話のターン（管理の権限でも他の利用者の会話は不可）、ターンの業務プロファイルの利用権限を確かめる。
- 実行は SQL 生成のジョブの実行の段階と同じ経路（`Nl2SqlService._execute_generated_sql`）: 業務プロファイルの範囲（`_resolve_allowed_objects`）→ 安全検査（`analyze_sql`。生成の後に範囲が変わっていれば `SQL_BLOCKED`）→ `execute_sql`（`OracleNl2SqlAdapter.user_data_connection(read_only=True)`）。actor は要求の利用者を `actor_scope` で明示し、system_admin と非 system_admin で別の接続 pool、DeepSec 有効時の非 system_admin は DATA USER の pool（借りるたびに利用者の context を設定・消去。#904）。
- 監査: 実行ごとに実行履歴（`HistoryItem`。`generation_only=false`・`session_id` は会話 ID・件数と列）を残し、構造化ログ `nl2sql_chat_sql_executed`（SQL の本文・行の値は出さない）を出す。
- 結果の行は保存しない。ターンのジョブに要約（`last_execution`: 状態・時刻・行数・列数・打ち切り・実行履歴の ID）だけを残し、会話を開き直したら「前回の実行（…）: 12 行・5 列。…」と「もう一度実行」を出す。理由: SQL 生成のジョブの永続の記録である実行履歴も件数と列だけで、会話は長く残るため業務データの行を会話に残さない（後で DeepSec・業務プロファイルの権限が変わっても古い行を見せない）。画面の中では結果をメモリ（TanStack Query のキャッシュ）にだけ持つ。

生成方法は SQL 生成画面の実行エンジンと同じ Select AI / Select AI Agent / OCI Enterprise AI（#890）。Select AI Agent は業務プロファイルの Agent の資産（SQL ツール・Agent・Task・Team）を使い、SQL ツールは `SHOWSQL` で SQL を作るだけで実行しない（`enable_human_tool` も無効）。返った SQL はほかの生成方法と同じ安全検査だけを通し、実行しない。Agent の資産が未同期・Oracle の接続が無いなど前提が整っていないときは、SQL 生成画面と同じくそのターンが失敗し、理由を会話の中に出す。従来の SQL 生成画面と MCP のジョブは既定の生成・実行を維持する。

## API と永続化

- `POST /api/nl2sql/jobs`: `generation_only: true` と質問・業務プロファイル・生成方法を渡す。継続時は直前の `previous_job_id` を渡す。クライアントから会話本文や SQL を信頼して受け取らない。
- オントロジー: チャットは `use_ontology_context: true`（SQL の生成の prompt に業務プロファイルの公開版のオントロジーの文脈を入れる）と `include_ontology_grounding: false`（生成後の接地確認をしない）を送る。チャットの画面は接地確認のグラフを表示しないため、結果の整形の段階でグラフを読まない（解釈の artifact `interpretation` も作らない。段階のログ `nl2sql_job_stage_step_finished` の `format_results` / `ontology_graph` は `skipped: true`・`skip_reason: "grounding_not_requested"`）。経路は SQL 生成と同じ `_run_job` で、要求の項目で接地確認を省くだけ（#1172）。`include_ontology_grounding` の未指定（SQL 生成の画面・MCP・API の既定）は `use_ontology_context` に従い、今までどおり公開版があれば接地確認する。接地確認は公開版の確定が前提のため、`use_ontology_context: false` なら指定にかかわらず行わない。
- `GET /api/nl2sql/chats`: 本人の会話の先頭ジョブを、現在利用可能な業務プロファイルで絞って 50 件ずつ返す。続きは `next_cursor`。
- `GET /api/nl2sql/chats/{conversation_id}`: 本人の会話と各ターンの永続ジョブを返す。ID は先頭のジョブ ID。
- 停止は既存の `POST /api/nl2sql/jobs/{job_id}/cancel`。既存の worker・lease・fence・再起動時の復旧を使う。

Oracle の既存 `NL2SQL_STATE_DOCUMENTS` の `jobs` を正本にする。新しい表や製品間コード依存は増やさない。ジョブに会話 ID・親ジョブと先頭マーカーを保存する。`sessionStorage` はユーザー・DB context ごとの選択 ID と未送信草稿だけに使い、会話の本文は保存しない。

## 文脈とアクセス

`menu.chat` は生成・業務プロファイル参照・スキーマ参照を含む。SQL 実行とプロファイル管理は含まない。会話の参照・継続は作成者に限り、現在のプロファイル利用権限を照合する。別のプロファイルに変えたら新しい会話にする。前のターンが未完了、会話が既に更新済み、存在しない親ジョブの場合は継続を拒否する。

1 会話は 50 往復まで。モデルの文脈は直近 10 往復、履歴 JSON は 32,000 文字を目安に古い完全なターンを取り除く。質問は 10,000 文字まで、過去 SQL は 16,000 文字まで。結果行・credential・権限設定は文脈に入れない。過去の発言は参照データとして扱い、現在のスキーマ・対象範囲・安全規則は既存の生成パイプラインを正本にする。

## 検証

`backend/tests/test_sql_chat_execute.py`・`test_nl2sql_db_roundtrips.py` の `test_chat_execution_connection_follows_requesting_actor` がチャットの実行（同じ経路・権限・上限・失敗・接続の種類・要約の保存）を、`frontend/tests/e2e/sql-chat-execution.spec.ts` が実行の画面（実行中・成功・0 行・打ち切り・失敗・DML・権限・開き直し）を確認する。`backend/tests/test_sql_chat.py` が生成のみ（Select AI Agent を含む）、文脈、別 worker での復元、所有者・プロファイル、古い会話、元の実行動作と安全検査を確認する。`frontend/tests/e2e/sql-chat.spec.ts` が desktop / 375px の多輪送信・履歴開閉・復元・停止・失敗・生成だけの権限・生成方法の選択と送る値・業務プロファイルの欄の形を確認する。実 Oracle / OCI 接続の動作確認と、決定論スタブによる CI の確認を区別する。
