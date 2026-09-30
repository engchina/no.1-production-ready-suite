# Golden Set Evaluation

`golden-set.example.json` は評価（`POST /api/evaluation/jobs/run`）に渡す評価ファイルのテンプレートです。
実データ投入後、各 `relevant_document_ids` を環境内の document id に置き換えて `golden-set.json` として管理します。

評価は job で動きます（#390）。投入すると `202` で job（`job_id`・`status=RUNNING`・`total_cases`）を返し、
`GET /api/evaluation/jobs/{job_id}` で進捗（`completed_cases` / `total_cases`・実行中のケース `current_case_id`）と、
終わったら結果（評価は `run_result`、比較は `compare_result`）を返します。実行中の job は
`POST /api/evaluation/jobs/{job_id}/cancel` で取り消せます。job は投入した利用者だけが見られます。

```bash
cp evaluation/golden-set.example.json evaluation/golden-set.json
curl -X POST http://localhost:8000/api/evaluation/jobs/run \
  -H 'Content-Type: application/json' \
  -d @evaluation/golden-set.json
# 返った job_id で、status が RUNNING でなくなるまで取得する
curl http://localhost:8000/api/evaluation/jobs/<job_id>
```

job 全体の時間の上限は `RAG_EVALUATION_JOB_TIMEOUT_SECONDS`（既定 3600 秒）です。1 ケースは回答生成の上限
（`RAG_ANSWER_TIMEOUT_SECONDS`、既定 300 秒）で打ち切り、時間切れになった工程を `error_stage` / `error_message` に
残して次のケースへ進みます。全体の上限に達したら、実行中のケースを打ち切り、残りのケースは実行せずに
`error_type=EvaluationTimeBudgetExceeded` の失敗として結果を返します（job は `SUCCEEDED`、評価は `passed=false`）。
同時に実行できる評価の job は 2 件までです。以前の同期の `POST /api/evaluation/run`・`/compare` は、外部から直接呼ぶ
利用のために残していますが、評価全体を 600 秒で打ち切ります（#383）。

## 評価の指標（#591）

各ケースは、回答エンジン（根拠付き回答。全体の既定の設定）で回答し、回答の記録（引用・根拠・実行記録）から
指標を求めます。評価は業務ビューを受け取りません（#301）。指標は「検索」「根拠」「回答」の 3 つの観点に整理した
9 つです。各指標は、その指標を測れるケースだけの平均で、対象の件数は `metric_case_counts` に返します。対象の
ケースが無い指標は `null` です（0 と区別します）。失敗したケースは `error_count` で数え、指標の平均には入れません。

| 観点 | 指標 | 意味 | 対象のケース |
|---|---|---|---|
| 検索 | `context_recall` | 正解の文書のうち、回答の根拠に取れた割合 | `relevant_document_ids` のあるケース |
| 検索 | `mrr` | 最初の正解の文書が何番目の根拠に出たか（逆数の平均） | 同上 |
| 根拠 | `faithfulness` | 回答の語句のうち、根拠の本文に含まれる割合（決定論の近似） | 拒答していないケース |
| 根拠 | `citation_traceability_coverage` | 引用を文書・ページ・要素までたどれる割合 | 引用のあるケース |
| 根拠 | `claim_support_rate` | 根拠のない主張・根拠と矛盾する主張が無いケースの割合（主張ごとの監査） | `standard_answer` のあるケース |
| 回答 | `answer_keyword_hit_rate` | 期待する語をすべて含む回答の割合 | `expected_answer_keywords` のあるケース |
| 回答 | `refusal_accuracy` | 答えるべき質問に答え、答えるべきでない質問に答えなかった割合 | すべてのケース |
| 回答 | `requirement_coverage` | 標準回答の必要な項目に、回答が対応した割合 | `standard_answer` のあるケース |
| 回答 | `answer_pass_rate` | 4 軸（正確性・網羅性・根拠との整合性・生成品質）で 16 / 20 点以上、かつ監査を終えた割合 | `standard_answer` のあるケース |

- `answerable: false` のケース（資料に答えが無い質問）は、拒答の正しさだけを測ります。`answerable` を省略したときは、
  `relevant_document_ids`・`expected_answer_keywords`・`standard_answer` のどれも無いケースを答えるべきでない質問と
  みなします。
- `standard_answer` のあるケースは、回答を標準回答と LLM で比較します（LLM を複数回呼ぶため時間がかかります）。
  軸ごとの理由・主張ごとの判定は、`trace_id` の回答の記録に保存します。比較できなかったケース（時間切れ・入力の
  上限など）は指標に入れず、`answer_evaluation_error` として数え、評価を合格にしません。
- 削除した指標（`precision_at_k`・`recall_at_k`・`groundedness_pass_rate`・`context_precision`・`response_relevancy`・
  `noise_sensitivity`・`bbox_citation_coverage`・`element_lineage_coverage`・`content_kind_hit_rate`・`section_coverage`）
  の `thresholds` は受け付けません（422）。ケースの `expected_content_kind`・`expected_section_paths` と、評価の
  `mode`・`rerank_top_n` は、以前の評価ファイルを読めるように無視します（比較の experiment では受け付けません）。
  保存済みの評価の結果に残る古い指標は、読み込み時に捨てます（画面は削除した指標・基準の名前を原文のまま出します）。

CI / staging gate では `thresholds` か評価の基準（`suite`。`standard`＝標準（既定）/ `strict`＝厳格）を使い、
レスポンスの `passed=false`、`error_count>0`、または `threshold_failures` 非空を失敗条件にします。閾値は、測れた
指標だけに適用します（標準回答の無い golden set では、標準回答が必要な指標を判定しません）。`failure_reason_counts`
は case 単位の失敗理由分布で、`retrieval_miss`・`partial_recall`・`unexpected_refusal`・`unexpected_answer`・
`answer_keyword_miss`・`low_groundedness`・`unsupported_claim`・`missing_content`・`answer_failed` などから、
次に調整すべき工程（検索・根拠・回答）を切り分けます。

単発の `/run` でも任意の `rag_overrides` を指定でき、回答エンジンの設定（`query_strategy`・`answer_flow`・
`neighbor_child_count`・`rerank_enabled`）と、RRF 定数（`rrf_k`）・同じ group から足す child の上限
（`context_group_max_chunks`）・Oracle vector target accuracy を一時的に上書きできます。

複数設定の比較には `POST /api/evaluation/jobs/compare` を使います（進捗の件数は experiment × ケース）。`compare.example.json` のように `experiments` に `top_k`、`filters`、必要に応じて `rag_overrides` の候補を並べると、同じ golden set で評価し、`ranking_metric`（既定 `context_recall`）に基づく `best_experiment_id` と順位付き結果を返します。調整では、まず `context_recall` / `mrr` で質問拡張戦略・rerank・近傍 child 数の候補を絞り、次に `refusal_accuracy` と、標準回答のある golden set の `answer_pass_rate` / `claim_support_rate` で回答生成フローを比較します。nightly（`rag-evaluation-nightly.yml`）は `compare.example.json` を使い、標準回答による比較（LLM）は行いません。

CI / nightly では CLI を使うと、評価結果 JSON を artifact として保存しつつ終了コードで gate できます。CLI は評価の job を投入し、終わるまで状態を取得して（`--poll-interval`、既定 5 秒）、結果を `{"data": 結果, "job": job の状態}` として保存します。入力 JSON に `experiments` があれば compare request として検証し、未指定時の送信先も `/api/evaluation/jobs/compare` に切り替えます。compare の gate 判定は rank 1 の best experiment の metrics を使います。

```bash
cd backend
uv run python -m app.rag.evaluation_cli \
  ../evaluation/golden-set.json \
  --api-base-url http://localhost:8000 \
  --output ../evaluation/evaluation-result.json

uv run python -m app.rag.evaluation_cli \
  ../evaluation/compare.example.json \
  --api-base-url https://<staging-host> \
  --output ../evaluation/evaluation-compare-result.json
```

検索 latency / p95 gate には `search-load.example.json` を使います。`cases`、`repeat`、`concurrency`、`thresholds` を定義し、`/api/search` の client/server p50/p95、error rate、`diagnostics.stream_stage_timings` の stage p95 を artifact 化します。結果 JSON と trend JSON には query / answer / context 原文を残しません。

```bash
cd backend
uv run python -m app.rag.search_load_cli \
  ../evaluation/search-load.example.json \
  --api-base-url https://<staging-host> \
  --output ../evaluation/search-load-result.json \
  --trend-output ../evaluation/search-load-trend.json
```

終了コード:

- `0`: gate 成功。
- `1`: API は応答したが評価 threshold、search load threshold、または error rate が gate 条件を満たさない。
- `2`: golden set / search load scenario ファイルや CLI 引数が不正。
- `3`: 評価 CLI で API 接続、HTTP 応答、レスポンス形式の問題が起きた。

`RAG_EVALUATION_API_BASE_URL`、`RAG_EVALUATION_RUN_API_URL`、`RAG_EVALUATION_COMPARE_API_URL`、`RAG_EVALUATION_API_URL`、`RAG_EVALUATION_TIMEOUT_SECONDS`（job の終わりを待つ秒数。既定 3900 秒。job の既定の上限 3600 秒で打ち切った結果を受け取れるよう、それより長く待つ。超えたら job を取り消して終了コード 3 を返す。#390）、`RAG_EVALUATION_POLL_INTERVAL_SECONDS`、`RAG_EVALUATION_TENANT_ID`、`RAG_EVALUATION_USER_ID` でも指定できます。`--api-url` は最優先で、`--api-base-url` は入力形式に応じて `/api/evaluation/jobs/run` または `/api/evaluation/jobs/compare` を付与します。`--api-url` や `RAG_EVALUATION_*_API_URL` に以前の同期 API の URL（`/api/evaluation/run`・`/compare`）を指定していても、job の API に読み替えます。nightly（`RAG Evaluation Nightly`）は、workflow の timeout に収まるよう `RAG_EVALUATION_TIMEOUT_SECONDS=900` で待ちます。tenant/user の raw 値は CLI 出力には表示しません。

`RAG_SEARCH_LOAD_API_BASE_URL`、`RAG_SEARCH_LOAD_API_URL`、`RAG_SEARCH_LOAD_TIMEOUT_SECONDS`、`RAG_SEARCH_LOAD_TENANT_ID`、`RAG_SEARCH_LOAD_USER_ID` でも検索 load CLI を指定できます。GitHub Actions の `RAG Evaluation Nightly` workflow は evaluation trend と search-load trend を同じ `rag-evaluation-nightly` artifact に保存します。`workflow_dispatch` の `search_load_path` を空文字にすると search load gate だけを skip できます。

## File Processing Golden Fixtures

`docs/evaluation/file-processing-golden-set.json` は PDF / image / Office / HTML / Markdown / TSV table / email / duplicate / corrupted file / unsupported audio の取込品質を追跡する manifest です。各 case は `scenario`、期待 parser profile、期待 chunk template、必須 check を持ち、`evaluation/file-processing-fixtures/` のサンプルファイルを参照します。音声は承認済みの文字起こし経路ができるまで `unsupported_audio` として明示的に skip し、parser warning と `audio_transcription_not_configured` reason を gate します。

同梱 fixture は標準ライブラリだけで再生成できます。

```bash
python3 scripts/generate_file_processing_fixtures.py
```

local CI では backend から contract gate を実行できます。出力 JSON には OCR 原文や chunk 本文を含めず、case ごとの parser/template/check 結果、manifest の全 metric に対する `metric_summary`、および manifest `thresholds` を評価した `threshold_results` だけを保存します。各 metric は `measured` / `partial` / `requires_staging` を明示し、staging が必要な metric を local gate で未検証のまま成功扱いしません。local では parser fallback 率、表 QA、element lineage、低信頼文書率、失敗 segment 率などを測定し、OCI Enterprise AI が必要な page coverage / OCR / reading order は staging pending として残します。local で測定できる threshold が未達の場合は CLI が exit `1` を返します。

```bash
cd backend
uv run python -m app.rag.file_processing_golden_cli \
  ../docs/evaluation/file-processing-golden-set.json \
  --output ../evaluation/file-processing-report.json \
  --trend-output ../evaluation/file-processing-trend.json
```

出力の `staging_requirements` は、OCI Enterprise AI / Object Storage / Oracle 26ai / UI preview が必要な pending check を case 単位で列挙します。`--trend-output` は `file-processing-trend.json` として、parser fallback rate、表 QA、page hit、bbox / preview addressability、source/backend coverage、real-world staging dataset policy summary、threshold status、result hash だけを含む非機密 trend snapshot を保存します。case detail、fixture path、OCR 原文、chunk 本文、検索 query / answer は含めません。nightly workflow はこの gate を先に実行し、`file-processing-report.json` と `file-processing-trend.json` を artifact に保存します。前回の非機密 trend を baseline として保持している場合は、trend regression gate で table QA / page hit / bbox / fallback rate / real-world policy / ingestion p95 などの退化を CI で止められます。

```bash
cd backend
uv run python -m app.rag.file_processing_trend_cli \
  ../evaluation/file-processing-trend.json \
  --baseline ../evaluation/baselines/file-processing-trend.json \
  --output ../evaluation/file-processing-trend-regression.json
```

staging で pending を残したくない場合は CLI の `--fail-on-pending`、または workflow dispatch の `fail_on_file_processing_pending=true` を使います。

実 staging 環境で pending check を閉じる場合は、Object Storage に fixture を保存し、一時 KB / document を作成して ingestion / search / chunk metadata / segment checkpoint / extraction artifact cache を検証する staging gate を実行します。結果 JSON は parser、segment、bbox、citation、canonical duplicate、artifact reuse の非機密 evidence と、retrieval recall / groundedness / ingestion p95 / bbox / citation traceability / element lineage / page hit / extraction page coverage / low confidence rate / failed segment rate の aggregate metrics、`threshold_results` だけを持ち、OCR 原文・chunk 本文・tenant/user secret は保存しません。staging report には `adapter_golden_gate` と `object_storage_artifact_chain` も含まれ、Docling / Unstructured の enabled/installed 表示ではなく同一 golden set の table QA / page hit / fallback / bbox / source coverage / contract 結果と、Object Storage put/get roundtrip、ingestion 後の full/segment artifact readback、artifact identity metadata 検証、segment artifact reuse を promotion blocker として扱います。artifact 本体は `raw_text` を含み得ますが、staging report / trend には readable/identity/count/byte 数だけを残し、OCR 原文は audit payload へ出しません。`--parser-adapter-contract-strict` は manifest の `fixture_root` と正向きの schema-remap `cases[].fixture` を contract runner に渡し、selected adapter ごとに少なくとも 1 件の passed remap case を要求します。インストール済み adapter が実 package + 実 fixture で `StructuredExtraction` へ remap できた証跡として `case_id` hash / `scenario` / parser backend / adapter import・distribution・version / schema count / reason code を残し、distribution/version metadata や schema-remap 証跡がない場合は `adapter_distribution_name_missing`、`adapter_package_version_missing`、`adapter_schema_remap_evidence_missing` で promotion blocker にします。corrupted / unsupported fixture は adapter smoke ではなく staging の safe-error / warning taxonomy gate で検証します。`staging_dataset_policy` が設定されているのに promotion 必須でない場合は `staging_dataset_policy_not_required`、必須 policy の実データ数 / source kind / scenario / 非機密レビュー / fixture 隔離が未達の場合は `staging_dataset_policy_failed` として止めます。blocker は件数と不足 source kind / scenario だけを出し、real-world case id や fixture path は出しません。staging で測定された threshold が未達の場合も report は `passed=false` になります。

外部依存へ接続する前に、まず preflight で OCI / Oracle / Enterprise AI / Object Storage の必須設定だけを確認できます。preflight 出力も secret 値を含みません。

```bash
cd backend
uv run python -m app.rag.file_processing_staging_cli \
  ../docs/evaluation/file-processing-golden-set.json \
  --preflight-only \
  --output ../evaluation/file-processing-staging-preflight.json
```

```bash
cd backend
uv run python -m app.rag.file_processing_staging_cli \
  ../docs/evaluation/file-processing-golden-set.json \
  --output ../evaluation/file-processing-staging-report.json \
  --trend-output ../evaluation/file-processing-staging-trend.json \
  --cleanup
```

nightly workflow でも `run_file_processing_staging=true` を指定すると同じ staging gate を実行し、`file-processing-staging-report.json` と `file-processing-staging-trend.json` を artifact に保存します。staging trend は retrieval recall、表 QA、page hit、bbox / preview addressability、adapter contract、adapter golden gate、Object Storage artifact chain、segment artifact reuse、runtime check status、real-world dataset policy coverage を時系列比較するための非機密 summary で、case detail や gate evidence は含めません。staging 昇格では `--require-promotion-ready` を付けると、baseline が promotion ready だったのに current が blocker 付きへ戻る退化も失敗扱いにできます。promotion gate は閾値を緩める変更だけでなく、`required_for_promotion=false`、`pending_checks_block_promotion=false`、必須 runtime check の削除も blocker にします。

```bash
cd backend
uv run python -m app.rag.file_processing_trend_cli \
  ../evaluation/file-processing-staging-trend.json \
  --baseline ../evaluation/baselines/file-processing-staging-trend.json \
  --output ../evaluation/file-processing-staging-trend-regression.json \
  --require-promotion-ready
```

GitHub Actions では `file_processing_trend_baseline_path` と `file_processing_staging_trend_baseline_path` を指定すると、同じ比較結果を `file-processing-trend-regression.json` / `file-processing-staging-trend-regression.json` として artifact に保存し、退化時に workflow を失敗させます。OCI Enterprise AI / Oracle / Object Storage の接続情報は runner 環境の `.env` または GitHub Actions の secret/variable から注入してください。

これらは local CI で parser routing、chunk lineage、manifest/asset 契約を安定検証するための小さな合成サンプルです。OCI Enterprise AI の OCR/reading order 品質、実スキャン PDF、実 Office レイアウト、実メール添付を gate する場合は、staging 用の非機密実データセットを追加し、同 manifest の scenario と required check に対応付けて nightly で評価してください。staging 用 manifest には `staging_dataset_policy` を入れると、real-world case 数、source kind / scenario coverage、非機密レビュー、fixture 隔離を manifest validation と promotion blocker の両方で強制できます。real-world case は synthetic fixture の流用ではなく、`fixture_kind: "real_world"`、`data_sensitivity: "non_sensitive"`、`reviewed_for_public_ci: true` を持ち、既定では `staging/` 配下の fixture を参照する必要があります。

```json
{
  "staging_dataset_policy": {
    "required_for_promotion": true,
    "min_real_world_cases": 6,
    "required_source_kinds": ["pdf", "office", "html", "email", "image"],
    "required_scenarios": [
      "scanned_pdf_ocr",
      "two_column_pdf_reading_order",
      "japanese_docx_layout",
      "japanese_pptx_slides",
      "japanese_xlsx_sheets",
      "email_thread_headers"
    ],
    "required_fixture_prefix": "staging/"
  }
}
```
