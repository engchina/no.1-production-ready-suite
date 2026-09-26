"""Argon2id password hash（3製品共通。#212）。policy と一時 password は users_roles。"""

from __future__ import annotations

from functools import lru_cache

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher


@lru_cache(maxsize=8)
def password_hasher(time_cost: int, memory_kib: int, parallelism: int) -> PasswordHash:
    return PasswordHash(
        (
            Argon2Hasher(
                time_cost=time_cost,
                memory_cost=memory_kib,
                parallelism=parallelism,
            ),
        )
    )


def hash_password(password: str, *, time_cost: int, memory_kib: int, parallelism: int) -> str:
    return password_hasher(time_cost, memory_kib, parallelism).hash(password)


def verify_password(
    password: str, password_hash: str, *, time_cost: int, memory_kib: int, parallelism: int
) -> tuple[bool, str | None]:
    """照合結果と、パラメータ更新が必要なときの新しい hash を返す。"""
    return password_hasher(time_cost, memory_kib, parallelism).verify_and_update(
        password, password_hash
    )
