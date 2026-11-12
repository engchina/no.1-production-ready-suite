"""Oracleの列注釈をcatalogの注釈欄まで保持する。実DBには接続しない。"""

from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.settings import Settings


class Cursor:
    def __init__(self, comment: str | None, object_type: str) -> None:
        self.comment = comment
        self.object_type = object_type

    def execute(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __iter__(self) -> Any:
        return iter(
            [
                (
                    "APP",
                    "OBJECT_A",
                    "対象の説明",
                    "VALUE_A",
                    self.comment,
                    "NUMBER",
                    "Y",
                    1,
                    3,
                    self.object_type,
                    22,
                    0,
                    12,
                    3,
                    None,
                )
            ]
        )

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class Connection:
    def __init__(self, comment: str | None, object_type: str) -> None:
        self.comment = comment
        self.object_type = object_type

    def cursor(self) -> Cursor:
        return Cursor(self.comment, self.object_type)


def catalog(comment: str | None = "測定値。単位はm", object_type: str = "VIEW") -> Any:
    @contextmanager
    def connection() -> Any:
        yield Connection(comment, object_type)

    adapter = OracleNl2SqlAdapter(Settings(_env_file=None))
    with (
        patch.object(adapter, "connection", connection),
        patch.object(adapter, "_load_constraints"),
        patch.object(adapter, "_load_view_dependencies", return_value=[]),
    ):
        return adapter.fetch_catalog_objects({("APP", "OBJECT_A")})


@pytest.mark.parametrize("comment", [None, "", " ", "測定値。単位はm、基準との差とは別。'注記'"])
@pytest.mark.parametrize("object_type", ["TABLE", "VIEW"])
def test_fetched_column_preserves_actual_comment(comment: str | None, object_type: str) -> None:
    column = catalog(comment, object_type).tables[0].columns[0]
    assert column.logical_name == (comment or "VALUE_A")
    assert column.comment == (comment or "")


def test_refresh_repository_detail_preserves_actual_comment() -> None:
    fetched = catalog()
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    repository.apply_schema_refresh(
        catalog=catalog(None),
        manifest={("APP", "OBJECT_A"): "v1"},
        changed_keys={("APP", "OBJECT_A")},
        deleted_keys=set(),
    )
    repository.apply_schema_refresh(
        catalog=fetched,
        manifest={("APP", "OBJECT_A"): "v2"},
        changed_keys={("APP", "OBJECT_A")},
        deleted_keys=set(),
    )
    detail = repository.get_schema_object("APP", "OBJECT_A")
    assert detail is not None
    assert detail.table.columns[0].logical_name == "測定値。単位はm"
    assert detail.table.columns[0].comment == "測定値。単位はm"
