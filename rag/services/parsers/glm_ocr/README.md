# parser-glm-ocr (GPU)

GLM-OCR を **GPU(CUDA)** で動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`
- GPU 依存(vLLM / CUDA torch / transformers / GLM-OCR モデル)は parser service 群に隔離
- compose では `gpu` profile で opt-in 起動

## モデルと環境変数

既定 runtime は GLM-OCR 公式 self-host の vLLM OpenAI-compatible endpoint。
統一 `POST /parse` contract への変換だけをこの microservice で行う。

| 環境変数 | 既定 | 説明 |
|---|---|---|
| `GLM_OCR_RUNTIME` | `vllm` | `vllm` / `transformers` |
| `GLM_OCR_VLLM_BASE_URL` | `http://parser-glm-ocr-vllm:8080/v1` | 公式 self-host vLLM endpoint |
| `GLM_OCR_VLLM_MODEL` | `glm-ocr` | vLLM served model name |
| `GLM_OCR_MODEL_ID` | `zai-org/GLM-OCR` | `transformers` runtime でロードする HuggingFace モデル repo id |
| `GLM_OCR_PROMPT` | `Text Recognition:` | 画像へ与える OCR プロンプト |
| `GLM_OCR_MAX_NEW_TOKENS` | `8192` | 生成上限トークン |
| `GLM_OCR_TORCH_DTYPE` | `bfloat16` | CUDA 上で使う dtype。`bfloat16` / `float16` / `float32` |
| `HF_HOME` | `/home/appuser/.cache/huggingface` | モデルキャッシュ。永続化は volume を割り当てる |

## GPU 統合シーム

実 OCR の呼び出しは `rag_parser_core.registry._run_glm_ocr`。既定では vLLM endpoint を
OpenAI-compatible API で呼ぶ。`GLM_OCR_RUNTIME=transformers` の場合だけ、旧来の
transformers 直ロードを退避経路として使う。remap 層は CPU の fixture テストで担保済み。

## Docker(GPU host)

```bash
docker build -f services/parsers/glm_ocr/Dockerfile -t parser-glm-ocr .
docker compose --profile gpu --profile gpu-vllm up parser-glm-ocr-vllm parser-glm-ocr
```
