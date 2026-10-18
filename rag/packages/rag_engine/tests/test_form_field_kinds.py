"""画面の図の項目の種類（form_fields の kind。#2404）の抽出・chunk の本文・台帳・回答の根拠の注記。

VLM は呼ばない（録画した応答の dict を describe_picture の差し替えで返す）。画面の図・項目名は合成の値だけを使う。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from rag_parser_core.language import FORM_FIELD_KIND_CHOICES, current_pack

from rag_engine.config import get_settings
from rag_engine.generation import form_field_kinds
from rag_engine.generation.answer_models import AnswerContext
from rag_engine.generation.answering import _grounded_prompt
from rag_engine.models.layout import LayoutRecord, PageImage
from rag_engine.models.llm import FORM_FIELD_KINDS, FormFieldOutput, PictureDescriptionOutput
from rag_engine.parsing.content_labels import content_labels
from rag_engine.parsing.picture_descriptions import _bounded_vision_description, describe_docling_pictures, vision_description_text
from rag_engine.parsing.vision_checkpoints import VisionCheckpoints
from rag_engine.parsing.vision_prompt_rules import refine_image_retrieval_prompt, vision_extraction_rules

# 合成の画面の図の VLM の応答（録画応答）。選択の項目・自由入力の項目・種類の分からない項目を 1 つずつ持つ。
RECORDED_RESPONSE = {
    "main_topic": "設定画面",
    "visual_kind": "screenshot",
    "form_fields": [
        {"name": "区分コード", "value": "A1", "state": "enabled", "kind": "select"},
        {"name": "備考欄", "value": "", "state": "enabled", "kind": "text"},
        {"name": "表示順", "value": "1", "state": "", "kind": "unknown"},
    ],
    "visible_fields": ["区分コード", "備考欄", "表示順"],
    "retrieval_text": "設定画面の項目",
}


def _record(record_id: str = "docling-p1-1") -> LayoutRecord:
    return LayoutRecord(record_id, "docling", 1, 1, [10, 10, 80, 80], "image_top_left", 100, 100, "Picture", raw_type="picture")


def _chunk(uid: str, fields: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_uid=uid,
        id=uid,
        metadata={"image_evidence": [{"image_id": f"{uid}-img", "visual_structure": {"visual_kind": "form", "form_fields": fields}}]},
    )


def _context(*records: SimpleNamespace) -> AnswerContext:
    return AnswerContext(records=list(records), text="")


class OutputContractTests(unittest.TestCase):
    def test_kind_is_a_fixed_enum_and_falls_back_to_unknown(self) -> None:
        schema = FormFieldOutput.model_json_schema()["properties"]["kind"]
        self.assertEqual(tuple(schema["enum"]), FORM_FIELD_KINDS)
        self.assertEqual(FormFieldOutput(name="a", value="", state="", kind=" Select ").kind, "select")
        # 構造化出力を強制しない接続先の決まった値の外の語・空は unknown（推測で埋めない）。
        self.assertEqual(FormFieldOutput(name="a", value="", state="", kind="dropdown").kind, "unknown")
        self.assertEqual(FormFieldOutput(name="a", value="", state="", kind="").kind, "unknown")
        self.assertIn("kind", PictureDescriptionOutput.model_json_schema()["$defs"]["FormFieldOutput"]["required"])

    def test_rules_give_visual_criteria_and_unknown_for_kind(self) -> None:
        rules = vision_extraction_rules()
        self.assertIn("name / value / state / kind", rules)
        self.assertIn("unknown にし、項目名や値から推測しない", rules)

    def test_saved_template_json_example_gains_kind(self) -> None:
        old = (
            '  "form_fields": [{"name": "項目名", "value": "表示値。空欄なら空文字", "state": '
            '"selected / unselected / enabled / disabled / required / optional / unknown"}],'
        )
        refined = refine_image_retrieval_prompt("方針\n{\n" + old + "\n}")
        self.assertIn('"kind": "select / text / checkbox / radio / date / button / unknown"', refined)

    def test_language_pack_labels_every_kind_except_unknown(self) -> None:
        self.assertEqual(FORM_FIELD_KIND_CHOICES, tuple(kind for kind in FORM_FIELD_KINDS if kind != "unknown"))
        labels = content_labels(current_pack())
        for kind in FORM_FIELD_KIND_CHOICES:
            self.assertTrue(labels.form_field_kind(kind), kind)
        self.assertEqual(labels.form_field_kind("unknown"), "")


class ChunkTextTests(unittest.TestCase):
    def test_form_field_lines_carry_kind_label(self) -> None:
        text = vision_description_text(_bounded_vision_description(RECORDED_RESPONSE))
        self.assertIn("- 区分コード = A1 [enabled] (選択)", text)
        self.assertIn("- 備考欄 [enabled] (自由入力)", text)
        # 種類が分からない項目は種類を付けない。
        self.assertIn("- 表示順 = 1\n", text + "\n")

    def test_bounded_description_normalizes_kind(self) -> None:
        description = _bounded_vision_description({"form_fields": [{"name": "a", "kind": "SELECT"}, {"name": "b"}, "x"]})
        self.assertEqual(description["form_fields"], [{"name": "a", "kind": "select"}, {"name": "b", "kind": "unknown"}, "x"])

    def test_ingest_separates_select_and_text_fields_in_text_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            page_path = run_dir / "page.png"
            Image.new("RGB", (100, 100), "white").save(page_path)
            page = PageImage(page=1, width=100, height=100, pdf_width=100, pdf_height=100, image_path=str(page_path))
            record = _record()
            with patch("rag_engine.parsing.picture_descriptions.describe_picture", return_value=dict(RECORDED_RESPONSE)):
                stats = describe_docling_pictures([record], [page], run_dir=run_dir, pdf_name="synthetic.pdf", settings=get_settings())
        self.assertEqual((stats.succeeded, stats.failed), (1, 0))
        self.assertIn("区分コード = A1 [enabled] (選択)", record.text)
        self.assertIn("備考欄 [enabled] (自由入力)", record.text)
        kinds = {field["name"]: field["kind"] for field in record.raw["visual_structure"]["form_fields"]}
        self.assertEqual(kinds, {"区分コード": "select", "備考欄": "text", "表示順": "unknown"})


class LedgerTests(unittest.TestCase):
    def _save(self, ledger: VisionCheckpoints, key: str, response: dict) -> None:
        ledger.save(key, response, _record(), state="succeeded", reusable=True)

    def test_response_without_kind_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = VisionCheckpoints(Path(tmp), "hash")
            legacy = json.loads(json.dumps(RECORDED_RESPONSE))
            for field in legacy["form_fields"]:
                field.pop("kind")
            self._save(ledger, "a" * 64, legacy)
            self._save(ledger, "b" * 64, dict(RECORDED_RESPONSE))
            self._save(ledger, "c" * 64, {"retrieval_text": "図", "form_fields": []})
            self.assertIsNone(ledger.load("a" * 64))
            self.assertEqual(ledger.load("b" * 64), RECORDED_RESPONSE)
            self.assertIsNotNone(ledger.load("c" * 64))


class AnswerEvidenceTests(unittest.TestCase):
    SELECT = [{"name": "区分コード", "value": "", "state": "", "kind": "select"}]
    TEXT = [{"name": "区分コード", "value": "", "state": "", "kind": "text"}]
    # 業務ガイドの条件: 選択肢が項目の種類の語（言語資源）と同じ。言い換えでも対応できる。
    CONDITION = ("field_kind", "項目の種類", (("選択", ()), ("入力欄", ("自由入力",))))

    def test_only_fields_named_in_question_are_observed(self) -> None:
        context = _context(_chunk("c1", [*self.SELECT, {"name": "表示順", "kind": "text"}, {"name": "x", "kind": "select"}]))
        observed = form_field_kinds.question_field_kinds("区分コードの選択肢を追加したい", context)
        self.assertEqual(observed, [form_field_kinds.FieldKindObservation("c1", "区分コード", "select")])

    def test_child_fields_are_attributed_to_parent(self) -> None:
        child = _chunk("child-1", self.SELECT)
        parent = SimpleNamespace(chunk_uid="parent-1", id="parent-1", metadata={})
        tree = (SimpleNamespace(record=parent, children=(SimpleNamespace(record=child),)),)
        context = AnswerContext(records=[parent], text="", evidence_tree=tree)
        observed = form_field_kinds.question_field_kinds("区分コード", context)
        self.assertEqual([item.source_id for item in observed], ["parent-1"])

    def test_condition_resolves_when_kind_is_single(self) -> None:
        observed = form_field_kinds.question_field_kinds("区分コード", _context(_chunk("c1", self.SELECT)))
        resolved = form_field_kinds.resolve_conditions(observed, [self.CONDITION])
        self.assertEqual(resolved, [form_field_kinds.ResolvedCondition("field_kind", "項目の種類", "選択", "select")])
        text = form_field_kinds.question_field_kinds("区分コード", _context(_chunk("c2", self.TEXT)))
        self.assertEqual(form_field_kinds.resolve_conditions(text, [self.CONDITION])[0].value, "入力欄")

    def test_condition_stays_unknown_when_kinds_conflict_or_missing(self) -> None:
        both = form_field_kinds.question_field_kinds("区分コード", _context(_chunk("c1", self.SELECT), _chunk("c2", self.TEXT)))
        self.assertEqual(len(both), 2)
        self.assertEqual(form_field_kinds.resolve_conditions(both, [self.CONDITION]), [])
        self.assertEqual(form_field_kinds.resolve_conditions([], [self.CONDITION]), [])

    def test_only_conditions_whose_options_map_to_kinds_are_used(self) -> None:
        # 選択肢が項目の種類に 1 つしか対応しない条件・項目の種類と関係の無い条件は決めない。
        self.assertEqual(form_field_kinds.condition_kind_options([("選択", ()), ("その他", ())]), {})
        self.assertEqual(form_field_kinds.condition_kind_options([("はい", ()), ("いいえ", ())]), {})
        # 1 つの種類に 2 つの選択肢が当たる種類は使わない。
        self.assertEqual(
            form_field_kinds.condition_kind_options([("選択", ()), ("プルダウン", ()), ("自由入力", ())]),
            {},
        )
        self.assertEqual(
            form_field_kinds.condition_kind_options([("選択", ()), ("自由入力", ()), ("日付", ())]),
            {"select": "選択", "text": "自由入力", "date": "日付"},
        )

    def test_prompt_note_labels_evidence_and_known_condition(self) -> None:
        observed = form_field_kinds.question_field_kinds("区分コード", _context(_chunk("c1", self.SELECT), _chunk("c9", self.SELECT)))
        resolved = form_field_kinds.resolve_conditions(observed, [self.CONDITION])
        spans = [{"label": "E2", "source_id": "c1", "source_aliases": []}]
        note = form_field_kinds.prompt_note(observed, resolved, spans)
        # 生成に渡していない根拠（c9）の観測は出さない。
        self.assertEqual(
            note,
            "根拠の画面の図の項目の種類（質問に名前が出た項目）:\n- E2: 区分コード = 選択\n"
            "業務ガイドの条件のうち、根拠の画面の図の項目の種類で分かった値（この値に当たる分岐の手順で答える）: 項目の種類=選択",
        )
        self.assertEqual(form_field_kinds.prompt_note(observed, resolved, []), "")
        payload = form_field_kinds.trace_payload(observed, resolved)
        self.assertEqual(payload["resolved_conditions"], [{"id": "field_kind", "label": "項目の種類", "value": "選択", "kind": "select"}])

    def test_grounded_prompt_carries_note(self) -> None:
        prompt = _grounded_prompt(
            "区分コード",
            [],
            _context(),
            [],
            None,
            None,
            None,
            None,
            None,
            template="{{question}}\n{{images}}",
            field_kind_note="NOTE-LINE",
        )
        self.assertIn("NOTE-LINE", prompt)


if __name__ == "__main__":
    unittest.main()
