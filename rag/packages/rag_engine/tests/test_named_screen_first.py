"""画面名がモデルの順位を上書きしない。"""

import pytest

from rag_engine.generation import answering
from rag_engine.generation.answer_models import AnswerRecord


def record(identity, screen, business):
    return AnswerRecord(
        id=identity,
        chunk_uid=identity,
        engine="synthetic",
        engine_label="合成",
        page=1,
        seq_no=1,
        category="Chunk",
        text="合成の説明",
        source=identity + ".pdf",
        metadata={"section_path": [screen], "document": {"classification": {"large_category": business}}},
    )


@pytest.mark.parametrize(
    "question", ["画面 A の設定手順を教えてください。", "業務 A だけでなく他の資料も比較してください。", "画面 A は参照しないでください。"]
)
def test_question_labels_do_not_override_model_order(monkeypatch, question):
    first = record("a", "画面 A", "業務 A")
    second = record("b", "画面 B", "業務 B")
    monkeypatch.setattr(answering, "_rerank_records", lambda *args, **kwargs: [second, first])
    assert answering.rerank_records(question, [first, second], None) == [second, first]
