"""Argon2id password hash。policy と一時 password は3製品共通（pr_system_settings。#206）。"""

from __future__ import annotations

from functools import lru_cache

from pr_system_settings.users_roles import PasswordPolicyError as PasswordPolicyError
from pr_system_settings.users_roles import (
    generate_temporary_password as generate_temporary_password,
)
from pr_system_settings.users_roles import validate_password as validate_password
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.settings import get_settings


def hash_password(password: str) -> str:
    return _password_hash().hash(password)


def verify_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    return _password_hash().verify_and_update(password, password_hash)


@lru_cache
def _password_hash() -> PasswordHash:
    settings = get_settings()
    return PasswordHash(
        (
            Argon2Hasher(
                time_cost=settings.app_auth_argon2_time_cost,
                memory_cost=settings.app_auth_argon2_memory_kib,
                parallelism=settings.app_auth_argon2_parallelism,
            ),
        )
    )
