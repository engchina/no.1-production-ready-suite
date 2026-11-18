"""検索と、切った起点からの検索拡張(#5389)を続けて呼ぶテストの補助。

回答の流れでは rag_engine が rerank と起点の切りの後に ``AnswerEngine._expanded_search`` を呼ぶ。
ここでは rerank をしないので、検索の候補(検索の順)をそのまま切った起点として拡張する。
"""

from types import SimpleNamespace
from typing import Any


async def search_with_expansions(engine: Any, request: Any, state: Any, **kwargs: Any) -> Any:
    """``_search`` の結果に、その候補を起点にした検索拡張の根拠(``expansion_chunks``)を足す。"""
    result = await engine._search(request, state, **kwargs)
    ids = [child.chunk_uid for child in result.child_chunks]
    expansions = await engine._expanded_search(
        request,
        state,
        anchors=ids,
        candidate_ids=ids,
        retrieval_queries=kwargs.get("retrieval_queries", ()),
        page_ranges=kwargs.get("page_ranges", ()),
    )
    return SimpleNamespace(
        child_chunks=result.child_chunks,
        all_chunks=[*result.all_chunks, *expansions],
        expansion_chunks=expansions,
    )
