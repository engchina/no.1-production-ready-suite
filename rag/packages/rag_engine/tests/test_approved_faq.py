"""登録済みQ&A（質問と回答だけ。#5107）の保存・候補の照合・feedback からの登録を守るテスト。"""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rag_engine.knowledge.approved_faq import (
    APPROVED_FAQ_IMPORT_MODE_DELETE_THEN_INSERT,
    ApprovedFaqImportRow,
    ApprovedFaqRecord,
    ApprovedFaqSemanticIndex,
    ApprovedFaqSemanticItem,
    add_approved_faq_record,
    apply_approved_faq_import_rows,
    approved_faq_feedback_skip_reason,
    approved_faq_import_row_from_answer_feedback,
    approved_faq_records_signature,
    build_approved_faq_semantic_index,
    delete_approved_faq_records,
    load_approved_faq_excel_rows,
    load_approved_faq_records,
    load_approved_faq_semantic_index,
    promote_answer_feedback_to_approved_faq,
    save_approved_faq_semantic_index,
    suggest_approved_faq_questions,
)

QUESTION_A = "合成の手順 A を教えてください。"
QUESTION_B = "合成の帳票 B の出力方法を教えてください。"


def _write_corpus(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": [
                    {"id": "faq-1", "question": QUESTION_A, "approved_answer": "回答 A です。", "status": "approved"},
                    {"id": "faq-2", "question": QUESTION_B, "approved_answer": "回答 B です。", "status": "approved"},
                    {"id": "faq-3", "question": "未承認の質問", "approved_answer": "使わない回答です。", "status": "draft"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    path = tmp_path / "approved_faq.json"
    _write_corpus(path)
    return path


def test_loads_question_and_answer_records(corpus: Path) -> None:
    records = load_approved_faq_records(corpus)

    assert [record.id for record in records] == ["faq-1", "faq-2", "faq-3"]
    assert (records[0].question, records[0].approved_answer, records[0].status) == (QUESTION_A, "回答 A です。", "approved")


def test_suggests_only_approved_records_above_the_min_score(corpus: Path) -> None:
    records = load_approved_faq_records(corpus)

    assert [item.record.id for item in suggest_approved_faq_questions(QUESTION_A, records, min_score=0.92)] == ["faq-1"]
    assert suggest_approved_faq_questions("未承認の質問", records) == []
    assert suggest_approved_faq_questions("  ", records) == []


def test_suggestions_are_sorted_by_score_and_cut_to_the_limit() -> None:
    records = [
        ApprovedFaqRecord(id=f"faq-{index}", question=f"合成の質問 {index}", approved_answer="回答", status="approved")
        for index in range(5)
    ]

    suggestions = suggest_approved_faq_questions("合成の質問 3", records, limit=3, min_score=0.1)

    assert len(suggestions) == 3
    assert suggestions[0].record.id == "faq-3"
    assert [item.score for item in suggestions] == sorted((item.score for item in suggestions), reverse=True)


def test_high_score_requires_nearly_the_same_question_not_mere_containment() -> None:
    """包含を一律 0.92 にすると候補の下限と同値になり、短い語だけで候補が出る (#549)。"""
    records = [ApprovedFaqRecord(id="faq-1", question=QUESTION_A, approved_answer="手順です。", status="approved")]

    def matched(question: str, min_score: float) -> list[str]:
        return [item.record.id for item in suggest_approved_faq_questions(question, records, min_score=min_score)]

    for question in ("の", "合成の手順", QUESTION_A + "ただし別の場合です"):
        assert matched(question, 0.92) == []
        assert matched(question, 0.1) == ["faq-1"]
    for question in (QUESTION_A, QUESTION_A.replace("。", "？"), QUESTION_A.rstrip("。"), " 合成の手順 A を 教えてください。"):
        assert matched(question, 0.92) == ["faq-1"]


def test_semantic_index_can_match_a_lexically_different_question(tmp_path: Path) -> None:
    records = [
        ApprovedFaqRecord(id="faq-text", question="alpha maintenance", approved_answer="A", status="approved"),
        ApprovedFaqRecord(id="faq-semantic", question="beta transfer schedule", approved_answer="B", status="approved"),
    ]
    index = build_approved_faq_semantic_index(
        tmp_path / "semantic_index.json",
        records,
        model="test-embedding",
        dimensions=3,
        embedder=lambda texts, settings: [[0.0, 1.0, 0.0] if "alpha" in text else [1.0, 0.0, 0.0] for text in texts],
        settings=None,
    )

    suggestions = suggest_approved_faq_questions(
        "when is it deposited", records, semantic_index=index, semantic_query_embedding=[1.0, 0.0, 0.0], min_score=0.92
    )

    assert [item.record.id for item in suggestions] == ["faq-semantic"]
    assert (suggestions[0].match_method, suggestions[0].semantic_score) == ("semantic", 1.0)


def test_semantic_index_cache_roundtrips_with_the_record_signature(tmp_path: Path) -> None:
    records = [ApprovedFaqRecord(id="faq-1", question="QUESTION one", approved_answer="ANSWER one", status="approved")]
    cache_path = tmp_path / "runs" / "semantic_index.json"
    index = build_approved_faq_semantic_index(
        cache_path, records, model="m", dimensions=3, embedder=lambda texts, settings: [[1.0, 0.0, 0.0] for _ in texts], settings=None
    )
    signature = approved_faq_records_signature(records)

    loaded = load_approved_faq_semantic_index(cache_path, model="m", dimensions=3, records_signature=signature)

    assert index.records_signature == signature
    assert loaded is not None and len(loaded.items) == 1
    assert load_approved_faq_semantic_index(cache_path, model="other", dimensions=3, records_signature=signature) is None
    changed = [ApprovedFaqRecord(id="faq-1", question="QUESTION two", approved_answer="ANSWER one", status="approved")]
    assert approved_faq_records_signature(changed) != signature


def test_semantic_index_cache_is_written_atomically(tmp_path: Path) -> None:
    """cache は一時ファイル → replace で書き、途中の状態や一時ファイルを残さない (#828)。"""
    index = ApprovedFaqSemanticIndex(
        model="m", dimensions=2, records_signature="sig", items=(ApprovedFaqSemanticItem("r1", "質問", "h1", (0.1, 0.2)),)
    )
    cache = tmp_path / "cache" / "semantic.json"
    save_approved_faq_semantic_index(cache, index)
    assert [p.name for p in cache.parent.iterdir()] == ["semantic.json"]
    loaded = load_approved_faq_semantic_index(cache, model="m", dimensions=2, records_signature="sig")
    assert loaded is not None and loaded.items[0].record_id == "r1"


def test_adds_a_record_with_only_question_and_answer_and_skips_the_same_question(corpus: Path) -> None:
    now = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)

    result = add_approved_faq_record(corpus, question=" 合成の新しい質問ですか？ ", approved_answer="追加できます。", now=now)
    duplicate = add_approved_faq_record(corpus, question="合成の新しい質問ですか？", approved_answer="上書きしません。", now=now)
    raw = json.loads(corpus.read_text(encoding="utf-8"))["records"][-1]

    assert (result.inserted_count, result.deleted_count) == (1, 0)
    assert result.backup_path is not None
    assert (duplicate.inserted_count, duplicate.skipped_count) == (0, 1)
    assert set(raw) == {"id", "question", "approved_answer", "status", "source", "timestamps"}
    assert (raw["question"], raw["approved_answer"], raw["status"]) == ("合成の新しい質問ですか？", "追加できます。", "approved")
    assert raw["source"] == {"source_file": "manual"}


def test_deletes_records_by_id_only(corpus: Path) -> None:
    original = corpus.read_bytes()
    for selectors in (QUESTION_A, "unknown-id", " "):
        result = delete_approved_faq_records(corpus, selectors)
        assert result.deleted_count == 0 and result.backup_path is None
        assert corpus.read_bytes() == original

    result = delete_approved_faq_records(corpus, " faq-1\nfaq-2,faq-1 ")

    assert result.deleted_count == 2
    assert [record.id for record in load_approved_faq_records(corpus)] == ["faq-3"]


def test_delete_then_insert_replaces_the_same_question(corpus: Path) -> None:
    row = ApprovedFaqImportRow(question=QUESTION_A, approved_answer="新しい回答です。", source_file="faq.xlsx", sheet="FAQ", row=2)

    result = apply_approved_faq_import_rows(corpus, [row], mode=APPROVED_FAQ_IMPORT_MODE_DELETE_THEN_INSERT)
    replaced = next(record for record in load_approved_faq_records(corpus) if record.question == QUESTION_A)

    assert (result.deleted_count, result.inserted_count) == (1, 1)
    assert replaced.approved_answer == "新しい回答です。"
    assert replaced.source == {"source_file": "faq.xlsx", "sheet": "FAQ", "row": 2}


def test_reads_only_the_question_and_answer_columns_from_excel(tmp_path: Path) -> None:
    from openpyxl import Workbook

    path = tmp_path / "faq.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "FAQ"
    sheet.append(["QUESTION", "ANSWER", "NOTE", "NEGATIVE_QUESTIONS", "TARGET_KNOWLEDGE_BASES"])
    sheet.append(["質問1", "回答1", "memo", "対象外", "kb-1"])
    sheet.append(["", "", "empty"])
    sheet.append(["質問2", "回答2", "memo"])
    workbook.save(path)

    rows = load_approved_faq_excel_rows(path)

    assert rows == [
        ApprovedFaqImportRow(question="質問1", approved_answer="回答1", source_file="faq.xlsx", sheet="FAQ", row=2),
        ApprovedFaqImportRow(question="質問2", approved_answer="回答2", source_file="faq.xlsx", sheet="FAQ", row=4),
    ]


def test_excel_without_the_two_columns_is_rejected(tmp_path: Path) -> None:
    from openpyxl import Workbook

    path = tmp_path / "faq.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["QUESTION", "NOTE"])
    sheet.append(["質問1", "memo"])
    workbook.save(path)

    with pytest.raises(ValueError, match="QUESTION と ANSWER"):
        load_approved_faq_excel_rows(path)


def test_concurrent_additions_are_all_saved(tmp_path: Path) -> None:
    path = tmp_path / "approved_faq.json"
    barrier = threading.Barrier(8)
    errors: list[Exception] = []

    def add(index: int) -> None:
        barrier.wait()
        try:
            add_approved_faq_record(path, question=f"質問{index}", approved_answer=f"回答{index}")
        except Exception as exc:  # スレッド内の失敗を主スレッドの assert へ渡す。
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    # ロックがないと、同じ内容を読み込んだ保存同士が互いの追加を上書きして消す。
    assert {record.question for record in load_approved_faq_records(path)} == {f"質問{index}" for index in range(8)}
    assert [item.name for item in tmp_path.iterdir() if item.name.endswith(".tmp")] == []


def test_feedback_is_registered_with_only_question_and_answer(corpus: Path) -> None:
    corrected = {
        "feedback_id": "feedback-1",
        "feedback_type": "incorrect_answer",
        "question": QUESTION_A,
        "comment": "手順を直す。",
        "corrected_answer": "直した回答です。",
        "classification_filter": {"large_category": "分類 A"},
        "answer_trace": {"answer_text": "古い回答です。", "evidence_item_ids": ["chunk-1"], "source_document_ids": ["doc-1"]},
    }

    result = promote_answer_feedback_to_approved_faq(corpus, corrected)
    raw = next(item for item in json.loads(corpus.read_text(encoding="utf-8"))["records"] if item["question"] == QUESTION_A)

    assert result is not None and (result.deleted_count, result.inserted_count) == (1, 1)
    assert raw["approved_answer"] == "直した回答です。"
    assert set(raw) == {"id", "question", "approved_answer", "status", "source", "timestamps"}
    assert raw["source"] == {"source_file": "answer_feedback"}


def test_correct_feedback_uses_the_saved_answer_and_skips_unanswerable_answers() -> None:
    base = {"feedback_id": "fb", "question": "質問"}
    correct = {**base, "feedback_type": "correct", "corrected_answer": "使わない", "answer_trace": {"answer_text": "保存した回答"}}
    unanswerable = {
        **base,
        "feedback_type": "correct",
        "answer_trace": {"answer_text": "回答できません。", "confidence": "low", "insufficient_reason": "根拠不足"},
    }
    partial = {
        **base,
        "feedback_type": "correct",
        "answer_trace": {"answer_text": "一部の回答", "confidence": "medium", "insufficient_reason": "残りは不明"},
    }
    empty_wrong = {**base, "feedback_type": "incorrect_answer", "corrected_answer": "", "answer_trace": {"answer_text": "x"}}

    assert approved_faq_import_row_from_answer_feedback(correct) == ApprovedFaqImportRow(
        question="質問", approved_answer="保存した回答", source_file="answer_feedback"
    )
    assert "根拠不足" in approved_faq_feedback_skip_reason(unanswerable)
    assert approved_faq_import_row_from_answer_feedback(unanswerable) is None
    assert approved_faq_feedback_skip_reason(partial) == ""
    assert approved_faq_import_row_from_answer_feedback(empty_wrong) is None
