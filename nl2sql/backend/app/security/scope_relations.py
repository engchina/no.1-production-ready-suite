"""Profile 内の直接関連候補と Data Grant の参照依存検証。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .service import SecurityApiError


def scope_profiles() -> list[dict[str, Any]]:
    from app.features.nl2sql.service import nl2sql_service

    return [
        {
            "id": p.id,
            "name": p.name,
            "object_scope_version": p.object_scope_version,
            "objects": sorted(set(p.allowed_tables + p.allowed_views)),
        }
        for p in nl2sql_service.list_profiles()
    ]


def relation_catalog(
    service: Any, profile_id: str, target: str, *, cursor: Any = None
) -> dict[str, Any]:
    from .deepsec import _qualified

    profiles = [p for p in scope_profiles() if p["id"] == profile_id]
    if not profiles:
        raise SecurityApiError(404, "有効な Profile が見つかりません。")
    profile = profiles[0]
    objects = []
    for value in profile["objects"]:
        parts = value.upper().split(".")
        if len(parts) == 2:
            objects.append(_qualified(*parts))
    if target not in objects:
        raise SecurityApiError(400, "対象テーブルが Profile の範囲にありません。")
    if cursor is None:
        with service.pools.control_connection() as conn, conn.cursor() as db_cursor:
            return relation_catalog(service, profile_id, target, cursor=db_cursor)
    owner, name = target.split(".")
    cursor.execute(
        """
        SELECT fk.OWNER, fk.CONSTRAINT_NAME, fk.TABLE_NAME, pk.OWNER, pk.TABLE_NAME,
               fc.COLUMN_NAME, pc.COLUMN_NAME, fc.POSITION
          FROM ALL_CONSTRAINTS fk
          JOIN ALL_CONSTRAINTS pk ON pk.OWNER = fk.R_OWNER
            AND pk.CONSTRAINT_NAME = fk.R_CONSTRAINT_NAME
          JOIN ALL_CONS_COLUMNS fc ON fc.OWNER = fk.OWNER
            AND fc.CONSTRAINT_NAME = fk.CONSTRAINT_NAME
          JOIN ALL_CONS_COLUMNS pc ON pc.OWNER = pk.OWNER
            AND pc.CONSTRAINT_NAME = pk.CONSTRAINT_NAME AND pc.POSITION = fc.POSITION
         WHERE fk.CONSTRAINT_TYPE = 'R' AND fk.STATUS = 'ENABLED' AND fk.VALIDATED = 'VALIDATED'
           AND ((fk.OWNER = :owner AND fk.TABLE_NAME = :object_name)
             OR (pk.OWNER = :owner AND pk.TABLE_NAME = :object_name))
         ORDER BY fk.OWNER, fk.CONSTRAINT_NAME, fc.POSITION
    """,
        {"owner": owner, "object_name": name},
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in cursor.fetchall():
        child, parent = f"{row[0]}.{row[2]}", f"{row[3]}.{row[4]}"
        related = parent if child == target else child
        if related == target or related not in objects:
            continue
        key = f"{row[0]}.{row[1]}"
        relation = grouped.setdefault(
            key, {"id": key, "source": "FOREIGN_KEY", "target": related, "join_keys": []}
        )
        relation["join_keys"].append(
            {
                "source_column": str(row[5] if child == target else row[6]),
                "target_column": str(row[6] if child == target else row[5]),
            }
        )
    relations = list(grouped.values())
    for relation in relations:
        relation["version"] = hashlib.sha256(
            json.dumps(relation, sort_keys=True).encode()
        ).hexdigest()
    relations.extend(_ontology_relations(profile_id, target, objects))
    return {
        "profile_id": profile_id,
        "object_scope_version": profile["object_scope_version"],
        "objects": [value for value in objects if value != target],
        "relations": relations,
    }


def _ontology_relations(profile_id: str, target: str, objects: list[str]) -> list[dict[str, Any]]:
    from app.features.nl2sql.ontology_models import OntologyEdge, ProfileOntologyView
    from app.features.nl2sql.ontology_router import ontology_runtime

    result = []
    for document in ontology_runtime.store.list_documents(
        "profile_views", {"profile_id": profile_id}
    ):
        payload = ontology_runtime._stored_payload(document, collection="profile_view")
        view = ProfileOntologyView.model_validate(payload)
        if view.status != "published" or view.archived:
            continue
        revision = ontology_runtime.store.get_document(
            "revisions", {"revision_id": view.ontology_revision_id}
        )
        if not revision:
            continue
        header = ontology_runtime._stored_payload(revision, collection="revision")
        if header.get("status") != "published":
            continue
        for document in ontology_runtime.store.list_documents(
            "edges", {"revision_id": view.ontology_revision_id}
        ):
            edge = OntologyEdge.model_validate(
                ontology_runtime._stored_payload(document, collection="edge")
            )
            if (
                edge.id not in view.edge_ids
                or edge.review_status != "approved"
                or not edge.join_conditions
            ):
                continue
            pairs = []
            related = ""
            for join in edge.join_conditions:
                left, right = join.left, join.right
                if f"{right.owner}.{right.object_name}" == target:
                    left, right = right, left
                candidate = f"{right.owner}.{right.object_name}"
                if (
                    join.operator != "="
                    or f"{left.owner}.{left.object_name}" != target
                    or candidate == target
                    or candidate not in objects
                    or (related and related != candidate)
                ):
                    break
                related = candidate
                pairs.append(
                    {"source_column": left.column_name, "target_column": right.column_name}
                )
            else:
                result.append(
                    {
                        "id": edge.id,
                        "source": "ONTOLOGY",
                        "version": view.ontology_revision_id + ":" + str(view.view_version),
                        "target": related,
                        "join_keys": pairs,
                    }
                )
    return result


@dataclass(frozen=True)
class DependencyPlan:
    """今回のバッチが置換する grant と、適用後に追加される直接参照。"""

    replaced_grants: frozenset[tuple[str, str]]
    edges: dict[str, set[str]]


def validate_relation_dependency(
    cursor: Any, target: str, related: str, plan: DependencyPlan | None = None
) -> None:
    """View / grant 子問い合わせの依存を辿る。確認不能・遠隔・循環は適用しない。"""
    import sqlglot
    from sqlglot import exp

    pending: list[tuple[str, frozenset[str]]] = [(related, frozenset())]
    seen: set[str] = set()
    while pending:
        current, ancestors = pending.pop()
        if current == target or current in ancestors:
            raise SecurityApiError(400, "関連条件が対象テーブルへ循環参照します。")
        if current in seen:
            continue
        seen.add(current)
        if len(seen) > 64:
            raise SecurityApiError(400, "参照依存が複雑なため関連条件を検証できません。")
        if plan is not None:
            pending.extend((child, ancestors | {current}) for child in plan.edges.get(current, ()))
        owner, name = current.split(".")
        cursor.execute(
            """SELECT REFERENCED_OWNER, REFERENCED_NAME, REFERENCED_LINK_NAME
            FROM ALL_DEPENDENCIES WHERE OWNER = :owner AND NAME = :object_name
              AND REFERENCED_TYPE IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')""",
            {"owner": owner, "object_name": name},
        )
        for row in cursor.fetchall():
            if row[2]:
                raise SecurityApiError(400, "DB link を参照する関連条件は指定できません。")
            pending.append((f"{row[0]}.{row[1]}", ancestors | {current}))
        cursor.execute(
            """SELECT OWNER, GRANT_NAME, PREDICATE FROM DBA_DATA_GRANTS
            WHERE OBJECT_OWNER = :owner AND OBJECT_NAME = :object_name""",
            {"owner": owner, "object_name": name},
        )
        for grant_owner, grant_name, predicate in cursor.fetchall():
            if (
                plan is not None
                and (str(grant_owner).upper(), str(grant_name).upper()) in plan.replaced_grants
            ):
                continue
            if not predicate:
                continue
            try:
                parsed = sqlglot.parse_one("SELECT 1 WHERE " + str(predicate), read="oracle")
                for table in parsed.find_all(exp.Table):
                    if table.catalog or "@" in table.sql():
                        raise ValueError("remote object")
                    pending.append(
                        (f"{table.db or grant_owner}.{table.name}".upper(), ancestors | {current})
                    )
            except Exception as exc:
                raise SecurityApiError(
                    400, "関連テーブルの既存ポリシー依存を検証できません。"
                ) from exc
