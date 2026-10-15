"""回答に使った箇所の使われ方（確かめた説明 / 参考）を、引用の metadata と MCP の evidence に
載せる（#2040）。
"""

from typing import Any

from app.mcp import tools as mcp_tools
from app.rag.answer_engine import _outcome_from_result, _SearchState
from app.schemas.search import RetrievedChunk


class _EngineResult:
    """rag_engine の回答の結果（``_outcome_from_result`` が読む属性だけ）。"""

    def __init__(self, children: list[dict[str, Any]]) -> None:
        self.evidence_items = [{"chunk_id": "p", "children": children}]
        self.generation_trace: dict[str, Any] = {}
        self.execution_steps: list[Any] = []
        self.generated_queries: list[str] = []
        self.text_search_tokens: list[str] = []
        self.crag_attempts: list[Any] = []
        self.envelope: dict[str, Any] = {}
        self.external_data_items: list[Any] = []
        self.question_type: list[str] = []
        self.answer_text = "回答"

    def __getattr__(self, name: str) -> Any:
        return None


def _chunk(chunk_id: str) -> RetrievedChunk:
    return RetrievedChunk(document_id="d1", chunk_id=chunk_id, text="本文", score=0.5, metadata={})


def test_outcome_carries_answer_use_to_citation_metadata_and_mcp_evidence() -> None:
    state = _SearchState()
    state.chunks = {
        cid: _chunk(cid) for cid in ("c-confirmed", "c-reference", "c-unknown", "c-unused")
    }
    children = [
        {
            "chunk_id": "c-confirmed",
            "is_model_used": True,
            "model_usage_rank": 1,
            "answer_use": "confirmed",
        },
        {
            "chunk_id": "c-reference",
            "is_model_used": True,
            "model_usage_rank": 2,
            "answer_use": "reference",
        },
        # 使われ方の分からない流れ（決まった値でないもの）は載せない。
        {
            "chunk_id": "c-unknown",
            "is_model_used": True,
            "model_usage_rank": 3,
            "answer_use": "other",
        },
        # 回答に使っていない箇所には、値があっても使われ方を付けない。
        {"chunk_id": "c-unused", "is_model_used": False, "answer_use": "confirmed"},
    ]
    outcome = _outcome_from_result(_EngineResult(children), state)

    uses = {
        chunk.chunk_id: chunk.metadata.get("evidence_answer_use") for chunk in outcome.citations
    }
    assert uses == {
        "c-confirmed": "confirmed",
        "c-reference": "reference",
        "c-unknown": None,
        "c-unused": None,
    }
    tree_uses = {
        child["chunk_id"]: child["answer_use"]
        for child in outcome.diagnostics["evidence_tree"][0]["children"]
    }
    assert tree_uses == uses

    evidence = {chunk.chunk_id: mcp_tools._evidence(chunk) for chunk in outcome.citations}
    assert {cid: (item.used_in_answer, item.answer_use) for cid, item in evidence.items()} == {
        "c-confirmed": (True, "confirmed"),
        "c-reference": (True, "reference"),
        "c-unknown": (True, None),
        "c-unused": (False, None),
    }
