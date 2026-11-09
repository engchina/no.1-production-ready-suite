"""導出の要点は、依存する basis の原文を引用として必ず隣に持つ（結論の言い換えで条件が消えても原文が残る）。"""

import re
from types import SimpleNamespace

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answer_parts import check_answer_parts
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft
from rag_engine.generation.grounded import CheckedItem, generation_output_model

RULE = "申請は開始日の10日前までに行います。"
EXCEPTION = "区分Bの場合は3日前までです。"
TEXT = f"{RULE}\n{EXCEPTION}"
# 条件（日数）を落とした導出の結論。原文の写しではないので導出のまま残る。
DROPPED = "区分によって期限が異なるため、区分を確認してください。"


def catalog_case():
    span = {"label": "E1", "evidence_id": "source-r", "source_id": "s-r", "source": "synthetic.txt", "text": TEXT}
    snapshot = EvidenceUnitSnapshot.create(span, [(0, len(RULE)), (len(RULE) + 1, len(TEXT))], "explicit_units_v5")
    catalog = SentUnitCatalog([snapshot])
    return generation_output_model("申請の期限はいつですか。", unit_catalog=catalog), catalog, snapshot


def part(kind, unit_ids, requirement="期限", result=DROPPED):
    answer = {"kind": kind, "basis": {"unit_ids": unit_ids}}
    if kind == "answer":
        answer.update(reasoning="区分が不明なので場合分けする。", result=result)
    return {"request_id": "Q1", "requirement": requirement, "answer": answer}


def draft_of(parts, summary=(0,)):
    model, catalog, _ = catalog_case()
    raw = {"answer_parts": parts, "summary_part_indexes": list(summary), "confidence": "medium"}
    return canonical_generation_draft(model.model_validate(raw), unit_catalog=catalog)


def shown_texts(draft, index):
    """利用者へ示す要点の内容: 引用（ID から切り出した原文）と導出の結論。"""
    current = draft.answer_parts[index]
    return [draft.items[i].text for i in current.item_indexes] + [draft.applications[i].result for i in current.application_indexes]


def test_derived_part_keeps_its_basis_originals_as_quote_items_next_to_the_result():
    _, _, snapshot = catalog_case()
    draft = draft_of([part("answer", list(snapshot.unit_ids))])
    derived = draft.answer_parts[0]
    assert derived.result_kind == "derived"
    assert derived.application_indexes == [0]
    assert derived.item_indexes == draft.applications[0].item_indexes == [0]
    shown = "\n".join(shown_texts(draft, 0))
    assert RULE in shown and EXCEPTION in shown and DROPPED in shown
    # 摘要は導出の依存だけ（未確認の結論を原文の依存で公開する経路を作らない）。
    assert draft.summary == DROPPED
    assert draft.summary_item_indexes == [] and draft.summary_application_indexes == [0]


def test_quote_already_shown_by_a_direct_part_is_not_repeated_on_the_derived_part():
    _, _, snapshot = catalog_case()
    first, second = snapshot.unit_ids
    draft = draft_of([part("source", [first], requirement="原則の期限"), part("answer", [first, second])])
    direct, derived = draft.answer_parts
    assert direct.result_kind == "direct" and derived.result_kind == "derived"
    assert [draft.items[i].text for i in direct.item_indexes] == [RULE]
    # source の 1 単位と導出の 2 単位は範囲が違うので別の item。導出の引用は自分の basis の範囲を指す。
    assert derived.item_indexes == draft.applications[0].item_indexes
    same = draft_of([part("source", list(snapshot.unit_ids), requirement="期限の規則"), part("answer", list(snapshot.unit_ids))])
    assert same.answer_parts[1].item_indexes == []
    assert same.answer_parts[1].application_indexes == [0]


def test_quotes_of_another_request_are_not_deduplicated_against_this_request():
    _, _, snapshot = catalog_case()
    other = part("source", list(snapshot.unit_ids), requirement="別の要求")
    other["request_id"] = "Q2"
    draft = draft_of([other, part("answer", list(snapshot.unit_ids))])
    derived = draft.answer_parts[1]
    assert derived.item_indexes and all(draft.items[i].request_id == "Q1" for i in derived.item_indexes)


def test_derived_part_with_quotes_is_linked_only_when_its_conclusion_is_published():
    _, _, snapshot = catalog_case()
    draft = draft_of([part("answer", list(snapshot.unit_ids))])
    entries = {0: CheckedItem(draft.items[0], span={"evidence_id": "source-r"}, draft_index=0)}

    def application(published):
        return SimpleNamespace(published=published, application=draft.applications[0])

    assert check_answer_parts(draft, entries, [application(True)], {"Q1"})["status"] == "linked"
    assert check_answer_parts(draft, entries, [application(False)], {"Q1"})["status"] == "incomplete"
    assert check_answer_parts(draft, {}, [application(True)], {"Q1"})["status"] == "incomplete"


def run():
    calls = []

    def parse(system, prompt, config, schema, **options):
        calls.append(schema.__name__)
        if schema.__name__ != "GroundedDraftWire":
            raise RuntimeError(f"合成では呼ばない段: {schema.__name__}")
        ids = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)
        return schema.model_validate(
            {
                "answer_parts": [part("answer", ids)],
                "summary_part_indexes": [0],
                "request_coverage": [
                    {"request_id": "Q1", "evidence_scope": [{"evidence_id": "E1", "target": "期限", "relation": "direct", "link": None}]}
                ],
                "confidence": "medium",
            }
        )

    def unused(*args, **kwargs):
        raise AssertionError("追加の検索・画像呼出しは不要")

    record = AnswerRecord(
        id="source-r",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=TEXT,
        source="synthetic.txt",
        chunk_level="parent",
        chunk_id="source-r",
        source_run_id="synthetic",
        metadata={"layout": {"native_text_ranges": [{"start": 0, "end": len(TEXT)}]}},
    )
    settings = get_settings(environ={}, dotenv_path=None)
    with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
        response = synthesize_grounded_answer(
            "申請の期限はいつですか。", AnswerContext(records=[record], text=TEXT), settings, image_prompt_mode="text_only"
        ).response
    return response, calls
