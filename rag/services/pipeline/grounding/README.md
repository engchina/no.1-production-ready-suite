# pipeline ステージ: grounding

検索後処理プリセット(custom/lean/verified_context/context_enrich/compact/full_governed)→ 後処理段
フラグ(dependency_promotion / diversity / expansion_mode / compression / corrective)へ解決する
ステージマイクロサービス。解決ロジックは backend と **同一(`rag_pipeline_core.grounding`)** で
決定論・外部依存なし。custom preset と実際の後処理実行(dedupe/verify/expansion/compression)は
backend が担う。

| 項目 | 値 |
|---|---|
| stage | `grounding` |
| 既定 URL | `http://pipeline-grounding:8000` / dev port 8036 |
| profile 種別 | CPU(dev は uv プロセス) |

- `POST /run`(`GroundingStageRequest{pipeline}` → `GroundingStageResponse`)。
- `corrective`(CRAG 的 confidence-based corrective retrieval)は verified_context/full_governed で
  surface(挙動の本実装は backend 側で段階導入)。
- `GET /health` → `StageHealth`。

backend は `RAG_GROUNDING_SERVICE_ENABLED` 真かつ URL 設定時に preset 解決を委譲し、未達/失敗時は
in-process(同一ロジック)へ安全縮退する(custom は常に backend の legacy 設定)。
