# Agent のアーキテクチャ不変条件

> agent/AGENTS.md から移した詳細（#2276）。agent/AGENTS.md の規則と同じ強さで守る。規則を変えるときはこのファイルを直し、AGENTS.md には規則の要約と案内だけを書く。

## アーキテクチャ不変条件

- 実行は Control Plane の**組み込み Runtime**（`features/agent/builtin_runtime.py`）。
  - SDK は OpenAI Agents SDK（`openai-agents`。版を固定し、Dependabot の PR で上げる）。
  - モデルは OCI Enterprise AI の Responses API（システム設定 > モデルの接続）。別の LLM provider は組み込まない。
  - SDK の tracing は常に無効にする（業務データを外部へ送らない）。
- 外部の Runtime（OpenClaw / Hermes / DeerFlow など）・Binding・Docker の Runtime は持たない（#754）。
- 指示は「Agent の指示 + 割り当てた Skill の指示」。ツールは Skill の requirement が必要とするものだけを function tool として渡す。`server_id="control-plane"` は `tool_registry` のツール、それ以外は MCP 接続（`rag` / `nl2sql` / 登録した接続）の `tools/list` のツール（名前は `<接続>__<ツール>`）。Skill の指示は MCP のツールを素の名前（`rag_search`）で書いてよく、Run のときにモデルへ渡す名前へ書き直す。モデルが無い名前のツールを呼んでも Run は落とさず、呼ぶ名前をモデルへ返して呼び直させる（読み替えて実行はしない。#1303）。
- ツールの実行は `tool_registry.invoke`（ポリシー・ガードレール・監査・成果物）を必ず通す。
  - ポリシーが「拒否」のツールはモデルに渡さない。
  - 「承認」のツールは SDK の `needs_approval` で中断する。中断した Run は `waiting_approval` にし、SDK の状態を Run に保存する。
  - 承認がすべて決まったら状態を復元して再開する。却下したツールは実行しない。
- モデルに渡す入力は、モデルの入力の上限に収める（#2383。`features/agent/context_window.py`。SDK の `call_model_input_filter`）。
  - 予算は登録モデルの `max_input_tokens`（システム設定 > モデル）から、出力の上限（入力の上限の半分まで）と余白（`AGENT_CONTEXT_SAFETY_MARGIN_RATIO`）を引いた token の数。見積もりは platform の `estimate_input_tokens` × `AGENT_CONTEXT_TOKEN_ESTIMATE_SCALE`。
  - 超えるときは古いツールの結果から段階的に縮め（一覧の項目の長い文字列を先頭だけ → 後ろの項目を ID だけ → すべて ID だけ）、最新のツールの結果は古い結果を縮めても足りないときだけ縮める。利用者の質問・会話の履歴・指示は縮めない。ID の項目（`id`・`*_id`）は消さず、縮めた結果に `context_compaction`（元の文字数と、`rag_read_source` で読み直す次の手）を足す。
  - 縮めるのはモデルに渡す入力だけで、Run の step のツールの結果は全文のまま（回答の検証・引用の照合は step を使う）。縮めたことは Run のイベント（`context_compaction`）・metric `agent_context_compactions_total`・ログに残す。
  - 縮めても収まらなければ、モデルを呼ばずに `runtime.context_overflow`（対象を絞る・入力の上限の大きいモデルを選ぶ案内）で失敗させる。プロバイダが入力の超過の 400 を返したら、返った prompt の token 数で Run の残りの見積もりを補正して縮め、1 回だけ送り直す（2 回目も超過なら `runtime.context_overflow`）。
- 業務 Agent は版を持つ。編集は下書きで、「公開」した版だけが利用者の Run に使われる。「この版に戻す」で前の版を公開し直す。下書きで実行できるのは Agent 管理（admin）と、個人の業務 Agent の持ち主だけ（#2523）。会話は始めた Run の種類（下書き / 公開中の版）に固定し、下書きの会話はチャットの会話の履歴に出さない（編集画面の「試す」の履歴に出す。#2515）。
  - 個人の業務 Agent（#2523）: `agent.agents.build` だけを持つ利用者が作った業務 Agent は、作った人が持ち主（`owner_user_uuid`）になる。持ち主の Agent は対象範囲に足し（`owned_agent_ids`）、持ち主は編集・下書きで実行・評価・公開・削除ができる。共有は持ち主の申請（`POST /agents/{id}/share-request`）を Agent 管理が承認すると組織の業務 Agent（持ち主なし。元の持ち主は変更できない）になり、権限管理でロールに割り当てる。承認前の個人の Agent はロールに割り当てられない（409、権限管理の候補に出さない）。
  - 出力の項目（#2549。`output_fields`、版の項目）: 業務 Agent に key・表示名・型（文字 / 数値 / 日付 / 真偽）・必須・説明を最大 30 項目まで定義すると、その Run にだけ `record_output` を渡す（引数の schema は項目から作る。`output_fields.py`）。値の検証はコードで行い、項目ごとの誤りを `argument_errors` でモデルに返す。正しい記録は成果物 `structured_output`（`{columns, rows}`。呼び直すと置き換える）に残し、チャットに表と CSV で出す。記録せずに回答した Run は 1 回だけ差し戻す（`_require_output`。経過には `output_retry`）。それでも記録しなければ、画面が記録の無いことを示す。品質評価は、評価ケースの期待する出力（`expected_output`）を、記録した出力とコードで項目ごとに比べ（文字は全角半角・大小・空白を無視、数値は桁区切りも数、日付は ISO、真偽は はい / いいえ も。1 列目の値で行を組む）、項目の比べ方（`match`）が「意味が同じ」の文字の項目で文字が一致しなかったものだけを、ケースごとに 1 回の LLM の呼び出しでまとめて判定し（#2562。判定の理由は `reason`（#2568）、失敗したら文字の一致の結果のまま `note` に理由）、ケースの `field_results` / `field_correct` と概要の `field_accuracy` / `field_item_accuracy` に残す。合格率（公開前の評価の門）には含めない（#2558）。
  - 公開前の評価の門（#2519）: `AGENT_PUBLISH_MIN_PASS_RATE`（0〜1。既定 0 = 使わない）が 0 より大きいと、`POST /agents/{id}/publish` は、下書きを最後に変えた時刻（`draft_updated_at`。版の項目が変わったときだけ進む）の後に始めた下書きの評価（完了したもの）の最新の合格率を求め、足りなければ 409 `publish_evaluation_required`。回帰の門（#2569）: `AGENT_PUBLISH_MAX_PASS_RATE_DROP`（0〜1。未設定は使わない）を設定すると、公開中の版がある業務 Agent は、その下書きの評価と同じ評価セット・同じ評価ケース（`cases_fingerprint`）で公開中の版を評価した最新の評価を基準にし、下書きの合格率が「基準 − この値」より低ければ 409（基準の評価が無ければ、公開中の版を評価するよう案内して 409。初めての公開は対象外）。どちらかの門を使うときは、`AGENT_PUBLISH_MIN_CASES`（0 は求めない）で下書きの評価の合格率の分母のケースの数（`summary.judged_cases`）の下限も見る。既定はすべて使わない。閾値は、基線（今の版の合格率とそのぶれ）を測ってから決める。変更の無い再公開・前の版に戻す・スキルの更新だけの公開は対象にしない。
  - 版の履歴は直近 `AGENT_MAX_PUBLISHED_VERSIONS`（既定 5）版だけを残す。公開のときに古い版から消し（公開中の版・スキルの更新の取り込みの前の公開中の版は残す。番号は振り直さない。規則は `pr_backend_core.version_retention`）、Run・評価の記録は版の番号だけを持つので消えない。消えた版で続ける Run は公開中の版で動く（#2356）。
  - 公開の版は使うスキルの中身（指示・説明・MCP のツール・中身の hash）を固定し、公開中の版の Run は固定した中身で動く（下書きは今のスキル。組み込みのスキルも固定する）。スキルが変わった業務 Agent は「スキルが更新されています」を出し、再公開で取り込む（#1855）。
- Run は Agent を選ぶだけで作れる（Binding の選択は無い）。開発は API のプロセスで実行（`in_process`）し、本番は Oracle checkpoint の row lock と Run lease を使う runtime-dispatcher（`python -m app.features.agent.runtime_dispatcher`）。
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
