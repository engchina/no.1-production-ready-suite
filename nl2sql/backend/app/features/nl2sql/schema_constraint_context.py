"""SQL 生成へ渡す、可視の列だけに閉じた構造化制約メタデータ。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .models import SchemaTable
from .object_identity import catalog_match_key


def schema_constraint_context(tables: Sequence[SchemaTable]) -> list[str]:
    """現在のスキーマに残った PK・UK・FK を列順を保って直列化する。"""
    visible = {catalog_match_key(table.owner, table.table_name): table for table in tables}
    columns = {
        key: {catalog_match_key(column.column_name) for column in table.columns}
        for key, table in visible.items()
    }
    records: list[dict[str, Any]] = []
    for key, table in visible.items():
        for constraint in table.constraint_details:
            if constraint.constraint_type not in {"P", "U", "R"}:
                continue
            local = [catalog_match_key(column) for column in constraint.columns]
            if not local or not set(local) <= columns[key]:
                continue
            record: dict[str, Any] = {
                "table": key,
                "type": {"P": "primary_key", "U": "unique_key", "R": "foreign_key"}[
                    constraint.constraint_type
                ],
                "columns": local,
                "status": constraint.status,
            }
            if constraint.constraint_type == "R":
                if not constraint.referenced_owner or not constraint.referenced_table:
                    continue
                target = catalog_match_key(constraint.referenced_owner, constraint.referenced_table)
                remote = [catalog_match_key(column) for column in constraint.referenced_columns]
                # 複合キーを部分的に渡すと関係が変わるため、片側でも欠ける場合は全体を省く。
                if (
                    target not in visible
                    or len(local) != len(remote)
                    or not set(remote) <= columns[target]
                ):
                    continue
                record.update(referenced_table=target, referenced_columns=remote)
            records.append(record)
    if not records:
        return []
    return ["constraints:", *("- " + json.dumps(record, ensure_ascii=False) for record in records)]
