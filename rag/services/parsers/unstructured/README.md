# parser-unstructured

Unstructured を独立 image で動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`
- unstructured[all-docs] のバージョンは本サービス単独で upgrade 可能
- OS 依存(libGL / poppler / tesseract)は本 image 内に隔離

## Docker

build context は **リポジトリ root**:

```bash
docker build -f services/parsers/unstructured/Dockerfile -t parser-unstructured .
```
