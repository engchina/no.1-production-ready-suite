# parser マイクロサービス群

外部 parser を **backend から切り離した独立 FastAPI サービス**として動かす。各サービスは
独自の依存・Dockerfile を持ち、**単独で upgrade しても他 parser / backend に影響しない**。

| サービス | 実行 | 既定起動 | 備考 |
|---|---|---|---|
| `docling` | CPU | ✅ | PDF/Office/HTML/画像 |
| `marker` | CPU | ✅ | PDF/画像(LLM 補正は無効) |
| `unstructured` | CPU | ✅ | 多形式 partition |
| `mineru` | **GPU** | `--profile gpu` | 実 OCR/レイアウト解析 |
| `dots_ocr` | **GPU** | `--profile gpu` | 実 OCR(GitHub install) |
| `glm_ocr` | **GPU** | `--profile gpu` | 実 OCR(HuggingFace zai-org/GLM-OCR / transformers) |

## 共通 HTTP 契約(`rag_parser_core`)

- `POST /parse`(multipart: `file` / `content_type` / `source_profile` JSON)→ `ParseResponse`
  (= `StructuredExtraction` + parser メタ)
- `GET /health` → `{status, backend, package_name, package_version}`(readiness 用)

backend は取込時に `ParserServiceClient` で HTTP 委譲し、未達時は local / Enterprise AI VLM へ
fallback する。詳細は [AGENTS.md](../../AGENTS.md) の「Parser マイクロサービス」節。

## 起動

```bash
# CPU parser + backend + frontend(リポジトリ root から)
docker compose up backend ingestion-worker frontend parser-docling parser-marker parser-unstructured

# GPU parser(CUDA host)
docker compose --profile gpu up parser-mineru parser-dots-ocr parser-glm-ocr
```

> 依存(`rag-parser-core` path 依存)を変更したら、Docker build 前に各 pyproject の `uv lock` を
> 再生成すること。build context はリポジトリ root(共有 package を含めるため)。
