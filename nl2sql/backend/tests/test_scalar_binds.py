"""通常の bind 値の型を Oracle adapter で保つ。"""


def test_scalar_bind_is_not_forced_to_clob() -> None:
    from test_health import _FakeOracleDb, _FakeRuntimeOracleAdapter

    db = _FakeOracleDb()
    adapter = _FakeRuntimeOracleAdapter(db)
    adapter.execute_select(
        "SELECT :lookup_value FROM DUAL", 1, scalar_binds={"lookup_value": "A'001"}
    )
    assert {"lookup_value": "A'001"} in db.executed_params
    assert not any("lookup_value" in sizes for sizes in db.input_sizes)
