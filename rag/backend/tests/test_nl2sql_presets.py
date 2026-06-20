"""NL2SQL パイプライン preset レジストリの決定論テスト。"""

from app.config import Settings
from app.nl2sql.presets import (
    PIPELINE_ADAPTERS,
    adapter_keys,
    adapter_runtime,
    get_adapter,
    normalize_selection,
    pipeline_runtime,
    resolve_selection,
)


def test_adapter_keys_are_in_pipeline_order() -> None:
    assert adapter_keys() == (
        "schema_source",
        "schema_linking",
        "knowledge",
        "clarify",
        "generation",
        "correction",
        "agentic",
        "result",
        "evaluation",
    )


def test_every_adapter_default_is_first_option() -> None:
    for adapter in PIPELINE_ADAPTERS:
        assert adapter.default == adapter.options[0].name
        assert adapter.default in adapter.option_names


def test_env_key_is_uppercased_settings_field() -> None:
    adapter = get_adapter("schema_source")
    assert adapter is not None
    assert adapter.settings_field == "nl2sql_schema_source"
    assert adapter.env_key == "NL2SQL_SCHEMA_SOURCE"


def test_normalize_selection_falls_back_to_default() -> None:
    assert normalize_selection("knowledge", "nope") == "off"
    assert normalize_selection("knowledge", "few_shot") == "few_shot"
    assert normalize_selection("unknown_adapter", "x") == ""


def test_resolve_selection_reads_settings() -> None:
    settings = Settings(nl2sql_schema_linking="auto_prune")
    assert resolve_selection(settings, "schema_linking") == "auto_prune"


def test_adapter_runtime_marks_selected() -> None:
    runtime = adapter_runtime(Settings(nl2sql_result_profile="chart"), "result")
    assert runtime is not None
    assert runtime.selected == "chart"
    selected = [option.name for option in runtime.options if option.selected]
    assert selected == ["chart"]
    assert runtime.options[0].name == "table"


def test_pipeline_runtime_covers_all_adapters() -> None:
    runtimes = pipeline_runtime(Settings())
    assert tuple(runtime.key for runtime in runtimes) == adapter_keys()
    # 既定はすべて各 options[0]。
    for runtime in runtimes:
        assert runtime.selected == runtime.options[0].name


def test_unknown_adapter_runtime_is_none() -> None:
    assert adapter_runtime(Settings(), "nope") is None
