# Golden Set Evaluation

`golden-set.example.json` は `POST /api/evaluation/run` に渡す評価ファイルのテンプレートです。
実データ投入後、各 `relevant_document_ids` を環境内の document id に置き換えて `golden-set.json` として管理します。

```bash
cp evaluation/golden-set.example.json evaluation/golden-set.json
curl -X POST http://localhost:8000/api/evaluation/run \
  -H 'Content-Type: application/json' \
  -d @evaluation/golden-set.json
```

CI / staging gate では `thresholds` を必ず設定し、レスポンスの `passed=false`、`error_count>0`、または `threshold_failures` 非空を失敗条件にします。`groundedness_pass_rate` は回答が citation context に支えられている case の割合で、検索命中だけでなく根拠付き回答の品質も gate できます。`failure_reason_counts` は case 単位の失敗理由分布で、`retrieval_miss`、`partial_recall`、`answer_keyword_miss`、`low_groundedness` などから次に調整すべき RAG stage を切り分けます。

単発の `/run` でも任意の `rag_overrides` を指定でき、RRF 定数、context window、Oracle vector target accuracy などの非 secret RAG 設定を一時的に上書きできます。標準値を固める前の staging smoke では、`golden-set.example.json` のように明示した値で gate を固定しておくと、環境変数差分による評価ぶれを追いやすくなります。

複数設定の比較には `POST /api/evaluation/compare` を使います。`compare.example.json` のように `experiments` に `mode`、`top_k`、`rerank_top_n`、`filters`、必要に応じて `rag_overrides` の候補を並べると、同じ golden set で評価し、`ranking_metric` に基づく `best_experiment_id` と順位付き結果を返します。`rag_overrides` では RRF 定数、query expansion、context window、context diversity、隣接 context、context compression、Oracle vector target accuracy を一時的に上書きできます。AutoRAG 的な調整では、まず `recall_at_k` で retrieval 候補を絞り、次に `mrr` / `groundedness_pass_rate` で context 構成・rerank・prompt の候補を比較します。

CI / nightly では CLI を使うと、評価結果 JSON を artifact として保存しつつ終了コードで gate できます。CLI は入力 JSON に `experiments` があれば compare request として検証し、未指定時の送信先も `/api/evaluation/compare` に切り替えます。compare の gate 判定は rank 1 の best experiment の metrics を使います。

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

`RAG_EVALUATION_API_BASE_URL`、`RAG_EVALUATION_RUN_API_URL`、`RAG_EVALUATION_COMPARE_API_URL`、`RAG_EVALUATION_API_URL`、`RAG_EVALUATION_TIMEOUT_SECONDS`、`RAG_EVALUATION_TENANT_ID`、`RAG_EVALUATION_USER_ID` でも指定できます。`--api-url` は最優先で、`--api-base-url` は入力形式に応じて `/api/evaluation/run` または `/api/evaluation/compare` を付与します。tenant/user の raw 値は CLI 出力には表示しません。

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
  --output ../evaluation/file-processing-report.json
```

出力の `staging_requirements` は、OCI Enterprise AI / Object Storage / Oracle 26ai / UI preview が必要な pending check を case 単位で列挙します。nightly workflow はこの gate を先に実行し、`file-processing-report.json` を artifact に保存します。staging で pending を残したくない場合は CLI の `--fail-on-pending`、または workflow dispatch の `fail_on_file_processing_pending=true` を使います。

実 staging 環境で pending check を閉じる場合は、Object Storage に fixture を保存し、一時 KB / document を作成して ingestion / search / chunk metadata / segment checkpoint / extraction artifact cache を検証する staging gate を実行します。結果 JSON は parser、segment、bbox、citation、canonical duplicate、artifact reuse の非機密 evidence と、retrieval recall / groundedness / ingestion p95 / bbox / citation traceability / element lineage / page hit / extraction page coverage / low confidence rate / failed segment rate の aggregate metrics、`threshold_results` だけを持ち、OCR 原文・chunk 本文・tenant/user secret は保存しません。staging で測定された threshold が未達の場合も report は `passed=false` になります。

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
  --cleanup
```

nightly workflow でも `run_file_processing_staging=true` を指定すると同じ staging gate を実行し、`file-processing-staging-report.json` を artifact に保存します。OCI Enterprise AI / Oracle / Object Storage の接続情報は runner 環境の `.env` または GitHub Actions の secret/variable から注入してください。

これらは local CI で parser routing、chunk lineage、manifest/asset 契約を安定検証するための小さな合成サンプルです。OCI Enterprise AI の OCR/reading order 品質、実スキャン PDF、実 Office レイアウト、実メール添付を gate する場合は、staging 用の非機密実データセットを追加し、同 manifest の scenario と required check に対応付けて nightly で評価してください。
