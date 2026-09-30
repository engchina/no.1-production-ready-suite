"""既存の高度な回答フローへ I/O 関数を注入する境界。

公開 SDK の Protocol と併用し、CRAG や multi-query の既存挙動も保護する。
互換呼び出しの既定実装は composition root で遅延構築する。
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Sequence


@dataclass(frozen=True)
class AnswerDependencies:
    """既存関数と同じ引数・結果を受け渡す回答フロー用 I/O。

    search/check_ready は chunk_run_id・settings・検索条件を keyword で受け取る。
    parse_text/parse_images は既存 schema 型を受け取り、その型の結果を返す。
    rerank は質問・候補・settings と top_n を受け取り RerankTextRank を返す。
    business_domains は質問文を受け取り、質問が名指しした業務名（文書の大分類の値）を返す。省略時は
    domain profile の `business_patterns` で推定する。呼び出し元が検索範囲の業務名の一覧を持つときに渡す。
    screen_catalog / screen_chunks は画面目録の連携（#1108）の入力。検索結果が検索範囲の全 chunk を含まない
    呼び出し元が、目録と選んだ画面の chunk を検索範囲から読むときに渡す。省略時は検索結果の chunk から作る。
    screen_catalog は settings を受け取り {文書名: 番号付きの見出し} を返す。screen_chunks は links（選んだ
    （文書名、見出し）の列）・existing（候補にある chunk_uid）・settings を受け取り、その画面の child と親の
    StoredChunk を返す。
    """
    search: Callable[..., Any]
    check_ready: Callable[..., Any]
    parse_text: Callable[..., Any]
    parse_images: Callable[..., Any]
    rerank: Callable[..., Any]
    business_domains: Callable[[str], Sequence[str]] | None = None
    screen_catalog: Callable[..., dict[str, list[str]]] | None = None
    screen_chunks: Callable[..., Sequence[Any]] | None = None


_active: ContextVar[AnswerDependencies | None] = ContextVar("rag_engine_answer_dependencies", default=None)

# 回答 1 件あたりの LLM 呼出回数。実行記録が工程ごとの差分を取って表示する。呼出単位で分離するため
# 実行記録側が開始時に 0 へ設定し、終了時に元へ戻す。
llm_call_count: ContextVar[int] = ContextVar("rag_engine_llm_call_count", default=0)


def _count_llm_call() -> None:
    llm_call_count.set(llm_call_count.get() + 1)


@contextmanager
def bind_dependencies(dependencies: AnswerDependencies) -> Iterator[None]:
    """スレッド・タスクごとに I/O を切り替え、例外時も復元する。"""
    token = _active.set(dependencies)
    try:
        yield
    finally:
        _active.reset(token)


def _get() -> AnswerDependencies:
    current = _active.get()
    if current is not None:
        return current
    from rag_engine.composition import oracle_answer_dependencies
    return oracle_answer_dependencies()


def question_business_domains(question: str) -> list[str] | None:
    """注入された業務名の照合で、質問が名指しした業務名を返す。注入が無ければ None（profile で推定する）。"""
    current = _active.get()
    if current is None or current.business_domains is None:
        return None
    return list(current.business_domains(question))


def injected_screen_catalog(**kwargs) -> dict[str, list[str]] | None:
    """注入された画面目録を返す。注入が無ければ None（検索結果の chunk から作る）。"""
    current = _active.get()
    if current is None or current.screen_catalog is None:
        return None
    return current.screen_catalog(**kwargs)


def injected_screen_chunks(**kwargs) -> list[Any] | None:
    """注入された読み込みで、選んだ画面の chunk を返す。注入が無ければ None（検索結果の chunk から選ぶ）。"""
    current = _active.get()
    if current is None or current.screen_chunks is None:
        return None
    return list(current.screen_chunks(**kwargs))


def search_adb_hybrid_chunks(**kwargs):
    """注入された検索実装へ既存検索要求を転送する。"""
    return _get().search(**kwargs)


def check_adb_hybrid_search_ready(**kwargs):
    """準備不足は AdbHybridSearchUnavailable として呼び出し元へ伝播する。"""
    return _get().check_ready(**kwargs)


def parse_text_response(*args, **kwargs):
    """指定 schema のテキスト回答を取得する。呼出回数を llm_call_count に数える。"""
    _count_llm_call()
    return _get().parse_text(*args, **kwargs)


def parse_multimodal_response(*args, **kwargs):
    """指定 schema の画像付き回答を取得する。呼出回数を llm_call_count に数える。"""
    _count_llm_call()
    return _get().parse_images(*args, **kwargs)


def rerank_text_with_scores(*args, **kwargs):
    """指定した候補の順位と score を取得する。"""
    return _get().rerank(*args, **kwargs)
