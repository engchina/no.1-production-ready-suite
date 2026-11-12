"""オンライン生成の単一予算と、設定・容量・余量の境界。"""

from types import SimpleNamespace

import pytest

from pr_backend_core.config import PLATFORM_SETTING_FIELDS, settings_env_name
from pr_backend_core.output_budget import configured_online_output_tokens, online_output_budget


def test_single_budget_is_independent_of_registered_capacity() -> None:
    assert online_output_budget(500_000) == 65_536
    assert online_output_budget(500_000, configured=128_000) == 128_000
    assert online_output_budget(8_192) == 8_192


def test_every_remaining_limit_constrains_the_actual_request() -> None:
    assert online_output_budget(500_000, room=4_000) == 4_000
    assert online_output_budget(500_000, room=4_000, total_room=100) == 100
    for kwargs in ({"room": 0}, {"room": -10}, {"total_room": 0}, {"configured": 0}):
        with pytest.raises(ValueError):
            online_output_budget(500_000, **kwargs)


def test_shared_setting_has_one_platform_environment_name() -> None:
    assert "online_max_output_tokens" in PLATFORM_SETTING_FIELDS
    assert (
        settings_env_name("AGENT_", "online_max_output_tokens")
        == "PLATFORM_ONLINE_MAX_OUTPUT_TOKENS"
    )
    assert configured_online_output_tokens(SimpleNamespace()) == 65_536
    assert (
        configured_online_output_tokens(SimpleNamespace(online_max_output_tokens=128_000))
        == 128_000
    )
    with pytest.raises(ValueError):
        configured_online_output_tokens(SimpleNamespace(online_max_output_tokens=0))
