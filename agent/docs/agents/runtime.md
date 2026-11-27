# Agent のアーキテクチャ不変条件

> agent/AGENTS.md から移した詳細（#2276）。agent/AGENTS.md の規則と同じ強さで守る。規則を変えるときはこのファイルを直し、AGENTS.md には規則の要約と案内だけを書く。

## アーキテクチャ不変条件

- 実行は Control Plane の**組み込み Runtime**（`features/agent/builtin_runtime.py`）。
  - SDK は OpenAI Agents SDK（`openai-agents`。版を固定し、Dependabot の PR で上げる）。
  - モデルは OCI Enterprise AI の Responses API（システム設定 > モデルの接続）。別の LLM provider は組み込まない。
  - クォータナリ接続（Anthropic Claude の Messages API。#5546・#5580）のモデルは、業務 Agent の実行（ツールの呼び出し・並列のツール・複数のターン・承認待ちからの再開）も 1 回の生成（品質評価の判定・意味の判定・下書きの提案）も、platform の `pr_anthropic_messages` で呼ぶ（`anthropic_model.py`。`model_factory` が `AnthropicMessagesModel` を返す）。`AnthropicMessagesModel` は `OciResponsesModel` と同じ `_OciResponses` の層（単一の要求の予算・Run の実行予算・入力の上限の縮小と超過時の送り直し・未完了の判定・span）を通し、一番内側の `responses.create` だけを Messages API に写す。SDK の会話の item と保存する `RunState` は Responses API の形のまま（思考の block は reasoning item の `encrypted_content` に持つ）。会話の添付の画像の読み取りは `call_anthropic_messages`。別のモデル・接続へ切り替えない。forced の `tool_choice`（`required`・名前の指定）は `auto` で送る（Claude Opus 5.5 などは forced を 400 で拒否する。Anthropic の文書の代わりの方法。#5590）。
  - SDK の tracing は常に無効にする（業務データを外部へ送らない）。
- 外部の Runtime（OpenClaw / Hermes / DeerFlow など）・Binding・Docker の Runtime は持たない（#754）。
- 指示は「Agent の指示 + 割り当てた Skill の指示」。ツールは Skill の requirement が必要とするものだけを function tool として渡す。`server_id="control-plane"` は `tool_registry` のツール、それ以外は MCP 接続（`rag` / `nl2sql` / 登録した接続）の `tools/list` のツール（名前は `<接続>__<ツール>`）。Skill の指示は MCP のツールを素の名前（`rag_search`）で書いてよく、Run のときにモデルへ渡す名前へ書き直す。モデルが無い名前のツールを呼んでも Run は落とさず、呼ぶ名前をモデルへ返して呼び直させる（読み替えて実行はしない。#1303）。
- **RAG の回答をそのまま返す**（#5342）: スキルの metadata `production-ready-final-answer-tools`（`AgentSkillDefinition.final_answer_tools`。宣言は `mcp_requirements` のツールだけ）で宣言した RAG の回答のツール（組み込みは `rag-chat` の `rag_chat`・`rag-search` の `rag_search`）は、SDK の `Agent.tool_use_behavior`（関数）で、その結果の回答・登録済みQ&Aの候補（回答は空。確認のカード）・業務ガイドの確認（回答は確認の問い）を Run の最終の回答にする（`builtin_runtime.rag_final_answer`）。そのまま返すのは Run の中で回答のツールが 1 回だけ回答を返したときで、同じ応答で 2 回以上呼んだ（質問を分けて並行に呼んだなど）・前の応答で回答を返していたときは、結果をモデルに返してモデルが回答を書く（回答の対応もモデルの回答で決める。#5367）。回答のツールを宣言せずにツールを使うスキル（データつきのスキル・ファイルを作るスキルなど。同じツールを宣言なしで使うスキルを含む）も選んだ Agent（#5867。RAG で調べた内容をファイルにする「RAG 検索＋文書作成」「RAG チャット＋文書作成」など）・出力の項目・参照付きレポートの Agent はモデルが書く（業務 Agent の編集の「RAG の回答をそのまま返す」の案内も同じ判定で出さない）。モデルの回答のための差し戻しと整形（拒答・多段・出力・データの参照、引用の描画・作業の経過の行・章節の読み切り）は当てず、安全チェック・確認のカード・回答の根拠（引用は RAG の `used_in_answer`。`answer_evidence.rag_answer_evidence`）・回答の対応（RAG の対応のまま）は残す。
- **消えたスキル**（#5342）: 業務 Agent が選んだスキルが無い（組み込みのスキルの廃止など）とき、下書き・常に最新の版の Run は `runtime.skill_unavailable` で失敗する（黙って飛ばさない）。公開の版に固定した中身があればその中身で動く。業務 Agent の `unavailable_skill_ids` が画面の印・警告に使う。保存済みのスキル・固定したスキルの消えた RAG のツール名は読み込みで置き換える（`rag_retrieve_evidence`→`rag_search`、`rag_lookup_guides` は外す。指示の文は変えない。`skills.RETIRED_RAG_TOOL_NAMES`）。
- ツールの実行は `tool_registry.invoke`（ポリシー・ガードレール・監査・成果物）を必ず通す。
  - ポリシーが「拒否」のツールはモデルに渡さない。
  - 「承認」のツールは SDK の `needs_approval` で中断する。中断した Run は `waiting_approval` にし、SDK の状態を Run に保存する。
  - 回答前の確認（`ask_user_confirmation`）は、利用者に聞けるならどの経路でも中断して `waiting_confirmation` にする（#3352）。中断はツールを実行する前に応答ごとに 1 回決め（`on_llm_end`）、同じ応答のほかのツールは確認の答えまで実行しない（`tool.confirmation_pending`）。確認の呼び出しを中断せずに「確認していない」と返すのは、ツールの確認のカード・失敗の直後・表と列の確認・スキップ・上限だけ。
  - 承認がすべて決まったら状態を復元して再開する。却下したツールは実行しない。
  - 呼び先へ送る直前の境界は、通常の実行・承認の再開・MCP 接続のツール・登録済みのツールで同じ順に通す（`_ToolRecorder.invoke`。#2609・#2610・#2630）: ① 取消（終わった Run と、別の実行に置き換わった実行〔実行の token が違う。#2606〕では step を作らず handler を呼ばない。別のプロセスの取消も保存先の Run の行で判定し、書き込みでも取消を勝たせる。モデルの呼び出しの前も同じ判定で止める）→ ② 引数（壊れた JSON・object 以外を空の引数にせず、モデルに見せた schema で型・必須・enum・範囲・追加の項目・入れ子を確かめる。検索の件数の正規化の後に確かめ、実行環境が埋める非表示の引数は求めない。呼び先の契約は `tool_registry.invoke` も確かめる。外部の `$ref` は取りに行かない。誤りは値を含まない `argument_errors`（場所・種類・直し方）で返す。`tool_arguments.py`）→ ③ ツール権限・承認（SDK の `needs_approval` は呼び出しごとに最新のツール権限で決める。「承認」のツールは、その Run・呼び出し ID・ツール・引数の承認の記録がある呼び出しだけを実行する）→ ④ handler。
  - 取消の前に送った操作は取り消せない。結果が取消の後に届いたら事実のまま記録し（イベントに `run_status`）、取消のイベントに送っていた step（`in_flight_step_ids`）を残す。
  - 実行中の呼び出しは取消で打ち切る（#2691。`call_abort.py`）: ツールの呼び出しごとの打ち切りの印を文脈に置いて thread で呼び、同じプロセスの取消（`_cancel_run_locked`）はすぐに、別のプロセスの取消は呼び出しの間の定期の確認（`TOOL_CALL_CANCEL_POLL_SECONDS`。保存先の Run の行）で、この task の取消（SDK のツールの timeout）でも印を立てる。MCP の送信は印が立つと送らず・再試行せず、送信中の接続は状態を変えない手順と読み取り専用（注釈を信頼する接続の `readOnlyHint`）のツールだけ socket を `shutdown` して切る（書き込みのツールは送った後は結果を待つ）。打ち切った呼び出しの step は失敗ではなく取消（`runtime.tool_call_aborted`。取消から戻るまでの秒を `error_details.stopped_after_seconds` とログ `builtin_runtime_tool_call_aborted` に残す）。後始末（`cleanup`）の呼び出しは打ち切らない。
  - 取り消した Run が始めた NL2SQL のジョブは止める（#3318。`job_cancel.py`・`builtin_runtime.cancel_run_nl2sql_jobs`）: Run の step の `nl2sql_query` / `nl2sql_get_job` の結果で、ジョブごとの最後の状態が pending / running のもの（取消を送ったジョブは除く）に、Control Plane が `nl2sql_cancel_job` を後始末（`cleanup`）の step として Run の利用者のサービストークン（Run の版のデータの範囲の claim。ジョブを作ったときと同じ）で送る。予算・ツールで確かめた回数には数えず（trace_id は `run_cancel_`）、ポリシーの「拒否」は守る。実行中だった Run は実行している側が Run の終わりに送り（取消の後に届いた `nl2sql_query` の結果のジョブも分かる）、実行している側の無い Run（承認・回答前の確認の待ち・再開の待ち）は取消の入口（画面の `POST /runs/{id}/cancel`・MCP の `agent_cancel_run`）が予約する。止められなくても取消は済んでいる（ログ `builtin_runtime_nl2sql_job_cancel_failed`）。RAG などの読み取りのツールの扱い（接続を切る）は変えない。
  - ツールの結果の機微情報（機微な名前の項目・文の中の資格情報・Bearer・個人情報）は、成功の出力と同じ規則で、失敗の文・詳細（`error` / `error_details`。呼び先の応答の本文・JSON-RPC の error・例外の文・入れ子の詳細、文の中の URL の資格情報も）からも `tool_registry.invoke` の結果の境界（`_tool_result` → `sanitize_tool_error`）で伏せる（#2611）。モデルへの結果・Run の step・イベント・Run の失敗の文・ログはこの結果を使う。`argument_errors`（値を含まない）と有無を表す真偽の項目は残し、伏せた種類は `audit_metadata.error_masked` に名前だけ残す。
  - ツールの結果の画像（#2650。`tool_media.py`。MCP に依らない型で、MCP 接続・会話の添付などどのツールの結果でも同じ経路）: handler は `ToolOutputWithImages`（文字の結果と `ToolImage`）を返し、`tool_registry.invoke` が画像を `ToolResult` の非保存の属性（`tool_result_images`）に分ける。step・イベント・ログ・成果物には参照（`content_images` の `image_id`＝sha256・MIME・大きさ・由来〔接続・ツール・content の位置、RAG なら根拠の ID・文書の版 `chunk_set_id`・`region`（page・bbox）〕）だけを残し、base64 を残さない。`_ToolRecorder.deliver_images` が登録モデルの「画像対応」（`ModelTarget.vision_enabled`）と 1 回の結果の上限（`MAX_IMAGES_PER_RESULT`）で添付する画像を決め、参照に `model_input`（`attached` / `unsupported_model` / `over_limit` など）と、読めなかったときの `image_input_note` を書き、添付の結果（枚数・状態）を `audit_metadata.image_input` に残す（token の見積もりは入力の管理の側）。添付する画像は SDK の function tool の出力（`[ToolOutputText, ToolOutputImage]`。Responses API の `function_call_output` の `input_image`）で同じ呼び出しの次のモデルの入力に渡す。承認待ちで保存する SDK の状態からは画像のデータを外し（`strip_image_data`。読み直しの案内の文にする）、再開・会話の続きには画像を持ち越さない（要るときはツールで取り直し、呼び先が毎回認可する）。画像に対応しないモデルの代わりに既定の Vision モデルで読む編成は持たない（#2651・#2652）。
  - ツールが作ったファイル（成果物のファイル。#5837。`artifact_file_store.py`）: handler は `save_tool_file`（Run の ID・ファイル名・MIME・バイト列）で Run の成果物（kind `file`。`content` はファイル名・MIME・大きさ・sha256 だけ）に残し、その参照だけを結果に返す。
    - 検査: 形式は .xlsx / .docx / .pptx / .md の 4 つ（`FILE_ARTIFACT_EXTENSIONS`。.md は `text/markdown; charset=utf-8`。#5867）、1 つの上限は `AGENT_ARTIFACT_FILE_MAX_BYTES`（既定 20MB）。中身は形式ごとに確かめる（OOXML の 3 つは zip の署名、.md は UTF-8 の文字）。ファイル名は安全な形に直し（パス区切り・制御文字を除く・長さの上限）、拡張子は MIME から決める。合わないファイル・終わった Run・保存の失敗はツールのエラーにする（成功にしない）。
    - 中身（バイト列・base64）は Run の JSON と分けて保存先に持ち、step・イベント・ログ・snapshot・API の JSON・MCP の結果に載せない。memory / file は repository のメモリ（再起動で消え、ダウンロードは 404）、Oracle は `AGENT_RUNTIME_ARTIFACT_FILES` の BLOB（migration 018。書いたらすぐ commit し、別のプロセスも Run・成果物の ID で読む）。
    - 読み出しは `GET /runs/{run_id}/artifacts/{artifact_id}/download` だけ。Run の詳細・成果物と同じ判定（`_readable_run`）を通し、`Content-Disposition: attachment`（RFC 5987 の `filename*`）・`X-Content-Type-Options: nosniff`・保存した MIME で返す。ファイルの成果物でない ID・無い ID・保存した sha256 と合わない中身は 404。作った文書は回答の本文と同じく資料から作った内容なので、根拠の文書ごとの再確認（#3252）の対象にしない。
    - 消え方: Run の行を消す書き込み（`runtime_checkpoint_store.write_entities` の削除の印）が、同じ transaction でその Run のファイルも消す。全再作成は表ごと消す。
  - Excel のファイルを作るツール `xlsx_create`（#5837。`xlsx_tool.py`・生成の本体は `xlsx_workbook.py`・グラフは `xlsx_chart.py`）: `tool_registry` に登録した Control Plane のツールで、スキルが `control-plane:xlsx_create` を宣言した業務 Agent の Run にだけ渡る（組み込みのスキル `xlsx-create`。業務 Agent・Run に暗黙に足さない）。登録は組み込み Runtime がする（Run を読むので、ツールの層 `tools.py` からは import しない）。
    - 既定は承認なし（Run の成果物を作るだけで外部へ書かないので、`record_output` と同じく副作用なしの宣言。ツール権限で「承認」「拒否」に変えられる。MCP から作った Run・無人の Run でも呼べる）。
    - 引数は構造化した内容（題・シート・列の型と書式・集計・グラフ）。表の値は、その Run で取得済みの表の参照（`source`。回答の表の参照と同じ番号 `T1` と行・列の指定。`answer_data.data_index`・`selected_positions` を使い、値は保存した step の結果からコードが写す。モデルは値を書かない）か、モデルが書いた小さい表（`rows`。`MAX_WRITTEN_ROWS` 行まで）。無い参照・範囲外・型に合わない値・Excel の精度を超える数値は、切り捨て・丸めをせずに `tool.invalid_arguments`（`argument_errors`。値を含まない）で返す。
    - 作る範囲: 複数のシート、シートごとの題・説明の文と 1 つの表、固定の簡素な書式、列ごとの型（文字・数値・日付）と書式、列の幅、表の見出しの固定とフィルター、集計の行（合計・平均・件数・最小・最大。値ではなく Excel の数式）、表の右のグラフ（1 つのシートに 1 つ）。字体は 1 つ（`xlsx_workbook.FONT_NAME`。Word・PowerPoint と同じ名前で、ブックの既定の字体〔字体を指定しない cell と標準のスタイル。テーマの字体に従う印 `scheme` は外す〕・題と見出し・グラフに使い、埋め込まない。テーマの字体は変えない）。画像・ピボット・マクロ・条件付き書式・ほかの種類のグラフ・既存のファイルの編集は作らない。
    - グラフは Excel のグラフ（利用者が開いて値・体裁を編集できる。画像にしない）で、縦棒・横棒・折れ線・円の 4 つ。指定は `pptx_create` と同じ形（シートの `chart`: 種類と、そのシートの `columns` のどの列を項目・系列にするか。グラフ用の値を書く欄は無い）で、型・上限・検証も同じ `chart_spec.py`。値は別に持たず、そのシートの表の cell を参照する（系列の名前は見出しの cell、項目と値はデータの行。集計の行は含めない。利用者が cell の値を変えるとグラフも変わる。XML に入る値の控えは cell の値と同じ）。体裁は PowerPoint・Word と同じ `chart_drawing.py` の XML を使い、`xlsx_chart.py` が Excel に合わせる所だけを変える: cell の参照への書き換え、数値・日付の項目を数値の参照（`c:numRef`）にして cell の表示形式で出す（文字の参照のままだと Excel は日付を通し番号で表示する）、項目の軸を等間隔に固定（`c:auto` を 0）、グラフの全体の白い塗りと薄い線・角を丸めない（`c:roundedCorners` を 0）、字を cell に合わせた大きさにする（負の値の表示と凡例の位置の属性は、共通の `chart_drawing` が書く。#5858）。置き場所は表の右（1 列空けて、表の見出しの行から）、大きさは決まっている（`CHART_WIDTH_CM`・`CHART_HEIGHT_CM`。縦と横の比はスライドのグラフに合わせる。横棒だけは棒の数に合わせて高くする）。openpyxl の chart の class では描かず、置き場所・大きさと書き出す XML だけを openpyxl に渡す（`ChartBase._write` を差し替える）。
    - 安全と上限: 文字の cell は文字型で書く（`=` `+` `-` `@` で始まる文字を数式にしない。数式は集計の行だけ）。シート名は Excel の制約に合わせて直し、直した名前を結果に返す。内容の上限（シート・列・行・cell の合計は `xlsx_workbook` の定数、グラフの系列・項目の数と名前の長さは `chart_spec` の定数）と 1 つの Run のファイルの数（`MAX_FILES_PER_RUN`）を超えたら誤りを返す（黙って切り捨てない）。大きさ・形式は `save_tool_file` が検査する。
    - 結果は成果物の参照（`file`）とシートごとの要約（`sheets`: 名前・行数・列数・集計の行の数）・グラフごとの要約（`charts`: 置いたシートの名前・種類・系列の数・項目の数）だけ。中身は結果・イベント・ログに載せない。
  - Word のファイルを作るツール `docx_create`（#5837。`docx_tool.py`・生成の本体は `docx_document.py`・グラフの埋め込みは `docx_chart.py`。依存は `python-docx`）: `xlsx_create` と同じく `tool_registry` に登録した Control Plane のツールで、スキルが `control-plane:docx_create` を宣言した業務 Agent の Run にだけ渡る（組み込みのスキル `docx-create`）。既定は承認なし（副作用なしの宣言）。
    - 引数は構造化した内容（題・副題と、上から順のブロック `blocks`）。ブロックは 1 つの形の object で、`type`（`heading`・`paragraph`・`bullets`・`numbered`・`table`・`chart`・`page_break`）ごとに使う項目が決まり、使わない項目は null・空の配列にする（要る項目が無い・使わない項目に値があるときは、黙って捨てずに `tool.invalid_arguments`）。新しく作るだけで、既存のファイルの編集・読み取りはしない。
    - 表の値は `xlsx_create` と同じ 2 つの出所（その Run で取得済みの表の参照 `source` か、モデルが書いた小さい表 `rows`。`MAX_WRITTEN_ROWS` 行まで）。列の型（文字・数値・日付）と書式の名前も同じで、Word では書式を当てた文字にして入れ（四捨五入は Excel の表示と同じ）、数値・日付の列を右寄せにする。数値・日付として読めない値は誤りにする。
    - グラフは Word のグラフ（利用者が開いて「データの編集」で値・体裁を編集できる。画像にしない）で、種類・指定・上限・検証・描画は `pptx_create` と同じ（`chart_spec.py`・`chart_drawing.py`）。グラフのブロック（`chart`）は、表と同じ形のデータ（`table`。その Run の表の参照 `source` か、モデルが書いた `rows`。文書には出ず、グラフのデータになる）と、どの列を項目・系列にするか（`chart`。グラフ用の値を書く欄は無い）を持つ。題（`table.caption`）は表と同じく上に置き、代替テキストにもする。大きさは 1 つ（本文の幅 × `CHART_HEIGHT_MM`）、字の大きさは文書の本文に合う値（`CHART_TEXT_PT`・`CHART_LABEL_PT`。`chart_drawing` の既定はスライド用なので、文書の値を渡す）。
    - グラフの埋め込みは `docx_chart.py`（python-docx にグラフの API が無いので、OPC の層の内部の API〔`Part`・`XmlPart`・`next_partname`・`relate_to`・`next_id`・run の XML〕を使う。使う所はこの module だけにする）。グラフ 1 つにつき、グラフの部品（`/word/charts/chartN.xml`。content type は Override）と値のブック（`/word/embeddings/Microsoft_Excel_SheetN.xlsx`。`chart_drawing.chart_workbook` が作る、名前を文字型で書いたブック。content type は拡張子の Default）を足し、文書 → グラフ → ブックの関連と `c:externalData` でつなぐ。本文には行内の図（`wp:inline`。`wp:docPr` の id は文書の中で一意）で置くので、ページをまたがず、収まらなければ題と一緒に次のページへ送られる。確かめた python-docx の版は `tests/test_docx_chart.py` が持つ。版を上げるときは、そのテスト（部品・関連・content type・要素の順）に加えて、実機の Word（修復なしで開ける・編集できるグラフとして認識される・「データの編集」でセルを変えるとグラフの値が変わる）と OOXML の schema（`dml-chart.xsd`・`wml.xsd`）で確かめ直す。
    - 体裁は 1 つに決まっていて、引数で変えられない（テンプレートは使わない）: A4 縦・固定の余白、1 つの字体（`FONT_NAME`。東アジアの字体 `w:eastAsia` も指定し、埋め込まない）、フッターの中央のページ番号（field）、見出しは 3 段まで、リストの入れ子は 1 段、表は罫線つき・幅を固定・見出しの行に薄い網かけとページごとの繰り返し。文は装飾の無い文字として入れる（Markdown を解釈しない。段落の文の改行は段落の区切り）。画像・図・4 つ以外のグラフ・目次・ヘッダーとフッターの内容・脚注・コメント・変更履歴・段組み・横向き・文字の装飾は作らない。
    - 上限: 内容の上限（ブロック・表・グラフ・行・列・cell の合計・文字数。`docx_document` の定数。グラフは 1 つの文書に `MAX_CHARTS` 個まで）は、Word の表が大きい表に向かないので Excel より小さく、表の上限を超えたら「行・列を絞るか、Excel のファイルにする」ことを促す誤りを返す（黙って切り捨てない）。1 つの Run のファイルの数（`MAX_FILES_PER_RUN`）は形式を問わず数える。
    - 結果は成果物の参照（`file`）と、ブロックの種類ごとの数（`blocks`）・表ごとの行数と列数（`tables`）・グラフごとの種類と系列・項目の数（`charts`）だけ。中身は結果・イベント・ログに載せない。
  - PowerPoint のファイルを作るツール `pptx_create`（#5837。`pptx_tool.py`・生成の本体は `pptx_presentation.py`。依存は `python-pptx`）: `xlsx_create`・`docx_create` と同じく `tool_registry` に登録した Control Plane のツールで、スキルが `control-plane:pptx_create` を宣言した業務 Agent の Run にだけ渡る（組み込みのスキル `pptx-create`）。既定は承認なし（副作用なしの宣言）。
    - 引数は構造化した内容（ファイルの題と、ファイルに出る順のスライド `slides`）。スライドは 1 つの形の object で、`type`（`cover`・`section`・`bullets`・`table`・`comparison`・`chart`）ごとに使う項目が決まり、使わない項目は null・空の配列にする（要る項目が無い・使わない項目に値があるときは `tool.invalid_arguments`）。どのスライドにも発表者ノート（`notes`）を付けられる。新しく作るだけで、既存のファイルの編集・読み取りはしない。
    - 体裁は 1 つに決まっていて、引数で変えられない（テンプレート・マスターの選択は無い）: 16:9、白い背景、1 つの字体（`FONT_NAME`。英数字の `a:latin` と東アジアの `a:ea` の両方に書き、埋め込まない）、題の下の強調の色の細い線、表紙を除くスライドの右下のスライド番号。フッターの文・日付・画像・図形・SmartArt・アニメーションと画面の切り替え・文字の装飾・動画と音声は作らない。
    - 収まらない内容は、文字を小さくしたりスライドからはみ出させたりせずに、誤りを返す（直し方を書く。スライドを分ける・文を短くする・行と列を絞る）。字の大きさ・固定の行の高さ・枠の大きさを決めて、題・項目の文字数と数の上限を、上限いっぱいでも枠に収まる値にしている（`pptx_presentation` の定数。関係は `tests/test_pptx_presentation.py` が確かめる）。この上限は schema の制約ではなく生成の本体が確かめる（誤りに直し方を書くため）。表は 1 つの cell を 2 行までとし、列の幅に収まらない表は誤りにする。
    - 表の値は `docx_create` と同じ（その Run の表の参照 `source` か、モデルが書いた `rows`。列の型と書式を当てた文字にして入れ、数値・日付の列を右寄せにする）。参照した表が 1 枚の行の上限（`MAX_TABLE_ROWS_PER_SLIDE`）を超えるときは、題に「（続き）」を付けた次のスライドへ続け、見出しの行を毎回置く。続きを含めた上限（`MAX_TABLE_ROWS`）を超える表は「行を絞るか、Excel のファイルにする」ことを促す誤りにする。モデルが書く `rows` は 1 枚ぶんまで。スライドの数の上限（`MAX_SLIDES`）は、続きのスライドを含めて数える。
    - グラフは PowerPoint のグラフ（利用者が開いて値・体裁を編集できる。画像にしない）で、縦棒・横棒・折れ線・円の 4 つ。値は必ず表から取る: グラフのスライドは表と同じ形のデータ（`table`）を持ち、どの列を項目・系列にするかだけを列の番号で指定する（`chart`。グラフ用の値を書く欄は無い）。指定の型・上限・検証は、ファイルの形式にも Run にも依らない `chart_spec.py`（系列にできるのは数値の列だけ・系列と項目の数の上限・円は系列 1 つ）、描画は `chart_drawing.py`（スライドに依らない。色・凡例・値のラベル・軸の体裁は種類ごとに固定。負の値があっても読めるように、値に依らず、棒は負の値でも系列の色で塗り〔`c:invertIfNegative` を 0。無いと Office は白く塗って黒い線で囲む〕、項目の名前はグラフの端に置く〔`c:tickLblPos` を `low`。縦棒・折れ線は下、横棒は左。既定の「軸のそば」だと 0 の線の所に出て、負の値の棒・線に重なる。#5858〕。python-pptx の雛形の手当て〔軸の ID・`c:varyColors`・東アジアの字体・字の大きさ・凡例の位置の属性を省かない（省くと openpyxl が読めない）〕と、グラフの XML とブックをバイト列で返す入口 `chart_parts` を持つ）。グラフのデータの埋め込みのブック（利用者が「データの編集」で開く表）は `chart_drawing.chart_workbook` が作り、項目・系列の名前を文字型で書く（python-pptx が作るブックは `=` で始まる文字を数式に、URL の形の文字をリンクにするので使わない）。空の値は、縦棒・横棒・折れ線では空のまま描き（棒を置かない・線をつながない）、円では誤りにする。数値として読めない値・項目の名前が空の行・値がすべて空の系列は、どの種類でも誤りにする。
    - 結果は成果物の参照（`file`）と、スライドの種類ごとの数（`slides`）・スライドの数（`slide_count`）・表の続きのスライドの数（`continued_slides`）・表とグラフの要約（`tables`・`charts`）だけ。中身は結果・イベント・ログに載せない。
  - Markdown のファイルを作るツール `markdown_create`（#5867。`markdown_tool.py`）: `xlsx_create` と同じく `tool_registry` に登録した Control Plane のツールで、スキルが `control-plane:markdown_create` を宣言した業務 Agent の Run にだけ渡る（組み込みのスキル `markdown-create`）。既定は承認なし（副作用なしの宣言）。引数はファイル名（`file_name`。拡張子は .md になる）と本文（`content`。Markdown の文）だけで、本文は書いたとおりに UTF-8 で保存する（記法の検査・整形・描画はしない。記法はスキルが持つ）。確かめるのは UTF-8 で表せるか（対になっていないサロゲートは `tool.invalid_arguments`）と大きさの上限（ほかのファイルと同じ `AGENT_ARTIFACT_FILE_MAX_BYTES`）だけ。1 つの Run のファイルの数（`MAX_FILES_PER_RUN`）は形式を問わず数える。結果は成果物の参照（`file`）だけ。画面はプレビューせず、ほかのファイルと同じくダウンロードだけを出す。
  - `xlsx_create`・`docx_create`・`pptx_create`・`markdown_create` の共通の部品（#5837）: 表の参照の解決・ファイルの数の上限と保存の lock・内容の誤りの返し方・値を文字として入れる表（Word・PowerPoint）の列の引数と解決は `file_tool_support.py`（Run を読む）、列の型・書式の名前と形式に依らない値の読み方は `table_columns.py`、値を列の型で読んで書式を当てた文字にする所（Word・PowerPoint）は `table_text.py`、グラフの指定と描画は `chart_spec.py`・`chart_drawing.py`（どれも Run に依らない。Excel はグラフの値を別に持たず表の cell を参照するので、`chart_drawing` の XML を `xlsx_chart.py` が cell の参照に書き換える）。形式ごとの制約（Excel の精度・日付の始まりの年、Word の桁・文字数の上限、PowerPoint の 1 枚に入る量）は、それぞれの生成の本体が持つ。
  - 資料・データで確かめていない回答（#5859。`tool_grounding.py`。差し戻し・注意は `builtin_runtime`）: 根拠・データのツール（RAG・NL2SQL・登録した MCP 接続）を渡した Run で、モデルがどれも呼ばずに最終の回答を作ったときの扱いは、業務 Agent ごとの設定 `ungrounded_answer_policy`（版の項目。画面「資料・データで確かめていない回答」）で決める（利用者の決定、2026-11-26。それまでは常に拒答していた）。
    - `annotate`（既定。画面「注意を付けて表示する」）: モデルが返した回答をそのまま出し、回答の注意書き（成果物 `answer` の `notice`）の先頭に「この回答は、資料・業務データで確かめていません。」を足す（本文 `text` には混ぜない。運用設定の回答の注意書きが無効でも付ける）。差し戻さない・本文を置き換えない（モデルの外で回答を作り直さない）。Run の経過に差し戻し・拒答のイベントは残らない。回答の対応は値を変えず basis を `ungrounded_answer` にし、確かめるべき実行の「根拠を使わずに答えた」（`ungrounded_answer`）と品質評価の `answer_basis` が読む。
    - `refuse`（画面「表示せず「お答えできません」と返す」）: 1 回だけモデルへ差し戻し、それでも呼ばなければ本文を決まった拒答の文に置き換える（#1835 の動作のまま。回答の対応は `insufficient_evidence`、basis `no_tool_grounding` / `declined_without_tools`。確かめるべき実行は「答えられなかった」）。
    - 判定は「この Run でモデルが根拠・データのツールを呼んだか」だけで、回答の中身は見ない（回答と資料を照合する検査は持たない。#4714）。新しく作る業務 Agent・組み込みの業務 Agent・この項目の無い保存済みの業務 Agent は、すべて既定の `annotate`。チャット・自動実行・品質評価の Run は、その Run が使う版の設定に従う。
    - 例外は 2 つの設定で同じ（`annotate` が注意を付けるのは、`refuse` なら拒答になる回答だけ）: 確認の質問だけの回答（あいさつ・用件を聞くだけの回答を含む）、NL2SQL の SQL の生成前の確認で止まった Run、同じ会話の前の Run（会話の履歴の上限の中）が根拠・データのツールで答えた続き、利用者が確認を取り消した Run、根拠・データのツールを渡していない Run。実行補助を外した Run（`AGENT_TOOL_GROUNDING_REQUIRED=false`・品質評価の除去実験の `disabled_aids` の `tool_grounding`）は、どちらの設定でも何もしない。ファイルを作るツールは根拠・データのツールに数えず、ファイルを作った Run も同じ判定・同じ設定で扱う（例外にしない。既定では注意を付けて出す）。
    - 本文の確認の質問だけの回答の差し戻し（#1881・#1903）は回答前の確認の方針で決まり、この設定とは別に、どちらの設定でも行う: 確認のツールを渡した Run と、利用者に確認しない Run（方針「前提を示して回答」・利用者が「確認せずに回答」を選んだ Run・無人の Run〔品質評価・自動実行〕）では、ツールを呼ばずに本文の確認の質問だけを返した回答を 1 回差し戻す（あいさつ・用件を聞くだけの返事は除く）。差し戻しの後も本文の聞き返しなら拒答にせずそのまま出し、経過に `text_clarification` を残す。差し戻しの後にツールを呼ばずに答えた回答は、この設定どおりに扱う。
    - 画面の設定の区画は、根拠・データのツールを宣言したスキル（スキルの一覧の `grounding`）を選んだときだけ出す。注意の文は backend（`tool_grounding.UNGROUNDED_NOTICE`）だけが持ち、画面の案内は `GET /runtime/status` の `ungrounded_notice` を差し込む。
  - 進展の無いツールの呼び出しは Run を終える（#2580。`tool_loop_guard.py`）。保存した step から、モデルが呼んだツールごとに、同じ引数で同じ結果が `REPEAT_LIMIT` 回（`runtime.tool_call_repeated`）・実行の失敗が成功を挟まずに `FAILURE_LIMIT` 回（`runtime.tool_failures_repeated`）・引数の誤り（schema・呼び先の検証・データの範囲）が `ARGUMENT_ERROR_LIMIT` 回（`runtime.tool_argument_errors_repeated`）続いたかを数える（結果が変わる polling・引数が変わるページング・別のツールの失敗・承認待ち・取消・予算の超過・Control Plane の呼び出しは数えない。並列の呼び出しは始めた順）。上限の 1 回前の結果に `loop_notice` を足して差し戻し、上限に達したら同じ応答の残りの呼び出しを実行せず、次のモデルの呼び出しの前に Run を失敗にする（イベント `runtime.failed` の `error_code`・ログ `builtin_runtime_tool_loop_stopped`・metric `agent_tool_loop_stops_total`。引数・結果の値は出さない）。
- 安全チェック（OCI Guardrails。#5062。設定は 3 製品で共通の `PLATFORM_GUARDRAILS_INPUT_ENABLED` / `PLATFORM_GUARDRAILS_OUTPUT_ENABLED`、画面は「セキュリティ設定 › 安全チェック」、判定は `pr_system_settings.guardrails`、Agent の入口は `features/agent/guardrails.py`）: 入力の検査は、ON のとき利用者の文を使う最初の処理で、共通の経路だけで行う（#5109。入口ごとに個別に呼ばない。権限・会話・添付の確かめなど文を使わない処理は前にあってよい）。Run の目標（画面の `POST /runs`・MCP の `agent_chat`・自動実行の発火・品質評価のケースの質問と問い返しへの答え・再実行）は Run を作る唯一の公開の経路 `create_builtin_run` が lock の前に検査し、止めた入力では Run を作らない（ツールもモデルも呼ばず、入力を保存しない。画面は 422 の `GUARDRAIL_BLOCKED`（3 製品で同じ code。`pr_system_settings.guardrails.GUARDRAIL_BLOCKED_CODE`。画面は送信の失敗と分け、再送信を出さない。#5191）、MCP はツールのエラー、自動実行は `failed_to_start`、品質評価はケースを `run_failed` にして job を続ける）。回答前の確認への自由入力（画面・MCP の `agent_answer_confirmation`）は `decide_confirmation` が記録の前に、業務 Agent の下書きの提案の説明はその API が候補の取得とモデルの呼び出しの前に検査する。個人情報は伏せた文で後段に渡す（品質評価の判定のモデルにも伏せた質問・答えを渡す）。最終回答（`builtin_runtime._finish` のモデルの回答と回答案）は確定の前に検査し、個人情報は伏せ、プロンプト攻撃・不適切な内容は停止の文にする（ログ `builtin_runtime_answer_guardrail`）。**ツールの結果（RAG の根拠・資料の本文・NL2SQL の結果など）は OCI Guardrails で検査しない**（間接のプロンプト攻撃はツールの呼出の途中では止められない。画面に出る段階は最終回答の検査で守る）。保存だけの入力（自動実行・評価セットの作成と更新）は保存のときに検査せず、使うとき（発火・評価の実行）に検査する。単発のツールの呼び出し（`POST /tools/invoke`）の構造化の引数・業務 Agent とスキルの定義・評価ケースの期待する回答は検査しない。ツールの結果の語の言い回しでプロンプト攻撃を判定する正規表現（旧 `prompt_injection.*` の警告）は持たない（語の意味の判定をコードの正規表現にしない）。資格情報・メール・SSN・カード番号の形式による伏せ（`_guard_tool_output`）は続ける。
- RAG の回答のツール（`rag_search` / `rag_chat`）の縮退の使い方（#3546。`features/agent/rag_retrieval_state.py`）: 根拠が 0 件で安全チェックの未完了（`guardrail_degraded`）か任意の補助の処理の縮退（`degraded_steps`）があるときは、モデルへのツールの結果に案内（`retrieval_notice`。資料に記載が無いとは答えない）を足す（記録する step の結果は RAG のまま）。回答の対応の `absence_conclusion` はその `rag_search` を数えず、縮退した検索を使ったこと（`rag_degraded`）は対応の値を変えない記録だけのシグナルにする。
- モデルに渡す入力は、モデルの入力の上限に収める（#2383。`features/agent/context_window.py`。SDK の `call_model_input_filter`）。
  - 予算は登録モデルの `max_input_tokens`（システム設定 > モデル）から、出力の上限（入力の上限の半分まで）と余白（`AGENT_CONTEXT_SAFETY_MARGIN_RATIO`）を引いた token の数。見積もりは platform の `estimate_input_tokens` × `AGENT_CONTEXT_TOKEN_ESTIMATE_SCALE`。
  - 超えるときは古いツールの結果から段階的に縮め（一覧の項目の長い文字列を先頭だけ → 後ろの項目を ID だけ → すべて ID だけ）、最新のツールの結果は古い結果を縮めても足りないときだけ縮める。利用者の質問・会話の履歴・指示は縮めない。ID の項目（`id`・`*_id`）は消さず、縮めた結果に `context_compaction`（元の文字数と次の手）を足す。次の手は、RAG の根拠なら `rag_read_source` で読み直す、信頼できる読み取り専用のツールなら絞って呼び直す、副作用があり得るツール（書き込み・注釈を信頼しない接続）なら呼び直さずに残った内容で続ける（同じ操作を再実行させない。#2632）。
  - 画像（#2650）は「入力に残した画像の枚数 × Run のモデルの画像 1 枚の入力 token」で数える（#5334。登録モデルの `image_input_tokens`、値が無ければ共通の既定。値と出典は [platform/README.md](../../../platform/README.md) の「登録モデルごとの画像 1 枚の入力」。`resolve_model_target` → `ModelTarget` → `ContextLimits.image_input_tokens`。画像を添付した呼び出しは見積もりとプロバイダの数えた入力をログ `builtin_runtime_model_input_measured` に並べる）。同じ画像は最新の結果の 1 つだけに添付し、入力の画像は新しい結果から `MAX_IMAGES_IN_INPUT` 枚まで。予算を超えるときは古い結果の画像を文字を縮める前に外し、最新の結果の画像は文字を縮めても足りないときだけ外す。外した画像は ID・理由・読み直しの手の文に置き換え、`context_compaction` の記録に `images_removed` / `image_reason` を残す。
  - 縮めるのはモデルに渡す入力だけで、Run の step のツールの結果は全文のまま（引用の照合は step を使う）。縮めたことは Run のイベント（`context_compaction`）・metric `agent_context_compactions_total`・ログに残す。
  - 縮めても収まらなければ、モデルを呼ばずに `runtime.context_overflow`（対象を絞る・入力の上限の大きいモデルを選ぶ案内）で失敗させる。プロバイダが入力の超過の 400 を返したら、返った prompt の token 数で Run の残りの見積もりを補正して縮め、1 回だけ送り直す（2 回目も超過なら `runtime.context_overflow`）。
  - モデルの呼び出しごとの入力は、モデルの呼び出しの span（`gen_ai.chat`。送り直しは `pr.attempt` の別の span）の属性に残す（#2583。`model_call_observation.py`。本文は含めず、属性は platform の allowlist の名前だけ）: 指示の変わらない部分（業務 Agent の指示・スキル・データの範囲の案内。`build_sdk_agent` が Run の状態を足す前の指示）・指示の全体・ツールの定義の集合の指紋（`pr.context.instructions.static_fingerprint` / `.fingerprint`・`pr.context.tools.fingerprint`。sha256 の先頭 16 桁）、入力の部分ごとの見積もり（`pr.context.estimate.*`: 変わらない指示・Run の状態・ツールの定義・会話の履歴・ツールの結果と合計）、Run の予算と残り（`pr.context.budget.*`）、縮める前後と縮めた数（`pr.context.compaction.*`）。指紋の一致は cache の hit の証明ではない。cache の実測は usage の `gen_ai.usage.cache_read.input_tokens` で、プロバイダが返さなければ付けない（不明。0 と区別する）。呼び出しごとの usage は span だけに、Run の累計は SDK の `Usage`（`RunUsage`。承認の再開・差し戻しも累計を引き継いで上書き）だけに持ち、互いに足さない。送り直した試行のうち未完了・入力の超過の span は失敗で、累計に入るのは完了した span の usage だけ。
- 業務 Agent は版を持つ。編集は下書きで、「公開」した版だけが利用者の Run に使われる。「この版に戻す」で前の版を公開し直す。下書きで実行できるのは Agent 管理（admin）と、個人の業務 Agent の持ち主だけ（#2523）。会話は始めた Run の種類（下書き / 公開中の版）に固定し、下書きの会話はチャットの会話の履歴に出さない（編集画面の「試す」の履歴に出す。#2515）。
  - 個人の業務 Agent（#2523）: `agent.agents.build` だけを持つ利用者が作った業務 Agent は、作った人が持ち主（`owner_user_uuid`）になる。持ち主の Agent は対象範囲に足し（`owned_agent_ids`）、持ち主は編集・下書きで実行・評価・公開・削除ができる。共有は持ち主の申請（`POST /agents/{id}/share-request`）を Agent 管理が承認すると組織の業務 Agent（持ち主なし。元の持ち主は変更できない）になり、権限管理でロールに割り当てる。承認前の個人の Agent はロールに割り当てられない（409、権限管理の候補に出さない）。
  - 出力の項目（#2549。`output_fields`、版の項目）: 業務 Agent に key・表示名・型（文字 / 数値 / 日付 / 真偽）・必須・説明を最大 30 項目まで定義すると、その Run にだけ `record_output` を渡す（引数の schema は項目から作る。`output_fields.py`）。値の検証はコードで行い、項目ごとの誤りを `argument_errors` でモデルに返す。正しい記録は成果物 `structured_output`（`{columns, rows}`。呼び直すと置き換える）に残し、チャットに表と CSV で出す。記録せずに回答した Run は 1 回だけ差し戻す（`_require_output`。経過には `output_retry`）。それでも記録しなければ、画面が記録の無いことを示す。品質評価は、評価ケースの期待する出力（`expected_output`）を、記録した出力とコードで項目ごとに比べ（文字は全角半角・大小・空白を無視、数値は桁区切りも数、日付は ISO、真偽は はい / いいえ も。1 列目の値で行を組む）、項目の比べ方（`match`）が「意味が同じ」の文字の項目で文字が一致しなかったものだけを、ケースごとに 1 回の LLM の呼び出しでまとめて判定し（#2562。判定の理由は `reason`（#2568）、失敗したら文字の一致の結果のまま `note` に理由）、ケースの `field_results` / `field_correct` と概要の `field_accuracy` / `field_item_accuracy` に残す。合格率（公開前の評価の門）には含めない（#2558）。
  - 公開前の評価の門（#2519）: `AGENT_PUBLISH_MIN_PASS_RATE`（0〜1。既定 0 = 使わない）が 0 より大きいと、`POST /agents/{id}/publish` は、下書きを最後に変えた時刻（`draft_updated_at`。版の項目が変わったときだけ進む）の後に始めた下書きの評価（完了したもの）の最新の合格率を求め、足りなければ 409 `publish_evaluation_required`。回帰の門（#2569）: `AGENT_PUBLISH_MAX_PASS_RATE_DROP`（0〜1。未設定は使わない）を設定すると、公開中の版がある業務 Agent は、その下書きの評価と同じ評価セット・同じ評価ケース（`cases_fingerprint`）で公開中の版を評価した最新の評価を基準にし、下書きの合格率が「基準 − この値」より低ければ 409（基準の評価が無ければ、公開中の版を評価するよう案内して 409。初めての公開は対象外）。どちらかの門を使うときは、`AGENT_PUBLISH_MIN_CASES`（0 は求めない）で下書きの評価の合格率の分母のケースの数（`summary.judged_cases`）の下限も見る。既定はすべて使わない。閾値は、基線（今の版の合格率とそのぶれ）を測ってから決める。変更の無い再公開・前の版に戻す・スキルの更新だけの公開は対象にしない。
  - 版の履歴は直近 `AGENT_MAX_PUBLISHED_VERSIONS`（既定 5）版だけを残す。公開のときに古い版から消し（公開中の版・スキルの更新の取り込みの前の公開中の版・未完了の Run（実行待ち・実行中・承認や確認の待ち）が使う版は残して上限に数え、番号は振り直さない。規則は `pr_backend_core.version_retention`）、完了した Run・評価の記録は版の番号だけを持つので消えない。Oracle では公開の前に head を lock して別のプロセスの Run をそろえる（#4172）。それでも版が無い Run（旧データなど）は公開中の版で動く（#2356）。
  - 公開の版は使うスキルの中身（指示・説明・MCP のツール・中身の hash）を固定し、公開中の版の Run は固定した中身で動く（下書きは今のスキル。組み込みのスキルも固定する）。スキルが変わった業務 Agent は「スキルが更新されています」を出し、再公開で取り込む（#1855）。
- Run は Agent を選ぶだけで作れる（Binding の選択は無い）。開発は API のプロセスで実行（`in_process`）し、本番は Oracle checkpoint の row lock と Run lease を使う runtime-dispatcher（`python -m app.features.agent.runtime_dispatcher`）。
  - 実行の開始は、queued の Run を今の claim（lease の所有者・token・世代と期限）に対して 1 回だけ受け付ける条件付きの遷移にし、実行の区切りの書き戻しは実行の世代（token）で守る（`run_claim`。#2606。詳細は設計 §5）。開始の入口（dispatcher・in_process・承認の後の再開）を足すときもこの契約を通す。
- 保存先: Run・業務 Agent と、画面・API で変えた定義（Skill・MCP 接続・ツール権限）は `AGENT_RUNTIME_REPOSITORY_BACKEND` に従い、Oracle では共通の `PLATFORM_ORACLE_*` で `AGENT_RUNTIME_*` / `AGENT_CONTROL_PLANE_ITEMS` に保存する（テーブルはシステムテーブルが作る。アプリは DDL を実行しない）。
  - MCP 接続の秘密は `app.secret_box` で暗号化する。`.env` の宣言は保存しない。
  - 既定の `auto` は、DB の設定がそろっていれば `oracle_checkpoint`、無ければ `memory`（保存しない。再起動で消える）を起動時に選ぶ（`storage_backend`）。保存先の 1 件の Run の不整合では起動を止めず、Run 単位で直す・退避する（`quarantined`）。
  - DB に接続できないあいだも起動し（Run の repository は import 時に作らない）、接続できた時点で再起動せずに読み込む（`storage_bootstrap`。それまで業務の API は 503。docs/agent-control-plane-design.md §5.1.1）。
  - Oracle は Run・業務 Agent を 1 件 = 1 行（`AGENT_RUNTIME_ENTITIES`）で持ち、変えたものだけを書く（#1939。migration 016 が以前の全体の snapshot を移す）。repository の変更は変えた Run・業務 Agent に印を付け（`_mark_run_changed` など。Event を足すと自動）、書き込みは lock を放した後に行う（event loop の上の変更はバックグラウンドで書く）。`router.py` の `async def` の handler は repository を `run_in_threadpool` で呼ぶ（`tests/test_router_threadpool.py`）。
  - 書くプロセスは head（`AGENT_RUNTIME_CHECKPOINTS` の行の版 `VERSION_NO`・`WRITER_ID`）を lock し、前回の同期の後に別のプロセスが書いた行を取り込んでから書く（`runtime_checkpoint_store`。#1916。片方の書き込みがもう片方の更新を消さない。両方が同じ 1 件を変えたときだけ後の値）。
  - テストは `tests/conftest.py` で memory に固定する。保存先の状態は `GET /api/runtime/storage` が返し、「実行環境」の「保存先」のカードと、定義・実行を作る画面の先頭の `NonPersistentStorageNotice`（warning の Banner）が案内する。
- 利用状況・フィードバックの集計: Oracle の構成は Run の事実（`AGENT_RUN_FACTS`。1 Run = 1 行）を SQL で集計する（期間は 365 日まで。書き込みは `run_facts_store` のバックグラウンドの MERGE で、Run を止めない）。memory / file はメモリの Run を集計する（docs/agent-control-plane-design.md §5.2）。
- Agent はスキルだけを持つ。プラグイン（Skill・MCP・resource の配布単位）とマーケットプレイスは作り直さない（#1798・#1803 で削除）。スキルはビルトイン・`AGENT_SKILLS_DIR` の SKILL.md・`AGENT_SKILLS_DEFINITIONS_JSON`・画面で入れ、MCP 接続は直接登録する。
- SKILL.md と同じディレクトリの `references/` の下のテキスト / Markdown は、スキルの参照文書（`references`）として読む（Agent Skills の progressive disclosure。symlink を辿らず、ディレクトリの外を読まず、件数・大きさに上限）。指示には索引だけを入れ、その Run にだけ `skill_reference_read` を渡して必要な部分を読む。読み取りも `tool_registry.invoke` のポリシー・監査を通す。`scripts/` などの同梱のコードは読まず・実行しない。
- スキルは Agent Skills の形（`<name>/SKILL.md` ＋ `references/` の zip）で書き出せる（`GET /api/skills/{skill_id}/export`・画面のスキルの操作・MCP の `agent_skill_export`。`features/agent/skill_export.py`。#1874）。組み込みのスキルは `skills/` と同じ内容、画面のスキルは frontmatter を作り、`skills-ref` の検証を通す。
- Prompt / Workflow / Template は非実行・版管理 resource。独立 Workflow engine を作らない。

## Skill と実行の五つの不変条件（#3240）

業務 Agent・スキル・ツール・実行の正本。依存は `業務 Agent → スキル → ツール（MCP / Control Plane）` の一方向で、実行は組み込み Runtime。各条件を守るコードとテストを並べる（テストは `agent/backend/tests/`）。

1. **能力の宣言はスキル経由。宣言は認可ではない。**
   - 業務 Agent はツールの項目を持たない。使うツールは `skill_ids` で選んだスキルの `mcp_requirements` の和集合だけ（`builtin_runtime.agent_mcp_requirements` / `agent_tool_names`）。作成・変更の API はツールの直接の指定（`runtime.DIRECT_TOOL_FIELDS`: `tool_names`・`mcp_requirements`）を 422 で拒み、保存済みの旧い値（`tool_names`・`migration_required`）は読むときに無視する（未リリースのため互換を持たない）。
   - 宣言はモデルに渡す候補を決めるだけ。ツール権限（「承認」「拒否」）・承認・呼び先のデータの認可は、実行の直前に別に効く。
   - ツールの層（`tools.py` とそこから import する module）は、スキル・業務 Agent・Run の module を import しない。Run の終わりの判定は上の層が差し込む（`tools.set_run_finished_check`）。スキルの一覧のツール `agent_skill_list` は、この向きに反するので削除した。
   - スキルの追加・変更・削除は、ツールの登録・MCP 接続・ツール権限を変えない。使用中のスキルの削除を 409 にするのは上の層の整合の検査で、下の層からの逆依存ではない。
   - 実行環境が足すツールは能力の宣言ではない。`skill_reference_read`（その Run のスキルの参照文書）・`ask_user_confirmation`・`record_output`（業務 Agent の出力の項目）・会話の添付を読むツールは、Run 自身の業務 Agent・スキル・会話から組み込み Runtime が作り、外部の MCP のツールを足さない。Control Plane が自分で呼ぶ後始末（`nl2sql_cancel_job` など）と業務ガイドの照合も、モデルに渡すツールを広げない。どれも `tool_registry.invoke` を通す。
   - MCP の resources / prompts は使わない（使うのは `tools/list` と `tools/call` だけ）。スキルの `references/` はスキル自身の参照文書。
   - テスト: `test_skill_tool_boundary.py`（`test_agent_api_rejects_direct_tool_fields`・`test_directly_listed_tool_is_not_exposed_to_the_run`・`test_tool_layer_does_not_import_skills`・`test_skill_changes_do_not_change_tool_provision`）、`test_builtin_runtime.py::test_instructions_and_tools_come_from_agent_and_skills`、`test_mcp_tool_safety.py::test_skill_allowlist_is_not_widened_by_a_colliding_name`、`test_skill_editor_api.py::test_skill_used_by_agent_draft_cannot_be_deleted`。
2. **明示の構成を解決できなければ、暗黙に切り替えずに失敗する。**
   - スキルが要る MCP 接続・明示したツールが 1 つでも使えなければ、ある分だけで続けず、モデルを呼ぶ前に Run を `runtime.mcp_unavailable` で失敗にする（`build_sdk_agent`）。
   - スキルが宣言した Control Plane のツール（`control-plane:<名前>`）が登録に無いときも同じく、ある分だけで続けず、モデルを呼ぶ前に Run を `runtime.tool_unavailable` で失敗にする（`build_sdk_agent`・`declared_control_plane_tools`。どのスキルのどのツールかを失敗の文と警告のイベントに残す。保存・公開の時は検査しない。#5844）。組み込みのスキルが宣言するツールがすべて登録されていることはテストで確かめる（`test_skill_tool_boundary.py::test_builtin_skills_declare_only_registered_control_plane_tools`）。省略時の既定（業務 Agent の `model_id` が空なら既定のテキストモデル）を選ぶことは切り替えではない。
   - テスト: `test_builtin_runtime.py::test_partially_unavailable_required_mcp_stops_before_the_model`、`test_skill_tool_boundary.py::test_missing_required_tool_fails_the_run_instead_of_intersecting`、`test_skill_tool_boundary.py::test_unregistered_control_plane_tool_fails_the_run_instead_of_skipping`、`test_product_mcp.py::test_missing_user_fails_before_calling_without_a_substitute`。
   - モデル: platform の `enterprise_ai_connection_for_model` は、登録に無い `model_id`（登録を消したモデルなど）をプライマリ接続へ切り替えずに `EnterpriseAiModelNotRegisteredError` にする。空の ID は既定のテキストモデルの接続、既定のテキスト / Vision モデルの ID は登録モデルに無くてもプライマリ接続（`.env` だけの構成）。組み込み Runtime はこれを `runtime.model_not_registered` の Run の失敗にする（`test_builtin_runtime.py::test_unregistered_model_fails_without_switching_connections`、`platform/packages/system_settings_backend/tests/test_model_connections.py`）。
3. **未対応の操作を成功にしない。**
   - REST は状態に合う status と code を返す（ツールの直接の指定は 422）。MCP は契約の tool error / JSON-RPC の error を返す（未対応の method は `METHOD_NOT_FOUND`）。Run は失敗の状態と `error_code` で表す。
   - モデルに渡していない名前のツールの呼び出しは実行せず、呼べる名前をモデルへ返し、理由を残す（下の「宣言の外の呼び出しの監査」）。表示用の任意の項目は、契約で任意としたものだけ省いてよい。
   - テスト: `test_skill_tool_boundary.py::test_undeclared_calls_are_recorded_with_reasons_and_not_run`、`platform/packages/backend_core/tests/test_mcp.py`、`test_tool_call_boundary.py::test_invalid_arguments_never_reach_the_handler`。
4. **認可は検証済みの認証文脈から決める。**
   - 身元・ロール・対象範囲は、Cookie のセッション・API キー・サービストークンと、サーバーの権限の情報から決める。request の本文の利用者・ロール・承認者は使わない（承認の `decided_by` は Cookie の利用者）。
   - サービストークンの `sub` は Run の利用者（承認の後も承認者にしない）。モデルの引数で `sub`・データの範囲の claim は変わらない。共通の認証の詳細は [backend-standard.md](../../../platform/docs/backend-standard.md) と [mcp.md](./mcp.md)。
   - テスト: `test_security_scope.py::test_approval_is_scoped_and_decided_by_principal`、`test_agent_data_scope.py::test_model_arguments_do_not_change_the_token_subject_or_scope`、`test_builtin_runtime.py::test_mcp_tool_after_approval_uses_the_run_creator_token`。
5. **検索の本文・ツールの結果・モデルの出力は、権限・承認の根拠にしない。**
   - これらは操作と引数の提案にだけ使う。実行の直前のツール権限の判定と、承認の記録の照合は迂回できない。承認は Run・呼び出し ID・ツール・引数に結び付き、再開でも最新のツール権限を確かめる。
   - 承認の要否の根拠にする annotations は、注釈を信頼する接続の `tools/list` の定義だけ。ツールの結果の中の annotations・「承認済み」・権限の変更の要求は使わない。
   - テスト: `test_untrusted_content_authority.py`（合成の「承認済み」・権限の変更の要求・偽の annotations・偽の承認の ID を検索とツールの結果に入れ、その文言に従って書き込みを求める台本）、`test_tool_call_boundary.py::test_approval_is_bound_to_the_call_id_and_arguments` / `test_policy_changed_to_ask_after_build_waits_for_approval` / `test_policy_changed_to_deny_after_approval_is_not_executed`、`test_tool_policy_catalog.py::test_read_only_hint_is_used_only_for_trusted_connections`、`test_builtin_runtime.py::test_mcp_run_does_not_wait_for_approval`。

### 接続のすべてのツールの露出（棚卸し）

- 接続のすべてを渡すのは、`mcp_requirements` の `tool_names` が空の宣言だけ（SKILL.md の `production-ready-mcp-tools` に `<接続>` だけを書くか、画面の JSON で空にする）。組み込みのスキルは明示の一覧だけを使う（`test_builtin_skills_declare_explicit_tool_lists`）。接続のすべてを使うのは、利用者が作ったスキルだけ。
- そのスキルを使う業務 Agent には、接続に後から足したツールも渡る。公開の版も同じ（版が固定するのはスキルの中身で、接続のツールの一覧ではない）。
- 承認の要否は、呼び出しごとに今のツール権限で決まる。書き込みのツール（`readOnlyHint` が無いか false）と、注釈を信頼しない接続のツールは既定で「承認」。「拒否」のツールは渡さない（`test_whole_connection_exposes_added_tools_under_the_current_policy`）。
- 外部の接続にツールを足すときは、管理者がツール権限を見直す。露出を固定したいスキルは明示の一覧にする。既存のスキルを黙って明示の一覧に書き換えることはしない。
- 明示の一覧は、一覧に無いツールを渡さない。一覧のツールが接続に無ければ、交差集合で隠さずに Run を失敗にする（不変条件 2）。

### 宣言の外の呼び出しの監査

モデルが渡していない名前のツールを呼んだら、SDK の `tool_not_found` で Run を失敗にせず、呼べる名前をモデルへ返す（#1303）。呼び出しは実行しない。呼び出しごとに理由を決め、Run のイベント（`payload.undeclared_tool_call`: `tool_name`・`call_id`・`reason`。`severity=warning`）・ログ `builtin_runtime_undeclared_tool_call`・metric `agent_undeclared_tool_calls_total{reason}` に残す（`builtin_runtime.record_undeclared_call`）。検知を理由に、渡すツールを広げない。

| reason | 意味 |
|---|---|
| `name_mismatch` | 渡したツールを別の名前（素の名前・別の接頭辞）で呼んだ（モデルの名前違い） |
| `policy_denied` | スキルが宣言したが、ツール権限が「拒否」で渡していない |
| `declined` | スキルが宣言したが、この Run で利用者が確認を取り消した種類 |
| `name_conflict` | スキルが宣言したが、別の出自のツールと名前が重なった（#2596） |
| `previous_context` | 同じ会話の前の Run で呼んだが、この Run では渡していない（前の文脈） |
| `not_declared` | 登録済みの接続・Control Plane のツールの名前だが、業務 Agent のスキルが宣言していない（スキルの設定の不足か、宣言の外への試み） |
| `unknown` | どの接続・ツールの名前にも当たらない |

### 保存・公開・実行の就緒条件

保存できることは、実行できることを意味しない。段階ごとに確かめることを分ける。

| 段階 | 確かめること | 確かめないこと（後の段階で明示の失敗にする） |
|---|---|---|
| 保存（下書きの作成・変更） | 名前・ID の形、スキルが登録済み（400）、データの範囲を編集者が使える（`_verify_data_scopes`）、出力の項目の形、業種テンプレートが存在する、ツールの直接の指定が無い（422） | スキルが要る MCP 接続・ツールが今使えるか、モデルが登録済みか |
| 公開 | スキルが登録済み、スキルの中身の固定（`pin_skills`）、設定したときだけ評価の門（`AGENT_PUBLISH_MIN_PASS_RATE`・回帰の門） | 保存と同じ（公開の後にも接続・モデル・ツール権限は変わる） |
| 実行（Run の開始と各呼び出し） | 業務 Agent が有効で公開した版がある（下書きは管理者と持ち主だけ）、利用者の対象範囲、モデルと接続の解決（`runtime.model_not_configured` など）、要る MCP 接続と明示したツールがそろう（`runtime.mcp_unavailable`）、呼び出しごとの取消・引数・最新のツール権限・承認の照合、呼び先の認可（サービストークンの `sub` は Run の利用者） | — |

業務 Agent の一括の import は持たない。保存先の読み込みは 1 件の不整合を退避し（#853）、組み込みの業務 Agent を足す。スキルの読み込み（`AGENT_SKILLS_DIR`・`AGENT_SKILLS_DEFINITIONS_JSON`・画面）は形を検証する。

### #2586（出荷前の横断の検証）との対応

| #2586 の障害の条件 | 不変条件 | 確かめるテスト |
|---|---|---|
| 検索の結果の「承認不要」などの注入 | 5 | `test_untrusted_content_authority.py`（危険な次の操作を求める台本） |
| 権限外の操作・未承認の書き込み | 1・5 | `test_skill_tool_boundary.py`、`test_tool_call_boundary.py`（#2610）、`test_product_mcp.py::test_write_tools_require_approval_by_default` |
| 承認の後の利用者の委譲 | 4 | `test_builtin_runtime.py::test_mcp_tool_after_approval_uses_the_run_creator_token` |
| RAG の 503・timeout・接続の失敗 | 2・3 | `test_product_mcp.py`（再試行・書き込みを再送しない）、`test_builtin_runtime.py::test_unreachable_mcp_service_is_a_tool_error_and_the_run_continues` |
| 必須の接続・ツールの不足 | 2 | `test_partially_unavailable_required_mcp_stops_before_the_model`、`test_missing_required_tool_fails_the_run_instead_of_intersecting` |
| 取消の後の新しい外部の操作 | 3 | `test_tool_call_boundary.py::test_cancel_during_a_read_stops_the_following_write` / `test_approved_call_is_not_run_after_cancel`（#2609） |
| 二重の実行（古い所有者の開始） | 3 | `test_run_claim_fencing.py`（#2606） |

不変条件では扱わず #2586 に残すもの: 停止と復帰の手順（受付の停止・特定の能力の禁止・緊急の停止）とその演練、点検の証拠の集約（pass / blocker / warning / not-applicable）、承認の滞留の責任者、request / run / tool の ID の関連付けの測定、実 OCI / Oracle の隔離環境での確認。

## API backend の単一所有者（#4216）

同じ保存先の API backend は 1 プロセスだけ起動する。起動時の Run の中断復元、評価の復元、
自動実行の開始より先に所有権を取得し、競合したプロセスは起動に失敗する。Oracle は schema 全体で
共用する制御表 `AGENT_SCHEMA_OPERATIONS` の予約行 `__api_backend_owner__` を専用接続で `FOR UPDATE NOWAIT` し、
file は snapshot に隣接する `.owner.lock` の OS lock を使う。正常な checkpoint の head は lock しない。制御表はシステム表の再作成でも保持する。
制御表がまだ無い場合は管理画面の起動を許し、作成後に所有権を取得してから保存先を読む。
CLI と external dispatcher の既存の実行の claim は変えない。API は external モードでも排他にする。

所有権は終了時の書き込みを終えるまで保持する。Oracle の所有権の専用接続が失われた場合は、
監視が検出するとプロセスを終了する（通常 1 秒間隔、通信の上限 5 秒）。切断の検出には時間があるため、
外部操作の exactly-once は保証しない。DB 停止中は従来どおり画面の起動を許すが、所有権の取得までは
保存先の復元も自動実行も始めない。2 つ目の通常 backend に観察専用の役割は提供しない。

## 任意のレポート出力契約

`output_contract` は公開版に固定する任意の出力設定で、未設定の実行は従来のままにする。
設定した実行にだけ、既存の `record_output` と同じ位置付けの内部の回答案作成能力を渡す。
これは回答の形式を決めるものであり、Skill の外部ツール宣言や実行時の認可を増やすものではない。
モデルに任意のツール名、確認状態、内部ハッシュ、文字数上限を指定・上書きさせない。

回答案は根拠と値の参照を検証・展開してから計数し、Run 内の短い番号で不変の本文へ結び付ける。
確定後に本文を再展開・書き換えしない。再開では同じ対応表を使い、別の実行へ番号を持ち越さない。
専用確認節の状態と長さの保証を、自由な考察の意味検査や依頼全体の充足の証明と混同しない。
契約、計数の境界、互換性は [参照付きレポートの出力契約](../report-output-contract.md) を参照する。
