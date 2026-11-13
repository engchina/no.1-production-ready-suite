"""任意の報告契約を導入しても、既存の実行構成の指紋を変えない。"""

from app.features.agent.run_config import run_configuration


def test_default_run_configuration_retains_recorded_legacy_fingerprint() -> None:
    # 報告契約導入前の同じ合成構成から採取した値。旧記録の再生境界を確かめる。
    config = run_configuration(
        static_instructions="合成の指示",
        tools=[],
        skills=[],
        mcp_tools=[],
        model={"model_id": "synthetic-model"},
        data_scopes=None,
        sdk_version="fixture",
    )
    assert config["fingerprint"] == "f5b0c25b61579e35"
    assert set(config) == {
        "schema_version",
        "instructions",
        "tools",
        "skills",
        "mcp",
        "model",
        "data_scopes",
        "sdk_version",
        "fingerprint",
    }
