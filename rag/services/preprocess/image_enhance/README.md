# 前処理: 画像補正(`image_enhance`)

スキャン画像・写真を **OCR しやすい形へ補正**する前処理マイクロサービス。グレースケール化 →
ノイズ除去 → CLAHE コントラスト均一化 → 軽い傾き補正(deskew)を決定論で行い、PNG(可逆)で
返す。後段の OCR parser(mineru / dots_ocr / glm_ocr / Enterprise AI VLM)の精度を前段で
底上げする。**ローカル OSS(OpenCV)のみ**で完結し外部 SaaS は呼ばない(確定スタック非抵触)。

| 項目 | 値 |
|---|---|
| profile | `image_enhance` |
| 主依存 | opencv-python-headless + numpy(純ローカル CPU) |
| 既定 URL | `http://preprocess-image-enhance:8000` |
| dev port | 8015 |
| profile 種別 | CPU(dev は uv プロセス) |

## 補正パイプライン

1. デコード(失敗は passthrough)
2. 長辺 4000px 超は縮小(CPU/メモリ抑制)
3. グレースケール化
4. `fastNlMeansDenoising` でノイズ除去
5. CLAHE でコントラスト均一化
6. テキスト最小外接矩形から傾き推定 → ±15° 以内のみ補正(誤検出で画像を壊さない)

## 契約

- `POST /convert`(`rag_parser_core.ConvertResponse`)。出力は `image/png`。非画像・空・
  復号失敗・未対応 profile は **passthrough** へ縮退。
- `GET /health` → OpenCV 可用性で `ok` / `degraded`。

## 起動

```bash
# dev(ホストの uv プロセス)
uv run --directory services/preprocess/image_enhance uvicorn app.main:app --port 8015

# Docker(build context = リポジトリ root)
docker compose up preprocess-image-enhance
```
