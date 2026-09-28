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
  uvicorn app.main:app --host 127.0.0.1 --port 18011
```

## 起動(uv の venv + systemd。#286)

サービスごとの uv の venv(`uv sync --locked --no-dev --python 3.12`)で動き、本番・開発とも systemd の unit
(`production-ready-rag-preprocess-pdf-to-page-images.service`)で起動 / 停止する。Docker は使わない(Dockerfile は #356 で削除した)。

```bash
# rag/ で実行する
scripts/rag-services.sh install preprocess-pdf-to-page-images   # venv を作り unit と sudoers を登録して起動
scripts/rag-services.sh run preprocess-pdf-to-page-images       # systemd の無い環境で前面に起動
```
