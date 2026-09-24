# pipeline ステージ: generation

回答生成プロファイル(grounded_concise / detailed_cited / strict_extractive / structured_json /
bilingual_ja_en / inline_cited / custom)→ OCI Enterprise AI へ渡す system prompt 変種 + 構造化
出力フラグへ解決するステージマイクロサービス。解決ロジックは backend と **同一
(`rag_pipeline_core.generation`)** で決定論・外部依存なし。実 LLM 生成と custom(prompt version
store)/ persona override は backend が担う。外部 LLM provider は導入しない。

| 項目 | 値 |
|---|---|
| stage | `generation` |
| 既定 URL | `http://pipeline-generation:8000` / dev port 8033 |
| profile 種別 | CPU(dev は uv プロセス) |

- `POST /run`(`GenerationStageRequest{profile}` → `GenerationStageResponse{system_prompt, structured_output}`)。
- 新 profile `inline_cited`(SAFE 型逐句帰因): 各文の直後に [source#chunk_id] を即時付与。
- `GET /health` → `StageHealth`。

backend は `RAG_GENERATION_SERVICE_ENABLED` 真かつ URL 設定時に静的解決を委譲し、
サービス未起動・未到達時は in-process(同一ロジック)へ安全縮退する。remote が応答した後の
HTTP error / 不正応答は壊れたサービスとして停止する。
