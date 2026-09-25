# preprocess-office-to-pdf

parse の **前** に Office 文書(docx/pptx/xlsx/odt 等)を LibreOffice headless で PDF へ
変換する前処理マイクロサービス。

- 出力契約: `rag_parser_core` の `ConvertResponse`(`POST /convert`)
- readiness: `GET /health`(LibreOffice 導入状況)
- 変換依存(LibreOffice)は本サービス単独で upgrade 可能(他 parser / backend に非干渉)
- 依存未導入・変換失敗のときは `converted=false`(passthrough)を返し、backend は原本のまま parse する

## ローカル実行(開発)

```bash
# repo root から(共有 package の path source を解決するため)
uv run --directory services/preprocess/office_to_pdf \
  uvicorn app.main:app --host 0.0.0.0 --port 8010
```

## Docker

build context は **リポジトリ root**(compose が設定済み):

```bash
docker build -f services/preprocess/office_to_pdf/Dockerfile -t preprocess-office-to-pdf .
```
