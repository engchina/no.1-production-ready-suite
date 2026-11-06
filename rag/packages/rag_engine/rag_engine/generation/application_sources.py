"""推導監査へ渡す引用の、検証済み出典・版・選択範囲。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from rag_engine.generation.citation_unit_ids import BoundUnitRange, EvidenceUnitSnapshot
from rag_engine.generation.document_inputs import document_key


def application_item_citations(entry: Any) -> list[dict[str, Any]]:
    """同名・同文でもまとめず、本検証のprivate記録と整合する束縛を写す。

    既存の監査用citationsの選別とは分離する。旧行指定は検証した範囲を保つが、
    存在しないunit IDを補わない。座標とhashは抽出時と同じ送達viewのもの。
    """
    result = []
    for member in (entry, *getattr(entry, "also", ())):
        if row := _citation_binding(member):
            result.append(row)
    return result


def _citation_binding(member: Any) -> dict[str, Any] | None:
    # groundedからも使用するため、原文viewの形式処理だけを実行時に参照する。
    from rag_engine.generation import grounded

    citations = getattr(member.item, "citations", ())
    if not citations or not isinstance(member.span, dict):
        return None
    citation = citations[0]
    proof = getattr(citation, "_extraction", {})
    if not isinstance(proof, Mapping) or proof.get("status") != "extracted" or proof.get("authenticity") != "verified":
        return None
    binding = getattr(citation, "_unit_binding", None)
    if binding is not None and not isinstance(binding, BoundUnitRange):
        return None
    original = binding.snapshot.span() if binding is not None else member.span
    if proof.get("source_id") != original.get("source_id") or proof.get("resolved_evidence_id") != original.get("evidence_id"):
        return None
    text = str(original.get("text") or "")
    if proof.get("content_sha256") != hashlib.sha256(text.encode()).hexdigest():
        return None
    try:
        first, last = int(proof["char_start"]), int(proof["char_end"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= first < last <= len(text) or citation.quote != text[first:last].strip():
        return None
    if binding is not None:
        snapshot = binding.snapshot
        rebuilt = EvidenceUnitSnapshot.create(original, snapshot.units, snapshot.contract)
        if rebuilt.snapshot_id != snapshot.snapshot_id or rebuilt.unit_ids != snapshot.unit_ids:
            return None
        if proof.get("snapshot_id") != snapshot.snapshot_id or not 0 <= binding.first <= binding.last < len(snapshot.units):
            return None
        if (first, last) != (snapshot.units[binding.first][0], snapshot.units[binding.last][1]):
            return None
        units, contract = snapshot.units, snapshot.contract
    else:
        scope = json.dumps(
            {key: original[key] for key in ("document_scope", "source_aliases", "start", "end", "child_ranges") if key in original},
            ensure_ascii=False,
            sort_keys=True,
        )
        if proof.get("source_scope_sha256") != hashlib.sha256(scope.encode()).hexdigest():
            return None
        contract = str(proof.get("citation_unit_contract") or "")
        if contract in grounded._VERSIONED_UNIT_CONTRACTS:
            units = tuple(grounded.citation_units(text, contract_version=contract, image_text=grounded._image_text(original)))
        elif contract == "legacy_threshold_v1":
            units = tuple(grounded._legacy_citation_units(text))
        else:
            return None
    numbered = bool(units) if contract in grounded._EXPLICIT_UNIT_CONTRACTS else grounded._numbered(text, units)
    rendered = grounded._rendered_evidence_span(original, units=units, numbered=numbered)
    displayed = grounded._model_evidence_span(original, rendered, units)
    if proof.get("view_sha256") != hashlib.sha256(displayed.encode()).hexdigest():
        return None
    return {
        "evidence_id": original["evidence_id"],
        "source_id": original["source_id"],
        "document_scope": list(document_key(original)),
        "unit_ids": list(binding.unit_ids) if binding is not None else [],
        "char_start": first,
        "char_end": last,
        "quote": citation.quote,
    }
