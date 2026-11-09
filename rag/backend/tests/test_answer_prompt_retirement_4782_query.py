"""消した段の保存済みのプロンプトだけを、承認つきの migration で 1 回だけ消す（#4782）。"""

import pytest

from app.rag.answer_prompt_retirement_4782_query import RETIRED_SECTION_EXTRACTION_PROMPT_KEYS
from app.rag.answer_prompts import PROMPT_STAGES
from app.rag.oracle_schema import oracle_schema_migration_sections
from app.rag.system_schema import SystemSchemaManager
from tests.test_system_schema_manager import _FakeDatabase


@pytest.mark.parametrize(
    ("name", "keys"),
    [("20261110_4782_retire_section_extraction_prompts", RETIRED_SECTION_EXTRACTION_PROMPT_KEYS)],
)
def test_retired_stage_prompt_rows_are_deleted_once(name: str, keys: tuple[str, ...]) -> None:
    section = next(s for s in oracle_schema_migration_sections() if s.name == name)
    assert section.destructive_note
    assert section.sql.startswith("DELETE FROM RAG_ANSWER_PROMPTS\nWHERE PROMPT_KEY IN (")
    assert all(f"'{key}'" in section.sql for key in keys)
    # 残っている段の key を消さない。
    live = {f"{stage.id}.{part}" for stage in PROMPT_STAGES for part, _ref in stage.parts}
    assert not live & set(keys)
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)
    manager.initialize(allow_destructive=True)
    database.migrations.pop(section.name)
    database.executed.clear()
    manager.initialize(allow_destructive=True)
    needle = " ".join(section.sql.upper().split())
    assert sum(needle in sql for sql in database.executed) == 1
    manager.initialize(allow_destructive=True)
    assert sum(needle in sql for sql in database.executed) == 1
