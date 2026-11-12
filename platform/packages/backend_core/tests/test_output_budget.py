"""出力予算の三档と、登録容量・入力余量の硬い境界。"""

from pr_backend_core.output_budget import initial_output_budget, next_output_budget


def test_only_three_tiers_are_sent() -> None:
    assert initial_output_budget(131_072) == 16_384
    assert next_output_budget(16_384, 131_072) == 32_768
    assert next_output_budget(32_768, 131_072) == 65_536
    assert next_output_budget(65_536, 131_072) is None


def test_capacity_and_context_room_never_create_an_extra_tier() -> None:
    assert initial_output_budget(8_192) == 8_192
    assert next_output_budget(8_192, 8_192) is None
    assert next_output_budget(32_768, 49_152) is None
    assert initial_output_budget(65_536, room=4_000) == 4_000
    assert next_output_budget(16_384, 65_536, room=20_000) is None


def test_no_context_room_does_not_invent_one_output_token() -> None:
    import pytest

    for room in (0, -10):
        with pytest.raises(ValueError):
            initial_output_budget(65536, room=room)
