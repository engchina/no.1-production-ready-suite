# preprocess-pdf-to-page-images

parse の **前** に PDF の各ページを PyMuPDF でラスタライズし、画像のみの PDF を再構成する
前処理マイクロサービス(engchina/No.1-PdfParser-Free 風)。スキャン/複雑 PDF を VLM OCR
経路へ確実に載せるための変換。

- 出力契約: `rag_parser_core` の `ConvertResponse`(`POST /convert`、`page_map` 付き)
- readiness: `GET /health`(PyMuPDF 導入状況)
- 変換依存(PyMuPDF)は本サービス単独で upgrade 可能(他 parser / backend に非干渉)
- 依存未導入・変換失敗のときは `converted=false`(passthrough)を返し、backend は原本のまま parse する

## ローカル実行(開発)

```bash
# repo root から(共有 package の path source を解決するため)
uv run --directory services/preprocess/pdf_to_page_images \
  uvicorn app.main:app --host 0.0.0.0 --port 8011
```

## Docker

build context は **リポジトリ root**(compose が設定済み):

```bash
docker build -f services/preprocess/pdf_to_page_images/Dockerfile -t preprocess-pdf-to-page-images .
```
