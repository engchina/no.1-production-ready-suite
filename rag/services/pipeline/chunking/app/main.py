"""Chunking ステージマイクロサービス。

共有 contract(rag_pipeline_core)の app factory を使い、構造化抽出を選択戦略で分割して
返す。chunk 分割ロジックは backend と同一(rag_pipeline_core.chunking)で、決定論・外部依存
なし。pipeline ステージのプラグイン化の第 1 弾。
"""

from rag_pipeline_core.stage import StageHealth
from rag_pipeline_core.stage_service import create_chunking_app


def _health() -> StageHealth:
    """chunking は外部依存なしで常時 ready。"""
    version: str | None = None
    try:
        from importlib.metadata import version as dist_version

        version = dist_version("rag-pipeline-core")
    except Exception:  # noqa: BLE001 - version 取得失敗は readiness に影響させない
        version = None
    return StageHealth(
        status="ok",
        stage="chunking",
        package_name="rag_pipeline_core",
        package_version=version,
    )


app = create_chunking_app(health_probe=_health, title="pipeline-chunking")
