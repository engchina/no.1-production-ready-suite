# parser-dots-ocr (GPU)

Dots.OCR を **GPU(CUDA)** イメージで動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`
- GPU 依存(CUDA torch / Dots.OCR モデル)は本 image に隔離
- compose では `gpu` profile で opt-in 起動

## GPU 統合シーム

実 OCR の呼び出しは `rag_parser_core.registry._run_dots_ocr`。Dots.OCR は PyPI 配布が
無いため GitHub から install する(pyproject の `[tool.uv.sources]`)。

既定 runtime は Dots.mOCR 公式推奨の vLLM server + upstream `DotsOCRParser`。
統一 `POST /parse` contract への変換だけをこの microservice で行う。

主な環境変数:

- `DOTS_OCR_RUNTIME`: `vllm` / `hf_explicit_cuda`。既定は `vllm`
- `DOTS_OCR_PROTOCOL`: vLLM server protocol。既定は `http`
- `DOTS_OCR_IP`: vLLM server host。既定は `parser-dots-ocr-vllm`
- `DOTS_OCR_PORT`: vLLM server port。既定は `8000`
- `DOTS_OCR_MODEL_NAME`: vLLM served model name。既定は `model`
- `DOTS_OCR_MODEL_ID`: HuggingFace model id。既定は `rednote-hilab/dots.mocr`
- `DOTS_OCR_DEVICE`: 明示 CUDA device。既定は `cuda:0`
- `DOTS_OCR_TORCH_DTYPE`: `bfloat16` / `float16` / `float32`。既定は `bfloat16`
- `DOTS_OCR_ATTENTION_IMPLEMENTATION`: `sdpa` / `eager` / `flash_attention_2`。既定は `sdpa`
- `DOTS_OCR_PROMPT_MODE`: Dots.OCR prompt mode。既定は `prompt_layout_all_en`

## Docker(GPU host)

```bash
docker build -f services/parsers/dots_ocr/Dockerfile -t parser-dots-ocr .
docker compose --profile gpu --profile gpu-vllm up parser-dots-ocr-vllm parser-dots-ocr
```

vLLM を別途起動しない standalone 検証では `DOTS_OCR_RUNTIME=hf_explicit_cuda` を指定する。
