"""取得済みの列定義と結果から、確認状況の節だけを生成する。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .answer_data import REGISTRY_KEY, DataIndex, DataReferenceError
from .profile_schema_context import _Schema

REPORT_FIELDS_KEY = "_report_schema_fields"
MAX_REPORT_FIELD_CATALOGS = 32
MAX_REPORT_FIELD_ENTRIES = 4096
MAX_REPORT_FIELD_RESULTS = 128
MAX_REPORT_FIELDS_SNAPSHOT_BYTES = 1024 * 1024
_FIELD = r"^S[1-9][0-9]*:F[1-9][0-9]*$"
_LABEL_META = "[]【】`"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ConfirmationItem(_Strict):
    field_ref: str = Field(pattern=_FIELD)
    result_ref: str | None = Field(default=None, pattern=r"^T[1-9][0-9]*$")


class ConfirmationSection(_Strict):
    kind: Literal["confirmation_section"] = "confirmation_section"
    items: list[ConfirmationItem]


class _Field(_Strict):
    ref: str
    owner: str
    object_name: str
    column: str


class _Catalog(_Strict):
    step_id: str
    digest: str
    profile_id: str
    revision: str
    ref: str
    status: str
    complete: bool
    fields: list[_Field]


class _Observation(_Strict):
    sources: list[str]
    returned: int = Field(ge=0)
    nulls: int = Field(ge=0)

    @model_validator(mode="after")
    def bounded_nulls(self) -> Self:
        if self.nulls > self.returned:
            raise ValueError("NULL件数が取得行数を超えています。")
        return self


class _Result(_Strict):
    step_id: str
    digest: str
    profile_id: str
    ref: str
    data_key: str
    truncated: bool
    observations: list[_Observation]


class _Snapshot(_Strict):
    catalogs: list[_Catalog] = Field(default_factory=list)
    results: list[_Result] = Field(default_factory=list)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _literal_label(value: str) -> str:
    """識別名をJSON表記のcode spanにし、引用構文から分離する。"""
    label = json.dumps(value, ensure_ascii=False)
    for char in _LABEL_META:
        label = label.replace(char, f"\\u{ord(char):04x}")
    return "`" + label + "`"


class SchemaFieldIndex:
    def __init__(self, snapshot: Mapping[str, Any] | None = None) -> None:
        try:
            self.state = _Snapshot.model_validate(dict(snapshot or {}))
        except ValidationError as exc:
            raise DataReferenceError("保存した列参照の対応表が不正です。") from exc
        self._check_limits()
        references = [c.ref for c in self.state.catalogs]
        steps = [c.step_id for c in self.state.catalogs]
        if len(set(references)) != len(references) or len(set(steps)) != len(steps):
            raise DataReferenceError("保存した列参照の対応表が重複しています。")
        result_refs = [r.ref for r in self.state.results]
        result_steps = [r.step_id for r in self.state.results]
        if len(set(result_refs)) != len(result_refs) or len(set(result_steps)) != len(result_steps):
            raise DataReferenceError("保存した結果参照の対応表が重複しています。")
        for number, catalog in enumerate(self.state.catalogs, 1):
            if catalog.ref != f"S{number}" or any(
                field.ref != f"S{number}:F{position}"
                for position, field in enumerate(catalog.fields, 1)
            ):
                raise DataReferenceError("保存した列参照の番号が一致しません。")

    def _check_limits(self) -> None:
        encoded = json.dumps(self.state.model_dump(mode="json"), ensure_ascii=False).encode()
        if (
            len(self.state.catalogs) > MAX_REPORT_FIELD_CATALOGS
            or sum(len(c.fields) for c in self.state.catalogs) > MAX_REPORT_FIELD_ENTRIES
            or len(self.state.results) > MAX_REPORT_FIELD_RESULTS
            or len(encoded) > MAX_REPORT_FIELDS_SNAPSHOT_BYTES
        ):
            raise DataReferenceError(
                "確認に使う列参照の保存上限に達したため、今回の回答は作成できません。"
            )

    def snapshot(self) -> dict[str, Any]:
        self._check_limits()
        return self.state.model_dump(mode="json")

    def register_schema(self, step_id: str, output: Mapping[str, Any]) -> None:
        try:
            schema = _Schema.model_validate(output)
        except ValidationError as exc:
            raise DataReferenceError("取得した列定義の形式を確認できません。") from exc
        digest = _digest(output)
        previous = next((c for c in self.state.catalogs if c.step_id == step_id), None)
        if previous is not None:
            if previous.digest != digest:
                raise DataReferenceError("保存した列定義と取得結果が一致しません。")
            return
        number = len(self.state.catalogs) + 1
        fields = [
            _Field(ref=f"S{number}:F{position}", owner=owner, object_name=name, column=column)
            for position, (owner, name, column) in enumerate(
                ((obj.owner, obj.name, col.name) for obj in schema.objects for col in obj.columns),
                1,
            )
        ]
        identities = [(f.owner, f.object_name, f.column) for f in fields]
        if len(set(identities)) != len(identities):
            raise DataReferenceError("取得した列定義が重複しています。")
        self.state.catalogs.append(
            _Catalog(
                step_id=step_id,
                digest=digest,
                profile_id=schema.profile_id,
                revision=schema.refreshed_at,
                ref=f"S{number}",
                status=schema.status,
                complete=schema.page_complete
                and not schema.truncated
                and schema.next_cursor is None,
                fields=fields,
            )
        )

    def encode(self, step_id: str, output: Mapping[str, Any]) -> dict[str, Any]:
        result = deepcopy(dict(output))
        catalog = next((c for c in self.state.catalogs if c.step_id == step_id), None)
        if catalog is None:
            return result
        if catalog.digest != _digest(output):
            raise DataReferenceError("列定義の参照先が変更されています。")
        result["schema_ref"] = catalog.ref
        positions = {(f.owner, f.object_name, f.column): f.ref for f in catalog.fields}
        for obj in result["objects"]:
            for column in obj["columns"]:
                column["field_ref"] = positions[(obj["owner"], obj["name"], column["name"])]
        return result

    def render(self, section: ConfirmationSection, *, data_index: DataIndex | None = None) -> str:
        lines = ["## 確認状況", "", "列の識別名はJSON表記です。", ""]
        for item in section.items:
            pairs = [
                (c, f) for c in self.state.catalogs for f in c.fields if f.ref == item.field_ref
            ]
            if len(pairs) != 1:
                raise DataReferenceError("取得済みの列参照を指定してください。")
            catalog, field = pairs[0]
            result = None
            if item.result_ref is not None:
                result = next((r for r in self.state.results if r.ref == item.result_ref), None)
                if result is None or data_index is None:
                    raise DataReferenceError("取得済みの結果参照を指定してください。")
                if data_index.aliases.get(item.result_ref) != result.data_key:
                    raise DataReferenceError("保存した結果参照が一致しません。")
            message = "列の定義は確認済みです。今回の回答に対応する取得結果は未確認です。"
            latest = next(
                c for c in reversed(self.state.catalogs) if c.profile_id == catalog.profile_id
            )
            if (
                catalog.status != "available"
                or latest.status != "available"
                or latest.revision != catalog.revision
            ):
                message = "列の定義が古いか利用できないため、現在の状態は未確認です。"
            elif result is not None:
                identity = ".".join([field.owner, field.object_name, field.column])
                unambiguous = all(
                    part and "." not in part and '"' not in part
                    for part in (field.owner, field.object_name, field.column)
                )
                observations = [
                    o for o in result.observations if unambiguous and o.sources == [identity]
                ]
                if result.profile_id == catalog.profile_id and len(observations) == 1:
                    observation = observations[0]
                    if observation.returned == 0:
                        message = (
                            "指定した質問の取得結果は0行です。データ全体の不在を意味しません。"
                        )
                    else:
                        message = (
                            f"この列を参照した取得結果{observation.returned}行では、"
                            f"非NULL {observation.returned - observation.nulls}件、"
                            f"NULL {observation.nulls}件です。"
                            "元の列そのものの値か計算結果かは未確認です。"
                            "元の列の全データの判定ではありません。"
                        )
                    if result.truncated:
                        message += "取得上限に達しており、未取得行があります。"
            label = _literal_label(".".join([field.owner, field.object_name, field.column]))
            lines.append(f"- {label}: {message}")
        return "\n".join(lines)


def register_report_step(
    metadata: dict[str, Any],
    step_id: str,
    tool_name: str,
    output: Mapping[str, Any],
    *,
    trusted_builtin: bool,
) -> None:
    """呼出し元が組み込み接続を検証した後、実行の保存lock内で登録する。"""
    if not trusted_builtin:
        return
    if tool_name not in {
        "nl2sql__nl2sql_get_profile_schema",
        "nl2sql__nl2sql_query",
        "nl2sql__nl2sql_get_job",
    }:
        return
    index = SchemaFieldIndex(metadata.get(REPORT_FIELDS_KEY))
    if tool_name.endswith("get_profile_schema"):
        index.register_schema(step_id, output)
    elif output.get("status") == "done" and not output.get("error_code"):
        entries = metadata.get(REGISTRY_KEY, [])
        entry = next((e for e in entries if e["step_id"] == step_id), None)
        if entry is None:
            return
        previous = next((r for r in index.state.results if r.step_id == step_id), None)
        digest = _digest(output)
        if previous is not None:
            if previous.digest != digest:
                raise DataReferenceError("保存した結果が変更されています。")
            return
        rows = output.get("rows", [])
        columns = output.get("columns", [])
        observations = []
        column_metadata = output.get("column_metadata", [])
        if isinstance(column_metadata, list) and len(column_metadata) == len(columns):
            for name, column in zip(columns, column_metadata, strict=True):
                if not isinstance(column, dict) or column.get("name") != name:
                    continue
                sources = column.get("source_columns", [])
                if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
                    continue
                if not all(isinstance(row, dict) and name in row for row in rows):
                    continue
                observations.append(
                    _Observation(
                        sources=sources,
                        returned=len(rows),
                        nulls=sum(row[name] is None for row in rows),
                    )
                )
        index.state.results.append(
            _Result(
                step_id=step_id,
                digest=digest,
                profile_id=output.get("profile_id", ""),
                ref=f"T{entry['number']}",
                data_key=entry["key"],
                truncated=output.get("truncated", False),
                observations=observations,
            )
        )
    metadata[REPORT_FIELDS_KEY] = index.snapshot()


def model_report_fields(
    metadata: Mapping[str, Any], step_id: str, output: Mapping[str, Any]
) -> dict[str, Any]:
    return SchemaFieldIndex(metadata.get(REPORT_FIELDS_KEY)).encode(step_id, output)


def render_confirmation(
    metadata: Mapping[str, Any],
    section: ConfirmationSection,
    *,
    data_index: DataIndex | None = None,
) -> str:
    return SchemaFieldIndex(metadata.get(REPORT_FIELDS_KEY)).render(section, data_index=data_index)
