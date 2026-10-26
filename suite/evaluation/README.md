# suite/evaluation（`pr_suite_evaluation`）

製品横断の評価の CLI（`suite-evaluation`。#3240）。評価セットを製品の**公開の MCP の契約だけ**で流し、観測を保存して、製品の評価と同じ六指標で採点する。今の Target は業務 Agent（Agent の公開の MCP）。

## 置き場所と依存の向き

| 置き場所 | 持つもの |
|---|---|
| `rag/`・`nl2sql/`・`agent/` | 各製品の機能と、各製品自身の評価（RAG の評価ランナー・評価の job・評価の取り込みの CLI） |
| `suite/evaluation/` | 製品をまたぐ評価（この project）。独立の uv project で、常駐のサービスは持たない |
| `platform/packages/evaluation_scoring/` | 製品の評価と suite の評価が同じ意味で使う、決定的な採点の部品（`pr_evaluation_scoring`） |

- suite は platform の公開の package（`pr_evaluation_scoring`）だけに依存する。製品の Python の module・DB の表・保存ファイルを読まない（製品は `POST /api/mcp` を API キーで呼ぶ）。`tests/test_boundaries.py` が import を確かめる。
- platform は suite・製品を import しない。製品は suite を import しない。

## 構成

| 層 | module | 内容 |
|---|---|---|
| Dataset | `dataset.py` | 評価セット（製品の評価と同じ JSON）を読み、`file:<ファイル名>` を文書の対応表で配備先の `document_id` にし、内容の hash を取る |
| Target | `targets/agent_mcp.py` | 業務 Agent を `agent_chat`・`agent_get_run`・`agent_answer_confirmation`・`agent_get_run_observation`・`agent_cancel_run` で呼ぶ。会話の継続・回答の前の確認への答え・時間切れの取消 |
| Observation | `observation.py` | 往復ごとの外枠（状態・時間・参照・読んだ出力の版・失敗）と、公開の出力そのもの（`payload`） |
| Evaluator | `evaluators.py` | 観測から作った採点の入力（`views.py`）を、評価セットの期待と照らして採点する。指標ごとに scored / not_applicable / unevaluable と理由 |
| Experiment | `experiment.py` | 流す・採点する・保存した観測から採点し直す。評価セット・対象構成・採点の版と hash を記録する |
| Report | `report.py` | 集計（六指標・内訳・分母・合否）と、結果の比較の表 |

旧形式（#1305 より前の outcome の無い Run・#2738 より前の回答の節）の扱いは `targets/agent_legacy.py` にだけ置く。

## 使い方

```bash
cd suite/evaluation
uv sync --dev
export SUITE_EVALUATION_API_KEY=prak_...   # 使える製品に agent を含む API キー（引数に書かない）

# 1. 評価の資料を RAG に取り込み、文書の対応表を書く（RAG の評価の取り込みの CLI。rag/backend で）
#    uv run python -m app.rag.evaluation_corpus_cli ../evaluation/business-support/business-support.json \
#        --api-base-url http://127.0.0.1:8000 --output ../../tmp/eval/bs.resolved.json \
#        --document-map-output ../../tmp/eval/document-map.json ...

# 2. 業務 Agent に流して採点する
uv run suite-evaluation run ../../rag/evaluation/business-support/business-support.json \
    --document-map ../../tmp/eval/document-map.json \
    --agent-mcp-url http://127.0.0.1:8020/api/mcp --agent-id <公開済みの業務 Agent> \
    --guides ../../rag/evaluation/business-support/support-guides.json \
    --output ../../tmp/eval/d.json

# 3. 保存した観測を採点し直す（規則・期待を直したとき。モデル・製品を呼ばない）
uv run suite-evaluation score ../../tmp/eval/d.json \
    --dataset ../../rag/evaluation/business-support/business-support.json \
    --document-map ../../tmp/eval/document-map.json --output ../../tmp/eval/d.rescored.json

# 4. RAG 自身の評価の結果（A・C）と比べる
uv run suite-evaluation compare A=../../tmp/eval/a.json C=../../tmp/eval/c.json D=../../tmp/eval/d.json
```

結果のファイルは回答の本文と根拠の本文を含む。repo に commit せず、repo の直下の `./tmp/` などに置く。API キーは結果・ログに出さない。

## 採点の規則

指標は製品の評価の六指標（`required_evidence_recall`・`mrr`・`answer_correctness`・`requirement_coverage`・`claim_support_rate`・`response_decision_accuracy`）と同じ名前・同じ意味。平均は成功したケースのうち測れたものだけで、失敗したケースは分母に入らない。

- **ケースの失敗**（`status=error`・`case_error`）: Run の failed・cancelled・承認待ち・時間切れ（取り消す）・承認の要るツールを呼ばずに完了した Run（#3240 の Q7）・MCP を呼べない / 知らない `schema_version`。途中の往復で失敗したら、それまでの往復を採点しない（`error_stage=turn_<n>`、時間は往復の和）。
- **回答の前の確認**（`waiting_confirmation`）: 次の返答（評価セットの `turns[].reply`）を `agent_answer_confirmation` で答える（分かった条件の値と同じ名前の選択肢があれば選び、無ければ返答の文を自由入力にする）。確認の往復は「利用者に条件を確かめた」（needs_clarification）として採点する。次の返答が無ければ、確認で終わったケースとして Run を取り消す。
- **評価不能**（`metric_status.<指標>.status=unevaluable`。平均・分母に入れない。`denominators.metrics` に数と理由）:
  - `outcome_missing`: `outcome` の無い完了の Run。判断の正しさは測らず、引用も無ければ検索の指標も測らない（`--infer-missing-outcome` で旧形式の推定を選べる）。
  - `evidence_text_omitted`: 場所の条件に合う根拠の本文が省かれていて（`text_omitted`）、本文の条件を照合できない。
  - `evidence_withheld`: 見る人の今の権限で見られない根拠があり（`withheld_evidence_count`）、取れなかった根拠・1 位でない順位がその根拠のせいかを確かめられない。
  - `answer_judge_unavailable`: 標準回答はあるが、suite は回答の判定（LLM）を行わない。
- 閾値を付けた指標に評価不能のケースがあれば、測れたケースだけで合格にしない（`passed=null`）。

## 版と記録

- `metrics_version`: `six_metrics_v1+agent_v3`（`--infer-missing-outcome` では `+legacy_outcome` を足す）。
  - `agent_v1`: 段 1（rag の CLI。Agent の画面の API と Run の内部の記録）。
  - `agent_v2`: 段 2（rag の CLI。公開の Agent MCP の出力だけ。承認の要るツールを呼ばなかった Run を失敗にする）。
  - `agent_v3`: suite へ移した版。回答の前の確認に答える・`outcome` の無い Run と本文を省かれた根拠と見られない根拠を評価不能にする・Run を作れなかった往復にも `error_stage` を付ける。
- 結果の `provenance`: 評価セット（名前・解決した後の内容の sha256・ケースの id）、対象構成（MCP の URL・業務 Agent の ID・待ちの上限・読む出力の版と、その sha256）、採点（`metrics_version`・package の版・設定と sha256）。採点し直した結果は、観測を取ったときの評価セットを `observed_dataset` に残す。
- 基線: `tests/fixtures/agent_baseline/`（段 1・段 2 の合成の基線。差は fixture の README.md）。

## テスト

```bash
uv run pytest -n auto
uv run ruff format --check . && uv run ruff check . && uv run mypy src
```

Agent の MCP は `httpx.MockTransport` の台本（共有の fixture `platform/contracts/mcp/fixtures/agent-evaluation-baseline.json` の公開の出力と、テストの中の合成の出力）で再生し、実サービスは呼ばない。
