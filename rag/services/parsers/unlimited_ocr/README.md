# parser-unlimited-ocr

Unlimited-OCR を独立 GPU image で動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- 既定モデル: `baidu/Unlimited-OCR`
- 実行方式: 同一コンテナ内 SGLang(OpenAI-compatible streaming API)

```bash
docker build -f services/parsers/unlimited_ocr/Dockerfile -t parser-unlimited-ocr .
docker compose --profile unlimited-ocr up parser-unlimited-ocr
```
