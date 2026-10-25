# 製品横断の評価の基線（#3240 の段 1）

今の比較評価の CLI（`app.rag.agent_evaluation_cli`）が Agent の Run を採点・集計した結果を、合成の入力で固定する。評価を `suite/evaluation/` へ移す（段 2〜5）ときは、同じ fixture から同じ値を出すことを確かめる。値を意図して変えるときは、採点の version（`metrics_version`）を上げ、差と理由を PR とこのファイルに書く。

内容はすべて合成のデータで、業務の内容を含まない（AGENTS.md「業務の内容は書かない」）。

## ファイル

| ファイル | 内容 | テスト |
|---|---|---|
| `cases/<id>.json` | 1 ケース（`case` は `EvaluationCase`）と、往復ごとの保存した Run の観測（`turns[]`: `observation`・`elapsed_ms`・`run`）。`expected.scores` は採点、`expected.cli` は今の CLI に固有の出力 | `tests/test_agent_evaluation_baseline.py` |
| `summaries.json` | 評価セット（ケースの並びと閾値）ごとの集計（`expected.metrics`）・分母（`expected.denominators`）・CLI の比較の要約（`expected.cli`） | 同上 |
| `agent_internal_dependencies.json` | 採点・記録が使う、公開の Agent MCP に無い観測の一覧。各項目は観測を消すと変わる項目を `witness` に持つ | 同上 |
| `answer_sections.json` | 回答の本文のうち主張に数える部分（`grounding_text`）。`rag_sections` は RAG 自身の回答の節、`legacy_agent_sections` は Agent の回答の節（#2738 の前と後） | `tests/test_answer_metrics.py`（RAG）・`tests/test_answer_metrics_legacy_agent.py`（Agent） |

## 保証すること

- **採点（`expected.scores`）**: ケースの結果のうち Agent の Run の ID と本文を含まない項目（六指標・引用の文書・正解の文書・取れなかった根拠・対応と出所・往復ごとの判断・失敗の理由・失敗の種類と段・時間）。移設の後も一致させる。
- **集計（`expected.metrics`）**: 六指標の平均（成功したケースのうち測れたものだけ）、`metric_case_counts`、`error_count`、区分・分類・種類・段の数・期待する判断ごとの内訳、閾値の判定（`threshold_failures`・`passed`）。
- **分母（`expected.denominators`）**: ケースの数・失敗の数・最後の往復の観測の種類ごとの数（completed / failed / timeout / cancelled / waiting_approval / api_error）、指標ごとの測れた数（`measured`）と、成功したのに測れなかった数（`not_evaluable`）。失敗は指標の分母に入らず、失敗が 1 つでもあれば `passed=false`。どの指標も測れなければ `passed=null`。
- **失敗の扱い**: failed・cancelled・waiting_approval・時間切れ（取り消す）・Run を作れない（HTTP の失敗）は、どれもケースの失敗（`case_error`）。複数往復の途中で失敗したら、それまでの往復を採点せずケースの失敗にする（`error_stage=turn_<n>`、時間は往復の和）。
- **旧形式の扱い**: 成果物 `answer` に outcome の無い Run（#1305 より前）は、最後の `rag_search` の outcome、それも無ければ推定（最終の検証の定型の注記を除いた最後の行が問いなら確認の質問、引用の無い回答は拒答）。語彙に無い outcome は使わない。
- **今の CLI に固有の出力（`expected.cli`）**: 往復の Run の ID、診断（`diagnostics.answer`）、失敗の文言、Run の記録（`RunRecord`）、Agent に送った依頼（既知の条件を足した最初の質問・`thread_id`）、取り消した Run。段 4 で adapter に移すと形が変わりうる。変えるときは差を記録する。

## 再生の条件

`tests/test_agent_evaluation_baseline.py` の `replay` が、Agent の公開の HTTP API を fixture の保存した Run で再生して、`evaluate` を呼ぶ。

- 往復の時間は `turns[].elapsed_ms`（往復の始まりを 0 秒、Run の終わりを elapsed_ms の時刻にする時計）。
- 時間切れの上限は 2 秒（`RUN_TIMEOUT_SECONDS`）。時間切れの Run（`observation=timeout`）は running のまま返し、待ちの時計は取得のたびに 1 秒進む。
- `observation=api_error` の往復は `POST /api/runs` が `http_status` の失敗を返す。
- 業務ガイドの条件の名前（`--guides`）は渡さない（既知の条件は条件の id で書く）。

## 公開の Agent MCP に無い観測

`agent_internal_dependencies.json` の一覧。段 2 で Agent の MCP の出力（`platform/contracts/mcp/agent-tools.json`）の拡張を設計する入力にする。公開の出力は `answer`・`status`・`thread_id`・`confirmation`・`sources[]`（title・location・excerpt）・`passage_marks`・`removed_passages`・`answer_warnings`・`tools_used` など。

| 観測 | 使う項目 | 公開の Agent MCP |
|---|---|---|
| 根拠の `document_id`・`chunk_id`（ツールの出力） | MRR・必要な根拠の取得率・引用の文書・`retrieval_miss` / `evidence_miss`・引用の重複の除去 | 無い |
| 根拠の構造の場所（頁・章節・シート・行） | 必要な根拠の取得率（場所の条件の付いた根拠） | 一部（`sources[].location` は文字列） |
| 成果物 `answer` の outcome | 対応・出所・判断の正しさ・拒答の判定・検索の指標の対象外の判定（MRR・根拠の分母） | 無い |
| `rag_search` の outcome・clarifications・insufficient_reason | 旧形式の対応と出所・診断 | 無い |
| 成果物 `answer_validation` の status | Run の記録の `validation_status` | 一部（警告・印はある） |
| `usage.requests` | Run の記録の `model_requests` | 無い |
| ツールの引数 `include_superseded` | Run の記録の `superseded_searches`・旧版を検索したケース | 無い |
| 回答に使わなかった根拠 | CLI は返った根拠をすべて引用に数える | 一部（`sources[]` は使った根拠だけ） |
| ツールごとの呼び出しの数 | Run の記録の `tool_calls` | 一部（`tools_used` は名前だけ） |
| 業務 Agent の作成・公開、Run の取消 | `--create-agent`・時間切れの取消 | 無い（ツールが無い） |

回答の指標（`answer_correctness`・`requirement_coverage`・`claim_support_rate`）は、今の CLI が標準回答による評価を渡さないため、Agent の経路ではいつも測れない（`not_evaluable` に全ケースが入る）。

## 意図して作り直した値

### #3282: CLI の比較の要約の不具合の修正

段 1 の基線は、CLI の比較の要約の 2 つの不具合をそのまま固定していた。#3282 で CLI を直し、`summaries.json` の `expected.cli` のうち次の値だけを作り直した。採点（`cases/<id>.json` の `expected.scores`）・集計（`expected.metrics`）・分母（`expected.denominators`）は変えていない。

- `summary_markdown` の分類ごとの行（`<分類>: 回答・確認・保留の判断の正しさ`）: 要約の分類の `handling_correct_rate` を読むようにした。前は `metrics.response_decision_accuracy` を読んでいつも `-` だった。`all` / `completed` は `document_answerable` が 0.50、`clarification_required`・`environment_data_required`・`knowledge_missing`・`conflicting_sources` が 1.00、`passing` は `clarification_required` が 1.00（`uncategorized` は測れないので `-` のまま）。
- `summary.dangerous_case_count` と表の「危険な回答のケース」: 失敗の理由に `forbidden_action` が付いた成功のケースを数え、該当が無ければ 0 ではなく測れない（`null`・表は `-`）とした。前は今のケースの結果に無い項目（`forbidden_hits`）を数えていつも 0 だった。今の採点（六指標）は勧めてはいけない操作を測らないため、4 つの評価セットとも `null` / `-` になる。

## 基線を作り直すとき

採点を意図して変えたときと、CLI の出力の不具合を直したとき（`expected.cli` だけ。上の「意図して作り直した値」に記録する）だけ作り直す。今のコードで `replay` の結果（`case_result`・`suite_result`）を `expected` に書き、変わった値を PR で一つずつ説明する。移設のために作り直さない（移設の後の実装を、この基線で確かめる）。
