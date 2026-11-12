"""確認済みの物理単位を、実行したSQLの列位置へ保守的に伝える。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from .models import AllowedObjects, ResultColumnMetadata
from .object_identity import format_object_part, object_match_key


@dataclass(frozen=True)
class _Unit:
    unit: str | None = None
    difference_unit: str = ""
    sources: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()


def _merge(values: Sequence[_Unit]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return (
        tuple(sorted({s for value in values for s in value.sources})),
        tuple(sorted({s for value in values for s in value.provenance})),
    )


def _identifier(node: exp.Expr | None) -> str:
    return object_match_key(node.sql(dialect="oracle")) if node is not None else ""


def _visible(source: str, allowed: AllowedObjects) -> bool:
    column = sqlglot.parse_one(source, read="oracle")
    if not isinstance(column, exp.Column) or not column.table or not column.db:
        return False
    name = f"{_identifier(column.args.get('db'))}.{_identifier(column.args.get('table'))}"
    tables = {object_match_key(n) for n in allowed.table_names}
    if name not in tables:
        return False
    columns = {object_match_key(n): cols for n, cols in allowed.columns.items()}
    return not columns.get(name) or _identifier(column.this) in {
        object_match_key(c) for c in columns[name]
    }


def _qualified(owner: str, table: str, column: str) -> str:
    return ".".join(object_match_key(p) for p in [owner, table, column])


def _trusted(
    definitions: Sequence[Any], allowed: AllowedObjects, release: str
) -> tuple[dict[str, _Unit], list[tuple[exp.Expr, _Unit]]]:
    candidates: dict[str, list[_Unit]] = {}
    metrics: list[tuple[exp.Expr, _Unit]] = []
    for definition in definitions:
        # 公開だけではAIが推測した単位の確認を意味しない。定義単位のレビューを必須にする。
        if definition.review_status != "reviewed" or not getattr(definition, "unit", "").strip():
            continue
        provenance: tuple[str, ...] = (f"{release}:{definition.api_name}",)
        unit = definition.unit.strip()
        difference = getattr(definition, "difference_unit", "").strip()
        mappings = list(definition.mappings)
        visible_tables = {object_match_key(name) for name in allowed.table_names}
        if any(
            f"{object_match_key(mapping.owner)}.{object_match_key(mapping.object_name)}"
            not in visible_tables
            or (
                mapping.column_name
                and not _visible(
                    _qualified(mapping.owner, mapping.object_name, mapping.column_name), allowed
                )
            )
            for mapping in mappings
        ):
            continue
        if definition.kind in {"property", "shared_property", "value_type"}:
            if not mappings or any(
                not m.column_name
                or not _visible(_qualified(m.owner, m.object_name, m.column_name), allowed)
                for m in mappings
            ):
                continue
            for mapping in mappings:
                # 式によるmappingは、単純な物理列の単位と取り違えない。
                if mapping.expression_sql:
                    continue
                source = _qualified(mapping.owner, mapping.object_name, mapping.column_name)
                candidates.setdefault(source, []).append(
                    _Unit(unit, difference, (source,), provenance)
                )
        elif definition.kind == "metric" and definition.expression_sql:
            try:
                expression = sqlglot.parse_one(definition.expression_sql, read="oracle")
                columns = list(expression.find_all(exp.Column))
                sources = tuple(
                    sorted(
                        {
                            _qualified(
                                _identifier(c.args.get("db")),
                                _identifier(c.args.get("table")),
                                _identifier(c.this),
                            )
                            for c in columns
                        }
                    )
                )
                if (
                    columns
                    and all(c.db and c.table for c in columns)
                    and all(_visible(source, allowed) for source in sources)
                ):
                    metrics.append((expression, _Unit(unit, difference, sources, provenance)))
            except (ValueError, sqlglot.errors.SqlglotError):
                continue
    result: dict[str, _Unit] = {}
    for source, values in candidates.items():
        sources, provenance = _merge(values)
        signatures = {(v.unit, v.difference_unit) for v in values}
        result[source] = (
            _Unit(*next(iter(signatures)), sources, provenance)
            if len(signatures) == 1
            else _Unit(sources=sources)
        )
    return result, metrics


def result_column_metadata(
    sql: str,
    columns: Sequence[str],
    *,
    definitions: Sequence[Any],
    allowed: AllowedObjects,
    release_id: str,
) -> list[ResultColumnMetadata]:
    """別名は単位の根拠にしない。解決できない式や曖昧な参照はunknownにする。"""
    unknown = [ResultColumnMetadata(name=name) for name in columns]
    if not definitions or not release_id:
        return unknown
    try:
        units, metrics = _trusted(definitions, allowed, release_id)
        tree = sqlglot.parse_one(sql, read="oracle")
        scopes = traverse_scope(tree)
        outputs: dict[int, dict[str, _Unit]] = {}
        final_values: list[_Unit] = []
        final_names: list[str] = []
        for scope in scopes:
            if not isinstance(scope.expression, exp.Select):
                outputs[id(scope)] = {}
                continue
            sources = scope.sources

            def source_column(
                column: exp.Column, scope_sources: dict[str, exp.Table | Scope] = sources
            ) -> _Unit:
                name = _identifier(column.this)
                candidates: list[_Unit] = []
                if column.db and column.table:
                    physical_source = _qualified(
                        _identifier(column.args.get("db")),
                        _identifier(column.args.get("table")),
                        name,
                    )
                    return (
                        units.get(physical_source, _Unit(sources=(physical_source,)))
                        if _visible(physical_source, allowed)
                        else _Unit()
                    )
                selected = (
                    ([source] if (source := scope_sources.get(column.table)) is not None else [])
                    if column.table
                    else list(scope_sources.values())
                )
                for source in selected:
                    if isinstance(source, Scope):
                        if name in outputs.get(id(source), {}):
                            candidates.append(outputs[id(source)][name])
                    elif isinstance(source, exp.Table):
                        table = _identifier(source.this)
                        owner = _identifier(source.args.get("db"))
                        names = (
                            [f"{owner}.{table}"]
                            if owner
                            else [
                                n
                                for n in allowed.table_names
                                if object_match_key(n).endswith("." + table)
                            ]
                        )
                        for object_name in names:
                            physical = f"{object_match_key(object_name)}.{name}"
                            if _visible(physical, allowed):
                                candidates.append(units.get(physical, _Unit(sources=(physical,))))
                return candidates[0] if len(candidates) == 1 else _Unit()

            def exact_metric(expression: exp.Expr) -> _Unit | None:
                # 確認済みmetricは構造的に同一の式だけを使う。
                if not metrics:
                    return None
                canonical = expression.copy()
                for column in list(canonical.find_all(exp.Column)):
                    resolved = source_column(column)
                    if len(resolved.sources) != 1:
                        return None
                    column.replace(sqlglot.parse_one(resolved.sources[0], read="oracle"))
                normalized = canonical.sql(dialect="oracle", normalize=True)
                matches = [
                    unit
                    for metric, unit in metrics
                    if metric.sql(dialect="oracle", normalize=True) == normalized
                ]
                if not matches:
                    return None
                if len({(unit.unit, unit.difference_unit) for unit in matches}) != 1:
                    return _Unit()
                src, prov = _merge(matches)
                return _Unit(matches[0].unit, matches[0].difference_unit, src, prov)

            def resolve(expression: exp.Expr) -> _Unit:
                if isinstance(expression, (exp.Alias, exp.Paren)):
                    return resolve(expression.this)
                if isinstance(expression, exp.Column):
                    return source_column(expression)
                matched = exact_metric(expression)

                def combine(inferred: _Unit) -> _Unit:
                    if matched is None:
                        return inferred
                    if not matched.unit or (inferred.unit and inferred.unit != matched.unit):
                        return _Unit()
                    src, prov = _merge([matched, inferred])
                    return _Unit(matched.unit, matched.difference_unit, src, prov)

                if isinstance(expression, (exp.Sum, exp.Min, exp.Max, exp.Avg)):
                    inner = expression.this
                    if isinstance(inner, exp.Distinct) and len(inner.expressions) == 1:
                        inner = inner.expressions[0]
                    return combine(resolve(inner))
                if isinstance(expression, exp.Sub):
                    left, right = resolve(expression.this), resolve(expression.expression)
                    sources, provenance = _merge([left, right])
                    if (
                        left.unit
                        and left.unit == right.unit
                        and left.difference_unit
                        and left.difference_unit == right.difference_unit
                    ):
                        return combine(_Unit(left.difference_unit, "", sources, provenance))
                    if (
                        left.unit
                        and right.unit
                        and (
                            left.unit != right.unit
                            or (
                                left.difference_unit
                                and right.difference_unit
                                and left.difference_unit != right.difference_unit
                            )
                        )
                    ):
                        return _Unit(sources=sources)
                    return combine(_Unit(sources=sources))
                return matched if matched is not None else _Unit()

            values = [resolve(select) for select in scope.expression.selects]
            names = [
                _identifier(select.args.get("alias"))
                if isinstance(select, exp.Alias)
                else _identifier(select.this)
                if isinstance(select, exp.Column)
                else ""
                for select in scope.expression.selects
            ]
            if scope.outer_columns and len(scope.outer_columns) == len(names):
                names = [object_match_key(name) for name in scope.outer_columns]
            outputs[id(scope)] = {
                name: value
                for name, value in zip(names, values, strict=True)
                if name and names.count(name) == 1
            }
            if scope is scopes[-1]:
                final_values = values
                final_names = names
        if (
            len(set(columns)) != len(columns)
            or len(final_values) != len(columns)
            or not isinstance(tree, exp.Select)
            or any(select.is_star for select in tree.selects)
        ):
            return unknown
        if any(
            expected and expected != format_object_part(actual)
            for expected, actual in zip(final_names, columns, strict=True)
        ):
            return unknown
        return [
            ResultColumnMetadata(
                name=name,
                unit=value.unit,
                unit_status="known" if value.unit else "unknown",
                provenance=list(value.provenance) if value.unit else [],
                source_columns=list(value.sources),
            )
            for name, value in zip(columns, final_values, strict=True)
        ]
    except (ValueError, KeyError, sqlglot.errors.SqlglotError):
        return unknown


def confirmed_unit_records(
    markdown: str, definitions: Sequence[Any], actor: str
) -> list[dict[str, str]]:
    """既存の明示公開確認で、源文に書かれた単位の内容だけを固定する。"""
    from .ontology_definition_service import definition_fingerprint
    from .ontology_markdown_parser import parse_markdown_rules

    parsed = parse_markdown_rules(markdown).definitions
    fields = ("kind", "unit", "difference_unit", "mappings", "expression_sql")
    records = []
    for definition in definitions:
        declared = [d for d in parsed if d.api_name == definition.api_name]
        if len(declared) != 1 or not getattr(declared[0], "unit", "").strip():
            continue
        source = declared[0].model_dump(mode="json")
        target = definition.model_dump(mode="json")
        if any(source.get(key) != target.get(key) for key in fields):
            continue
        records.append(
            {
                "definition_id": definition.id,
                "api_name": definition.api_name,
                "hash": definition_fingerprint(target),
                "actor": actor,
            }
        )
    return records


def published_unit_definitions(runtime: Any, profile_id: str, release_id: str) -> list[Any]:
    """ジョブが固定した公開版だけを読み、旧自由記述や現在の下書きは使わない。"""
    if not release_id or release_id.startswith("ontology_revision_"):
        return []
    from .ontology_definition_service import definition_fingerprint
    from .ontology_definition_workspace import ProfileOntologyWorkspaceService
    from .ontology_published_context import require_current_scope

    # Markdownの一括公開やlegacy本文には、単位ごとの内容hash付きレビューが無い。
    record = runtime.store.get_artifact(release_id)
    if record and record.get("artifact_type") == "ontology_markdown_snapshot":
        from .ontology_markdown_workspace import MarkdownOntologyWorkspace
        from .ontology_unified_model import DEFINITIONS

        workspace = MarkdownOntologyWorkspace(runtime)
        snapshot = workspace.snapshot_from_record(profile_id, record)
        if snapshot.get("request", {}).get(
            "confirmed"
        ) is not True or not workspace.scope_is_current(
            profile_id, snapshot["profile_hash"], snapshot["schema_hash"]
        ):
            return []
        return [
            definition.model_copy(update={"review_status": "reviewed"})
            for definition in DEFINITIONS.validate_python(snapshot["definitions"])
            if any(
                review.get("definition_id") == definition.id
                and review.get("api_name") == definition.api_name
                and review.get("actor")
                and review.get("hash") == definition_fingerprint(definition.model_dump(mode="json"))
                for review in snapshot.get("unit_review_records", [])
            )
        ]
    if not record or record.get("artifact_type") != "ontology_release_v2":
        return []
    release = ProfileOntologyWorkspaceService(runtime).release(profile_id, release_id)
    if release is None:
        return []
    bundle = require_current_scope(runtime, profile_id, release)
    return [
        definition
        for definition in bundle.definitions
        if definition.review_status == "reviewed"
        and any(
            record.get("definition_id") == definition.id
            and record.get("hash")
            == definition_fingerprint(
                definition.model_dump(
                    mode="json",
                    exclude={"difference_unit"}
                    if "difference_unit" not in definition.model_fields_set
                    else set(),
                )
            )
            for record in bundle.review_records
        )
    ]
