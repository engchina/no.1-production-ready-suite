"""既存バックエンドの構築を集約する composition root。"""
from __future__ import annotations
from dataclasses import replace
from functools import partial
from pathlib import Path
from rag_engine.config import Settings
from rag_engine.profiles import DomainProfile
from rag_engine.ports import ArtifactStore, Embedder
from rag_engine.resources.runtime import Runtime
from rag_engine.dependencies import AnswerDependencies


def oracle_answer_dependencies() -> AnswerDependencies:
    """既存 Oracle/OCI 関数を束ねる。ここでは接続・モデル読込を行わない。"""
    from rag_engine.adapters.oracle.store import search_adb_hybrid_chunks, check_adb_hybrid_search_ready
    from rag_engine.adapters.oci import parse_text_response, parse_multimodal_response, rerank_text_with_scores
    return AnswerDependencies(search_adb_hybrid_chunks, check_adb_hybrid_search_ready,
                              parse_text_response, parse_multimodal_response, rerank_text_with_scores)


class OracleApplication:
    """各サービスと、そのモデル資源の寿命を束ねるアプリインスタンス。"""
    def __init__(self, settings: Settings, runtime: Runtime, *, dependencies: AnswerDependencies | None = None, artifact_store: ArtifactStore | None = None, embedder: Embedder | None = None):
        from rag_engine.adapters.sdk import DocumentParser, OracleIndexer, OracleRetriever, OciGenerator
        from rag_engine.chunking.service import ChunkingService
        from rag_engine.workflows import IngestionPipeline, QueryPipeline
        from rag_engine.generation.application import AnswerService
        from rag_engine.knowledge.service import KnowledgeService
        self.settings, self.runtime = settings, runtime
        self.parser = DocumentParser(settings, runtime, engines=tuple(settings.enabled_engines))
        self.chunker = ChunkingService(runtime.profile)
        # 索引と検索の vector 空間を揃えるため、同じ embedder を両方へ渡す。
        self.indexer = OracleIndexer(settings, runtime, embedder=embedder)
        self.retriever = OracleRetriever(settings, runtime, embedder=embedder)
        self.generator = OciGenerator(settings, runtime)
        self.ingestion = IngestionPipeline(self.parser, self.chunker, self.indexer)
        self.query = QueryPipeline(self.retriever, self.generator)
        if dependencies is None:
            dependencies = oracle_answer_dependencies()
            if embedder is not None:
                # advanced answer の検索 query も索引と同じ embedder で vector 化する。
                dependencies = replace(dependencies, search=partial(
                    dependencies.search, query_embedder=lambda query, _settings: embedder.embed([query], query=True)[0]))
        self.answers = AnswerService(settings, runtime, dependencies, artifact_store=artifact_store)
        self.knowledge = KnowledgeService(runtime)

    def close(self) -> None:
        """このインスタンスのモデル資源を解放する。"""
        self.runtime.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def create_oracle_application(
    workspace: str | Path, *, settings: Settings | None = None,
    profile: str | DomainProfile = "generic", dependencies: AnswerDependencies | None = None,
    artifact_store: ArtifactStore | None = None, embedder: Embedder | None = None,
) -> OracleApplication:
    """明示した workspace に既存 backend を構成する。構築時に外部接続は行わない。

    settings 未指定では環境変数や .env を読まず、軽量な generic 既定値を使う。
    相対設定パスは workspace 基準。FAQ は明示された場合だけ読み込む。
    embedder は索引と検索 query の両方に使う。dependencies を明示した場合、その search は変更しない。
    """
    from rag_engine.config import get_settings
    from rag_engine.profiles import load_profile, DomainProfile
    from rag_engine.resources.runtime import Runtime, ResourcePaths
    from rag_engine.resources.pool import ModelPool
    workspace = Path(workspace).resolve()
    chosen = load_profile(profile) if isinstance(profile, str) else profile
    if not isinstance(chosen, DomainProfile):
        raise TypeError("profile must be a DomainProfile or a built-in profile name")
    settings = settings or get_settings(environ={}, dotenv_path=None, domain_keywords_override=())
    def path(value):
        if value is None:
            return None
        value = Path(value)
        return value.resolve() if value.is_absolute() else (workspace / value).resolve()
    settings = replace(settings, workspace_dir=workspace, output_dir=path(settings.output_dir),
                       runtime_knowledge_path=path(settings.runtime_knowledge_path),
                       query_history_blocklist_path=path(settings.query_history_blocklist_path),
                       prompt_dir=path(settings.prompt_dir), faq_path=path(settings.faq_path), profile=chosen.name)
    prompts = settings.prompt_dir
    if chosen.name == "legacy" and prompts is None:
        prompts = workspace / "prompts"
    faq = settings.faq_path
    runtime = Runtime(ResourcePaths(workspace, settings.output_dir, prompts, faq), chosen, ModelPool())
    return OracleApplication(settings, runtime, dependencies=dependencies, artifact_store=artifact_store, embedder=embedder)
