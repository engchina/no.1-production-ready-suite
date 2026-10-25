from pr_backend_core.oracle_session import SESSION_INIT_SQL, init_oracle_session


class _Cursor:
    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str) -> None:
        self._executed.append(statement)


class _Connection:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self.executed)


def test_init_oracle_session_disables_result_cache_and_pins_utc() -> None:
    connection = _Connection()

    init_oracle_session(connection, "any-tag")

    # 1 回の ALTER SESSION で、result cache を使わず（#333）、
    # session の時間帯を UTC に固定する（#3255）。
    assert connection.executed == [SESSION_INIT_SQL]
    assert SESSION_INIT_SQL == "ALTER SESSION SET RESULT_CACHE_MODE = MANUAL TIME_ZONE = '+00:00'"


def test_session_time_zone_is_an_offset_not_a_region_name() -> None:
    # 名前の時間帯（'UTC'・'Asia/Tokyo'）の値は python-oracledb の thin mode で読めない
    # （DPY-3022。#3241）。
    time_zone = SESSION_INIT_SQL.split("TIME_ZONE = ", 1)[1]
    assert time_zone == "'+00:00'"
