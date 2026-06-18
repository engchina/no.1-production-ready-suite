# preprocess-service

parse の **前** に原本を一度だけ canonical な中間物へ変換する前処理マイクロサービス。

- 出力契約: `rag_parser_core` の `ConvertResponse`(`POST /convert`)
- readiness: `GET /health`(LibreOffice / PyMuPDF の導入状況・対応プリセット)
- 変換依存(LibreOffice / PyMuPDF)は本サービス単独で upgrade 可能(他 parser / backend に非干渉)

## 対応プリセット

| profile | 変換 | 実装 |
|---|---|---|
| `office_to_pdf` | Office → PDF | LibreOffice headless(`soffice`) |
| `pdf_to_page_images` | PDF → 画像のみ PDF(各ページをラスタライズ) | PyMuPDF |

`passthrough` / `text_normalize` は backend in-process で処理するため、本サービスは呼ばれない。
依存が未導入・変換失敗のときは `converted=false`(passthrough)を返し、backend は原本のまま parse する。

## ローカル実行(開発)

```bash
# repo root から(共有 package の path source を解決するため)
uv run --directory services/preprocess \
  uvicorn app.main:app --host 0.0.0.0 --port 8010
```

## Docker

build context は **リポジトリ root**(compose が設定済み):

```bash
docker build -f services/preprocess/Dockerfile -t preprocess .
```
