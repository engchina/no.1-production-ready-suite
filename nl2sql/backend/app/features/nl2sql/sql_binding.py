"""SQL の scope と公開 schema から列の束縛を検証する。SQL 本文は変更しない。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from .models import SchemaTable
from .oracle_dialect import OracleConversion


class SqlGenerationValidationError(ValueError):
    pass


def column_binding_violations(sql: str, tables: Sequence[SchemaTable], *, owner: str) -> list[str]:
    schema = MappingSchema(dialect=OracleConversion, normalize=False)
    for table in tables:
        schema.add_table(
            exp.Table(
                this=exp.Identifier(this=table.table_name, quoted=True),
                db=exp.Identifier(this=table.owner, quoted=True),
            ),
            {column.column_name: "UNKNOWN" for column in table.columns},
            normalize=False,
        )
    # DUAL は利用者の schema に無い Oracle の1行の表。
    for dual_owner in {owner, "SYS"}:
        if dual_owner:
            schema.add_table(
                exp.Table(this=exp.to_identifier("DUAL"), db=exp.to_identifier(dual_owner)),
                {"DUMMY": "VARCHAR"},
                normalize=False,
            )
    try:
        tree = sqlglot.parse_one(sql, read=OracleConversion)
        qualify(
            tree,
            dialect=OracleConversion,
            db=owner or None,
            schema=schema if tables else None,
            validate_qualify_columns=True,
        )
    except OptimizeError as exc:
        return ["SQL_COLUMN_BINDING_INVALID: " + str(exc)] if tables else []
    except SqlglotError:
        return []  # 構文の検査は parse_oracle_sql が担当する。
    return []
