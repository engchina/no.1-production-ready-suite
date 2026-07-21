"""Argon2id password policy。"""

from __future__ import annotations

import re
import secrets
import string
from functools import lru_cache

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.settings import get_settings

_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "password123!",
        "admin",
        "administrator",
        "letmein",
        "qwerty",
        "welcome",
        "welcome1",
        "welcome123!",
        "oracle",
        "oracle123",
        "oracle123!",
        "changeme",
        "changeme123!",
        "admin123!",
        "qwerty123!",
        "letmein123!",
        "1234567890",
    }
)


class PasswordPolicyError(ValueError):
    """公開可能な password policy 違反。"""


def validate_password(password: str, *, login_name: str, min_length: int, max_length: int) -> None:
    errors: list[str] = []
    if len(password) < min_length or len(password) > max_length:
        errors.append(f"パスワードは {min_length}～{max_length} 文字で入力してください。")
    if not re.search(r"[A-Z]", password):
        errors.append("英大文字を 1 文字以上含めてください。")
    if not re.search(r"[a-z]", password):
        errors.append("英小文字を 1 文字以上含めてください。")
    if not re.search(r"[0-9]", password):
        errors.append("数字を 1 文字以上含めてください。")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("記号を 1 文字以上含めてください。")
    lowered = password.casefold()
    if lowered in _COMMON_PASSWORDS or (login_name and login_name.casefold() in lowered):
        errors.append("推測されやすいパスワードは使用できません。")
    if errors:
        raise PasswordPolicyError(" ".join(errors))


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


def generate_temporary_password(length: int = 20) -> str:
    """各文字種を必ず含む一時 password を生成する。"""
    alphabet = string.ascii_letters + string.digits + "!@#$%_-+="
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%_-+="),
    ]
    required.extend(secrets.choice(alphabet) for _ in range(max(length - 4, 0)))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)
