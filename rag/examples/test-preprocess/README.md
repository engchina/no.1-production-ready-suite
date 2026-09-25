# 前処理(Preprocess)動作確認サンプル

サービス管理画面に並ぶ 7 つの前処理マイクロサービスを、実入力で確認するためのサンプルファイル。
各サービスの `convert()` を実依存込みで叩く(LibreOffice / PyMuPDF / openpyxl / OpenCV / Presidio)。

| サンプル | サービス (slug) | 機能 |
|---|---|---|
| `document.docx` | `preprocess-office-to-pdf` | Office → PDF |
| `pages.pdf` | `preprocess-pdf-to-page-images` | PDF → 画像PDF |
| `table.csv` | `preprocess-csv-to-json` | CSV → JSON |
| `table.xlsx` | `preprocess-excel-to-json` | Excel → JSON |
| `urls.txt` | `preprocess-url-to-markdown` | URL → Markdown(実ネット取得 + SSRF ガード) |
| `photo.png` | `preprocess-image-enhance` | 画像補正(downscale / deskew / denoise) |
| `pii.txt` | `preprocess-pii-redact` | PII マスク(EMAIL / PHONE / URL / カード番号) |

## 実行方法

各サービスは独立 venv。`<service>` ディレクトリで `uv run` する。
`EX` は本フォルダの絶対パス(`examples/test-preprocess`)に読み替える。

```bash
# 例: Excel → JSON
cd services/preprocess/excel_to_json
EX=../../../examples/test-preprocess uv run python - <<'PY'
import os, app.converters as c
data = open(f"{os.environ['EX']}/table.xlsx","rb").read()
o = c.convert(data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "excel_to_json", None)
print(o.converted, o.derived_content_type, o.warnings)
print((o.derived_bytes or b"").decode("utf-8","replace"))
PY
```

`preprocess_profile`(第3引数)は各サービスの slug 末尾と一致させる:
`office_to_pdf` / `pdf_to_page_images` / `csv_to_json` / `excel_to_json` / `url_to_markdown` / `image_enhance` / `pii_redact`。
プロファイルが一致しない・依存欠如・変換失敗時は `ConvertOutcome.passthrough`(`converted=False`)へ縮退する。

### 注意

- `office_to_pdf` は `source_profile.extension`(例 `.docx`)で入力 suffix を決める。`SourceProfile` を渡すか、既定では `.docx` 扱い。
- `url_to_markdown` は実ネット取得。`_is_safe_url` が公開 IP のみ許可するため、社内環境では到達可能な公開 URL に置き換える。テストでは `fetcher`/`extractor` を注入してネット不要にもできる。
- `pii_redact` は初回実行時に spaCy 日本語モデル(`ja_core_news_lg`, 約530MB)を取得する。
