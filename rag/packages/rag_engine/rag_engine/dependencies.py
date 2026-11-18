"""既存の高度な回答フローへ I/O 関数を注入する境界。

公開 SDK の Protocol と併用し、既存の回答の挙動を保護する。
互換呼び出しの既定実装は composition root で遅延構築する。
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pr_backend_core.observability.model_purpose import model_call_purpose

if TYPE_CHECKING:
    from rag_engine.ports import Embedder


@dataclass(frozen=True)
class AnswerDependencies:
    """既存関数と同じ引数・結果を受け渡す回答フロー用 I/O。

    search/check_ready は chunk_run_id・settings・検索条件を keyword で受け取る。
    parse_text/parse_images は既存 schema 型を受け取り、その型の結果を返す。
    rerank は質問・候補・settings と top_n を受け取り RerankTextRank を返す。
    section_outline / section_chunks は章節の拡張（#1436）の入力。章節と chunk の対応を持つ呼び出し元が渡す。
    section_outline は chunk_uid を受け取り、その chunk の文書の章節の一覧（`section_expansion.SectionOutline`。
    対応が無い chunk は None）を返す。section_chunks は document_id・chunk_set_id・section_id を受け取り、
    章節の chunk（child と親の StoredChunk。child は章節の中の順）を返す。部分取得や失敗は
    `SectionRead(complete=False)` で区別する。省略時は章節で広げない。
    embedder は業務ガイドの手順の充足で、根拠を共有する手順のどれを item が説明したかを語で決められないときに、
    意味の近さで決める embedding（`ports.Embedder`。索引と同じ OCI Generative AI の Cohere Embed。#2649）。省略時は
    語の判定だけで決める。
    report_degraded は補助の処理が失敗して空のまま続けたことを、処理の種類と例外を受け取って
    呼び出し元の診断に残す（#3259。例外の文は呼び出し元が残さない）。省略時は残さない。
    current_section_paths は長い根拠を分ける区切りに使う、子の chunk の今の章節（章節ナビゲーションが正本。#4682）。
    chunk の ID の列を受け取り {chunk の ID: 章節の名前の列} を返す（どの章節にも入らない chunk は空の列、対応の無い chunk は
    含めない）。省略時や読めないときは取込の章節で区切る。
    context_references は章節の拡張・親の読取で回答の文脈に入った chunk を起点にした参照先（#5315）。keyword の
    seeds（{chunk の ID: 由来}。文脈の順）と present（文脈にある chunk の ID）を受け取り、足す参照先の chunk（child と親の
    StoredChunk。``reference_expansion`` の印つき）を返す。深さ・上限・期限は呼び出し元が持つ。省略時は辿らない。
    search_expansions は検索拡張（参照の展開・目次からの章節の選択・実体・文書構造。#5389）。検索ごとに rerank・下限・
    文書ごとの上限・``top_k`` の切りの後に呼ぶ。keyword の anchors（切った起点の chunk_uid。rerank の後の順）と candidate_ids
    （rerank に渡した検索の候補の chunk_uid）と、その検索に渡した検索の条件（``search`` と同じ keyword）を受け取り、拡張の
    根拠の StoredChunk（rerank にかけず最後の根拠の並びへ合わせる。#5162）を返す。省略時は拡張しない。
    """

    search: Callable[..., Any]
    check_ready: Callable[..., Any]
    parse_text: Callable[..., Any]
    parse_images: Callable[..., Any]
    rerank: Callable[..., Any]
    section_outline: Callable[..., Any] | None = None
    section_chunks: Callable[..., Sequence[Any]] | None = None
    embedder: "Embedder | None" = None
    report_degraded: Callable[[str, BaseException], None] | None = None
    # 精排・起点選択の後に親/近傍を読む。未指定の SDK は取得済みの原文だけで構築する。
    context_chunks: Callable[..., Sequence[Any]] | None = None
    record_retrieval: Callable[[Mapping[str, Any]], None] | None = None
    # 正式生成に選んだ文書だけを、その版と検索範囲で読む。
    document_background: Callable[..., Sequence[Mapping[str, Any]]] | None = None
    current_section_paths: Callable[[Sequence[str]], Mapping[str, Sequence[str]]] | None = None
    context_references: Callable[..., Sequence[Any]] | None = None
    search_expansions: Callable[..., Sequence[Any]] | None = None
    # 独立の経路（Wiki。#4941）の原文。通常検索の候補を渡さず、根拠の最後に集合として足す。
    additional_sources: Callable[[], Sequence[Any]] | None = None


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


def section_dependencies() -> tuple[Callable[..., Any], Callable[..., Sequence[Any]]] | None:
    """注入された章節の読み込み（章節の一覧, 章節の chunk）。注入が無ければ None（章節で広げない。#1436）。"""
    current = _active.get()
    if current is None or current.section_outline is None or current.section_chunks is None:
        return None
    return current.section_outline, current.section_chunks


def injected_embedder() -> "Embedder | None":
    """注入された embedding（#2649）。注入が無ければ None（業務ガイドの手順の充足を語の判定だけで決める）。"""
    current = _active.get()
    return None if current is None else current.embedder


def injected_current_section_paths(chunk_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
    """注入された chunk の今の章節（#4682）。注入が無ければ空（取込の章節で区切る）。"""
    current = _active.get()
    return {} if current is None or current.current_section_paths is None else current.current_section_paths(chunk_ids)


def injected_additional_sources() -> list[Any]:
    """注入された独立の経路の原文（#4941）。注入が無ければ空。"""
    current = _active.get()
    return [] if current is None or current.additional_sources is None else list(current.additional_sources())


def injected_context_references(*, seeds: Mapping[str, str], present: Sequence[str]) -> list[Any] | None:
    """注入された、回答の文脈の chunk を起点にした参照先（#5315）。注入が無ければ None（辿らない）。"""
    current = _active.get()
    if current is None or current.context_references is None:
        return None
    return list(current.context_references(seeds=seeds, present=present))


def report_degraded(kind: str, exc: BaseException) -> None:
    """補助の処理 ``kind`` の失敗を、注入された記録に残す（#3259）。注入が無ければ何もしない。"""
    current = _active.get()
    if current is not None and current.report_degraded is not None:
        current.report_degraded(kind, exc)


def search_adb_hybrid_chunks(**kwargs):
    """注入された検索実装へ既存検索要求を転送する。"""
    return _get().search(**kwargs)


def injected_search_expansions(**kwargs: Any) -> list[Any]:
    """切った起点からの検索拡張の根拠（#5389）。注入が無ければ空。"""
    current = _active.get()
    if current is None or current.search_expansions is None:
        return []
    return list(current.search_expansions(**kwargs))


def check_adb_hybrid_search_ready(**kwargs):
    """準備不足は AdbHybridSearchUnavailable として呼び出し元へ伝播する。"""
    return _get().check_ready(**kwargs)


def parse_text_response(*args: Any, **kwargs: Any) -> Any:
    """指定 schema のテキスト回答を取得する。呼出回数を llm_call_count に数える。"""
    _count_llm_call()
    with model_call_purpose(kwargs.pop("purpose", None)):
        return _get().parse_text(*args, **kwargs)


def parse_multimodal_response(*args, **kwargs):
    """指定 schema の画像付き回答を取得する。呼出回数を llm_call_count に数える。"""
    _count_llm_call()
    with model_call_purpose(kwargs.pop("purpose", None)):
        return _get().parse_images(*args, **kwargs)


def rerank_text_with_scores(*args, **kwargs):
    """指定した候補の順位と score を取得する。"""
    return _get().rerank(*args, **kwargs)


def injected_context_chunks(**kwargs) -> list[Any] | None:
    """選択済み起点の原文を現在の検索範囲で遅延取得する。"""
    current = _active.get()
    if current is None or current.context_chunks is None:
        return None
    return list(current.context_chunks(**kwargs))


def record_retrieval_stage(stage: Mapping[str, Any]) -> None:
    """内容を推定せず、実際の入力・採用・打切りを呼出元へ記録する。"""
    current = _active.get()
    if current is not None and current.record_retrieval is not None:
        current.record_retrieval(stage)


def injected_document_background(**kwargs: Any) -> list[Mapping[str, Any]] | None:
    current = _active.get()
    if current is None or current.document_background is None:
        return None
    return list(current.document_background(**kwargs))
