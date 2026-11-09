"""区切り線の真正性と、説明を支える引用としての可用性を分離する。"""

from typing import Any

from rag_parser_core.language import bind_pack, builtin_pack, resolve

from rag_engine.generation import grounded, passage_marks
from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem

BODY = "対象を選択し、内容を入力して実行します。"


def evidence(text: str, label: str = "E1") -> dict[str, Any]:
    return {
        "evidence_id": f"stable-{label}",
        "label": label,
        "source_id": f"parent-{label}",
        "source": "synthetic.pdf",
        "function": ("synthetic.pdf", "合成操作"),
        "text": text,
    }


def check(text: str, *, lines: str = "1", interval: tuple[int | None, int | None] | None = (1, 1)) -> tuple[Any, list[dict[str, Any]]]:
    span = evidence(text)
    grounded.format_functions([span])
    citation = GroundedCitation(evidence_id="E1", lines=lines)
    citation._wire_range = interval
    proposal = GroundedItem(kind="operation", text=BODY, citations=[citation])
    checked, dropped = grounded.verify("操作方法は？", GroundedDraft(items=[proposal], summary="", confidence="low"), [span])
    return next(row for row in checked if row.item.text == BODY), dropped


def test_existing_language_pack_without_new_reason_uses_builtin_label() -> None:
    base = builtin_pack()
    old_labels = [
        f"{label}={code}"
        for label, code in base.mapping("answer_labels", "passage_reason_labels").items()
        if code != "citation_content_unusable"
    ]
    old_pack = resolve(base, {"answer_labels": {"passage_reason_labels": old_labels}})
    with bind_pack(old_pack):
        assert passage_marks.reason("citation_content_unusable")["label"] == "引用が区切り線・書式のみ"
