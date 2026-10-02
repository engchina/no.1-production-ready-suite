"""共有の Oracle 接続 pool（#793）。fake の oracledb で、作成・再利用・作り直し・返却を確かめる。"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from pr_backend_core.oracle_pool import (
    OraclePoolSize,
    SharedOraclePool,
    connect_kwargs_fingerprint,
)
from pr_backend_core.oracle_session import init_oracle_session


class _Connection:
    def __init__(self) -> None:
        self.closed = 0
        self.rollbacks = 0
        self.call_timeout = 0

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed += 1


class _Pool:
    def __init__(self, kwargs: dict[str, Any], *, busy: bool = False) -> None:
        self.kwargs = kwargs
        self.acquired: list[_Connection] = []
        self.closed: list[bool] = []
        self.busy = busy

    def acquire(self) -> _Connection:
        connection = _Connection()
        self.acquired.append(connection)
        return connection

    def close(self, force: bool = False) -> None:
        if self.busy and not force:
            raise RuntimeError("DPY-2014: pool has busy connections")
        self.closed.append(force)


class _FakeOracledb:
    POOL_GETMODE_TIMEDWAIT = 3

    def __init__(self) -> None:
        self.pools: list[_Pool] = []
        self.busy_next = False

    def create_pool(self, **kwargs: Any) -> _Pool:
        pool = _Pool(kwargs, busy=self.busy_next)
        self.pools.append(pool)
        return pool


def _pool(fake: _FakeOracledb, **kwargs: Any) -> SharedOraclePool:
    return SharedOraclePool(name="test", oracledb_loader=lambda: fake, **kwargs)


KWARGS = {"user": "APP", "password": "secret", "dsn": "db_high", "wallet_location": "/w"}


def test_pool_is_created_once_and_reused() -> None:
    fake = _FakeOracledb()
    pool = _pool(fake, size=OraclePoolSize(min=1, max=6))

    for _ in range(5):
        with pool.connection(KWARGS) as connection:
            assert isinstance(connection, _Connection)

    assert len(fake.pools) == 1
    created = fake.pools[0].kwargs
    assert created["user"] == "APP"
    assert created["wallet_location"] == "/w"
    assert (created["min"], created["max"], created["increment"]) == (1, 6, 1)
    assert created["getmode"] == _FakeOracledb.POOL_GETMODE_TIMEDWAIT
    assert created["wait_timeout"] > 0
    assert created["session_callback"] is init_oracle_session
    # 借りた接続はすべて pool に返している。
    assert [c.closed for c in fake.pools[0].acquired] == [1] * 5


def test_pool_is_recreated_when_connect_kwargs_change() -> None:
    fake = _FakeOracledb()
    pool = _pool(fake)

    with pool.connection(KWARGS):
        pass
    with pool.connection({**KWARGS, "password": "rotated"}):
        pass

    assert len(fake.pools) == 2
    assert fake.pools[0].closed == [False]
    assert fake.pools[1].closed == []


def test_busy_pool_is_closed_later_and_on_shutdown() -> None:
    fake = _FakeOracledb()
    fake.busy_next = True
    pool = _pool(fake)
    with pool.connection(KWARGS):
        pass
    fake.busy_next = False

    # 貸し出し中の接続がある古い pool は、処理を止めずに後で閉じる。
    with pool.connection({**KWARGS, "dsn": "other_high"}):
        pass
    assert fake.pools[0].closed == []

    pool.close()
    assert fake.pools[0].closed == [True]
    assert fake.pools[1].closed == [True]

    # 閉じた後は次に借りるときに作り直す。
    with pool.connection(KWARGS):
        pass
    assert len(fake.pools) == 3


def test_exception_rolls_back_and_releases() -> None:
    fake = _FakeOracledb()
    pool = _pool(fake)

    with pytest.raises(ValueError), pool.connection(KWARGS):
        raise ValueError("boom")

    connection = fake.pools[0].acquired[0]
    assert connection.rollbacks == 1
    assert connection.closed == 1


def test_on_acquire_is_applied_each_time() -> None:
    fake = _FakeOracledb()
    pool = _pool(fake)

    def set_timeout(connection: Any) -> None:
        connection.call_timeout = 5000

    with pool.connection(KWARGS, on_acquire=set_timeout) as connection:
        assert connection.call_timeout == 5000


def test_concurrent_first_use_creates_one_pool() -> None:
    fake = _FakeOracledb()
    pool = _pool(fake)
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        with pool.connection(KWARGS):
            pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(fake.pools) == 1
    assert len(fake.pools[0].acquired) == 8


def test_resize_recreates_pool_with_new_size() -> None:
    fake = _FakeOracledb()
    pool = _pool(fake)
    with pool.connection(KWARGS):
        pass
    pool.resize(OraclePoolSize.of(2, 8))
    with pool.connection(KWARGS):
        pass

    assert [(p.kwargs["min"], p.kwargs["max"]) for p in fake.pools] == [(1, 4), (2, 8)]


def test_pool_size_is_clamped() -> None:
    assert OraclePoolSize.of(0, 0) == OraclePoolSize(min=1, max=1)
    assert OraclePoolSize.of(3, 2) == OraclePoolSize(min=3, max=3)
    assert OraclePoolSize.of("2", "x") == OraclePoolSize(min=2, max=4)


def test_fingerprint_does_not_contain_secret() -> None:
    fingerprint = connect_kwargs_fingerprint(KWARGS)
    assert "secret" not in fingerprint
    assert fingerprint == connect_kwargs_fingerprint(dict(reversed(list(KWARGS.items()))))
    assert fingerprint != connect_kwargs_fingerprint({**KWARGS, "password": "other"})
