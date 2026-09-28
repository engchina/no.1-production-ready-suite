# preprocess-csv-to-json

parse の **前** に CSV 原本を決定論で構造化 JSON(ヘッダ列をキーにしたレコード配列)へ
変換する前処理マイクロサービス。engchina/No.1 系 `csv2json` の本プロジェクト再マップ。

- 出力契約: `rag_parser_core` の `ConvertResponse`(`POST /convert`)
- readiness: `GET /health`(外部依存なしで常時 ready)
- 純 Python(`csv` + `json`)のみ。LibreOffice / PyMuPDF を含まない軽量イメージ。

## 変換仕様

| 項目 | 挙動 |
|---|---|
| 文字コード | UTF-8(BOM 可)優先、失敗時 charset-normalizer で推定 |
| デリミタ | `,` `\t` `;` `\|` を Sniffer で推定(失敗時はカンマ) |
| 出力 | `{"columns": [...], "row_count": N, "rows": [{列: 値}, ...]}` の JSON |
| 縮退 | 空・復号失敗・行 0 のときは `converted=false`(passthrough) |

## ローカル実行(開発)

```bash
# repo root から(共有 package の path source を解決するため)
uv run --directory services/preprocess/csv_to_json \
  uvicorn app.main:app --host 0.0.0.0 --port 8012
```

## 起動(uv の venv + systemd。#286)

サービスごとの uv の venv(`uv sync --locked --no-dev --python 3.12`)で動き、本番・開発とも systemd の unit
(`production-ready-rag-preprocess-csv-to-json.service`)で起動 / 停止する。Docker は使わない(`Dockerfile` は後続の PR で削除する)。

```bash
# rag/ で実行する
scripts/rag-services.sh install preprocess-csv-to-json   # venv を作り unit と sudoers を登録して起動
scripts/rag-services.sh run preprocess-csv-to-json       # systemd の無い環境で前面に起動
```
