# parser-mineru (GPU)

MinerU を **GPU(CUDA)** イメージで動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`
- GPU 依存(CUDA torch / MinerU モデル)は本 image に隔離
- compose では `gpu` profile で opt-in 起動(CPU 環境では起動しない)

## GPU 統合シーム

実 OCR の呼び出しは `rag_parser_core.registry._run_mineru`(MinerU の高レベル API を
順に試行)。MinerU の version で API が変わるため、実 GPU 環境で疎通を確認すること。
remap 層(出力→`StructuredExtraction`)は CPU の fixture テストで担保済み
(`packages/rag_parser_core/tests/test_ocr_engine_remap.py`)。

## Docker(GPU host)

```bash
docker build -f services/parsers/mineru/Dockerfile -t parser-mineru .
docker compose --profile gpu up parser-mineru
```
