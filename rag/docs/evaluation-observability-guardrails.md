# 評価・観測性・ガードレール

## 評価

API: `POST /api/evaluation/jobs/run`・`POST /api/evaluation/jobs/compare`（job。#390）。`GET /api/evaluation/jobs/{job_id}` で進捗と結果、`POST /api/evaluation/jobs/{job_id}/cancel` で取り消し。同期の `POST /api/evaluation/run`・`/compare` は互換のため残す。

設定比較 API: `POST /api/evaluation/compare`

評価ケースは query と、期待値（関連 document id・回答に含めたいキーワード・標準回答）で構成する。
リポジトリには `evaluation/golden-set.example.json` と `evaluation/compare.example.json` を同梱している。実データ投入後、document id と期待キーワードを環境に合わせて `evaluation/golden-set.json` として管理する。

```json
{
  "cases": [
    {
      "id": "policy-flow-001",
      "query": "経費申請の承認フローは？",
      "relevant_document_ids": ["<document-id>"],
      "expected_answer_keywords": ["部門長", "承認"],
      "standard_answer": "申請者が提出し、部門長が承認します。"
    },
    {
      "id": "out-of-scope-001",
      "query": "社員食堂の来月の献立は？",
      "answerable": false
    }
  ],
  "top_k": 20,
  "filters": {
    "status": "INDEXED"
  },
  "suite": "standard"
}
```

`cases` は 1 件以上必須です。各ケースは回答エンジン（根拠付き回答）で、業務ビューを使わずに全体の既定の設定で回答し（#301）、回答の記録（引用・根拠・実行記録）から指標を求める（#591）。#594 で回答はこの方式だけになった（#591 では評価だけ回答エンジンを固定していた）。回答の記録は通常の回答と同じく保存し（`trace_id` で確かめられる）、標準回答による評価の結果も同じ回答の記録に保存する。

### 評価の指標（#591）

指標は「検索」「根拠」「回答」の 3 つの観点に整理した 9 つ。rag_poc が最後に使っていた 7 つの指標（平均点・合格・未裏付け主張・必要内容欠落・監査での除去・拒答・引用照合での除外）と、Ragas・TruLens の RAG triad・ARES の観点を照らして、残す・まとめる・削除を決めた。

| 観点 | 指標 | 意味 | 対象のケース | 由来 |
|---|---|---|---|---|
| 検索 | `context_recall` | 正解の文書のうち、回答の根拠に取れた割合 | `relevant_document_ids` のあるケース | 旧 `recall_at_k` と `context_recall`（同じ計算）をまとめた。Ragas ID-based context recall |
| 検索 | `mrr` | 最初の正解の文書の順位の逆数 | 同上 | 残す（rag_poc の検索評価も使う） |
| 根拠 | `faithfulness`（画面の名前は「根拠との語句の一致率（参考）」） | 回答の語句のうち、根拠の本文に含まれる割合（決定論の近似） | 拒答していないケース | **参考値**。言い換えるだけで下がるため、閾値の判定・ケースの失敗理由・1 件の回答の合否に使わない（#711）。閾値は目安として表示する |
| 根拠 | `citation_traceability_coverage` | 引用を文書・ページ・要素までたどれる割合 | 引用のあるケース | 旧 `bbox_citation_coverage`・`element_lineage_coverage` をまとめた（bbox・要素の細部は file-processing の golden gate が見る） |
| 根拠 | `claim_support_rate`（画面の名前は「Faithfulness（根拠への忠実性）」） | LLM が回答を主張に分け、根拠のない主張・根拠と矛盾する主張が無いケースの割合 | `standard_answer` のあるケース | 業界の faithfulness（RAGAS・DeepEval・Azure・Google の groundedness）と同じ考え方。保存値の key は変えない（#711） |
| 回答 | `answer_keyword_hit_rate` | 期待する語をすべて含む回答の割合 | `expected_answer_keywords` のあるケース | 残す（標準回答の無い golden set の決定論の近似） |
| 回答 | `refusal_accuracy` | 答えるべき質問に答え、答えるべきでない質問に答えなかった割合 | すべてのケース | rag_poc の「拒答」。RGB の negative rejection。否定 case の特別扱い（#301）もこれにまとめた |
| 回答 | `requirement_coverage` | 標準回答の必要な項目に、回答が対応した割合 | `standard_answer` のあるケース | rag_poc の「必要内容欠落」（網羅性の軸の対応率） |
| 回答 | `answer_pass_rate` | 標準回答による評価で、1 件の回答で測れる指標（claim_support_rate・requirement_coverage・refusal_accuracy・citation_traceability_coverage）がすべて閾値以上だった割合（#680） | `standard_answer` のあるケース | faithfulness は語句の一致の近似のため、1 件の回答の合否には使わない（参考値） |

削除した指標: `precision_at_k`（正解の文書が k 件より少ないと 1.0 に届かず、回答エンジンは rerank の件数を使わない）・`context_precision`（正解の文書以外の補足の根拠を減点する。根拠の質は `faithfulness` と `claim_support_rate` で見る）・`response_relevancy`（質問の語を繰り返すだけで上がる近似）・`noise_sensitivity`（失敗理由の件数から作った独自の式で、失敗理由と重なる）・`content_kind_hit_rate`・`section_coverage`（期待値の無いケースを 1.0 と数え平均を押し上げ、ほぼ使われていない）。rag_poc の「監査での除去」「引用照合での除外」は回答生成の工程の診断で、利用者が見る品質ではないため指標にしない（回答の記録の実行記録で確かめる）。

- 各指標は、その指標を測れるケースだけの平均にし、対象の件数を `metric_case_counts` に返す。対象のケースが無い指標は `null`（0 と区別する）。失敗したケース（検索失敗・時間切れ）は `error_count` で数え、指標の平均には入れない。
- **答えるべきでない質問（#301）**: `answerable: false` のケース（省略時は、正解の文書・期待する語・標準回答のどれも無いケース）は、拒答の正しさだけを測る。拒答は、回答の本文か引用が無い回答と、回答エンジンが不足の理由を返しモデルが使った根拠が 1 つも無い回答。
- **標準回答による評価**: `standard_answer` のあるケースは、回答の記録の評価の入力（根拠・引用）で、標準回答による評価（`evaluate_answer_payload` が標準回答から比較の範囲を固定 → 項目の照合・主張の監査を LLM で行い、`app/rag/answer_metrics.py` が評価の基準の指標と閾値で合否を付ける。#680）を行う。LLM を複数回呼ぶため、1 件の上限は 600 秒。比較できなかったケース（時間切れ・入力の上限・評価の記録が無い）は指標に入れず `answer_evaluation_error` とし、評価を合格にしない。
- `passed`: 閾値をすべて満たし、`error_count=0` で、標準回答による評価がすべて終わったか。閾値は測れた指標だけに適用する。
- `threshold_failures`: 閾値を下回った metric、実測値、閾値の一覧。CI gate ではこの配列を失敗理由として出力する。
- 削除した指標の `thresholds` は受け付けない（422）。ケースの `expected_content_kind`・`expected_section_paths` と評価の `mode`・`rerank_top_n` は以前の評価ファイルを読めるように無視する（比較の experiment では受け付けない）。保存済みの評価の結果（job・artifact）に残る古い指標は読み込み時に捨て、画面は削除した指標・基準の名前を原文のまま出す。

### Evaluation アダプター(評価の基準 = 閾値のプリセット)

CI gate の閾値を毎回インラインで書かずに選べるよう、**Evaluation アダプター(`rag_evaluation_suite`)** に評価の基準を 2 つ束ねる（#591）。`app/rag/evaluation_adapter.py` が基準を `EvaluationThresholds` へ解決し、`GET/PATCH /api/settings/evaluation-suite` と「評価の基準」画面で切り替える。

| 基準 | 用途 | 閾値 |
|---|---|---|
| `standard`（標準。既定） | 日常の確認と nightly の回帰 | context_recall 0.8 / mrr 0.6 / faithfulness 0.7 / citation_traceability 0.9 / claim_support 0.9 / answer_keyword 0.8 / refusal 0.9 / requirement_coverage 0.8 / answer_pass 0.7 |
| `strict`（厳格） | リリース前の判定 | context_recall 0.9 / mrr 0.8 / faithfulness 0.8 / citation_traceability 0.95 / claim_support 1.0 / answer_keyword 0.9 / refusal 1.0 / requirement_coverage 0.9 / answer_pass 0.8 |

- **解決順**: request の明示 `thresholds` > request の `suite`(任意) > 設定 `rag_evaluation_suite`。`EvaluationMetrics.evaluation_suite`(compare では各 experiment の metrics)に確定した基準を残す。
- 削除した基準（`request_only` / `retrieval_focused` / `balanced` / `ragas_like` / `strict_ci`）は、保存済みの `RAG_EVALUATION_SUITE` を起動時に後継（`strict_ci` → `strict`、ほか → `standard`）へ寄せる。API では受け付けない。閾値を使わない評価は、request に `thresholds: {}` を書く。
- 評価の基準はグローバル設定(と request の `suite`)だけで決まり、業務ビューでは上書きしない。保存済みの `view_config.query.evaluation_suite` は読み込み時に無視し、次回保存で消える。

レスポンスには `case_results` も含める。各 case について `case_id`、`trace_id`、`status`、取得 document id、関連 document id、hit document id、case 単位の context recall / reciprocal rank / faithfulness（overlap count）/ citation traceability、回答キーワード命中、拒答したか（`abstained`）とそれが期待どおりか（`refusal_correct`）、標準回答による評価の要約（`answer_evaluation`: status・点・合否・主張の裏付け・必要な項目の対応率）、guardrail warning、diagnostics、elapsed ms、error type・error stage・error message を返す。測れない値は `null`。検索失敗または timeout の case は `status=error` として残し、query 本文や例外 message はレスポンスへ出さない。評価 runner が捕捉した case 失敗は `rag_search_audit` にも残し、timeout は `error_stage=timeout`、その他の case 例外は `error_stage=evaluation` とする。

**時間の上限（#383）**: 1 case は、チャット・検索の回答と同じ回答生成の上限（`RAG_ANSWER_TIMEOUT_SECONDS`、既定 300 秒）で打ち切る。回答フローは質問の理解・根拠の評価（CRAG）・回答の生成・監査で LLM を何度か呼ぶため、検索だけの上限（旧 `RAG_SEARCH_TIMEOUT_SECONDS`、30 秒。#383 で削除）では足りない。時間切れの case は `status=error`・`error_type=TimeoutError` とし、時間切れになった工程を `error_stage`（進捗の stage と同じ名前。例: `answer_step:文書検索（1回目）`）と `error_message`（例:「評価ケースの回答生成が上限の 5 分以内に終わりませんでした（時間切れになった工程: 文書検索（1回目））。…」。query 本文は含めない）に残して、評価は次の case へ進む。評価全体の上限は、job（#390）では `RAG_EVALUATION_JOB_TIMEOUT_SECONDS`（既定 3600 秒。compare は experiment の間で共有）、同期の `/run`・`/compare` では 600 秒（`EVALUATION_RUN_TIMEOUT_SECONDS`。Nginx は 660 秒待つ）。上限に達したら実行中の case を時間切れにし、残りの case は pipeline を呼ばずに `error_type=EvaluationTimeBudgetExceeded` の失敗として返す（`passed=false`）。

**評価の job（#390）**: 評価・比較は job で動く。投入（`202`）→ 状態の取得 → 結果の流れで、状態は `rag_evaluation_jobs`（`RUNNING` / `SUCCEEDED` / `FAILED` / `CANCELLED`）に保存する。進捗は終わった case の数 / 全体（compare は experiment × case の通しの数）と、実行中の case の id（compare は experiment の id も）。job は投入を受けた backend のプロセスで、投入した request の文脈（利用者・対象範囲）のまま動き、query の本文は保存しない。job は投入した利用者（tenant と user の hash）だけが取得・取り消しできる。実行中のプロセスは 10 秒ごとに heartbeat を DB の時刻で書き、取り消し（別のプロセスへの取り消しを含む）は heartbeat・進捗の更新で気づいて評価を止める。プロセスが止まって heartbeat が 120 秒途絶えた job は、状態の取得・投入のときに `FAILED` にする。同時に実行できる job は 2 件まで（`409`）。評価が例外で止まった job は `FAILED` にし、`error_message` には例外の型だけを出す（本文は出さない）。結果は成功したときだけ返し（`run_result` / `compare_result`）、評価 artifact（`rag_evaluation_runs`）にも保存する。

**画面（#390）**: 品質評価の画面は job id を作業状態（`sessionStorage`）に残し、戻ったとき・再読込したときはその id でサーバーの状態を確かめる（評価を送り直さない）。実行中は、実行状況のカードに状態・件数と割合・実行中の case・経過時間（`ProcessingIndicator placement="job"`）と取り消しを出し、結果の領域は Skeleton にする。ケース別結果の表は、失敗した case（`status=error`）に「エラー」のバッジ・時間切れになった工程（`error_stage`。表示名は回答生成の進捗と同じ）・`error_message` を出す。

各 case には `failure_reasons` を付与し、集計として `failure_reason_counts` を返す。理由は `retrieval_miss`・`partial_recall`（検索）、`low_groundedness`・`unsupported_claim`（根拠）、`unexpected_refusal`・`unexpected_answer`・`answer_keyword_miss`・`missing_content`・`answer_failed`（回答）、`answer_evaluation_error`・`guardrail_warning`・`case_error` に固定する。この分布で、検索・根拠・回答のどこを次に調整すべきかを切り分ける。

`/api/evaluation/compare` は `cases` と複数の `experiments` を受け取る。各 experiment は `id`、`top_k`、`filters`、任意の `rag_overrides` を持ち（回答エンジンが使わない `mode`・`rerank_top_n` は受け付けない）、同じ golden set に対して順番に `/run` 相当の評価を行う。`rag_overrides` は回答エンジンの全体の既定（`query_strategy`・`answer_flow`・`neighbor_child_count`・`rerank_enabled`）と、RRF 定数（`rrf_k`）・同じ group から足す child の上限（`context_group_max_chunks`）・Oracle vector target accuracy だけを一時的に上書きし、secret やモデル credential は扱わない（削除したキーは 422）。結果は `ranking_metric`（既定 `context_recall`）、`best_experiment_id`、rank 付き experiment results として返る。順位は `passed=true` を優先し、その後 ranking metric 降順（測れなかった experiment は後ろ）、error 数昇順、failure reason 件数昇順、experiment id 昇順で安定化する。

本番運用では、カテゴリごとに 20-50 件の golden set を作り、chunking / embedding model / rerank top N / prompt を変えたときにこの API を CI または nightly job で実行する。CI では `thresholds` を指定し、`passed=false`、`error_count>0`、または `threshold_failures` 非空なら失敗にする。nightly では API レスポンス artifact と trend summary を保存して回帰差分を追う。`python -m app.rag.evaluation_cli` は入力 JSON に `experiments` がある場合 `/api/evaluation/compare` へ送り、rank 1 の best experiment の metrics を終了コードの gate 判定に使う。`--trend-output` は aggregate metrics、best experiment、experiment rank、result hash だけを含む非機密 JSON を出力し、query/context 原文や `case_results` は含めない。

file-processing staging の promotion guard は、manifest の閾値が緩められていないことも検査する。`retrieval_recall`、`table_qa_accuracy`、`page_hit_accuracy` に加え、`preview_addressability_coverage`、`table_cell_lineage_coverage`、`visual_chunk_metadata_completeness`、`ingestion_quality_report_completeness`、`parser_warning_taxonomy_coverage`、`parser_routing_accuracy`、`adapter_contract_coverage`、source/backend coverage、fallback/failed segment rate を中核閾値として固定し、RAGFlow 的な citation-to-preview、Docling/Marker 的な cell-level lineage、Unstructured 的な warning taxonomy が UI 上だけでなく昇格判定でも退化しないようにする。

`python -m app.rag.parser_adapter_contract_cli` は runtime の parser adapter compatibility matrix を出力する。feature flag と parser サービスの readiness（`/health`。外部 parser は backend の venv に入れず、独立サービスへ HTTP で委譲する。#286 / #343）に加えて、サービスに届く有効な adapter だけ fixture を実 parser path へ通し、`StructuredExtraction` への remap count と source-kind contract を確認する。`--manifest docs/evaluation/file-processing-golden-set.json --strict` を使うと、既定 fixture ではなく staging manifest の `fixture_root` と `adapter_schema_remap=true` が付いた正向き schema-remap `cases[].fixture` を case 単位で展開し、scenario / parser backend / adapter import・distribution・version / element/page/table/cell/asset/bbox count / reason code を remap 証跡として残す。strict では selected adapter ごとに少なくとも 1 件の `passed` remap case を要求し、active package があっても real fixture から schema remap 証跡を出せない場合は `adapter_schema_remap_evidence_missing` の blocking failure にする。さらに manifest の source kind は corrupted / unsupported case を schema-remap smoke から除外した後も縮小せず保持し、可ルーティング source kind に正向き fixture が 1 件もない場合は `adapter_schema_remap_fixture_missing_for_source` で止める。corrupted / unsupported fixture は adapter smoke ではなく staging の safe-error / warning taxonomy gate が担当するが、PDF などの正向き schema-remap fixture なしに HTML だけで adapter contract を合格にはできない。PDF/image は page lineage、image は bbox/asset lineage、HTML/email/Office は semantic/header/slide/sheet/table lineage を要求し、adapter distribution/version metadata が欠ける場合も `adapter_distribution_name_missing` / `adapter_package_version_missing` で失敗にするため、単に element が返っただけでは adapter smoke を合格にしない。artifact には source kind、status、parser backend、adapter import・distribution・version、schema count、reason code、fixture/case の hash label だけを含め、fixture root、fixture file name、case id、抽出本文、OCR 原文は含めない。設定画面の「運用診断」と `GET /api/settings/parser-adapters/contract` は #492 で削除し、この matrix は CLI と nightly だけが出す。staging artifact には `adapter_contract_matrix_summary` も含め、backend/source ごとの status、backend/source/status count、source-kind status count、passed/missing source kinds、passed/missing scenario、passed / blocking failure の `case:<hash>` evidence、blocking failure source/backend/scenario、reason/warning taxonomy だけで失敗箇所を確認できるようにする。trend regression はこの case hash set も比較するため、source/scenario/count が同じでも baseline の実 manifest case を別 fixture に置き換えた strict smoke は regression になる。`backend_source_status` は同一 backend/source の複数 case を最後の結果で上書きせず、失敗・fallback・fixture missing などを passed より優先する代表 status に集約し、詳細分布は `backend_source_status_counts` に残す。この summary も artifact-safe payload から作るため、blocking failure に real-world case id や fixture file name は出さない。

GitHub Actions の `RAG Evaluation Nightly` は parser adapter contract CLI を file-processing golden gate より先に実行し、API base URL が未設定の夜間実行でも非機密 artifact を保存する。通常は parser サービスに届かない adapter を status として記録するだけにする（runner には parser サービスが無いため）。`run_file_processing_staging=true` かつ `require_real_world_file_processing_manifest=true` の production staging では workflow が `--strict` と `--parser-adapter-contract-strict` を自動的に有効化し、単独 smoke を厳格化したい場合は `parser_adapter_contract_strict=true` でも同じ経路を使う。strict が有効な workflow は、届く parser サービスが必要なため（届かなければ `adapter_missing` で失敗する）、job の中で Docling の parser サービスを CPU 版 torch で起動し（`RAG_SERVICES_TORCH=cpu scripts/rag-services.sh sync` / `run parser-docling`）、`/health` を待ってから gate を実行する（#366。手順と所要時間は deployment.md）。file-processing staging CLI にも `--parser-adapter-contract-strict` を渡す。file-processing staging CLI も contract CLI と同じく、adapter の可用性を常に parser サービスの `/health` で判定する（`RAG_PARSER_READINESS_PROBE_ENABLED` を付けずに手で実行しても backend の venv の package では判定しない）。`--strict` は adapter backend を `auto` 相当にし、Docling / Unstructured の feature flag を有効化した runtime snapshot で remap smoke を実行する。schema remap に失敗した adapter/source kind は promotion blocker として扱い、`parser_adapter_contract_source_kinds` で PDF / Office / HTML / email / image などの対象を絞れる。file-processing staging artifact でも同じ strict settings を preflight、実 ingestion/search client、`adapter_contract_coverage` に使うため、runtime が local のままなのに adapter contract だけ合格する状態を避ける。全 backend を横断する matrix では非 routing 対象 pair の `unsupported` は coverage 情報に留めるが(source kind ごとの候補は `rag_parser_core.capabilities.ADAPTER_CAPABILITIES` が宣言する backend だけで、Docling は PDF と画像だけ。#366)、CLI で `--backend` と `--source-kind` を明示した strict smoke では `unsupported` も blocking failure として扱い、「対応を掲げたが実際は routing されない」状態を合格にしない。

`rag-file-processing-staging --preflight-only --parser-adapter-contract-strict` も、単なる package readiness ではなく manifest の `fixture_root` と `adapter_schema_remap=true` の `cases[].fixture` を使って同じ parser adapter contract を実行する。これにより、Docling / Unstructured が installed/active と表示されても、実 package が staging fixture を `StructuredExtraction` へ remap できない場合は preflight 段階で `parser_adapter_contract.passed=false` になり、実 staging client 作成前に停止できる。

file-processing staging artifact では runtime adapter contract の合否を `promotion_blockers` だけでなく `metrics.adapter_contract_coverage`、`metric_evidence.adapter_contract_coverage`、`threshold_results[adapter_contract_coverage]` にも反映する。contract summary は `reason_code_counts`、`warning_code_counts`、`blocking_failure_reason_counts` も出し、CI / dashboard / 手動レビューのどこから見ても、外部 adapter の schema remap failure が “品質指標は通っているが promotion blocker だけ赤い” という不整合や、case を全件展開しないと失敗理由が分からない状態にならない。adapter golden gate も同じ contract summary の passed / backend passed / blocking failure `case:<hash>` evidence を持つため、golden gate だけを見ても「どの実 manifest case で schema remap が証明されたか」を count ではなく非機密 hash set で確認できる。

Object Storage artifact chain は final artifact の redaction だけでなく、sanitization 前の `metric_evidence` / runtime check evidence / gate evidence に `raw_text`、`ocr_text`、`query`、`answer`、`table_text` などの本文系 key が混入した場合も `object_storage_audit_payload_not_redacted` として promotion blocker にする。これにより extraction artifact 自体は復旧用に Object Storage へ保持しても、CI / audit / staging payload の evidence 経路へ OCR 原文を流す実装を合格にしない。

staging の `parser_adapter_source_routes` は `parser_adapter_contract` の backend/source 成功 case で補正する。runtime 候補順で最初の backend が active でも、その source kind の schema remap 証跡がなければ、同じ source kind で contract passed の active backend へ回し、なければ local fallback とする。route には `*_adapter_contract_unverified_for_source` を warning として残し、promotion artifact では `adapter_golden_gate_source_route_contract_missing` と `source_route_contract_gap_source_kinds` で固定順 routing への退化を止める。staging trend も source kind ごとの candidate / attempted / active / selected backend と route warning を保存し、PDF/Office/HTML/email/image の selected backend が local fallback へ戻る、active candidate が減る、または contract gap warning が増える退化を baseline 比較で止める。

`object_storage_artifact_chain` は preflight の probe put/get だけでは合格にしない。staging ingestion 後に `StructuredExtraction.parser_artifacts.extraction_artifact_path` と成功 segment checkpoint の `artifact_path` を Object Storage から実際に `get` し、JSON payload の `extraction_artifact_schema_version`、`extraction_artifact_kind`、`extraction_artifact_document_id`、segment id、page range が document/checkpoint と一致することを数値 evidence にする。promotion では probe / full artifact / segment artifact の URI scheme も非機密 evidence として保持し、`oci://` でない local/mock chain は readable でも `object_storage_*_not_oci` blocker にする。artifact 本体には OCR / extraction text が含まれ得るため、artifact chain は scheme、readable count、identity verified count、payload byte count、error count だけを report/trend に残し、raw OCR 本文を audit artifact へ出さない。trend regression は roundtrip `ok`、roundtrip scheme の `oci` 維持、full artifact cached/OCI/readable/identity-present/identity-verified case count、segment artifact expected/OCI/readable/identity-verified count、retry case count、retained successful segment artifact count、audit redaction を baseline から落とせず、integrity error、cache miss、successful segment rewrite、non-OCI artifact count の増加も blocker にする。さらに artifact/retry の positive evidence と cache miss / integrity error / rewrite の bad evidence は `case:<hash>` のみを trend に残し、件数が同じでも fixture が別 case に置き換わった場合や bad case が追加された場合を regression にする。

file-processing golden CLI の `--trend-output` は `file-processing-trend.json` として、parser fallback rate、表 QA、page hit、bbox / preview addressability、source/backend coverage、threshold status、promotion blocker count、result hash だけを含む非機密 trend snapshot を出力する。staging CLI の `--trend-output` は `file-processing-staging-trend.json` として、実 OCI / Oracle / Object Storage 経路の retrieval recall、表 QA、page hit、bbox / preview addressability、adapter contract、parser adapter scorecard、backend/source-kind matrix、chunk template scorecard、segment artifact reuse、table cell lineage evidence、preview addressability evidence、runtime check status を保存する。`python -m app.rag.file_processing_trend_cli` は current trend を baseline trend と比較し、高いほど良い metric の低下、低いほど良い metric の増加、failure / promotion blocker の増加、必要時の `promotion_ready` 退化を exit `1` で止める。比較対象は同一 `kind` の trend に限定し、top-level `case_count` / `gate_count` が baseline から減った場合も regression にするため、staging trend を local trend に差し替えたり、fixture / gate 面を縮めて同じ総合 metric を出す運用を合格にしない。baseline にある比較可能 metric が current から消えた場合も `metric_missing_from_current` として止めるため、`adapter_contract_coverage` や `table_qa_accuracy` のような厳しい指標を出力しないことで比較対象から外す運用も合格にしない。`runtime_check_status_counts`、`promotion_blocker_code_counts`、`threshold_status_counts`、`threshold_failures` も比較対象にし、runtime smoke の `ok` 減少、`failed` / `skipped` / `pending` などの bad status 増加、blocker code 追加、threshold failure metric 追加を総合 metric が同じでも regression として止める。`dependency_context_recall`、`chunk_block_integrity`、`chunk_contextual_coherence`、`reading_order_consistency`、`structural_section_coverage`、`cross_page_table_continuity_coverage`、source/backend coverage、warning taxonomy、table/visual lineage などの構造・dependency 指標は zero-drop として扱い、Adaptive Chunking / M3DocDep 的な文書構造能力を 0.01 だけ落として通すことも許可しない。adapter contract trend は case count だけでなく scenario set / passed scenario / missing scenario / blocking scenario、backend/source passed pair、backend/scenario passed pair、backend/source/status count、adapter package/version pair、warning code count、blocking failure reason count も比較するため、two-column PDF や Office table などの難 scenario を同数の簡単 fixture に置き換えた場合、Marker/PDF のような特定 adapter/source 能力が落ちた場合、Docling/two-column PDF のような特定 adapter/scenario 能力が別 adapter に接管された場合、Docling / Unstructured の runtime package version が baseline から drift した場合、代表 status が同じでも fallback / failed / fixture missing / missing / disabled の件数が増えた場合、passed 件数だけが減ってテスト面が薄くなった場合、warning taxonomy や failure reason taxonomy が悪化した場合も regression として検出できる。parser adapter scorecard trend は selected/recommended backend、metrics source/applied target、backend entry ごとの score/status/rank/metric count/installed/executable/warning を比較するため、推奨が local へ戻った、外部 adapter が missing/disabled になった、または scorecard の実測根拠が薄くなった場合も regression にする。backend/source-kind matrix は required/covered/missing source kind と backend/source pair を比較するため、総合 coverage が同じでも特定 source kind や backend/source 証跡が抜けた場合は regression にする。table cell lineage trend は coverage に加えて expected/resolved/covered/lineage ref count と unresolved/uncovered count を比較し、`case:<hash>` の positive/bad evidence set も比較するため、coverage や count が同じでも表 cell fixture や citation evidence が別 case に置き換わった場合は regression にする。preview addressability trend も chunk/extraction bbox target count、bbox chunk count、addressable/unaddressable target count に加えて addressable / unaddressable / chunk-bbox case hash set を比較するため、bbox overlay fixture や element/table-cell/asset bbox evidence の同数入れ替えも止める。chunk template trend は recommended template だけでなく、template entry ごとの score、status、promotion_blocking、expected/measured case count、covered/missing source kind、covered/missing scenario、reason code count も比較するため、特定 template の測定面が薄くなった場合や `html_semantic` / `table_preserve_rows` が未測定化した場合も regression として検出できる。staging trend では real-world manifest の宣言数だけでなく、`executed_real_world_case_count`、`executed_compliant_real_world_case_count`、実行済み source kind / scenario 数、`missing_executed_source_kinds`、`missing_executed_scenarios`、`execution_error_count` の退化も regression にするため、strict adapter smoke が本物 fixture から少しずつ外れる運用を防げる。比較結果は `file-processing-trend-regression.json` / `file-processing-staging-trend-regression.json` として、比較 metric、許容幅、regression reason、result hash だけを含み、case detail / OCR 原文 / chunk 本文 / query / answer は含めない。nightly workflow は full report、trend、regression summary を保存し、RAGFlow / Docling / Marker / Unstructured 的な文書処理能力が時間経過で退化していないかを raw OCR 本文や chunk 本文を保存せずに追跡する。

adapter golden gate trend は、単なる `passed` / `failed` だけでなく、`mode`、`metrics_source`、selected/recommended backend、metrics applied target、required / manifest / covered source kinds、contract case count、contract missing source kinds、source route contract gap source kinds、blocker code count も比較する。bad set は件数だけでなく新規追加された source kind / metric / blocker code も比較するため、`missing_source_kinds` や `blocker_codes` が同数で別の悪化へ入れ替わった場合も regression になる。これにより Docling / Marker / Unstructured の推奨が local fallback へ戻る、staging metrics が runtime 値へすり替わる、PDF/Office/HTML/email/image の coverage が薄くなる、source route が contract gap を抱えたままになる、または blocker taxonomy が増える退化を、総合 metric が同じでも阻断できる。

parser adapter contract trend も、baseline で証明済みの source kind / backend / passed source kind / passed scenario が消えることと、missing / blocking source kind・scenario・backend が新規追加されることを比較する。case 数や missing 件数が同じでも、two-column PDF や Office layout の schema remap 証跡を HTML などの簡単 scenario に置き換えたり、blocking backend を別 adapter にすり替える退化は regression になる。

parser adapter source route、parser adapter scorecard、chunk template scorecard も reason / warning / missing set の新規追加を比較する。さらに source route は candidate / attempted / active backend の削除、chunk template は covered source kind / scenario の削除も検出する。件数が同じでも `local_fallback_due_to_contract_gap`、`adapter_package_missing`、`chunk_template_scenario_evidence_missing` のような悪化 code に置き換わった場合、または Marker/PDF や Office template coverage のような正向き証跡が別 backend/source/scenario に入れ替わった場合は regression とし、strict runtime smoke と staging manifest の schema remap evidence が同量の別 taxonomy で希釈されることを防ぐ。

real-world staging policy と backend/source-kind matrix も集合内容を比較する。`executed_source_kinds` / `executed_scenarios` は baseline から消えた source / scenario を regression にし、`missing_source_kinds` / `missing_scenarios` / `missing_executed_*` は新規追加を regression にする。backend/source-kind matrix の `missing_source_kinds` も同様に新規追加を止めるため、case 数や missing 件数が同じでも、Office fixture を外して別 source の fixture に置き換えるような coverage drift を合格にしない。

表 QA case は任意で `expected_table_cell_refs` を持てる。指定された場合、staging gate は回答に期待値が含まれることに加えて、検索 citation の metadata (`table_cell_refs` / `cell_refs` / `formula_cell_refs` など) が該当 cell ref を覆い、かつ同 ref が `StructuredExtraction.tables[].cells[].metadata` に解決できることを要求する。CSV/TSV/Markdown/HTML/Office の local table parser は A1 形式の `cell_ref` を `ExtractionTableCell.metadata` に付与し、row-group chunk には該当範囲の `table_cell_refs` を保持するため、単なる表 chunk 命中ではなく cell-level citation lineage を CI で検証できる。

外部 parser adapter から来る table cell metadata も同じ契約に正規化する。普通のセル住所は `cell_ref` として保持し、`formula_cell_ref` は `formula` / cached value などの式情報がある場合だけ追加する。これにより Docling / Marker / Unstructured の cell address を表 QA・preview jump へ使いつつ、通常セルを formula lineage と誤判定しない。

staging 用の実データ manifest は任意で `staging_dataset_policy` を持てる。`required_for_promotion=true` の場合、`fixture_kind=real_world` の case 数、required source kinds、required scenarios、`data_sensitivity=non_sensitive`、`reviewed_for_public_ci=true`、既定 `staging/` 配下の fixture 参照を manifest validation で確認する。これにより synthetic fixture をコピーしただけの “real-world gate” や、レビューされていない顧客文書を nightly artifact に流す運用を防ぐ。policy の validation は case id / source kind / scenario / fixture path prefix / status code だけを扱い、OCR 原文や chunk 本文を artifact に出さない。staging promotion では policy が設定されているのに `required_for_promotion=false` の場合を `staging_dataset_policy_not_required`、必須 policy の coverage / review / fixture 隔離が未達の場合を `staging_dataset_policy_failed` として明示的に止める。さらに staging CLI は manifest 上の合規 case だけでなく、本実行の `case_results` に real-world case が含まれ、required source kinds / scenarios を実測したかも `executed_*` evidence として検査する。`rag-file-processing-staging --require-real-world-policy` と nightly workflow の `require_real_world_file_processing_manifest=true` は policy 未設定の synthetic-only manifest を preflight で止めるため、production promotion では宣言なしの staging を通せない。manifest に real-world case を書いただけで実行 plan から漏れた場合も `staging_dataset_policy_failed` になる。promotion blocker には件数と不足 source kind / scenario だけを残し、real-world case id や fixture path を含めない。

### 回答品質の検証 CLI

`python -m app.rag.answer_verify_cli` は、rag_poc の検証スクリプトを移植した手動の検証ツール。`answers` / `regression` は、実行中の backend の API を呼ぶ（`--api-base-url`、既定 `http://localhost:8000`。`--tenant-id` / `--user-id` は `evaluation_cli` と同じ）。1 件ずつ `<out>/<id>.json` に保存し、既にあれば飛ばすので、中断しても同じコマンドで残りを実行できる。最後に `<out>/summary.md` を書く。結果には質問と回答の本文が入るため、`--out` は Git 管理外（`.runs/` 配下）にする。

```bash
# QA（id / question / standard_answer）を業務ビューで回答し、標準回答で評価する（評価の基準の指標と閾値。rag_poc の run_answer_eval.py 相当）
uv run python -m app.rag.answer_verify_cli answers --qa qa.json --business-view <業務ビュー ID> --out .runs/answers/<label>
# rag_poc の cases.json（id / question / expect）で回答を文字列の規則で判定する（rag_poc の run_regression.py）
uv run python -m app.rag.answer_verify_cli regression --cases cases.json --business-view <業務ビュー ID> --out .runs/regression/<label> --repeat 2
# CRAG goldset をオフラインで評価する（--llm-judge で backend のモデル設定の LLM に判定させる）
uv run python -m app.rag.answer_verify_cli crag-goldset crag_goldset.json
```

- `answers` は回答の記録がある回答だけ評価できる（安全ポリシーで止めた質問など、回答フローを通らなかった件はエラーとして記録する。#594 で回答はすべて同じ回答フローになった）。
- `regression` の判定は rag_poc と同じ規則（`applied_all` / `applied_excludes` / `gap_contains`）で、LLM を使わない。`cases.json` の `run_id` / `pdf` は読み捨てる（業務ビューの KB が検索範囲になる）。
- `crag-goldset` の終了コードも rag_poc と同じ（CRAG が標準 RAG より劣れば 1）。
- rag_poc の `evaluate_crag_grader.py` は、rag_poc 独自の ADB の保存先から候補を組み立てる設計のため移植していない。

## 観測性

Prometheus metrics は `/metrics` で公開する。

主なメトリクス:

- `rag_http_requests_total`
- `rag_http_request_duration_seconds`
- `rag_search_requests_total`
- `rag_search_duration_seconds`
- `rag_search_stage_duration_seconds`
- `rag_retrieval_hits`
- `rag_evaluation_cases_total`
- `rag_evaluation_case_duration_seconds`
- `rag_ingestion_documents_total`
- `rag_ingestion_chunks`
- `rag_ingestion_stage_duration_seconds`
- `rag_guardrail_findings_total`
- `rag_rate_limit_decisions_total`

`rag_search_stage_duration_seconds` は `mode`、`stage`、`outcome` label を持つ。`stage` は `history_rewrite`、`answer`、`retrieval`（検索だけの経路）、`outcome` は `success`、`error`、`cancelled` を使う。回答フローの中のどの工程（検索・Rerank・根拠確認・回答の生成など）が遅いかは、`diagnostics.answer.execution_steps`（負荷試験 CLI `search_load_cli` が工程ごとの p95 を集計する）で切り分ける。

`rag_ingestion_stage_duration_seconds` は `stage`、`outcome` label を持つ。`stage` は `vlm_extraction`、`chunking`、`embedding`、`indexing`、`outcome` は `success`、`error`、`cancelled` を使い、OCI Enterprise AI の OCR/構造化、chunking、OCI Generative AI embedding、Oracle AI Database indexing のどこで遅延・失敗しているかを切り分ける。

`rag_evaluation_cases_total{mode,status}` と `rag_evaluation_case_duration_seconds{mode,status}` は golden set の case 単位で記録する。`status` は `success` / `error` に固定し、case id や query 本文は label に入れない。

`rag_guardrail_findings_total{surface,code,severity,action}` は query、answer の guardrail finding を数える。`surface` は `query` / `answer`、`action` は `blocked` / `warning` に固定し、query 本文、回答本文、OCR 原文、tenant/user id は label に入れない。

`rag_rate_limit_decisions_total{scope,outcome}` は高コスト API の rate limit 判定を数える。`scope` は `search` / `evaluation` / `upload` / `ingest`、`outcome` は `allowed` / `blocked` に固定し、tenant/user id、IP、query 本文は label に入れない。429 応答には `Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset-After` を返す。

RAG 検索レスポンスには `trace_id` を含める。OCI Enterprise AI、OpenTelemetry、Langfuse を接続する場合は、この `trace_id` を親 ID として OCR、取込の各工程と、回答の `history_rewrite`・`answer`・`retrieval` の各 span に渡す。

検索・取込 pipeline は `app.trace` logger へ `rag_trace_span` イベントも出す。payload は `trace_event` に入り、`trace_id`、`span_name`、`outcome`、`duration_ms`、低 cardinality の attributes、`error_type` だけを含む。検索 stage は `history_rewrite`（チャットの会話履歴による質問の書き換え）、`answer`（回答フロー）、検索だけの経路の `retrieval`、取込 stage は `vlm_extraction`、`chunking`、`embedding`、`indexing` を使う。回答フローの中の工程（質問の理解・文書検索・Rerank・根拠確認など）の時間は、span ではなく `diagnostics.answer.execution_steps` と回答の記録に残る（旧 standard の `embedding`・`rerank`・`context_*`・`generation` などの stage は #595 で削除した）。query 本文、context 本文、OCR 原文、prompt、例外 message、tenant/user id の raw 値は含めない。この構造化ログは OpenTelemetry span や Langfuse trace へ橋渡しするための境界であり、Prometheus の aggregate metrics では追えない単一 request の遅延・失敗箇所を調査するために使う。`RAG_TRACE_EXPORT_HTTP_ENDPOINT` を設定すると、同じ脱機密化済み event を非同期 HTTP JSON で collector / gateway へ送信する。queue が満杯または送信失敗しても request は失敗させず、`rag_trace_export_dropped` / `rag_trace_export_failed` を `app.trace` logger に残す。

検索レスポンスと評価ケース結果には `diagnostics` も含める。これは `retrieval_strategy`（常に `hybrid`）、`retrieval_strategy_adapter`（`grounded` / `retrieval_only` / `blocked`）、`answer`（回答フローの診断。工程ごとの実行記録 `execution_steps`・根拠の構成・信頼度など）、安全チェックの policy / backend / 縮退、filter key、ナレッジベースの件数、適用した KB / 業務ビューの設定、RAG 設定 fingerprint だけで構成し、query 本文や secret は含めない。低召回や設定変更による品質回帰を trace id と合わせて調査するために使う。旧 standard の診断（`top_k`・`rerank_top_n`・query variant 件数・検索 / rerank / 去重 / context の加工の件数・context window・`stream_stage_timings` など）と、検索 UI の「適応展開」「依存昇格」の件数の表示は #595 で削除した。

citation の `metadata` には `section_title`、`section_path`、`section_level`、`content_kind`、`chunk_group_id`、`chunk_group_kind`、`chunk_part_index`、`chunk_part_count`、`text_sha256`、`text_chars` と、`retrieval_mode`、`vector_rank`、`keyword_rank`、`vector_score`、`keyword_score`、`rrf_score` を入れられる場合だけ含める。query 本文や OCR 原文は含めず、hybrid 検索で vector 側の召回漏れか keyword 側の語彙不一致か、また複雑文書のどの章節・親要素が根拠を拾ったかを per-case に確認する。

すべての HTTP レスポンスには `X-Request-ID` を付ける。未処理例外は `ApiResponse` の 500 に統一し、クライアントには内部詳細を返さず、`app.main` logger の `unhandled_api_error` に `request_id`、HTTP method、path、例外型を記録する。

### 監査ログ

RAG 検索ごとに `app.audit` logger へ `rag_search_audit` イベントを出す。payload は `audit_event` に入り、`trace_id`、`request_id`、`outcome`、filter key、guardrail code、検索件数・citation 件数、設定 fingerprint、引用 document id、経過時間を含む（検索モードは常に `hybrid`。旧 standard の検索・context の内訳の列は #595 以降は既定値で、既存の監査の行を変えないため列は残す。#596）。`X-Tenant-ID` / `X-User-ID` がある場合は raw 値ではなく `tenant_id_hash` / `user_id_hash` として保存する。`RAG_AUDIT_CONTEXT_HASH_SALT` は production で `.env` から注入する。

`outcome` は `success`、`blocked`、`error` を使う（`no_results` は旧 standard で citation が 0 件のとき LLM 生成をスキップしたことを示していた。#595 以降は記録しないが、列の値として残る）。回答フロー（検索・rerank・生成を含む）と answer guardrail の例外は `error` として記録し、API timeout は `error_stage=timeout` として記録する。監査ログには `error_stage` と `error_type` だけを残す。例外 message は query や回答本文を含む可能性があるため検索監査ログへ出さない。

取込ごとに `rag_ingestion_audit` イベントも出す。`trace_id`、`request_id`、`tenant_id_hash`、`user_id_hash`、`document_id`、`outcome`、原本 SHA-256、byte 数、document type、抽出 confidence、chunk/vector 件数、経過時間、エラー種別を含む。ユーザーが修正できる取込エラーだけ短い message を残し、未知の内部/SDK エラーでは例外 message を保存しない。

監査ログには query、回答本文、OCR 原文、tenant/user id の raw 値を出さない。query と原本は SHA-256 hash とメタデータのみ、tenant/user id は hash のみを保存し、契約番号・金額・取引先名などの機密業務データをログへ漏らさない。

production（`RAG_AUTH_MODE=production`）では client の `X-Tenant-ID` と `X-RAG-Allowed-*` header を使わず、tenant なし（単一 tenant）で動かし、対象範囲はログイン中の利用者（MCP ではサービストークンの利用者）から決める（#214 / #225）。local（`RAG_AUTH_MODE=local`）だけは開発・検証のため、`X-Tenant-ID` がある request で document / chunk の `tenant_id_hash` と照合して一覧、詳細、重複判定、retrieval を同一 tenant に限定する（tenant header がない場合は全体を参照できる）。同じく local で `X-RAG-Allowed-Document-Ids` / `X-RAG-Allowed-Category-Names` を付与した場合は、その request の一覧、詳細、chunk count、retrieval も指定 scope に閉じる。scope header が存在するが有効値が 0 件の場合は deny-all とし、未指定の場合だけ制限なしとして扱う。これらの raw scope 値は監査ログへ出さない。

## ガードレール

実装: `backend/app/rag/guardrails.py`

現在の参照ポリシー:

- 長すぎる query を拒否する。
- system prompt や過去指示の無視を求める prompt injection を拒否する。判定は NFKC・ゼロ幅空白やソフトハイフンなど表示されない書式文字(Unicode Cf)の除去・大小文字の正規化をした文字列で行い、`system_prompt` / `system-prompt` のような区切りの違いも同じ対象として扱う。
- `drop/delete/update/insert` などの SQL 変更文らしさは警告し、検索のみ実行する。
- query と answer の個人番号、口座番号、電話番号、メールアドレスらしき値は `[機微情報]` へマスクし、`sensitive_identifier_redacted` warning として返す。全角英数字・全角記号(`＠` `－`)・長音記号の区切り・不可視文字を挟んだ値もマスクする(その場合、マスク後の文字列は NFKC 正規化した表記になる)。マスク後の query を embedding / retrieval / 回答生成に使い(回答の記録に保存する質問もマスク後)、raw 値を監査ログや metrics へ出さない。
- チャットの会話履歴と今回の質問は、未信頼データとしてタグで囲んで回答生成へ渡す。囲む前に `&` `<` `>` を実体参照へ置き換え、発話に閉じタグを書いて囲みの外へ指示を置けないようにする。会話履歴から書き換えた質問も、元の質問と同じ安全チェックに通し、拒否されたら書き換えを使わない。
- 根拠が足りない場合は、回答フローが根拠の評価（CRAG）と回答文の生成後の根拠確認で、足りない理由（`insufficient_reason`）と人手確認の要否を回答に付ける（旧 standard の no-results 回答への短絡は #595 で削除した）。
- 回答に secret らしき文字列が含まれる場合は表示を止める。
- citation がある回答でも、回答と検索根拠の token / n-gram 重なりが少ない場合は `low_groundedness` warning を返し、監査ログにも guardrail code を残す。これは軽量なヒューリスティックであり、本番では LLM-as-judge や RAGAS 等の groundedness 評価で補強する。
- `search`、`evaluation`、`upload`、`ingest` は app 内 rate limit で保護する。bucket key は hash 済み tenant/user context を優先し、匿名時も client host を hash 化して使う。raw tenant/user id、IP、query 本文はログや metrics に出さない。本番では OCI API Gateway / Ingress / Redis などの共有 limiter と併用する。
- 認可済み document/category scope header がある場合は document 一覧、詳細、chunk count、retrieval のすべてに適用する。

ガードレールの厳格度は **Guardrail アダプター(`rag_guardrail_policy`)** で手動選択する。`app/rag/guardrail_adapter.py` が policy を effective 値へ解決し、`GuardrailPolicy` と `evaluate_groundedness(min_overlap=..., min_ratio=...)` を駆動する。`standard`(既定・現行値を再現)/ `strict`(groundedness 閾値↑)/ `lenient`(閾値↓で warning 抑制)/ `regulated`(strict + low_groundedness を error severity へ昇格し監査強調)。NeMo Guardrails / Llama Guard 的な概念を外部 SaaS や追加 LLM 呼び出しなしの決定論ヒューリスティックへ再マップする。`GET/PATCH /api/settings/guardrail` と専用設定画面で切替し、`SearchDiagnostics.guardrail_policy` に残す。既定 standard は現行挙動と一致する。

本番では以下を追加する。

- 業務固有の個人情報分類器や DLP サービスによる追加マスキング。
- citation がある回答に対する LLM-as-judge / RAGAS 等の追加 groundedness 評価。
- 権限・ロール情報を hash context へ追加する場合は、raw 値を出さず環境変数で管理する salt または HMAC key で相関する。
