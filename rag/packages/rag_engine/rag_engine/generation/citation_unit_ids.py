"""送達した原文の引用単位を、表示番号から独立した ID へ結ぶ。

モデルに見せて選ばせるのは単位のラベル（根拠のラベルと単位の順番。``E2.1``）だけにする。単位の ID
（原文の版から決まる hash）は版の同一性と出典の記録に使い、モデルの入力には出さない。根拠を指す書き方を、
根拠のブロックのラベルとそれを延ばした単位のラベルの 1 つの体系にするため（#5078）。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import GenericAlias
from typing import Annotated, Any, Literal, cast

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
    # モデルに見せる単位のラベル（``unit_label``）。表示の名前なので、版の同一性の比較には使わない。
    unit_labels: tuple[str, ...] = field(default=(), compare=False)

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
                )
                if key in span
            },
            "units": normalized,
            "contract": contract,
        }
        snapshot_id = "S" + _digest(identity)
        unit_ids = tuple("U" + _digest([snapshot_id, start, end])[:24] for start, end in normalized)
        label = str(span.get("label") or "")
        return cls(
            snapshot_id,
            json.dumps(dict(span), ensure_ascii=False, sort_keys=True),
            contract,
            normalized,
            unit_ids,
            # ラベルの無い根拠はモデルに示さないので、選択の名前には版の ID をそのまま使う。
            tuple(unit_label(label, index) for index in range(1, len(normalized) + 1)) if label else unit_ids,
        )

    @property
    def label(self) -> str:
        """モデルに示した根拠のブロックのラベル（``E2``）。"""
        return str(self.span().get("label") or "")

    def span(self) -> dict[str, Any]:
        return json.loads(self.span_json)

    def prompt_record(self) -> dict[str, str]:
        """原文の復号は一度だけ行い、修正で選べる単位を入力へ出す。"""
        original = self.span()
        text = str(original.get("text") or "")
        return {
            "snapshot_id": self.snapshot_id,
            "source": str(original.get("source") or ""),
            "text": "\n".join(f"[{name}] {text[start:end]}" for (start, end), name in zip(self.units, self.unit_labels, strict=True)),
        }


def unit_label(block_label: str, index: int) -> str:
    """根拠のブロックのラベルと、その中の単位の順番（1 始まり）から作る単位のラベル（``E2`` の 1 番目は ``E2.1``）。"""
    return f"{block_label}.{index}"


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
        self._blocks: dict[str, tuple[str, ...]] = {}
        for snapshot in snapshots:
            for index, name in enumerate(snapshot.unit_labels):
                existing = self._units.get(name)
                if existing is not None and (existing[0].snapshot_id, existing[1]) != (snapshot.snapshot_id, index):
                    raise ValueError("異なる原文単位のラベルが重複しています。")
                self._units.setdefault(name, (snapshot, index))
            if (block := snapshot.label) and snapshot.unit_labels:
                self._blocks.setdefault(block, snapshot.unit_labels)

    @property
    def unit_labels(self) -> tuple[str, ...]:
        """この送信で選べる単位のラベル。"""
        return tuple(self._units)

    @property
    def block_labels(self) -> tuple[str, ...]:
        """引用できる単位を持つ根拠のブロックのラベル。"""
        return tuple(self._blocks)

    def block_units(self, block_label: str) -> tuple[str, ...]:
        """根拠のブロックのすべての単位のラベル。"""
        return self._blocks[block_label]

    def bind(self, unit_labels: Sequence[str]) -> tuple[BoundUnitRange, ...]:
        """同じ版の隣接単位だけを組にする。未選択の間の単位は引用しない。"""
        if not unit_labels:
            raise ValueError("引用単位が選択されていません。")
        grouped: dict[str, tuple[EvidenceUnitSnapshot, set[int]]] = {}
        for name in unit_labels:
            if name not in self._units:
                raise ValueError("今回の送信にない引用単位です。")
            snapshot, index = self._units[name]
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
        """自由な起止整数を持たず、実提示の単位のラベルの列挙から選ぶ契約。"""
        if not self.unit_labels:
            empty_fields: dict[str, Any] = {"unit_ids": (list[str], Field(max_length=0))}
            return create_model("NoCitationUnits", __config__=ConfigDict(extra="forbid"), **empty_fields)
        unit_type = Literal.__getitem__(self.unit_labels)
        fields: dict[str, Any] = {
            "unit_ids": (GenericAlias(list, unit_type), Field(min_length=1, description="今回の入力に表示された引用単位のラベル。")),
        }
        return create_model("CitationUnitSelection", __config__=ConfigDict(extra="forbid"), **fields)


def _unit_coverage_type(catalog: SentUnitCatalog, request_ids: Sequence[str] = ()) -> type[BaseModel]:
    """根拠間の関係を示す原文にも、同じ単位選択の契約を使う。``request_ids`` を渡すと request_id はその列挙だけを受ける。"""
    from copy import deepcopy

    from rag_engine.models.llm import RequestCoverageRange, RequestEvidenceScopeRange

    fields: dict[str, Any] = {"link": (catalog.selection_model() | None, deepcopy(RequestEvidenceScopeRange.model_fields["link"]))}
    scope = create_model("UnitRequestEvidenceScope", __base__=RequestEvidenceScopeRange, **fields)
    coverage_fields: dict[str, Any] = {
        "evidence_scope": (GenericAlias(list, scope), deepcopy(RequestCoverageRange.model_fields["evidence_scope"]))
    }
    if request_ids:
        coverage_fields["request_id"] = (
            cast(Any, Literal)[tuple(dict.fromkeys(request_ids))],
            deepcopy(RequestCoverageRange.model_fields["request_id"]),
        )
    return create_model("UnitRequestCoverage", __base__=RequestCoverageRange, **coverage_fields)


def _one_plan_per_request(plans: list[Any]) -> list[Any]:
    """同じ要求の計画は 1 件だけ（#4587）。下流は要求ごとの計画として読むので、2 件目以降には使い道が無い。"""
    repeated = [request_id for request_id, count in Counter(plan.request_id for plan in plans).items() if count > 1]
    if repeated:
        raise ValueError(f"request_coverage に要求 {'、'.join(repeated)} の計画が複数あります。要求ごとに計画を 1 件にしてください。")
    return plans


def request_coverage_field(base: type[BaseModel], catalog: SentUnitCatalog, request_ids: Sequence[str] = ()) -> tuple[Any, Any]:
    """計画の出力の request_coverage の型と欄の定義（回答の生成の出力には計画が無い。#4611）。

    ``request_ids`` （その回の要求の id。空でないとき）を渡すと、計画は要求ごとにちょうど 1 件にする（#4587）:
    request_id はその id の列挙、件数の下限と上限は要求の数、同じ id の 2 件目は検証で拒む。要求の数に関係なく
    上限まで受けると、弱いモデルが同じ要求の計画を言い回しを変えて繰り返し、出力が膨らむ・終わらない。
    """
    from copy import deepcopy

    from annotated_types import MaxLen, MinLen
    from pydantic import AfterValidator

    info = deepcopy(base.model_fields["request_coverage"])
    coverage = _unit_coverage_type(catalog, request_ids)
    if not request_ids:
        return GenericAlias(list, coverage), info
    count = len(dict.fromkeys(request_ids))
    info.metadata = [item for item in info.metadata if not isinstance(item, MinLen | MaxLen)] + [MinLen(count), MaxLen(count)]
    return Annotated[GenericAlias(list, coverage), AfterValidator(_one_plan_per_request)], info


def unit_generation_model(base: type[BaseModel], catalog: SentUnitCatalog) -> type[BaseModel]:
    """この送信の生成型だけ、引用の選択を実提示単位の ID にする。"""
    from copy import deepcopy
    from typing import get_args

    item_base = get_args(base.model_fields["items"].annotation)[0]
    citation_info = deepcopy(item_base.model_fields["citations"])
    if not catalog.unit_labels:
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
