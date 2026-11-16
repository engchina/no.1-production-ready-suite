"""rerank の後の工程のテストが共有する、合成の child / parent の記録。"""

from rag_engine.generation import answering as a


def child(name, seq, text, source, parent, score=None, run="run", question_score=None):
    metadata = {"section_path": ["管理-(1)登録"]}
    if score is not None:
        metadata["rerank"] = {"candidate_index": seq, "rank": seq, "relevance_score": score, "min_relevance_score": 0.0, "filtered": False}
        if question_score is not None:
            metadata["rerank"]["question_relevance_score"] = question_score
    return a.AnswerRecord(
        id=name,
        engine="docling",
        engine_label="Docling",
        page=seq,
        seq_no=seq,
        category="Chunk",
        text=text,
        source=source,
        source_run_id="source",
        chunk_id=name,
        chunk_uid=f"{run}:{name}",
        chunk_level="child",
        chunk_seq=seq,
        parent_chunk_uid=f"{run}:{parent}",
        metadata=metadata,
    )


def parent(name, seq, text, source, run="run"):
    return a.AnswerRecord(
        id=name,
        engine="docling",
        engine_label="Docling",
        page=seq,
        seq_no=seq,
        category="Chunk",
        text=text,
        source=source,
        source_run_id="source",
        chunk_id=name,
        chunk_uid=f"{run}:{name}",
        chunk_level="parent",
        chunk_seq=seq,
        parent_chunk_uid="",
        metadata={"section_path": ["管理-(1)登録"]},
    )
