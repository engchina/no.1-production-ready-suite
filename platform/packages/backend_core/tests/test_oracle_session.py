from pr_backend_core.oracle_session import DISABLE_RESULT_CACHE_SQL, init_oracle_session


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


def test_init_oracle_session_disables_result_cache() -> None:
    connection = _Connection()

    init_oracle_session(connection, "any-tag")

    assert connection.executed == [DISABLE_RESULT_CACHE_SQL]
    assert DISABLE_RESULT_CACHE_SQL == "ALTER SESSION SET RESULT_CACHE_MODE = MANUAL"
