"""ファイルシステム不要の Small-to-Big 分割サービス。"""
from copy import deepcopy
from dataclasses import asdict
from docrag.chunking import build_small_to_big_chunks
from docrag.models.contracts import ChunkRequest, ChunkBatch
from docrag.profiles import DomainProfile
from docrag.resources.runtime import Runtime, ResourcePaths
from pathlib import Path


class ChunkingService:
    """既存の表・画像・親子構造を維持し、業務設定を明示して分割する。"""
    def __init__(self, profile: DomainProfile | None = None):
        self.profile = profile or DomainProfile()

    def chunk(self, request: ChunkRequest) -> ChunkBatch:
        """入力を変更せず分割する。サイズ設定の不正値は ValueError。"""
        document = request.document
        config = request.config.validate()
        engines = list(dict.fromkeys(record.engine for record in document.records))
        payload = {
            "pdf_name": document.source_name,
            "records": [asdict(record) for record in document.records],
            "pages": [asdict(page) for page in document.pages],
            "engines": [{"engine": engine, "label": engine} for engine in engines],
            "classification": deepcopy(dict(document.classification)),
            "document_metadata": deepcopy(dict(document.metadata)),
        }
        # 純変換では資源を読み書きしない。profile のみを既存アルゴリズムへ渡す。
        runtime = Runtime(ResourcePaths(Path("."), Path(".")), self.profile)
        with runtime.activate():
            chunks = build_small_to_big_chunks(
                payload, source_run_id=document.source_run_id or document.document_id,
                selected_engine_ids=engines, config=config, source_page_count=document.page_count,
            )
        return ChunkBatch(document, tuple(chunks), config)
