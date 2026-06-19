# parser-glm-ocr (GPU)

GLM-OCR を **GPU(CUDA)** イメージで動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`
- GPU 依存(CUDA torch / transformers / GLM-OCR モデル)は本 image に隔離
- compose では `gpu` profile で opt-in 起動

## モデルと環境変数

GLM-OCR は専用 pip package を持たず HuggingFace 配布(既定 `zai-org/GLM-OCR`)。
transformers で HF からモデルをロードする。

| 環境変数 | 既定 | 説明 |
|---|---|---|
| `GLM_OCR_MODEL_ID` | `zai-org/GLM-OCR` | ロードする HuggingFace モデル repo id |
| `GLM_OCR_PROMPT` | (Markdown 書き起こし指示) | 画像へ与える OCR プロンプト |
| `GLM_OCR_MAX_NEW_TOKENS` | `8192` | 生成上限トークン |
| `HF_HOME` | `/home/appuser/.cache/huggingface` | モデルキャッシュ。永続化は volume を割り当てる |

## GPU 統合シーム

実 OCR の呼び出しは `rag_parser_core.registry._run_glm_ocr`。ラッパー module `glm_ocr` が
あればそのエントリポイントを使い、無ければ transformers で HF モデルをロードする。API は
実 GPU 環境で疎通確認すること。remap 層は CPU の fixture テストで担保済み。

## Docker(GPU host)

```bash
docker build -f services/parsers/glm_ocr/Dockerfile -t parser-glm-ocr .
docker compose --profile gpu up parser-glm-ocr
```
