"""根拠の画面の図の項目の種類を、回答の材料と業務ガイドの条件に使う（#2404）。

取込の Vision は画面の図の項目（``form_fields``）に種類（``kind``: select / text / checkbox / radio / date /
button / unknown）を付け、chunk の metadata（``image_evidence[].visual_structure.form_fields``）に残す。
回答では、質問に名前が出た項目だけを根拠から拾い、

- 項目の種類を根拠の注記として生成の材料に渡す（どの根拠の画面の図か、表示の語は言語資源）。
- 業務ガイドの分からなかった条件（backend が ``evidence_conditions`` として渡す）のうち、選択肢が項目の種類に
  対応する条件（``condition_kind_options``）を、拾った種類が 1 つに決まるときだけ既知にする。種類が割れる・
  拾えないときは決めない（今までどおり）。
- 条件が決まったら、backend が決まり方ごとに前もって作った業務ガイドの手順の計画（``evidence_plans``。#2689）に
  切り替える: 外れた分岐の手順を回答の手順から外し、決まった分岐の手順を必須にし、業務ガイドのルールの本文を
  決まった条件の版にする（``with_resolved_plan``）。分岐の判断は backend の業務ガイドの照合が正本で、ここでは
  決まった条件の組で計画を選ぶだけ。

照合は決定的（LLM を呼ばない）。項目名は文字の形（NFKC・大小・記号・空白）をそろえて、質問の中に含まれるかで比べる。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from rag_engine.generation.answer_payload import _metadata_image_evidence
from rag_engine.models.llm import form_field_kind
from rag_engine.parsing.content_labels import content_labels

# 質問の中に名前を探す項目名の最短の長さ（そろえた後の文字数）。1 字の項目名は質問の別の語に当たりやすい。
MIN_FIELD_NAME_CHARS = 2
# 生成の材料に出す観測の上限（同じ項目・種類・根拠は 1 つにまとめた後）。
MAX_NOTE_OBSERVATIONS = 12

# 業務ガイドの条件の形（backend が照合の結果から渡す）: (条件の id, 名前, ((選択肢, (言い換え, ...)), ...))。
EvidenceCondition = tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]]
# 条件が決まったときの業務ガイドの手順の計画（backend が渡す。#2689）:
# (決まった条件の (id, 値) の列, 残る手順の (id, 必須か) の列, 業務ガイドのルールの id, そのルールの本文)。
EvidencePlan = tuple[tuple[tuple[str, str], ...], tuple[tuple[str, bool], ...], str, str]


@dataclass(frozen=True)
class FieldKindObservation:
    """根拠（parent の source_id）の画面の図で、質問に名前が出た項目とその種類。"""

    source_id: str
    name: str
    kind: str


@dataclass(frozen=True)
class ResolvedCondition:
    """根拠の画面の図の項目の種類で値が決まった業務ガイドの条件。"""

    condition_id: str
    label: str
    value: str
    kind: str


def normalized_name(text: str) -> str:
    """照合に使う文字の形（NFKC・小文字・文字と数字だけ）。"""
    folded = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return "".join(char for char in folded if unicodedata.category(char)[0] in {"L", "N"})


def record_form_fields(record: Any) -> list[tuple[str, str]]:
    """record の metadata の画面の図の項目（名前, 種類）。種類が unknown の項目と名前の無い項目は除く。"""
    metadata = getattr(record, "metadata", None)
    if not isinstance(metadata, dict):
        return []
    fields: list[tuple[str, str]] = []
    for image in _metadata_image_evidence(metadata):
        structure = image.get("visual_structure")
        items = structure.get("form_fields") if isinstance(structure, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            kind = form_field_kind(item.get("kind"))
            if name and kind != "unknown":
                fields.append((name, kind))
    return fields


def _source_id(record: Any) -> str:
    return str(getattr(record, "chunk_uid", "") or getattr(record, "id", "") or "")


def question_field_kinds(question: str, context: Any) -> list[FieldKindObservation]:
    """回答の context の根拠から、質問に名前が出た画面の図の項目と種類を、根拠の順に返す。

    child の metadata の項目は、生成に渡す根拠（parent）の source_id に帰属させる。
    """
    target = normalized_name(question)
    if not target:
        return []
    tree = tuple(getattr(context, "evidence_tree", ()) or ())
    groups: list[tuple[Any, list[Any]]] = (
        [(parent.record, [parent.record, *(child.record for child in parent.children)]) for parent in tree]
        if tree
        else [(record, [record]) for record in getattr(context, "records", ()) or ()]
    )
    seen: set[tuple[str, str, str]] = set()
    observations: list[FieldKindObservation] = []
    for owner, members in groups:
        source_id = _source_id(owner)
        for member in members:
            for name, kind in record_form_fields(member):
                key = normalized_name(name)
                if len(key) < MIN_FIELD_NAME_CHARS or key not in target or (source_id, key, kind) in seen:
                    continue
                seen.add((source_id, key, kind))
                observations.append(FieldKindObservation(source_id, name, kind))
    return observations


def condition_kind_options(options: Iterable[tuple[str, Sequence[str]]]) -> dict[str, str]:
    """条件の選択肢のうち、項目の種類に対応する選択肢（種類 → 選択肢）。

    選択肢の語（選択肢そのものと言い換え）が言語資源の項目の種類の語（content_labels.form_field_kinds）と
    文字の形をそろえて一致すれば、その種類の選択肢とする。2 つの種類に当たる選択肢・1 つの種類に 2 つの選択肢が
    当たる種類は使わない。項目の種類の条件とみなすのは、2 つ以上の種類に選択肢が対応するときだけ（それ未満は空）。
    """
    words_by_kind = {
        kind: {normalized_name(word) for word in words} - {""} for kind, words in content_labels().form_field_kind_words.items()
    }
    by_kind: dict[str, list[str]] = {}
    for option, words in options:
        keys = {normalized_name(word) for word in (option, *words)} - {""}
        kinds = [kind for kind, kind_words in words_by_kind.items() if keys & kind_words]
        if len(kinds) == 1:
            by_kind.setdefault(kinds[0], []).append(option)
    mapping = {kind: values[0] for kind, values in by_kind.items() if len(values) == 1}
    return mapping if len(mapping) >= 2 else {}


def resolve_conditions(observations: Sequence[FieldKindObservation], conditions: Iterable[EvidenceCondition]) -> list[ResolvedCondition]:
    """観測した種類が 1 つに決まるとき、その種類に対応する選択肢で条件を決める。割れていれば何も決めない。"""
    kinds = {item.kind for item in observations}
    if len(kinds) != 1:
        return []
    (kind,) = kinds
    resolved: list[ResolvedCondition] = []
    for condition_id, label, options in conditions:
        value = condition_kind_options(options).get(kind)
        if value:
            resolved.append(ResolvedCondition(condition_id, label, value, kind))
    return resolved


def resolved_plan(resolved: Sequence[ResolvedCondition], plans: Iterable[EvidencePlan]) -> EvidencePlan | None:
    """決まった条件の組（id と値）がちょうど一致する計画。条件が決まらない・一致する計画が無ければ None。"""
    if not resolved:
        return None
    wanted = sorted((item.condition_id, item.value) for item in resolved)
    return next((plan for plan in plans if sorted(plan[0]) == wanted), None)


def with_resolved_plan(runtime_knowledge: Any, resolved: Sequence[ResolvedCondition], plans: Iterable[EvidencePlan]) -> Any:
    """決まった条件の計画に切り替えた runtime knowledge（#2689）。計画が無ければそのまま返す。

    手順ごとの根拠・検索の手がかりは計画に残る手順だけにし、必須に数えない手順は計画の必須かで決め直し、
    業務ガイドのルール（rule の id が一致するもの）の本文を計画の本文にする。
    """
    plan = resolved_plan(resolved, plans) if runtime_knowledge is not None else None
    if plan is None:
        return runtime_knowledge
    _conditions, steps, rule_id, rule_content = plan
    kept = {step_id for step_id, _required in steps}
    rules = tuple(
        replace(rule, content=rule_content) if rule_content and rule.rule_id == rule_id else rule
        for rule in runtime_knowledge.matched_rules
    )
    return replace(
        runtime_knowledge,
        matched_rules=rules,
        guide_steps=tuple(item for item in runtime_knowledge.guide_steps if item[0] in kept),
        guide_optional_steps=tuple(step_id for step_id, required in steps if not required),
        guide_step_terms=tuple(item for item in runtime_knowledge.guide_step_terms if item[0] in kept),
    )


def prompt_note(
    observations: Sequence[FieldKindObservation],
    resolved: Sequence[ResolvedCondition],
    spans: Sequence[Mapping[str, Any]],
) -> str:
    """生成の材料に入れる根拠の注記。生成に渡す根拠（spans）に無い出典の観測は出さない。"""
    labels: dict[str, str] = {}
    for span in spans:
        label = str(span.get("label") or "")
        for source_id in (span.get("source_id"), *(span.get("source_aliases") or ())):
            if source_id and label:
                labels.setdefault(str(source_id), label)
    names = content_labels()
    lines = []
    for item in observations:
        label = labels.get(item.source_id)
        word = names.form_field_kind(item.kind)
        if label and word:
            lines.append(f"- {label}: {item.name} = {word}")
    lines = list(dict.fromkeys(lines))[:MAX_NOTE_OBSERVATIONS]
    if not lines:
        return ""
    parts = ["根拠の画面の図の項目の種類（質問に名前が出た項目）:\n" + "\n".join(lines)]
    if resolved:
        parts.append(
            "業務ガイドの条件のうち、根拠の画面の図の項目の種類で分かった値（この値に当たる分岐の手順で答える）: "
            + "、".join(f"{item.label}={item.value}" for item in resolved)
        )
    return "\n".join(parts)


def trace_payload(
    observations: Sequence[FieldKindObservation], resolved: Sequence[ResolvedCondition], plans: Iterable[EvidencePlan] = ()
) -> dict[str, Any]:
    """回答の記録に残す観測と、決まった条件と、切り替えた業務ガイドの手順の計画（手順の id と必須か）。"""
    plan = resolved_plan(resolved, plans)
    return {
        "observations": [{"source_id": item.source_id, "name": item.name, "kind": item.kind} for item in observations],
        "resolved_conditions": [
            {"id": item.condition_id, "label": item.label, "value": item.value, "kind": item.kind} for item in resolved
        ],
        **({"guide_plan": [{"step_id": step_id, "required": required} for step_id, required in plan[1]]} if plan else {}),
    }
