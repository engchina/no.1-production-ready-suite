# デプロイメント

## ローカル開発

```bash
cd backend
cp .env.example .env
uv sync   # 外部 parser は services/parsers/<name> の独立サービスで動く(backend には載せない)。
          # 単一 adapter をローカルで試す場合のみ per-adapter extra(例: `uv sync --extra docling`)。
          # marker と unstructured は pillow 系で共存不可のため同時 install はできない。
uv run uvicorn app.main:app --reload

cd ../frontend
cp .env.example .env.local
npm ci
npm run dev
```

Docker Compose:

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

backend は OCI / Oracle 接続情報を前提に起動し、`/u01/production-ready-rag` を永続ボリュームにする。
コンテナ healthcheck は `/api/ready` を使う。`oci_common`、`enterprise_ai`、`genai`、`oracle`、`object_storage` の設定グループを確認する。
`ENVIRONMENT=production` では `audit_context_salt` も確認し、`AUDIT_CONTEXT_HASH_SALT` を必須にする。すべて `ok` のときだけ 200 になり、`missing`、`invalid`、`missing_credentials`、`wallet_not_found` が含まれる場合は 503 になる。

## 本番構成

推奨:

- Frontend: Vite build artifact を配信する nginx container を OCI Container Instances または OKE に配置。
- Backend: FastAPI + Uvicorn/Gunicorn container を OKE に配置。
- Storage: OCI Object Storage。
- DB: Oracle 26ai。RAG チャンクは `VECTOR(1536, FLOAT32)`。
- LLM/VLM: OCI Enterprise AI。
- Embedding/Rerank: OCI Generative AI。
- Observability: Prometheus、OpenTelemetry、Langfuse gateway。`TRACE_EXPORT_HTTP_ENDPOINT` を設定すると、脱機密化済み RAG span event を非同期 HTTP JSON で転送する。
- Secret: `.env` から環境変数として注入。
- Audit: `app.audit` の `rag_search_audit` / `rag_ingestion_audit` 構造化ログをログ基盤へ転送し、必要に応じて Oracle audit table に永続化する。`X-Tenant-ID` / `X-User-ID` は raw 値を保存せず hash 化し、`AUDIT_CONTEXT_HASH_SALT` は `.env` から注入する。

backend container は production entrypoint として Gunicorn + `uvicorn.workers.UvicornWorker` を使う。worker 数と timeout は `WEB_CONCURRENCY`、`GUNICORN_TIMEOUT`、`GUNICORN_GRACEFUL_TIMEOUT`、`GUNICORN_KEEP_ALIVE`、listen port は `PORT` で調整する。local 開発だけ `uvicorn app.main:app --reload` を使い、本番では `/api/ready` と golden set gate で昇格判定する。

frontend container は lockfile ベースの `npm ci` で build し、runtime stage では Vite の `dist/` を nginx で静的配信する。`/api/*` は `BACKEND_URL` へリバースプロキシし、SSE のため proxy buffering は無効化する。
frontend build は外部 font service に依存せず、CSS の日本語第一 font stack で表示する。これにより CI / OKE build が Google Fonts などへの外向き通信に依存しない。

## リリース前チェック

Pull Request と `main` への push では `.github/workflows/ci.yml` が以下の品質門を実行する。ローカルで先に確認する場合も同じコマンドを使う。

backend:

```bash
cd backend
uv run ruff check .
uv run black --check .
uv run mypy .
uv run pytest --cov=app
uv run bandit -r app
uv run pip-audit
```

frontend:

```bash
cd frontend
npm run lint
npm run typecheck
npm audit --audit-level=moderate
npm run build
```

container:

```bash
docker compose config
```

RAG 品質:

```bash
cp evaluation/golden-set.example.json evaluation/golden-set.json
# document id と期待キーワードを対象環境に合わせて編集してから実行する
curl -X POST http://localhost:8000/api/evaluation/run \
  -H 'Content-Type: application/json' \
  -d @evaluation/golden-set.json
```

CI / nightly / staging 昇格では curl ではなく評価 gate CLI を使い、レスポンス JSON を artifact として保存する。

```bash
cd backend
uv run python -m app.rag.evaluation_cli \
  ../evaluation/golden-set.json \
  --api-base-url https://<staging-host> \
  --output ../evaluation/evaluation-result.json \
  --trend-output ../evaluation/evaluation-trend.json

uv run python -m app.rag.evaluation_cli \
  ../evaluation/compare.example.json \
  --api-base-url https://<staging-host> \
  --output ../evaluation/evaluation-compare-result.json \
  --trend-output ../evaluation/evaluation-compare-trend.json

uv run python -m app.rag.search_load_cli \
  ../evaluation/search-load.example.json \
  --api-base-url https://<staging-host> \
  --output ../evaluation/search-load-result.json \
  --trend-output ../evaluation/search-load-trend.json

uv run python -m app.rag.file_processing_golden_cli \
  ../docs/evaluation/file-processing-golden-set.json \
  --output ../evaluation/file-processing-report.json

uv run python -m app.rag.parser_adapter_contract_cli \
  --output ../evaluation/parser-adapter-compatibility.json

uv run python -m app.rag.parser_adapter_contract_cli \
  --strict \
  --manifest ../docs/evaluation/file-processing-golden-set.json \
  --source-kind pdf \
  --source-kind html \
  --source-kind email \
  --source-kind office \
  --source-kind image \
  --output ../evaluation/parser-adapter-compatibility-strict.json

uv run python -m app.rag.file_processing_staging_cli \
  ../docs/evaluation/file-processing-golden-set.json \
  --output ../evaluation/file-processing-staging-report.json \
  --cleanup \
  --parser-adapter-contract-strict
```

`evaluation/golden-set.example.json`、`evaluation/compare.example.json`、`evaluation/search-load.example.json` は API / CLI request schema に合うテンプレートとしてテストで検証する。運用する `evaluation/golden-set.json` には `thresholds` を含める。複雑文書の case では `expected_content_kind` と `expected_section_paths` を指定し、`content_kind_hit_rate` / `section_coverage` threshold で document-level recall だけでは拾えない表・図・コード・メール・章節 lineage の退化を止める。compare の `experiments[].rag_overrides` では RRF 定数、query expansion、context window、context diversity、隣接 context、context compression、Oracle vector target accuracy を一時的に上書きし、staging の golden set で安全に比較できる。評価 CLI は入力 JSON に `experiments` がある場合は compare request とみなし、`--api-base-url` から `/api/evaluation/compare` を自動で選ぶ。search load CLI は `cases`、`repeat`、`concurrency`、`thresholds` を受け取り、`/api/search` の client/server p50/p95、error rate、`diagnostics.stream_stage_timings` の stage p95 を query / answer 原文なしで artifact 化する。file-processing golden CLI は local parser / chunk / citation 契約に加えて parser fallback、低信頼文書率、失敗 segment 率などの取込品質 metric を検証し、OCI Enterprise AI / Oracle / Object Storage を伴う確認が残る場合は `passed: true` でも `promotion_ready: false` と `promotion_blockers` を artifact に出す。parser adapter compatibility CLI は Docling / Marker / Unstructured がインストール済みの環境だけ実 adapter remap smoke を実行し、未導入・未選択 adapter は status として記録する。artifact は source kind、status、parser backend、schema count、source-kind contract reason code だけを含み、抽出本文は保存しない。PDF/image では page lineage、image では bbox/asset lineage、HTML/email/Office では semantic/header/slide/sheet/table lineage も contract として見るため、単に element が 1 件返っただけでは合格にしない。本番昇格判定では `passed` だけでなく `promotion_ready` を必ず確認し、`pending_staging_checks` や `extraction_page_coverage` などの staging 必須 threshold が残る場合は `rag-file-processing-staging` を実行するか、CI で `fail_on_file_processing_pending=true` を指定して失敗扱いにする。file-processing staging CLI は `report.passed` でも promotion blocker が残る場合は exit `1` を返すため、Object Storage artifact cache などの必須 runtime check が skip された環境を CI で止められる。`file-processing-trend` CLI は保存済みの trend baseline と current trend を比較し、table QA / page hit / bbox / preview addressability / fallback rate / ingestion p95 / blocker count の退化を exit `1` で止める。どの CLI も gate 失敗時は exit `1`、入力不備は exit `2` を返す。tenant 分離を検証する評価では `--tenant-id` / `RAG_EVALUATION_TENANT_ID` または `RAG_SEARCH_LOAD_TENANT_ID` を設定する。tenant/user の raw 値は CLI 出力に表示しない。GitHub Actions の `RAG Evaluation Nightly` workflow は `RAG_EVALUATION_API_BASE_URL` repository variable が未設定なら skip し、設定済みなら evaluation result/trend と search-load result/trend を同じ artifact としてアップロードする。search load だけを外す場合は `workflow_dispatch` の `search_load_path` を空文字にする。

`RAG Evaluation Nightly` は API base URL が未設定でも `parser-adapter-compatibility.json` と file-processing artifact を先に作る。通常実行では外部 parser package が未導入でも status を記録するだけだが、`run_file_processing_staging=true` かつ `require_real_world_file_processing_manifest=true` の production staging では workflow が strict adapter contract を自動的に有効化する。単独 smoke を厳格化したい場合は `workflow_dispatch` で `parser_adapter_contract_strict=true` を指定して同じ経路を使える。strict が有効な場合、workflow は `install_parser_adapters` が false でも共存可能な docling + unstructured の per-adapter extra(`--extra docling --extra unstructured`)を同期し(marker は pillow 系で unstructured と共存不可のため in-process smoke から除外し、各 parser サービス側で検証する)、parser adapter contract CLI へ `--manifest ../${file_processing_manifest_path}` と `--strict`、file-processing staging CLI へ `--parser-adapter-contract-strict` を渡す。必要に応じて `parser_adapter_contract_source_kinds=pdf,html,email,office,image` のように対象 source kind を絞る。strict mode は CLI の `--strict` と同じく adapter backend を `auto` 相当にし、Docling / Marker / Unstructured の feature flag を有効化した runtime snapshot で、manifest の `fixture_root` と `adapter_schema_remap=true` が付いた `cases[].fixture` を case 単位に実 package へ通して schema remap を検証する。file-processing staging でも同じ strict settings を preflight、実 ingestion/search client、`adapter_contract_coverage` artifact に使うため、runtime が local のままなのに adapter contract だけ合格する状態を避ける。`parser-adapter-compatibility.json` と staging payload の `parser_adapter_contract` は fixture root / fixture file name / case id を hash label に置き換えるため、非機密 real-world manifest を使っても CI artifact から顧客文書名を読めない。`--preflight-only` でも strict 時は同じ manifest fixture contract を実行し、installed/active だけで schema remap 証跡がない adapter を先に止める。production 昇格では synthetic golden manifest だけでなく、`staging_dataset_policy` 付きの非機密 real-world manifest を `file_processing_manifest_path` に指定し、`fixture_kind=real_world`、`data_sensitivity=non_sensitive`、`reviewed_for_public_ci=true`、`staging/` fixture 隔離を manifest validation で通す。workflow の `require_real_world_file_processing_manifest` は既定 true で、staging 実行時に `rag-file-processing-staging --require-real-world-policy` を渡すため、synthetic-only manifest は real OCI / Oracle client 作成前に失敗する。staging payload の `staging_dataset_policy` は manifest 合規件数に加えて `executed_real_world_case_count`、`executed_compliant_real_world_case_count`、`missing_executed_source_kinds`、`missing_executed_scenarios` も返すため、real-world case を宣言しただけで本実行から漏れた場合は promotion blocker になる。`file_processing_trend_baseline_path` / `file_processing_staging_trend_baseline_path` を指定すると、current trend と baseline trend を比較し、`file-processing-trend-regression.json` / `file-processing-staging-trend-regression.json` を artifact に保存する。staging trend 比較では `promotion_ready` だけでなく、adapter contract の scenario set / passed scenario / missing scenario / blocking scenario、backend/source passed pair、backend/scenario passed pair、backend/source/status bad count、backend/source passed count、warning code count、blocking failure reason count、executed real-world case 数、compliant executed case 数、実行済み source kind / scenario 数、missing executed source/scenario、execution error count の退化も blocker にする。package missing、adapter fallback、fixture missing、schema remap empty、trend regression などの blocking failure が残れば workflow を失敗させる。full matrix では Docling/email のような非 routing 対象 pair は `unsupported` として記録するだけだが、CLI で `--strict --backend docling --source-kind email` のように backend/source を明示した場合は虚偽の対応表明として blocking failure にする。

## 運用パラメータ

- `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP`: 通常の構造認識・再帰文字・親子階層・固定長では、既定の 800 / 120 から評価する。設定可能範囲は chunk size が 200-32,000 文字、overlap が 0-8,000 文字で、overlap は chunk size 未満にする。
- 見出し単位・ページ単位では、見出し/ページを第一境界として保つため 32,000 / 0 を推奨する。32,000 文字は長大な単位だけを同じ境界内で再分割する安全上限であり、chunk を常に大きくする目標値ではない。[Cohere Rerank 4](https://docs.oracle.com/en-us/iaas/Content/generative-ai/cohere-rerank-4-0.htm) の context は 32,000 token だが、文字数上限と token 上限は同一ではない。
- `RAG_CONTEXT_WINDOW_CHARS`: LLM の入力制限と citation 数のバランスで決める。レスポンスの citations は実際に context へ入った chunk だけになるため、golden set で必要な引用数を確認して調整する。
- `RAG_CONTEXT_DIVERSITY_LAMBDA`: rerank anchor の MMR 風 diversity 重み。既定 1.0 は rerank 順を維持する。0.2-0.8 を golden set で比較し、`diagnostics.context_diversified_count`、recall、answer keyword hit、context window からの引用落ちを見て調整する。
- `RAG_CONTEXT_GROUP_EXPANSION_ENABLED` / `RAG_CONTEXT_GROUP_MAX_CHUNKS`: `chunk_group_id` が同じ sibling chunk を生成 context へ追加する。既定は無効。表・箇条書き・長い章節を複数 chunk に分割した文書で `diagnostics.context_group_expanded_count`、citation 数、context 文字数、groundedness、p95 latency を golden set / staging smoke で確認してから有効化する。
- `RAG_CONTEXT_NEIGHBOR_WINDOW`: rerank anchor の同一文書前後 chunk を生成 context へ追加する window。既定 0 は無効。1-2 から試し、`diagnostics.context_expanded_count`、citation 数、LLM context 文字数、p95 latency を golden set / staging smoke で確認してから広げる。
- `RAG_CONTEXT_COMPRESSION_ENABLED` / `RAG_CONTEXT_COMPRESSION_MAX_SENTENCES` / `RAG_CONTEXT_COMPRESSION_MAX_CHARS_PER_CHUNK`: 長い chunk から query 関連 sentence / line だけを抽出して LLM context を節約する。既定は無効。表や規程 PDF の長い chunk で `diagnostics.context_compressed_count`、`context_compression_saved_chars`、groundedness、answer keyword hit を golden set で見ながら有効化する。
- `RAG_MIN_SIMILARITY`: recall を落としすぎないよう、評価セットで確認して調整する。
- `RAG_RRF_K`: hybrid retrieval の Reciprocal Rank Fusion 定数。小さいほど上位 rank を強く優先する。golden set で keyword/vector の寄与と citation 安定性を確認して調整する。
- `RAG_QUERY_EXPANSION_ENABLED` / `RAG_QUERY_EXPANSION_MAX_VARIANTS`: retrieval 前に deterministic な業務同義語 query expansion を行う。日本語/英語混在 query の recall を上げる目的で既定有効。variant 数を増やすと embedding / Oracle retrieval 呼び出し数も増えるため、golden set と OCI / Oracle の p95 latency を見て 1-3 から調整する。audit / trace には query 本文や展開語ではなく variant 件数だけを残す。
- `RAG_EMBEDDING_CACHE_ENABLED` / `RAG_EMBEDDING_CACHE_MAX_ENTRIES` / `RAG_EMBEDDING_BATCH_SIZE`: backend process 内で OCI Generative AI embedding 結果を LRU cache する。cache key は本文そのものではなく、model id、input type、dimension、本文 SHA-256 から作る。batch 内や連続検索で同じ query/chunk が出た場合は miss だけを OCI へ送る。miss は最大 96 件かつ合計 100,000 文字の先に達した単位で OCI embedding request に分割し、返却順を元入力順へ戻す。単一入力は 100,000 文字を超えると拒否し、本文を暗黙に切り詰めない。[Cohere Embed 4](https://docs.oracle.com/en-us/iaas/Content/generative-ai/cohere-embed-4.htm) の 128k token はリクエスト全入力の token 総量であり、この文字数予算とは別の保守的な保護値である。worker 間共有はしないため、容量と batch size は worker 数、メモリ、OCI payload limit、p95 latency を見て調整する。`MAX_ENTRIES=0` は無効化と同じ。
- `RAG_RERANK_CACHE_ENABLED` / `RAG_RERANK_CACHE_MAX_ENTRIES`: backend process 内で OCI Generative AI rerank 結果を LRU cache する。cache key は query/document 原文ではなく SHA-256、model id、top_n、document 順序から作る。候補順や top_n が変わると別 cache entry になる。頻出 FAQ / 評価実行 / 再検索の p95 latency と OCI 呼び出し数を見て調整する。`MAX_ENTRIES=0` は無効化と同じ。
- `RAG_SEARCH_TIMEOUT_SECONDS`: `/api/search` と `/api/search/stream` の pipeline timeout。OCI / Oracle の p95 latency と worker 数に合わせる。
- `RAG_STREAM_REALTIME_ENABLED`: 廃止予定の互換設定。値にかかわらず回答の完全生成、PII マスク、groundedness、回答検査が終わるまで SSE `delta` は送信しない。次リリースで削除する。
- `OCI_GUARDRAILS_TIMEOUT_SECONDS`: OCI Guardrails 検査の timeout。既定 5 秒。障害時は `regulated` が fail-closed、その他はローカル検査へ縮退し、非機密 warning と metrics / audit code を残す。
- `ORACLE_VECTOR_TARGET_ACCURACY`: Oracle AI Vector Search の問い合わせ側 `FETCH APPROX ... WITH TARGET ACCURACY`。既定は 95。staging / golden set で召回率とレイテンシを見ながら調整する。
- `OCI_ENTERPRISE_AI_ENDPOINT` / `OCI_ENTERPRISE_AI_LLM_PATH` / `OCI_ENTERPRISE_AI_VLM_PATH`: OCI Enterprise AI の OpenAI-compatible gateway endpoint と LLM/VLM path。Enterprise AI は `OCI_ENTERPRISE_AI_API_KEY` による Bearer 認証で呼び出し、staging smoke で VLM/LLM 契約を確認する。Enterprise AI の model deployment / gateway response は `prediction(s)`、`output(s)`、`inference_response`、OpenAI 風 `choices`、JSON 文字列 envelope を正規化してから Pydantic schema / text 抽出へ進める。
- `OCI_ENTERPRISE_AI_VLM_INPUT_MODE`: Enterprise AI VLM への入力搬送方式。`auto` は画像を inline data URL、PDF など非画像を `/files` 経由にする。`files_api` は画像も含めて VLM 入力を明示的に `/files` へアップロードし、`file_id` を `/responses` payload へ渡す。`inline_image` は画像だけ inline で送り、PDF/Office fallback など非画像は設定変更を促して停止する。`API パス` は通常 `/responses` のままにし、`/files` は endpoint から自動生成する。
- `OCI_ENTERPRISE_AI_LLM_MAX_OUTPUT_TOKENS` / `OCI_ENTERPRISE_AI_VLM_MAX_OUTPUT_TOKENS`: OpenAI-compatible Responses payload の `max_output_tokens`。既定値は LLM 1200、VLM/OCR 65536。`status=incomplete` / `reason=max_output_tokens` は取込エラーとして利用者に返す。
- `RAG_PDF_SEGMENTATION_ENABLED` / `RAG_PDF_MAX_PAGES_PER_SEGMENT` / `RAG_PDF_MAX_SEGMENTS`: PDF 取込時に元 PDF を page segment へ分けて VLM へ送る。既定は有効、10 ページ/segment、最大 300 segment。segment が `max_output_tokens` で途切れた場合は単ページに分割して再試行する。
- `RAG_PARSER_ADAPTER_BACKEND`: 任意の外部 parser adapter 選択。`local` は本プロジェクト標準 parser のみ、`auto` は有効化済み adapter を source-aware に選ぶ。PDF は `docling` → `marker` → `unstructured`、画像は `unstructured` → `marker` → `docling`、Office/HTML は `docling` → `unstructured`、email は `unstructured`、単純 text/markdown/csv/json は local parser を優先する。`docling` / `marker` / `unstructured` を明示するとその adapter を優先するが、現行 adapter 実装が扱えない source では `*_adapter_source_unsupported` warning を残して標準 parser / Enterprise AI fallback へ戻す。adapter 出力は必ず `StructuredExtraction` / `DocumentElement` / citation metadata へ再マップし、Oracle 26ai、OCI Enterprise AI、OCI Generative AI Cohere の確定スタックは変更しない。
- `RAG_PARSER_DOCLING_ENABLED` / `RAG_PARSER_MARKER_ENABLED` / `RAG_PARSER_UNSTRUCTURED_ENABLED`: Docling / Marker / Unstructured adapter の feature flag。**外部 parser は services/parsers/<name> の独立サービスへ切り出した**ため、標準 backend image と `scripts/start-backend.sh` は parser 依存を同期しない(`uv sync` は lean)。各 parser のバージョンは各サービスの pyproject(`docling==2.103.0` / `marker-pdf[full]==1.10.2` / `unstructured[all-docs]==0.23.1`)で固定する。`marker-pdf==1.10.2` の `pillow<11.0.0` と `unstructured` の `pi-heif>=1.2.0`(`pillow>=11.1.0`)は共存不可で、これがサービス分離の理由。backend は取込時に各サービスへ HTTP 委譲し、サービス未達なら標準 parser / Enterprise AI fallback へ戻して `*_adapter_service_unreachable` を warning に出す。`RAG_PARSER_<name>_SERVICE_URL` で URL、`RAG_PARSER_SERVICE_TIMEOUT_SECONDS` で timeout、`RAG_PARSER_READINESS_PROBE_ENABLED` で readiness の /health 問い合わせを制御する。設定と導入状態は `GET /api/settings/parser-adapters` と `rag-file-processing-staging --preflight-only` の `parser_adapters` で確認する。schema remap smoke の実行証跡は必要時に `GET /api/settings/parser-adapters/contract` で確認し、通常の readiness 取得では重い fixture parse を走らせない。両方の出力には `parser_adapter_scorecard` も含まれ、readiness と file-processing golden/staging 指標から推奨 backend を機械可読に返す。外部 adapter を staging metrics で推奨するには retrieval recall、table QA、page hit、element lineage、fallback rate の中核証拠が必要で、不足時は `adapter_metric_evidence_incomplete` を warning として返し local fallback を優先する。file-processing staging では selected adapter が未導入の場合、`parser_adapter_preflight` を失敗させ、実 OCI / Oracle client 作成前に停止する。明示 adapter が staging 指標で local fallback 未満の場合も `parser_adapter_scorecard_mismatch` を promotion blocker として返す。staging payload の `parser_adapter_source_routes` は `source_kind` ごとの `candidate_order`、`attempted_order`、`active_order`、`selected_backend`、warning を返し、PDF / image / Office / HTML / email / audio / text の routing が CI artifact として監査できる。audio は現時点では転写サービスを有効化していないため `candidate_order=[]`、`selected_backend=local`、`unsupported_audio` / `audio_transcription_not_configured` として明示し、外部 adapter へ誤って流さない。legacy ingestion で `SourceProfile` が欠落していても、parser registry は `audio/*` content-type を同じ unsupported path に固定する。
  - Unstructured adapter は対応する runtime では `include_page_breaks=true`、PDF/画像では `strategy=auto` と `infer_table_structure=true` を要求する。adapter 関数の signature を見て未対応 kwargs は渡さないため、古い Unstructured API でも不要な fallback を増やさない。
  - 外部 adapter の block metadata に `parent_id` / `section_path` / heading level が含まれる場合は `DocumentElement.parent_id` / `section_path` へ再マップする。metadata が不足する場合も title block の reading order から section stack を補完し、citation / chunk lineage を保持する。
  - 外部 adapter が `FigureCaption` / `TableCaption` を parent_id なしで返す場合は、reading order 上の直前 figure / table へ parent-child lineage を補完する。figure caption は同一 chunk の `dependency_edges` へ入り、table caption も `content_kind=table` として filter / citation lineage に残す。
  - 外部 adapter の table `cells` / `table_cells` が row / col / text / bbox / span / confidence を持つ場合は `ExtractionTableCell` へ保持する。caption text より cell structure を優先して chunk text を作るため、table QA と table cell review が flat markdown だけに依存しない。
  - 外部 adapter の `Formula` / `Equation` block は `latex` / `formula` / `mathml` などの metadata から本文を復元し、`DocumentElement(content_kind=equation)` と chunk metadata の `equation_format` に残す。公式 block が `text` を持たない場合でも検索・citation から落とさない。
  - 外部 adapter の bbox は `x/y/width/height`、`x/y/w/h`、`left/top/right/bottom`、`xmin/ymin/xmax/ymax` などを `DocumentElement.bbox` / `ExtractionTableCell.bbox` / `ExtractionAsset.bbox` の `xyxy` へ正規化し、要素 chunk では `bbox_coordinate_mode` / `bbox_unit` も metadata に残す。preview overlay / citation jump / table cell review は adapter 固有の座標 key に依存しない。
  - 外部 adapter の `Image` / `Picture` / `Figure` block は `DocumentElement(content_kind=figure)` だけでなく `ExtractionAsset` にも昇格し、chunk metadata へ `asset_id` を残す。figure citation から asset export / preview audit へ辿れるようにする。
- `GET /api/documents/{document_id}/extraction-export?format=json|markdown|html|chunks`: 保存済み extraction を JSON / Markdown / escaped HTML / chunk view として返す監査用 API。`chunks` は embedding を含めず、HTML は原本 HTML を実行せず escaped review source として返す。DocumentPreviewWorkspace の抽出エクスポート panel、CI artifact、parser adapter 比較の確認に使う。原本再解析や外部 parser の直接呼び出しは行わない。
  - `tables[].cells` がある表は safe `<table>` として再構成し、`data-table-id` / row / col / bbox lineage を保持する。cells がない旧 extraction は escaped `<pre>` に fallback する。
  - `assets[]` は Markdown / HTML 監査 view に `asset_id` / kind / page / bbox / alt text として表示する。HTML export では asset 実体や Object Storage path を埋め込まず、escaped text と `data-asset-id` / `data-kind` / `data-page` / `data-bbox` のみを返す。
  - `DocumentChunkView.metadata` と `RetrievedChunk.metadata` は recursive JSON metadata を保持できる。`element_ids`、`dependency_edges`、table row group、bbox などの lineage は配列/オブジェクトのまま返せるため、chunk preview / citation jump / CI artifact が文字列 split に依存しない。
- `rag-file-processing-staging` の promotion gate は、実測 metrics の合否に加えて中核閾値の弱体化も検査する。`table_qa_accuracy`、`page_hit_accuracy`、`retrieval_recall`、bbox / section / dependency / parser fallback 系の閾値が基準より緩い場合は `promotion_threshold_too_loose` で昇格を止める。
- `rag-file-processing-staging` の metrics は staging gate の実測値に加えて、local contract で証明済みの `parser_routing_accuracy`、`parser_warning_taxonomy_coverage`、`reading_order_consistency`、`table_structure_fidelity`、`visual_chunk_metadata_completeness` などを同じ artifact に統合する。これにより OCI / Oracle が必要な gate と local parser/chunker で十分検証できる gate を分離しつつ、promotion 判定は 1 つの metrics payload で完結する。
- file-processing staging の通常実行 payload には `chunk_template_scorecard` も含まれる。manifest の `expected_chunk_template` と staging/golden metrics を使い、`pdf_layout` / `office_slide` / `office_sheet` / `markdown_by_heading` / `html_semantic` / `email_thread` / `table_preserve_rows` / `ocr_page` などの template 健康度を評価する。`chunk_block_integrity`、`chunk_contextual_coherence`、`chunk_size_compliance` などの core 指標が低い場合は `chunk_template_scorecard_blocked` を promotion blocker として返す。さらに template ごとの expected / measured case count、covered / missing source kinds、covered / missing scenarios を artifact に残し、ある template の未測定を別 template の良好な aggregate 指標で隠さない。
- `INGESTION_QUEUE_STARTUP_RECOVERY_ENABLED` / `INGESTION_QUEUE_STARTUP_DRAIN_LIMIT` / `INGESTION_QUEUE_STALE_RUNNING_SECONDS` / `INGESTION_QUEUE_WORKER_CONCURRENCY` / `INGESTION_JOB_MAX_ATTEMPTS`: 永続化済み取込 job の起動時回復、stale RUNNING 判定、同時実行数、最大試行回数を制御する。QUEUED/RUNNING job は `/api/documents/ingestion-jobs/{job_id}/cancel` で `CANCELLED` にでき、worker は終了時に cancel 済み job を `SUCCEEDED` / `FAILED` で上書きしない。実行中の Enterprise AI / Oracle 呼び出しを強制中断するものではないため、外部 timeout と stale recovery も併用する。
- `INGESTION_QUEUE_DEDICATED_WORKER_ENABLED` / `INGESTION_QUEUE_INPROCESS_WORKER_ENABLED` / `INGESTION_QUEUE_POLL_INTERVAL_SECONDS`: 取込実行を API の event loop から切り離す専用ワーカー機構。`DEDICATED_WORKER_ENABLED=false`(既定)では従来どおりリクエスト後のバックグラウンドタスクで取込を実行する。`true` にすると API はキュー投入のみ行い、`app.rag.ingestion_worker.IngestionQueueWorker` がキュー(`rag_ingestion_jobs`)を `claim_ingestion_job` の row lock 付きで消費する。`INPROCESS_WORKER_ENABLED=true`(既定)なら同じ API プロセスの lifespan 内でワーカーを起動するため単一コンテナでも完結する。**ただし in-process ワーカーは Gunicorn worker プロセスごとに 1 つ起動するため、`WEB_CONCURRENCY>1` だと実効同時取込数が `WEB_CONCURRENCY × INGESTION_QUEUE_WORKER_CONCURRENCY` まで増え、OCI/Oracle を過負荷にし得る**(row lock で二重実行はしないが総並行数が乗算される)。in-process ワーカーを使う場合は `WEB_CONCURRENCY=1` にするか、API では `INPROCESS_WORKER_ENABLED=false` にして取込を別プロセスへ切り出すこと。別プロセス/別コンテナへ切り出す場合は `uv run --no-sync python -m app.rag.ingestion_worker` を別途起動する(docker-compose の `ingestion-worker` service)。起動時には `ingestion_inprocess_worker_enabled` warning ログで多重化の注意を出す。重い解析・PDF 分割・base64・チャンク・graph index・埋め込みなどの CPU/同期処理は取込・検索とも `asyncio.to_thread` でワーカースレッドへ退避し、event loop を塞がない。ワーカーは複数同時起動しても row lock により同一 job の二重実行が起きないため、`replicas` を増やして水平スケールできる。`POLL_INTERVAL_SECONDS` は QUEUED ジョブのポーリング間隔で、同一プロセス内の enqueue は即時起床通知で待たずに拾う。
- `OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE` / `OCI_ENTERPRISE_AI_VLM_PAYLOAD_TEMPLATE`: Enterprise AI gateway ごとの request shape が標準 payload と異なる場合にだけ設定する JSON object template。文字列 placeholder は `${prompt}` / `${context}` / `${mime_type}` / `${data_base64}`、object placeholder は `"${messages}"` / `"${parameters}"` / `"${response_format}"` / `"${structured_extraction_schema}"` のように完全な文字列値として置く。未設定なら標準 payload を使い、VLM には upload metadata の MIME type を渡す。
- `OCI_ENTERPRISE_AI_LLM_RESPONSE_PATH` / `OCI_ENTERPRISE_AI_VLM_RESPONSE_PATH`: Enterprise AI gateway の response が既知 envelope ではなく独自の深い JSON 構造に包まれる場合だけ指定する JSON Pointer。例: `/payload/results/0/generated/text`、`/payload/results/0/document`。未設定なら既知 envelope を自動判定する。
- `WEB_CONCURRENCY`: backend container の Gunicorn worker 数。OKE / Container Instances の CPU 割当、OCI / Oracle の p95 latency、同時実行数から決める。
- `GUNICORN_TIMEOUT` / `GUNICORN_GRACEFUL_TIMEOUT` / `GUNICORN_KEEP_ALIVE`: worker timeout、停止猶予、keep-alive 秒数。`RAG_SEARCH_TIMEOUT_SECONDS` より短くしない。
- `TRACE_EXPORT_HTTP_ENDPOINT`: 空なら構造化ログ + Prometheus のみ。設定時は `rag.trace_span` event を OpenTelemetry / Langfuse gateway へ非同期 POST する。query 本文、context 本文、OCR 原文、prompt、例外 message は送らない。
- `TRACE_EXPORT_HTTP_BEARER_TOKEN` / `TRACE_EXPORT_TIMEOUT_SECONDS` / `TRACE_EXPORT_QUEUE_SIZE`: trace export の認証、送信 timeout、queue 上限。queue full や送信失敗は `rag_trace_export_dropped` / `rag_trace_export_failed` として記録し、RAG request は失敗させない。
- `GUARDRAIL_MAX_QUERY_CHARS`: UI 側の入力制限と合わせる。
- `RATE_LIMIT_ENABLED`: 高コスト API の app 内 limiter。外部 API Gateway / Ingress limiter と併用できる。
- `RATE_LIMIT_WINDOW_SECONDS`: fixed-window limiter の窓幅。
- `RATE_LIMIT_SEARCH_REQUESTS` / `RATE_LIMIT_EVALUATION_RUNS` / `RATE_LIMIT_UPLOADS` / `RATE_LIMIT_INGEST_REQUESTS`: tenant/user hash 単位の窓内上限。OCI / Oracle / LLM の quota と業務ピークに合わせて調整する。
- `X-RAG-Allowed-Document-Ids` / `X-RAG-Allowed-Category-Names`: 認証ゲートウェイまたはアプリケーション権限層が認可済み scope として backend へ付与する request header。backend は raw 値を監査ログへ出さず、document 一覧、詳細、chunk count、retrieval に deny-by-default の scope filter として適用する。外部クライアントから直接信頼しない。
- `GUARDRAIL_MASK_SENSITIVE_IDENTIFIERS`: query / answer 内の個人番号、口座番号、電話番号、メールアドレスらしき値を `[機微情報]` にマスクする。外部 DLP と責務分担する場合だけ無効化を検討する。

安全チェックを含むリリースは、コード展開後に `uv run python -m app.rag.chat_history_sanitization --dry-run --format json` を実行し、Oracle バックアップを取得してから `--apply` する。適用後は `rag_guardrail_findings_total` の `guardrail_backend_unavailable` / `prompt_injection` / `low_groundedness` と阻止率を監視する。CLI は冪等で、原文の別バックアップをアプリ DB 内には作成しない。

## OCI へ切り替えるときの順序

1. Oracle 26ai に document / chunk / audit tables を作成する。まず backend container または CI runner で `uv run python -m app.rag.oracle_schema --output ../artifacts/oracle-schema.sql --manifest-output ../artifacts/oracle-schema.manifest.json` を実行し、DDL 成果物と manifest の hash / statement 数をレビューする。既存 DB を現行 DDL 契約へ寄せる場合は `uv run python -m app.rag.oracle_schema --migration --output ../artifacts/oracle-schema-migration.sql --manifest-output ../artifacts/oracle-schema-migration.manifest.json` を実行し、migration artifact をレビューして適用する。V3 の構築 artifact(`rag_chunk_sets`、`rag_document_extractions`、`rag_artifact_layers`、`rag_kb_chunk_set_bindings`、`rag_chunks.chunk_set_id`)を既存データへ反映する場合は、あわせて `uv run python -m app.rag.variant_backfill_cli --format sql --checks-only --output ../artifacts/variant-backfill-checks.sql` と `uv run python -m app.rag.variant_backfill_cli --format json --output ../artifacts/variant-backfill.manifest.json` を生成し、[oracle-variant-backfill-runbook.md](./oracle-variant-backfill-runbook.md) の acceptance を staging artifact として保存する。生成 SQL は document table に `content_sha256`、`file_size_bytes`、`duplicate_of_document_id`、`tenant_id_hash` を含め、`content_sha256` と `tenant_id_hash, status, uploaded_at` に索引を作る。chunk table は `VECTOR(1536, FLOAT32)`、HNSW ベクトル索引(`COSINE`、目標精度 `95`、neighbors `32`、efconstruction `500`)、`tenant_id_hash`、`document_id + chunk_index`、`chunk_set_id + chunk_index` 用索引を含め、retrieval で tenant 条件、request access scope 条件、KB serving chunk_set 条件を必ず適用できるようにする。audit table は query 本文、OCR 原文、tenant/user id の raw 値を保存せず、hash、request id、trace id、guardrail code、retrieval/rerank/context diversity/context group expansion/context expansion/context compression/citation 件数、context compression 節約文字数、context 文字数、設定 fingerprint、error type を保存する。レビュー済み SQL を SQLcl や管理された migration 手順で適用してから次へ進む。

   回答生成設定を Oracle 正本へ切り替えるリリースでは、schema migration 適用後に
   `uv run python -m app.rag.generation_settings_migration --format json` で旧 `.env` profile と
   `prompt-versions.json` の import 対象を確認し、問題がなければ同コマンドへ `--apply` を付ける。
   apply は version ID 単位で冪等で、Oracle に `GLOBAL` 行が既にある場合はその profile / active
   pointer を上書きしない。旧ファイルは 1 バージョン周期残すが、新 backend は読み書きしない。
2. `ObjectStorageClient` の OCI Object Storage SDK 実装を有効化する。`OBJECT_STORAGE_REGION` / `OBJECT_STORAGE_NAMESPACE` / `OBJECT_STORAGE_BUCKET` を設定し、保存 URI が `oci://namespace/bucket/key` になり、取得時に namespace / bucket 不一致を拒否することを staging で確認する。取込前に Object Storage から取得した bytes が document table の `file_size_bytes` / `content_sha256` と一致することも確認する。
3. `OracleClient` の python-oracledb pool、vector search、keyword search、document/chunk persistence、隣接 context 取得を有効化する。`ORACLE_USER` / `ORACLE_DSN` / `ORACLE_PASSWORD` または `ORACLE_CLIENT_LIB_DIR/network/admin` に配置した wallet を設定する。`INGESTING` / `ERROR` への状態遷移では該当 document の chunk/index 行と古い抽出結果を削除し、検索対象は `INDEXED` に限定する。staging では `VECTOR_DISTANCE`、`FETCH APPROX ... WITH TARGET ACCURACY`、Oracle Text `CONTAINS`、document 別 chunk count、`chunk_index` window による同一 document 前後 chunk 取得、`INDEXED` 文書が hybrid search の citation に含まれることを確認する。
4. `OciGenAiClient` の embedding / rerank は OCI Generative AI Inference SDK 実装を使う。staging では `OCI_CONFIG_FILE` / profile / region / compartment / model id、Cohere Embed v4 の 1536 次元、Cohere Rerank v4 fast の返却件数・候補 index 範囲・index 重複なし・finite score を確認する。
5. `OciEnterpriseAiClient` は `OCI_ENTERPRISE_AI_ENDPOINT`、`OCI_ENTERPRISE_AI_API_KEY`、`OCI_ENTERPRISE_AI_LLM_MODEL`、`OCI_ENTERPRISE_AI_VLM_MODEL`、`OCI_ENTERPRISE_AI_LLM_PATH`、`OCI_ENTERPRISE_AI_VLM_PATH` を使って Enterprise AI endpoint を呼び出す。標準 payload で合わない model deployment / gateway は `OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE` / `OCI_ENTERPRISE_AI_VLM_PAYLOAD_TEMPLATE` で request shape を差し替え、response envelope が独自の場合は `OCI_ENTERPRISE_AI_LLM_RESPONSE_PATH` / `OCI_ENTERPRISE_AI_VLM_RESPONSE_PATH` で候補 node を指定する。staging ではまず `uv run python -m app.rag.enterprise_ai_probe --surface both --dry-run` で URL、template 使用有無、response path 使用有無、payload key / shape、JSON byte 数を確認し、その後 `uv run python -m app.rag.enterprise_ai_probe --surface both` で LLM/VLM を直接呼び出す。probe は回答本文や OCR 本文を出さず、text 文字数・element 件数だけを artifact に残す。ここで Bearer 認証、timeout/retry、VLM の MIME type / 構造化抽出 JSON schema、LLM の citation-grounded 生成 payload、response parsing を実 endpoint で確認する。VLM response は `StructuredExtraction` へ検証し、LLM response は空 text を fail fast する。
6. staging にデプロイし、`/api/ready` の checks がすべて `ok` になることを確認する。production 昇格時は `ENVIRONMENT=production` にして、追加 checks の `audit_context_salt` も `ok` にする。Oracle は `ORACLE_USER` / `ORACLE_DSN` に加えて `ORACLE_PASSWORD` または `ORACLE_CLIENT_LIB_DIR/network/admin` に存在する Wallet が必要。
7. staging 環境でまず `uv run python -m app.rag.staging_smoke --preflight-only` を実行する。OCI/Oracle 接続設定、Enterprise AI LLM/VLM 設定、Cohere embedding/rerank 設定、`UPLOAD_STORAGE_BACKEND=oci`、Object Storage namespace/bucket がそろっていることを JSON の `checks` で確認する。preflight 失敗時は外部依存へ接続せず、secret 値も出力しない。
8. preflight が `ok=true` なら `uv run python -m app.rag.staging_smoke` を実行する。Object Storage put/get、Oracle document 作成、Enterprise AI VLM、chunking、embedding、Oracle indexing、hybrid search、Enterprise AI LLM 生成を 1 回通し、作成した smoke document が citation に含まれることを確認する。既定 query は一意な `SMOKE-...` marker の原文引用を要求し、検索は新規 `document_id` に限定される。既定 query では LLM 回答にも marker が含まれない場合に `stage=rag_answer_marker` で失敗する。JSON 出力の `ok`、`marker`、`query`、`answer_contains_marker`、`trace_id`、`chunk_count`、`citation_count`、`cleanup`、`diagnostics.oracle_vector_target_accuracy`、必要に応じて `diagnostics.context_diversified_count` / `diagnostics.context_group_expanded_count` / `diagnostics.context_expanded_count` / `diagnostics.context_compressed_count` / `diagnostics.context_compression_saved_chars` を保存し、staging gate の artifact にする。既定では evidence として作成物を保持し、`cleanup` は `skipped` になる。DB/Object Storage を汚したくない一時確認では `uv run python -m app.rag.staging_smoke --cleanup` を使い、成功・失敗どちらでも作成済み Oracle document/chunk と Object Storage object の削除 status を確認する。失敗時は preflight の `checks` または本実行の `stage` / `cause_type` を見て、Object Storage、Oracle、VLM、embedding、retrieval、context diversity、context group expansion、context expansion、context compression、generation のどこで止まったかを切り分ける。query を変える場合は `--query "確認用キーワード {marker} を要約してください"` のように `{marker}` placeholder を残す。
9. golden set 評価と負荷試験を通してから production へ昇格する。
