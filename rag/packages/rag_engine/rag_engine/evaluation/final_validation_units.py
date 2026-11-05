"""最終検証へ実送達した原文単位だけを、自由な引用のコピーなしで束縛する。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import GenericAlias
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, create_model

from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot
from rag_engine.generation.grounded import CITATION_UNIT_CONTRACT, citation_units

UNIT_CONTRACT = "final_validation_units_v1"


@dataclass(frozen=True)
class FinalUnitRequest:
    inputs: dict[str, Any]
    units: dict[str, dict[str, Any]]

    def bind(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        """重複・他送信のIDは集合全体を拒否し、一部の引用として採らない。"""
        if len(ids) != len(set(ids)) or any(uid not in self.units for uid in ids):
            raise ValueError("今回の送信にない単位ID、または重複した単位IDです。")
        return [dict(self.units[uid]) for uid in ids]


def prepare_unit_request(inputs: Mapping[str, Any]) -> FinalUnitRequest:
    """本文/元座標は内部に残し、送信では各単位の原文を一度だけ提示する。"""
    units: dict[str, dict[str, Any]] = {}
    items = []
    # 共通の送信投影を使う。用途は原文fragmentの信頼検査を通した投影からだけ受け取る。
    from rag_engine.evaluation.answer_eval import model_request

    originals = list(inputs.get("evidence_items") or ())
    safe_items = model_request(dict(inputs)).get("evidence_items") or ()
    for original, safe in zip(originals, safe_items, strict=True):
        item = dict(original)
        text = str(original.get("text") or "")
        # image_purposesは、上流でscope/hashを検査して射影した値だけを受け取る。
        purposes = safe.get("image_purposes") or []
        boundaries = {point for row in purposes for point in (row["start"], row["end"]) if type(point) is int and 0 <= point <= len(text)}
        base_units = citation_units(text, image_text=item.get("origin") == "image_extraction")
        positions: list[tuple[int, int]] = []
        for start, end in base_units:
            points = [start, *sorted(point for point in boundaries if start < point < end), end]
            positions.extend((a, b) for a, b in zip(points, points[1:], strict=False) if text[a:b].strip())
        scope = {
            key: item[key]
            for key in (
                "document_id",
                "recipe_id",
                "chunk_set_id",
                "version",
                "source_run_id",
                "id",
                "evidence_id",
                "fragment_start",
                "fragment_end",
            )
            if key in item
        }
        scope["image_purposes"] = purposes
        span = {"source_id": item.get("id"), "source": item.get("source"), "text": text, "document_scope": scope}
        snapshot = EvidenceUnitSnapshot.create(span, positions, UNIT_CONTRACT + ":" + CITATION_UNIT_CONTRACT)
        shown = []
        for (start, end), uid in zip(snapshot.units, snapshot.unit_ids, strict=True):
            row = {
                "unit_id": uid,
                "evidence_id": str(item["evidence_id"]),
                "source_id": str(item.get("id") or ""),
                "text": text[start:end],
                "start": start,
                "end": end,
                "fragment_start": item.get("fragment_start", 0),
                "scope": scope,
            }
            if uid in units:
                raise ValueError("実送達する単位IDが重複しています。")
            units[uid] = row
            unit = {"unit_id": uid, "text": row["text"]}
            selected_purposes = [
                {"start": max(start, r["start"]) - start, "end": min(end, r["end"]) - start, "purpose": r["purpose"]}
                for r in purposes
                if r["start"] < end and start < r["end"]
            ]
            if selected_purposes:
                unit["image_purposes"] = selected_purposes
                row["image_purposes"] = selected_purposes
            shown.append(unit)
        # 元の最終検証が提示する場所・種類だけを残し、内部scope/hashを送信しない。
        prompt_item = {
            key: safe[key]
            for key in (
                "id",
                "evidence_id",
                "parent_id",
                "source",
                "source_run_id",
                "page_start",
                "page_end",
                "section_path",
                "sheet_name",
                "row_start",
                "row_end",
                "cell_range",
                "evidence_type",
                "origin",
                "tags",
            )
            if key in safe
        }
        prompt_item["units"] = shown
        items.append(prompt_item)
    return FinalUnitRequest({**inputs, "evidence_items": items}, units)


def final_unit_schema(base: type[BaseModel], request: FinalUnitRequest) -> type[BaseModel]:
    """schemaのenumと実行時exact bindingを分離し、raw否定の診断を失わない。"""
    properties = base.model_json_schema()["properties"]
    fields: dict[str, Any] = {}
    for name in properties:
        if name in {"evidence_id", "evidence_quote", "source_id"}:
            continue
        info = deepcopy(base.model_fields[name])
        if name == "answer_quote":
            info.default = ""
            info.metadata = []
        if name == "evidence_target":
            info.description = "選択した原文単位が当てはまる対象。原文と見出しの語で短く書く。"
        fields[name] = (base.model_fields[name].annotation, info)
        if name == "answer_passage_id":
            # Pydanticは文字列の構造だけを検査する。enumの強制検査は必ずrequest.bindで行う。
            # 未登録IDのcontradictedも診断として保持し、根拠に束縛した否定へ昇格させない。
            extra: dict[str, Any] = {"items": {"type": "string", "enum": list(request.units)}} if request.units else {"maxItems": 0}
            fields["evidence_unit_ids"] = (list[str], Field(json_schema_extra=extra))
    claim = create_model("FinalValidationUnitClaim", __config__=ConfigDict(extra="forbid"), **fields)
    output_fields: dict[str, Any] = {
        "claim_checks": (GenericAlias(list, claim), Field(default_factory=list, max_length=128)),
    }
    return create_model("FinalValidationUnitAudit", __config__=ConfigDict(extra="forbid"), **output_fields)


def restore_unit_claims(
    output: BaseModel, request: FinalUnitRequest, claim_type: type[BaseModel], output_type: type[BaseModel]
) -> BaseModel:
    """意味判定を変えずに出典を復元。不正参照は否定保持付きの診断にする。"""
    result = []
    for wire in cast(Any, output).claim_checks:
        values = wire.model_dump()
        ids = values.pop("evidence_unit_ids")
        raw_status = values["status"]
        invalid = False
        try:
            selected = request.bind(ids)
        except ValueError:
            selected = []
            invalid = True
        reference = "resolved" if selected else ("audit_id_invalid" if ids else "audit_id_missing")
        if raw_status == "supported" and not selected:
            values["status"] = "citation_error"
        elif raw_status == "citation_error" and selected:
            values["status"] = "unassessed"
        if raw_status in {"supported", "contradicted", "citation_error", "figure_only"} and not selected:
            values["reason"] = "原文単位の参照を確認できません。" + values["reason"]
        # 一つでも不正なら選択集合全体が空。部分的な出典の回復は行わない。
        eids = list(dict.fromkeys(row["evidence_id"] for row in selected))
        result.append(
            claim_type.model_validate(
                {
                    **values,
                    "evidence_id": ",".join(eids),
                    "source_id": selected[0]["source_id"] if selected else "",
                    "evidence_quote": selected[0]["text"] if selected else "",
                    "support_quote": "",
                    "audit_status": raw_status,
                    "audit_evidence_id": ",".join(ids),
                    "reference_check": reference,
                    "unit_selection": selected,
                    "unit_selection_invalid": invalid,
                }
            )
        )
    return output_type.model_construct(claim_checks=result)
