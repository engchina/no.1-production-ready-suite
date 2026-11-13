"""独立した Oracle container の間で pytest の収集結果を分割する。"""

from __future__ import annotations

import hashlib


def shard_for(nodeid: str, count: int) -> int:
    if count < 1:
        raise ValueError("shard 数は 1 以上にしてください。")
    return int.from_bytes(hashlib.sha256(nodeid.encode()).digest()[:8], "big") % count


def pytest_addoption(parser):
    group = parser.getgroup("oracle-shard")
    group.addoption("--oracle-shard-count", type=int, default=1)
    group.addoption("--oracle-shard-index", type=int, default=0)


def pytest_collection_modifyitems(config, items):
    count = config.getoption("--oracle-shard-count")
    index = config.getoption("--oracle-shard-index")
    if count < 1 or not 0 <= index < count:
        import pytest

        raise pytest.UsageError("Oracle shard の count / index が範囲外です。")
    selected, deselected = [], []
    for item in items:
        target = selected if shard_for(item.nodeid, count) == index else deselected
        target.append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
