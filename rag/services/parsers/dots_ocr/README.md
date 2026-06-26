# parser-dots-ocr (GPU)

Dots.OCR を **GPU(CUDA)** イメージで動かす parser マイクロサービス。

vLLM(OpenAI 互換)サーバと parser FastAPI を **単一イメージ/単一コンテナ**で起動する。
従来あった別建ての `parser-dots-ocr-vllm` sidecar は廃止し、この 1 サービスへ統合した。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`(parser プロセス)+ 内蔵 vLLM `/health` を runtime_health で反映
- GPU 依存(vLLM / CUDA torch / Dots.OCR モデル)は本 image に隔離
- compose では `gpu`(または `dots-ocr`)profile で opt-in 起動

## 構成

公式 vLLM イメージ `vllm/vllm-openai`(vLLM 0.11+ で Dots.OCR を公式サポート)をベースに、
Dots.OCR 本体(GitHub 配布)と parser app の HTTP 依存を追加する。Dots.OCR の requirements は
`transformers==4.56.1` を hard pin するため、base(vLLM 用 transformers)を壊さないよう
`--no-deps` で導入し、vLLM 経路に必要な軽量依存だけを別途入れる。

コンテナ起動時に [`entrypoint.sh`](./entrypoint.sh) が:

1. `vllm serve rednote-hilab/dots.mocr`(`--served-model-name model --chat-template-content-format string`)を `127.0.0.1:8080` で内部起動
2. parser FastAPI(gunicorn)を `0.0.0.0:8000` で公開起動

parser(upstream `DotsOCRParser`)は同一コンテナ内 localhost の vLLM へ OCR を HTTP 委譲する。
モデルロード完了前はサービス管理 UI 上で停止として可視化される。

## 主な環境変数

| 環境変数 | 既定 | 説明 |
|---|---|---|
| `DOTS_OCR_RUNTIME` | `vllm` | `vllm` / `hf_explicit_cuda` |
| `DOTS_OCR_PROTOCOL` | `http` | vLLM server protocol |
| `DOTS_OCR_IP` | `127.0.0.1` | 同一コンテナ内 vLLM host |
| `DOTS_OCR_PORT` | `8080` | 同一コンテナ内 vLLM port |
| `DOTS_OCR_MODEL_NAME` | `model` | vLLM served model name |
| `DOTS_OCR_MODEL` | `rednote-hilab/dots.mocr` | vLLM がロードする HuggingFace model id(entrypoint) |
| `DOTS_OCR_GPU_MEMORY_UTILIZATION` | `0.90` | vLLM の GPU メモリ利用率 |
| `DOTS_OCR_TENSOR_PARALLEL_SIZE` | `1` | vLLM tensor parallel 数 |
| `DOTS_OCR_VLLM_EXTRA_ARGS` | (空) | vLLM への追加引数 |
| `DOTS_OCR_PROMPT_MODE` | `prompt_layout_all_en` | Dots.OCR prompt mode |

`DOTS_OCR_RUNTIME=hf_explicit_cuda` の退避経路(transformers 直ロード)も残している。

## GPU 統合シーム

実 OCR の呼び出しは `rag_parser_core.registry._run_dots_ocr`。既定では localhost の vLLM
endpoint を upstream `DotsOCRParser(use_hf=False)` 経由で呼ぶ。remap 層は CPU の fixture
テストで担保済み。

## Docker(GPU host)

```bash
docker build -f services/parsers/dots_ocr/Dockerfile -t parser-dots-ocr .
docker compose --profile dots-ocr up parser-dots-ocr
```

参考: [dots.ocr (HuggingFace)](https://huggingface.co/rednote-hilab/dots.ocr) /
[dots.ocr (GitHub)](https://github.com/rednote-hilab/dots.ocr) /
[vLLM Dots OCR PR](https://github.com/vllm-project/vllm/pull/24645)
