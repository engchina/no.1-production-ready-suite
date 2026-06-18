# parser-docling

Docling を独立 image で動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`(導入 version を返す)
- docling のバージョンは本サービス単独で upgrade 可能(他 parser / backend に非干渉)

## ローカル実行(開発)

```bash
# repo root から(共有 package の path source を解決するため)
uv run --directory services/parsers/docling \
  uvicorn app.main:app --host 0.0.0.0 --port 8001
```

## Docker

build context は **リポジトリ root**(compose が設定済み):

```bash
docker build -f services/parsers/docling/Dockerfile -t parser-docling .
```
