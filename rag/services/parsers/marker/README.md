# parser-marker

Marker を独立 image で動かす parser マイクロサービス(PDF / 画像)。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`
- LLM 補正は無効(非 OCI provider を混ぜない)
- marker-pdf のバージョンは本サービス単独で upgrade 可能

## Docker

build context は **リポジトリ root**:

```bash
docker build -f services/parsers/marker/Dockerfile -t parser-marker .
```
