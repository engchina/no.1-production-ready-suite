# 前処理(Preprocess)マイクロサービス群

parse の **前** に原本を一度だけ canonical な中間物へ変換する前処理ステージ。
`services/parsers/<name>` と同じく **1 変換 = 1 独立マイクロサービス**で、各々独自依存・
独自 Dockerfile で独立 upgrade / 独立スケールでき、相互・backend に非干渉。

| サービス | profile | 変換 | 主依存 | 既定 URL |
|---|---|---|---|---|
| [office_to_pdf](./office_to_pdf) | `office_to_pdf` | Office → PDF | LibreOffice | `http://preprocess-office-to-pdf:8000` |
| [pdf_to_page_images](./pdf_to_page_images) | `pdf_to_page_images` | PDF → 画像PDF | PyMuPDF | `http://preprocess-pdf-to-page-images:8000` |
| [csv_to_json](./csv_to_json) | `csv_to_json` | CSV → 構造化 JSON | (純 Python) | `http://preprocess-csv-to-json:8000` |
| [excel_to_json](./excel_to_json) | `excel_to_json` | Excel(.xls/.xlsx) → 構造化 JSON | openpyxl + xlrd | `http://preprocess-excel-to-json:8000` |
| [url_to_markdown](./url_to_markdown) | `url_to_markdown` | URL → クリーン Markdown | httpx + trafilatura | `http://preprocess-url-to-markdown:8000` |
| [image_enhance](./image_enhance) | `image_enhance` | 画像 → OCR 向け補正画像 | OpenCV | `http://preprocess-image-enhance:8000` |

`passthrough` / `text_normalize` は backend in-process で処理するため、サービスは呼ばれない。

- 出力契約は共有 package `rag_parser_core` の `ConvertResponse`(`POST /convert`)で統一。
- backend は `app.clients.preprocess_service.PreprocessServiceClient` が profile ごとに
  対応サービス URL(`RAG_PREPROCESS_<PROFILE>_SERVICE_URL`)へ HTTP 委譲する。
  サービス無効・未達・timeout 時は warning を付けて **passthrough(原本そのまま parse)** へ
  安全に縮退する。
- 選択は `RAG_PREPROCESS_PROFILE`、委譲の有効化は `RAG_PREPROCESS_ENABLED`、timeout は
  `RAG_PREPROCESS_SERVICE_TIMEOUT_SECONDS`。

## 起動

- **開発**: 触る変換のサービスだけローカル起動(各サービス README 参照)。
  in-process(passthrough / text_normalize)は何も起動不要。
- **本番**: 各サービスを Docker イメージ化し、Docker Compose(単一ホスト)/ OKE
  (Deployment+Service)/ OCI Container Instances へ独立デプロイ・独立スケールする。
  build context は **リポジトリ root**(共有 package を含めるため)。
