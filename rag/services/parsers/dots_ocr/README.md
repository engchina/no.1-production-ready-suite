# parser-dots-ocr (GPU)

Dots.OCR を **GPU(CUDA)** イメージで動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`
- GPU 依存(CUDA torch / Dots.OCR モデル)は本 image に隔離
- compose では `gpu` profile で opt-in 起動

## GPU 統合シーム

実 OCR の呼び出しは `rag_parser_core.registry._run_dots_ocr`。Dots.OCR は PyPI 配布が
無いため GitHub から install する(pyproject の `[tool.uv.sources]`)。API は実 GPU 環境で
疎通確認すること。remap 層は CPU の fixture テストで担保済み。

## Docker(GPU host)

```bash
docker build -f services/parsers/dots_ocr/Dockerfile -t parser-dots-ocr .
docker compose --profile gpu up parser-dots-ocr
```
