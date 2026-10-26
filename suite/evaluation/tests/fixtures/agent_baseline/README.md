# 製品横断の評価の基線（#3240 の段 1・段 2）

比較評価の CLI（`app.rag.agent_evaluation_cli`）が Agent の Run を採点・集計した結果を、合成の入力で固定する。評価を `suite/evaluation/` へ移す（段 3〜5）ときは、同じ fixture から同じ値を出すことを確かめる。値を意図して変えるときは、採点の version（`metrics_version`）を上げ、差と理由を PR とこのファイルに書く。

内容はすべて合成のデータで、業務の内容を含まない（AGENTS.md「業務の内容は書かない」）。

## ファイル

| ファイル | 内容 | テスト |
|---|---|---|
| `cases/<id>.json` | 1 ケース（`case` は `EvaluationCase`）と、往復ごとの観測の種類と時間と Run の ID（`turns[]`: `observation`・`elapsed_ms`・`run_id`）。`expected.scores` は採点、`expected.cli` は今の CLI に固有の出力 | `tests/test_agent_evaluation_baseline.py` |
| `platform/contracts/mcp/fixtures/agent-evaluation-baseline.json`（共有） | Run の ID ごとの公開の Agent MCP の出力（`agent_get_run`・`agent_get_run_observation`、終わっていない Run は `agent_cancel_run`）と、その元の合成の Run の記録（`source`。Agent の内部の形で、CLI と rag のテストは読まない） | Agent の `tests/test_evaluation_baseline_observation.py`（本物の MCP の入口で作った出力と一致し、契約の outputSchema に合う）・rag の上のテスト（公開の出力だけで再生） |
| `summaries.json` | 評価セット（ケースの並びと閾値）ごとの集計（`expected.metrics`）・分母（`expected.denominators`）・CLI の比較の要約（`expected.cli`） | `tests/test_agent_evaluation_baseline.py` |
| `agent_internal_dependencies.json` | 段 1 の CLI が Run の内部の記録から読んでいた観測と、それを返す公開の Agent MCP の項目（`public_fields`）の対応。各項目は公開の項目を消すと変わる採点の項目を `witness` に持つ。足さなかった観測は `not_added` | 同上 |
| `answer_sections.json` | 回答の本文のうち主張に数える部分（`grounding_text`）。`rag_sections` は RAG 自身の回答の節、`legacy_agent_sections` は Agent の回答の節（#2738 の前と後） | `tests/test_answer_metrics.py`（RAG）・`tests/test_answer_metrics_legacy_agent.py`（Agent） |

## 保証すること

- **採点（`expected.scores`）**: ケースの結果のうち Agent の Run の ID と本文を含まない項目（六指標・引用の文書・正解の文書・取れなかった根拠・対応と出所・往復ごとの判断・失敗の理由・失敗の種類と段・時間）。移設の後も一致させる。
- **集計（`expected.metrics`）**: 六指標の平均（成功したケースのうち測れたものだけ）、`metric_case_counts`、`error_count`、区分・分類・種類・段の数・期待する判断ごとの内訳、閾値の判定（`threshold_failures`・`passed`）、採点の版（`metrics_version`。Agent の経路は `six_metrics_v1+agent_v2`）。
- **分母（`expected.denominators`）**: ケースの数・失敗の数・最後の往復の観測の種類ごとの数（completed / failed / timeout / cancelled / waiting_approval / api_error）、指標ごとの測れた数（`measured`）と、成功したのに測れなかった数（`not_evaluable`）。失敗は指標の分母に入らず、失敗が 1 つでもあれば `passed=false`。どの指標も測れなければ `passed=null`。
- **失敗の扱い**: failed・cancelled・waiting_approval・回答前の確認で止まった Run（取り消す）・時間切れ（取り消す）・承認の要るツールを呼ばずに完了した Run（`skipped_approval_tools`・観測の `tool_calls[].status=approval_skipped`。#3240 の Q7）・Run を作れない（HTTP の失敗・ツールのエラー・知らない `schema_version`）は、どれもケースの失敗（`case_error`）。複数往復の途中で失敗したら、それまでの往復を採点せずケースの失敗にする（`error_stage=turn_<n>`、時間は往復の和）。
- **旧形式の扱い**: 公開の `outcome` が null の完了の Run（成果物に outcome の無い #1305 より前の Run・語彙に無い値）は、回答の文から推定する（最終の検証の定型の注記を除いた最後の行が問いなら確認の質問、引用の無い回答は拒答。`outcome_source=inferred`）。`rag_search` の outcome は公開しないので使わない。
- **今の CLI に固有の出力（`expected.cli`）**: 往復の Run の ID、診断（`diagnostics.answer`）、失敗の文言、Run の記録（`RunRecord`。使った版 `agent_version`・呼ばなかった承認のツール・見られない根拠と本文を省かれた根拠の数・取消の結果を含む）、Agent に送った依頼（`agent_chat` の `agent_id`・既知の条件を足した最初の質問・`thread_id`）、取り消した Run。段 4 で adapter に移すと形が変わりうる。変えるときは差を記録する。

## 再生の条件

`tests/test_agent_evaluation_baseline.py` の `replay` が、Agent の公開の MCP（`POST /api/mcp` の `tools/call`）を共有の fixture の公開の出力で再生して、`evaluate` を呼ぶ。CLI が送る引数は契約（`agent-tools.json` の inputSchema）で確かめる。

- 往復の時間は `turns[].elapsed_ms`（往復の始まりを 0 秒、Run の終わりを elapsed_ms の時刻にする時計）。
- 時間切れの上限は 2 秒（`RUN_TIMEOUT_SECONDS`）。時間切れの Run（`observation=timeout`）の `agent_get_run` は running（`in_progress=true`）のまま返し、待ちの時計は読むたびに 1 秒進む。期限を過ぎたら `agent_cancel_run` を送る。
- `observation=api_error` の往復は `agent_chat` が `http_status` の HTTP の失敗を返す。
- 観測は 1 回（`limit=100`）で全件を返す（ページのたどり方は `tests/test_agent_evaluation_cli.py`）。
- 業務ガイドの条件の名前（`--guides`）は渡さない（既知の条件は条件の id で書く）。

## 公開の Agent MCP の項目

`agent_internal_dependencies.json` の一覧。段 1 で公開の Agent MCP に無かった観測は、段 2（設計は draft PR #3316、実装は #3319・#3320・#3322・#3326・#3327・#3330）で次の公開の項目になった。テストは公開の項目を消すと、段 1 で内部の記録を消したときと同じ採点の項目が変わることを確かめる。

| 観測 | 使う項目 | 公開の Agent MCP |
|---|---|---|
| 根拠の `document_id`・`chunk_id` | MRR・必要な根拠の取得率・引用の文書・`retrieval_miss` / `evidence_miss`・引用の重複の除去 | `agent_get_run_observation.evidence[].document_id` / `chunk_id`（`rank` の順） |
| 根拠の構造の場所（頁・章節・シート・行） | 必要な根拠の取得率（場所の条件の付いた根拠） | `evidence[].locator`（RAG の `EvidenceLocator` と同じ名前） |
| 回答の対応 | 対応・出所・判断の正しさ・拒答の判定・検索の指標の対象外の判定 | `agent_get_run.outcome` |
| 最終の検証の状態 | Run の記録の `validation_status` | `agent_get_run_observation.validation_status` |
| モデルの呼び出しの数 | Run の記録の `model_requests` | `agent_get_run_observation.usage.requests` |
| 旧版も検索したか | Run の記録の `superseded_searches`・旧版を検索したケース | `tool_calls[].include_superseded`（真偽だけ） |
| 回答に使わなかった根拠 | CLI は返った根拠をすべて引用に数える | `evidence[]` は全件（`next_cursor`）、引用は `cited` |
| ツールごとの呼び出しの数 | Run の記録の `tool_calls` | `tool_calls[]`（呼び出しごと） |
| Run の取消 | 時間切れの取消 | `agent_cancel_run`（冪等。`result` を記録） |
| 業務 Agent の作成・公開 | 段 1 の `--create-agent` | 足さない（#3240 の Q4）。公開済みの業務 Agent を `--agent-id` で指定し、`agent_version` を記録する |
| `rag_search` の outcome・clarifications・insufficient_reason | 旧形式の対応と出所・診断 | 足さない（下の「意図して作り直した値」） |

回答の指標（`answer_correctness`・`requirement_coverage`・`claim_support_rate`）は、今の CLI が標準回答による評価を渡さないため、Agent の経路ではいつも測れない（`not_evaluable` に全ケースが入る）。

## 意図して作り直した値

### #3282: CLI の比較の要約の不具合の修正

段 1 の基線は、CLI の比較の要約の 2 つの不具合をそのまま固定していた。#3282 で CLI を直し、`summaries.json` の `expected.cli` のうち次の値だけを作り直した。採点（`cases/<id>.json` の `expected.scores`）・集計（`expected.metrics`）・分母（`expected.denominators`）は変えていない。

- `summary_markdown` の分類ごとの行（`<分類>: 回答・確認・保留の判断の正しさ`）: 要約の分類の `handling_correct_rate` を読むようにした。前は `metrics.response_decision_accuracy` を読んでいつも `-` だった。`all` / `completed` は `document_answerable` が 0.50、`clarification_required`・`environment_data_required`・`knowledge_missing`・`conflicting_sources` が 1.00、`passing` は `clarification_required` が 1.00（`uncategorized` は測れないので `-` のまま）。
- `summary.dangerous_case_count` と表の「危険な回答のケース」: 失敗の理由に `forbidden_action` が付いた成功のケースを数え、該当が無ければ 0 ではなく測れない（`null`・表は `-`）とした。前は今のケースの結果に無い項目（`forbidden_hits`）を数えていつも 0 だった。今の採点（六指標）は勧めてはいけない操作を測らないため、4 つの評価セットとも `null` / `-` になる。

### #3240 の段 2: 公開の Agent MCP だけで採点する（採点の版 `six_metrics_v1+agent_v2`）

CLI は Agent の画面の API（`POST /api/runs`・`GET /api/runs/{id}`）と Run の内部の記録をやめ、公開の MCP（`agent_chat`・`agent_get_run`・`agent_get_run_observation`・`agent_cancel_run`）の出力だけで採点する。段 1 の 14 ケースの Run を公開の出力にして再生した結果、六指標・集計の値（`metrics` の平均・`metric_case_counts`・内訳・合否）と分母は変わらず、採点の項目は次の 3 ケースの対応の出所だけが変わった（どれも `rag_search` の outcome を公開しないため。設計の §3）。

- `legacy-rag-search-outcome`: `observed_outcome` が conditional → answered、`outcome_source` が agent_rag_search → inferred（回答の文に問いが無く引用があるので answered と推定する。期待する対応に answered も含むので判断の正しさは変わらない）。
- `multi-turn-clarify-then-answer`: 1 往復目の `turn_results[0].outcome_source` が agent_rag_search → inferred（最後の行が問いなので推定も needs_clarification）。
- `unknown-outcome-vocabulary`: `outcome_source` が agent_rag_search → inferred（推定も answered）。

あわせて次を変えた。

- **承認の要るツールを呼ばなかった Run（#3240 の Q7）**: MCP の Run は承認の要るツールを呼ばずに答える（`waiting_approval` にならない）。成功に数えると必要な操作をしていない回答を採点するので、ケースの失敗（`error_type=AgentRunApprovalSkipped`）にした。ケース `approval-skipped-run` を足し、評価セット `all` に入れた（`all` のケースの数・失敗の数・`document_answerable` の数・`failed_case_ids` が 1 つずつ増えた）。
- **採点の版**: Agent の経路の `metrics_version` を `six_metrics_v1` → `six_metrics_v1+agent_v2` にした（RAG 自身の評価の版 `six_metrics_v1` は変えない）。
- **`expected.cli`**: 診断から `rag_search` 由来の `insufficient_reason` と、確認の質問で聞いた条件（`clarifications`）が無くなった。Run の記録に `agent_version`・`skipped_approval_tools`・`withheld_evidence_count`・`evidence_text_omitted`・`cancel_result` を足し、送った依頼は `run_requests`（`goal`）から `chat_requests`（`question`）にした。fixture の `thread_id` は契約の形（`thread_<32 桁の hex>`）にした。
- `cases/<id>.json` の `turns[].run`（Agent の内部の記録）は共有の fixture の `source` に移し、ケースは Run の ID だけを持つ。

## 基線を作り直すとき

採点を意図して変えたときと、CLI の出力の不具合を直したとき（`expected.cli` だけ。上の「意図して作り直した値」に記録する）だけ作り直す。今のコードで `replay` の結果（`case_result`・`suite_result`）を `expected` に書き、変わった値を PR で一つずつ説明する。移設のために作り直さない（移設の後の実装を、この基線で確かめる）。Agent の公開の出力を意図して変えたときは、Agent の `UPDATE_AGENT_EVALUATION_BASELINE=1 uv run pytest tests/test_evaluation_baseline_observation.py` で共有の fixture を書き直し、この基線の採点が変わらないことを確かめる。
