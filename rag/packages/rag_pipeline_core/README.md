# rag_pipeline_core

pipeline ステージのプラグイン化のための共有契約パッケージ。backend と各ステージ
マイクロサービス(`services/pipeline/<stage>`)が **同一の決定論ロジック + HTTP 契約** を
共有する。`rag_parser_core` と同じ軽量方針(依存は pydantic + rag_parser_core のみ、
HTTP サーバ依存は `[service]` extra に隔離)。

- `chunking.py` — chunk 分割の決定論ロジック(backend `app.rag.chunking` はここを再 export)。
- `stage.py` — `StageHealth` と各ステージの request/response wire schema。
- `stage_service.py` — `create_chunking_app` など 1 ステージ用 FastAPI app factory。

backend は `app.clients.pipeline_stage.PipelineStageClient` で `POST /run` へ委譲する。
サービス未起動・未到達時は in-process(同一ロジック)へ安全縮退し、remote が応答した後の
HTTP error / 不正応答は壊れたサービスとして停止する。
