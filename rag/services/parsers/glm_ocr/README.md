# parser-glm-ocr (GPU)

GLM-OCR を **GPU(CUDA)** で動かす parser マイクロサービス。

vLLM(OpenAI 互換)サーバと parser FastAPI を **単一イメージ/単一コンテナ**で起動する。
従来あった別建ての `parser-glm-ocr-vllm` sidecar は廃止し、この 1 サービスへ統合した。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`(parser プロセス)+ 内蔵 vLLM `/health` を runtime_health で反映
- GPU 依存(vLLM / CUDA torch / transformers / GLM-OCR モデル)は本 image に隔離
- compose では `gpu`(または `glm-ocr`)profile で opt-in 起動

## 構成

公式 vLLM イメージ `vllm/vllm-openai` をベースに parser app の HTTP 依存だけを追加する。
コンテナ起動時に [`entrypoint.sh`](./entrypoint.sh) が:

1. `vllm serve zai-org/GLM-OCR`(`--served-model-name glm-ocr`)を `127.0.0.1:8080` で内部起動
2. parser FastAPI(gunicorn)を `0.0.0.0:8000` で公開起動

parser は同一コンテナ内 localhost の vLLM へ OCR を HTTP 委譲する。モデルロード完了前は
サービス管理 UI 上で「推論サーバー未起動」として可視化される。

## 環境変数

| 環境変数 | 既定 | 説明 |
|---|---|---|
| `GLM_OCR_RUNTIME` | `vllm` | `vllm` / `transformers` |
| `GLM_OCR_VLLM_BASE_URL` | `http://127.0.0.1:8080/v1` | 同一コンテナ内 vLLM endpoint |
| `GLM_OCR_VLLM_MODEL` | `glm-ocr` | vLLM served model name |
| `GLM_OCR_MODEL` | `zai-org/GLM-OCR` | vLLM がロードする HuggingFace モデル repo id。既定モデルはビルド時にイメージへ焼き込み済み(`--build-arg GLM_OCR_MODEL=...` で差替) |
| `GLM_OCR_GPU_MEMORY_UTILIZATION` | `0.90` | vLLM の GPU メモリ利用率 |
| `GLM_OCR_TENSOR_PARALLEL_SIZE` | `1` | vLLM tensor parallel 数 |
| `GLM_OCR_VLLM_EXTRA_ARGS` | (空) | vLLM への追加引数。例: 投機的デコード `--speculative-config.method mtp --speculative-config.num_speculative_tokens 1` |
| `GLM_OCR_PROMPT` | `Text Recognition:` | 画像へ与える OCR プロンプト |
| `GLM_OCR_MAX_NEW_TOKENS` | `8192` | 生成上限トークン |
| `HF_HOME` | `/root/.cache/huggingface` | モデルキャッシュ。既定モデルはイメージ同梱なので実行時 DL は発生しない |

`GLM_OCR_RUNTIME=transformers` の退避経路(transformers 直ロード)も残しているが、
本 image は vLLM 経路を既定とする。

## GPU 統合シーム

実 OCR の呼び出しは `rag_parser_core.registry._run_glm_ocr`。既定では localhost の vLLM
endpoint を OpenAI-compatible API で呼ぶ。remap 層は CPU の fixture テストで担保済み。

## Docker(GPU host)

```bash
docker build -f services/parsers/glm_ocr/Dockerfile -t parser-glm-ocr .
docker compose --profile glm-ocr up parser-glm-ocr
```

参考: [GLM-OCR (HuggingFace)](https://huggingface.co/zai-org/GLM-OCR) /
[GLM-OCR (GitHub)](https://github.com/zai-org/GLM-OCR) /
[vLLM recipe](https://docs.vllm.ai/projects/recipes/en/latest/GLM/GLM-OCR.html)
