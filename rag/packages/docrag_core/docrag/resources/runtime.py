"""明示的なアプリ資源と、既存関数へ渡すリクエスト単位のスコープ。"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from docrag.profiles import DomainProfile, load_profile
from docrag.resources.pool import ModelPool


@dataclass(frozen=True)
class ResourcePaths:
    """呼び出し元が所有する保存先。構築時にはファイルを作成しない。"""

    workspace: Path
    output: Path
    prompts: Path | None = None
    faq: Path | None = None
    evaluation: Path | None = None


@dataclass
class Runtime:
    """業務設定とモデルの寿命をアプリごとに分離する。"""

    paths: ResourcePaths
    profile: DomainProfile
    model_pool: ModelPool | None = None

    @contextmanager
    def activate(self) -> Iterator["Runtime"]:
        """既存の同期関数へ資源を渡す。例外時も前のスコープを復元する。"""
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    def close(self) -> None:
        """このアプリが所有する常駐モデルを解放する。"""
        if self.model_pool is not None:
            self.model_pool.unload_all()


_current: ContextVar[Runtime | None] = ContextVar("docrag_runtime", default=None)


def current_runtime() -> Runtime | None:
    """有効なアプリ資源を返す。互換関数の直接呼び出し時は None。"""
    return _current.get()


def current_profile() -> DomainProfile:
    """互換関数には従来の profile、新 API には明示された profile を適用する。"""
    runtime = current_runtime()
    return runtime.profile if runtime is not None else load_profile("legacy")
