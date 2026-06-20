# pipeline ステージ: guardrail

安全ポリシー(standard/strict/lenient/regulated)→ groundedness 厳格度 + 監査強調へ解決する
ステージマイクロサービス。解決ロジックは backend と **同一(`rag_pipeline_core.guardrail`)** で
決定論・外部依存なし。block_prompt_injection / PII マスク / max_query_chars は backend 設定由来、
OCI Generative AI Guardrails backend(`rag_guardrail_backend`)は別レイヤーで共存。外部安全 SaaS
は導入しない(NeMo strict 等は「より厳しい groundedness + 監査強調」へ再マップ済み)。

| 項目 | 値 |
|---|---|
| stage | `guardrail` |
| 既定 URL | `http://pipeline-guardrail:8000` / dev port 8034 |
| profile 種別 | CPU(dev は uv プロセス) |

- `POST /run`(`GuardrailStageRequest{policy}` → `GuardrailStageResponse`)。
- `GET /health` → `StageHealth`。

backend は `RAG_GUARDRAIL_SERVICE_ENABLED` 真かつ URL 設定時に静的解決を委譲し、未達/失敗時は
in-process(同一ロジック)へ安全縮退する。
