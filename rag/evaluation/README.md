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

終了コード:

- `0`: 評価 gate 成功。
- `1`: API は正常に応答したが `passed=false`、`error_count>0`、または `threshold_failures` 非空。
- `2`: golden set ファイルや CLI 引数が不正。
- `3`: 評価 API への接続、HTTP 応答、レスポンス形式の問題。

`RAG_EVALUATION_API_BASE_URL`、`RAG_EVALUATION_RUN_API_URL`、`RAG_EVALUATION_COMPARE_API_URL`、`RAG_EVALUATION_API_URL`、`RAG_EVALUATION_TIMEOUT_SECONDS`、`RAG_EVALUATION_TENANT_ID`、`RAG_EVALUATION_USER_ID` でも指定できます。`--api-url` は最優先で、`--api-base-url` は入力形式に応じて `/api/evaluation/run` または `/api/evaluation/compare` を付与します。tenant/user の raw 値は CLI 出力には表示しません。
