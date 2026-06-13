# Golden Set Evaluation

`golden-set.example.json` は `POST /api/evaluation/run` に渡す評価ファイルのテンプレートです。
実データ投入後、各 `relevant_document_ids` を環境内の document id に置き換えて `golden-set.json` として管理します。

```bash
cp evaluation/golden-set.example.json evaluation/golden-set.json
curl -X POST http://localhost:8000/api/evaluation/run \
  -H 'Content-Type: application/json' \
  -d @evaluation/golden-set.json
```

CI / staging gate では `thresholds` を必ず設定し、レスポンスの `passed=false`、`error_count>0`、または `threshold_failures` 非空を失敗条件にします。

CI / nightly では CLI を使うと、評価結果 JSON を artifact として保存しつつ終了コードで gate できます。

```bash
cd backend
uv run python -m app.rag.evaluation_cli \
  ../evaluation/golden-set.json \
  --api-url http://localhost:8000/api/evaluation/run \
  --output ../evaluation/evaluation-result.json
```

終了コード:

- `0`: 評価 gate 成功。
- `1`: API は正常に応答したが `passed=false`、`error_count>0`、または `threshold_failures` 非空。
- `2`: golden set ファイルや CLI 引数が不正。
- `3`: 評価 API への接続、HTTP 応答、レスポンス形式の問題。

`RAG_EVALUATION_API_URL`、`RAG_EVALUATION_TIMEOUT_SECONDS`、`RAG_EVALUATION_TENANT_ID`、`RAG_EVALUATION_USER_ID` でも指定できます。tenant/user の raw 値は CLI 出力には表示しません。
