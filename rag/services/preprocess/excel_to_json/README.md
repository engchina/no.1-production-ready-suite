# preprocess-excel-to-json

parse の **前** に Excel(`.xls` / `.xlsx`)原本を決定論で構造化 JSON(シート単位の
レコード配列)へ変換する前処理マイクロサービス。

- 出力契約: `rag_parser_core` の `ConvertResponse`(`POST /convert`)
- readiness: `GET /health`(openpyxl / xlrd 導入状況)
- `.xlsx` は openpyxl(read_only / data_only)、`.xls` は xlrd で読む(形式は magic bytes 判定)
- 依存(openpyxl / xlrd)は本サービス単独で upgrade 可能(他 parser / backend に非干渉)
- 依存未導入・解析失敗・空のときは `converted=false`(passthrough)を返し、backend は原本のまま parse する

## 変換仕様

| 項目 | 挙動 |
|---|---|
| 対応形式 | `.xlsx`(openpyxl)/ `.xls`(xlrd 2.x) |
| シート | 複数シートをシート順に走査 |
| 出力 | `{"sheets": [{"name", "columns", "row_count", "rows": [{列: 値}]}], "sheet_count": N}` |
| セル値 | None→空文字、整数 float は小数点を落とす、日付は ISO 8601 |
| 縮退 | 空・依存欠如・解析失敗・行 0 のときは passthrough |

## ローカル実行(開発)

```bash
# repo root から(共有 package の path source を解決するため)
uv run --directory services/preprocess/excel_to_json \
  uvicorn app.main:app --host 127.0.0.1 --port 18013
```

## 起動(uv の venv + systemd。#286)

サービスごとの uv の venv(`uv sync --locked --no-dev --python 3.12`)で動き、本番・開発とも systemd の unit
(`production-ready-rag-preprocess-excel-to-json.service`)で起動 / 停止する。Docker は使わない(Dockerfile は #356 で削除した)。

```bash
# rag/ で実行する
scripts/rag-services.sh install preprocess-excel-to-json   # venv を作り unit と sudoers を登録して起動
scripts/rag-services.sh run preprocess-excel-to-json       # systemd の無い環境で前面に起動
```
