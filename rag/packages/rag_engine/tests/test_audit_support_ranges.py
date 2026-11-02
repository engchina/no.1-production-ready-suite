"""監査の裏付けを原文の写しから根拠の ID と番号の範囲へ変えた契約（#4152）。モデルはスタブ。根拠は合成。"""

import json
from collections.abc import Sequence
from typing import Any
from unittest.mock import patch

import pytest
from audit_ranges import line_range

from rag_engine.evaluation.answer_validation import ClaimAuditOutput, validate_answer_claims
from rag_engine.generation import grounded
from rag_engine.models.audit_wire import audit_wire_schema
from rag_engine.models.llm import GroundedFactAudit, GroundedItemReview

FIRST = "対象を選びます。"
SECOND = "保存ボタンを押します。"
THIRD = "別の画面の説明です。"
LONG = "\n".join([FIRST, SECOND, THIRD])
ANSWER = "保存ボタンを押します。\n根拠：manual.pdf p.1"
EVIDENCE = [{"id": "c1", "source": "manual.pdf", "page_start": 1, "text": LONG}]


def _validate(
    ranges: Any, *, status: str = "supported", evidence: Sequence[dict[str, Any]] = EVIDENCE, extra: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """監査が ``ranges(根拠の一覧)`` の範囲を返すスタブで検証する（監査し直しにも同じものを返す）。"""
    calls: list[dict[str, Any]] = []

    def parse(system: str, inputs: str, settings: Any, schema: Any, provider_id: str | None = None) -> ClaimAuditOutput:
        payload = json.loads(inputs)
        calls.append(payload)
        documents = [item for item in payload["evidence_items"] if item["id"] == "c1"]
        return ClaimAuditOutput.model_validate(
            {
                "claim_checks": [
                    {
                        "answer_quote": "段落",
                        "answer_passage_id": passage["id"],
                        "status": status,
                        "evidence_id": ",".join(item["evidence_id"] for item in documents),
                        "support_ranges": ranges(documents),
                        "applicability": "matched",
                        "source_id": "",
                        "reason": "合成の判定",
                        **(extra or {}),
                    }
                    for passage in payload["answer_passages"]
                ]
            }
        )

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        return validate_answer_claims("質問", ANSWER, [dict(item) for item in evidence], settings=None), calls


def test_copy_fields_are_not_in_the_schemas_sent_to_the_models() -> None:
    final = ClaimAuditOutput.model_json_schema()["$defs"]["ValidationClaimCheck"]["properties"]
    assert {"support_ranges", "evidence_id"} <= set(final)
    assert not {"evidence_quote", "support_quote"} & set(final)
    review = GroundedItemReview.model_json_schema()
    assert "support_range" in review["properties"] and "support_quote" not in review["properties"]
    assert "quote" not in review["$defs"]["AuditLineRange"]["properties"]
    wire = json.dumps(audit_wire_schema(GroundedFactAudit, ("Q1",)).model_json_schema(), ensure_ascii=False)
    assert "support_quote" not in wire and "support_range" in wire


def test_evidence_is_numbered_for_the_model_and_the_code_cuts_the_original() -> None:
    result, calls = _validate(lambda docs: [line_range(docs[0], SECOND)])
    shown = next(item for item in calls[0]["evidence_items"] if item["id"] == "c1")["text"]
    assert shown.splitlines() == [f"1| {FIRST}", f"2| {SECOND}", f"3| {THIRD}"]
    [claim] = result["claim_checks"]
    assert (claim["status"], claim["reference_check"], claim["support_quote"]) == ("supported", "resolved", SECOND)
    # 根拠の本文（結び付けた根拠）に番号を持ち込まない。
    assert claim["evidence_quote"] == LONG


def test_single_unit_evidence_has_no_numbers_and_null_means_the_whole_text() -> None:
    evidence = [{"id": "c1", "source": "manual.pdf", "page_start": 1, "text": SECOND}]
    result, calls = _validate(lambda docs: [line_range(docs[0])], evidence=evidence)
    assert next(item for item in calls[0]["evidence_items"] if item["id"] == "c1")["text"] == SECOND
    assert result["claim_checks"][0]["support_quote"] == SECOND
    assert result["claim_checks"][0]["status"] == "supported"


def test_full_width_text_is_folded_for_the_model_and_cut_from_the_original() -> None:
    """モデルが読むのは文字の形をそろえた本文で、切り出す原文は元の本文のまま。"""
    original = "ＨＲＭは第２水曜です。\n対象は全社です。"
    evidence = [{"id": "c1", "source": "manual.pdf", "page_start": 1, "text": original}]
    result, calls = _validate(lambda docs: [{**line_range(docs[0]), "line_start": 1, "line_end": 1}], evidence=evidence)
    assert "1| HRM" in next(item for item in calls[0]["evidence_items"] if item["id"] == "c1")["text"]
    assert result["claim_checks"][0]["support_quote"] == "ＨＲＭは第２水曜です。"


def test_quote_in_a_model_response_is_ignored() -> None:
    """応答に写しの欄があっても読まない。原文はコードが切り出した値だけ。"""
    fabricated = "根拠に無い合成の文。"
    result, _calls = _validate(
        lambda docs: [line_range(docs[0], SECOND)], extra={"evidence_quote": fabricated, "support_quote": fabricated}
    )
    [claim] = result["claim_checks"]
    assert claim["support_quote"] == SECOND and fabricated not in json.dumps(claim, ensure_ascii=False)


def test_separate_ranges_are_cut_and_joined_with_the_gap_mark() -> None:
    result, _calls = _validate(lambda docs: [line_range(docs[0], FIRST), line_range(docs[0], THIRD)])
    [claim] = result["claim_checks"]
    assert claim["status"] == "supported"
    assert claim["support_quote"] == f"{FIRST} … {THIRD}"
    assert grounded.quote_in_text(claim["support_quote"], LONG)


@pytest.mark.parametrize(
    "lines",
    [(99, 99), (0, 1), (3, 2), (None, None), (1, None)],
    ids=["out-of-bounds", "zero", "inverted", "null-on-numbered", "half-null"],
)
def test_invalid_numbers_become_a_citation_error_without_guessing(lines: tuple[int | None, int | None]) -> None:
    result, calls = _validate(lambda docs: [{"evidence_id": docs[0]["evidence_id"], "line_start": lines[0], "line_end": lines[1]}])
    [claim] = result["claim_checks"]
    assert (claim["status"], claim["audit_status"], claim["reference_check"]) == ("citation_error", "supported", "audit_range_invalid")
    assert claim["support_quote"] == ""
    assert claim["audit_support_range"].endswith(f":{lines[0]}-{lines[1]}")
    # 参照の誤りなので、誤りを差し戻して 1 回だけ監査し直す。
    assert len(calls) == 2 and calls[1]["validation_feedback"].count("audit_range_invalid") == 1
    assert claim["reference_reaudit"] == "rejected"


def test_range_of_an_unknown_evidence_id_is_invalid() -> None:
    result, _calls = _validate(lambda docs: [{"evidence_id": "E0000000000000000000", "line_start": 1, "line_end": 1}])
    assert result["claim_checks"][0]["reference_check"] == "audit_range_invalid"


def test_chunk_id_of_several_fragments_is_not_guessed_to_a_fragment() -> None:
    """番号は片段ごとなので、複数の片段を持つ chunk の id では範囲を決めない。"""
    long_text = "\n".join(f"項目 {number} の説明です。" + "あ" * 80 for number in range(40))
    evidence = [{"id": "c1", "source": "manual.pdf", "page_start": 1, "text": long_text}]
    result, calls = _validate(lambda docs: [{"evidence_id": "c1", "line_start": 1, "line_end": 1}], evidence=evidence)
    assert sum(item["id"] == "c1" for item in calls[0]["evidence_items"]) > 1
    assert result["claim_checks"][0]["reference_check"] == "audit_range_invalid"


def test_contradicted_judgement_survives_an_invalid_range() -> None:
    """矛盾の判定は、範囲が無効でも失わない（本文に矛盾が残らないように）。"""
    result, calls = _validate(
        lambda docs: [{"evidence_id": docs[0]["evidence_id"], "line_start": 99, "line_end": 99}],
        status="contradicted",
    )
    [claim] = result["claim_checks"]
    assert (claim["status"], claim["reference_check"]) == ("contradicted", "resolved")
    assert claim["support_quote"] == "" and len(calls) == 1


def test_generation_audit_evidence_uses_the_same_numbers_as_the_cut() -> None:
    span = {"evidence_id": "E1", "label": "E1", "text": LONG}
    view = grounded.audit_line_view(span)
    assert view.rendered.splitlines()[1] == f"2| {SECOND}"
    assert view.range_text(2, 3) == (f"{SECOND}\n{THIRD}", None)
    assert view.range_text(4, 4) == ("", "citation_range_out_of_bounds")
    assert view.range_text(None, None) == ("", "citation_range_missing")
    # 上限で切った本文の番号と単位が同じなので、切り出しも同じ本文から行う。
    capped = grounded.audit_line_view(span, max_chars=len(FIRST) + 1)
    assert capped.rendered == FIRST + "\n" and capped.range_text(None, None) == (FIRST, None)
