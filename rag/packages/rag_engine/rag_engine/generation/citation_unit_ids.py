"""送達した原文の引用単位を、表示番号から独立した ID へ結ぶ。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import GenericAlias
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, create_model


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class EvidenceUnitSnapshot:
    """本文・座標の版が同じときだけ、同じ引用単位の ID を持つ。"""

    snapshot_id: str
    span_json: str
    contract: str
    units: tuple[tuple[int, int], ...]
    unit_ids: tuple[str, ...]

    @classmethod
    def create(cls, span: Mapping[str, Any], units: Sequence[tuple[int, int]], contract: str) -> EvidenceUnitSnapshot:
        text = str(span.get("text") or "")
        normalized = tuple(units)
        if any(type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) for start, end in normalized):
            raise ValueError("引用単位の座標が原文の範囲外です。")
        if any(left[1] > right[0] for left, right in zip(normalized, normalized[1:], strict=False)):
            raise ValueError("引用単位の座標が重複しています。")
        identity = {
            "source_id": span.get("source_id"),
            "source": span.get("source"),
            "text": text,
            "provenance": {
                key: span[key]
                for key in (
                    "document_scope",
                    "start",
                    "end",
                    "child_ranges",
                    "version",
                    "document_version",
                    "source_run_id",
                    "chunk_uid",
                    "origin",
                    "image_text_source_context",
                )
                if key in span
            },
            "units": normalized,
            "contract": contract,
        }
        snapshot_id = "S" + _digest(identity)
        return cls(
            snapshot_id,
            json.dumps(dict(span), ensure_ascii=False, sort_keys=True),
            contract,
            normalized,
            tuple("U" + _digest([snapshot_id, start, end])[:24] for start, end in normalized),
        )

    def span(self) -> dict[str, Any]:
        return json.loads(self.span_json)

    def prompt_record(self) -> dict[str, str]:
        """原文の復号は一度だけ行い、修正で選べる単位を入力へ出す。"""
        original = self.span()
        text = str(original.get("text") or "")
        return {
            "snapshot_id": self.snapshot_id,
            "source": str(original.get("source") or ""),
            "text": "\n".join(f"[{uid}] {text[start:end]}" for (start, end), uid in zip(self.units, self.unit_ids, strict=True)),
        }


@dataclass(frozen=True)
class BoundUnitRange:
    snapshot: EvidenceUnitSnapshot
    first: int
    last: int

    @property
    def text(self) -> str:
        original = str(self.snapshot.span().get("text") or "")
        return original[self.snapshot.units[self.first][0] : self.snapshot.units[self.last][1]]

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return self.snapshot.unit_ids[self.first : self.last + 1]


class SentUnitCatalog:
    """この送信に含めた単位だけを選択可能にする。グローバルに保持しない。"""

    def __init__(self, snapshots: Sequence[EvidenceUnitSnapshot]) -> None:
        self.snapshots = tuple(snapshots)
        self._units: dict[str, tuple[EvidenceUnitSnapshot, int]] = {}
        for snapshot in snapshots:
            for index, unit_id in enumerate(snapshot.unit_ids):
                existing = self._units.get(unit_id)
                if existing is not None and (existing[0].snapshot_id, existing[1]) != (snapshot.snapshot_id, index):
                    raise ValueError("異なる原文単位の ID が重複しています。")
                self._units.setdefault(unit_id, (snapshot, index))

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(self._units)

    def bind(self, unit_ids: Sequence[str]) -> tuple[BoundUnitRange, ...]:
        """同じ版の隣接単位だけを組にする。未選択の間の単位は引用しない。"""
        if not unit_ids:
            raise ValueError("引用単位が選択されていません。")
        grouped: dict[str, tuple[EvidenceUnitSnapshot, set[int]]] = {}
        for unit_id in unit_ids:
            if unit_id not in self._units:
                raise ValueError("今回の送信にない引用単位です。")
            snapshot, index = self._units[unit_id]
            grouped.setdefault(snapshot.snapshot_id, (snapshot, set()))[1].add(index)
        bound = []
        for snapshot, selected in grouped.values():
            first = last = -1
            for index in sorted(selected):
                if first < 0:
                    first = last = index
                elif index == last + 1:
                    last = index
                else:
                    bound.append(BoundUnitRange(snapshot, first, last))
                    first = last = index
            bound.append(BoundUnitRange(snapshot, first, last))
        return tuple(bound)

    def selection_model(self) -> type[BaseModel]:
        """自由な起止整数を持たず、実提示 ID の列挙から選ぶ契約。"""
        if not self.unit_ids:
            empty_fields: dict[str, Any] = {"unit_ids": (list[str], Field(max_length=0))}
            return create_model("NoCitationUnits", __config__=ConfigDict(extra="forbid"), **empty_fields)
        unit_type = Literal.__getitem__(self.unit_ids)
        fields: dict[str, Any] = {
            "unit_ids": (GenericAlias(list, unit_type), Field(min_length=1, description="今回の入力に表示された引用単位 ID。")),
        }
        return create_model("CitationUnitSelection", __config__=ConfigDict(extra="forbid"), **fields)


def _unit_coverage_type(catalog: SentUnitCatalog) -> type[BaseModel]:
    """根拠間の関係を示す原文にも、同じ単位選択の契約を使う。"""
    from copy import deepcopy

    from rag_engine.models.llm import RequestCoverageRange, RequestEvidenceScopeRange

    fields: dict[str, Any] = {"link": (catalog.selection_model() | None, deepcopy(RequestEvidenceScopeRange.model_fields["link"]))}
    scope = create_model("UnitRequestEvidenceScope", __base__=RequestEvidenceScopeRange, **fields)
    coverage_fields: dict[str, Any] = {
        "evidence_scope": (GenericAlias(list, scope), deepcopy(RequestCoverageRange.model_fields["evidence_scope"]))
    }
    return create_model("UnitRequestCoverage", __base__=RequestCoverageRange, **coverage_fields)


def unit_scope_model(base: type[BaseModel], catalog: SentUnitCatalog) -> type[BaseModel]:
    from copy import deepcopy

    fields: dict[str, Any] = {
        "request_coverage": (GenericAlias(list, _unit_coverage_type(catalog)), deepcopy(base.model_fields["request_coverage"]))
    }
    return create_model(base.__name__, __base__=base, **fields)


def unit_generation_model(base: type[BaseModel], catalog: SentUnitCatalog) -> type[BaseModel]:
    """この送信の生成型だけ、引用の選択を実提示単位の ID にする。"""
    from copy import deepcopy
    from typing import get_args

    item_base = get_args(base.model_fields["items"].annotation)[0]
    citation_info = deepcopy(item_base.model_fields["citations"])
    if not catalog.unit_ids:
        citation_info = Field(default_factory=list, max_length=0, description=citation_info.description)
    item_fields: dict[str, Any] = {"citations": (GenericAlias(list, catalog.selection_model()), citation_info)}
    item = create_model("UnitGroundedItemWire", __base__=item_base, **item_fields)
    fields: dict[str, Any] = {
        "items": (GenericAlias(list, item), deepcopy(base.model_fields["items"])),
        "request_coverage": (GenericAlias(list, _unit_coverage_type(catalog)), deepcopy(base.model_fields["request_coverage"])),
    }
    model = create_model(base.__name__, __base__=base, **fields)
    cast(Any, model)._uses_unit_ids = True
    return model
