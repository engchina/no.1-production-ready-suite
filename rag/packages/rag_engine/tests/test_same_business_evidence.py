"""業務名に一致しない候補も正常な検索結果として残す。"""

from rag_engine.generation import answering
from test_named_screen_first import record


def test_other_business_is_not_excluded(monkeypatch):
    records = [record("b", "画面 B", "業務 B"), record("a", "画面 A", "業務 A")]
    monkeypatch.setattr(answering, "_rerank_records", lambda *args, **kwargs: records)
    assert answering.rerank_records("業務 A の説明を確認する", records, None) == records
