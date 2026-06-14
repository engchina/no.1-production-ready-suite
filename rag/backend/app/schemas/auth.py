"""認証 API のスキーマ。"""

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    """ログインリクエスト。"""

    username: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=4096)
    remember_me: bool = True

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        """ユーザー名の前後空白を除去する。"""
        return value.strip()


class AuthUser(BaseModel):
    """ログイン済みユーザーの表示情報。"""

    id: str
    name: str
    role: str


class AuthStatus(BaseModel):
    """現在の認証状態。"""

    mode: str
    auth_required: bool
    authenticated: bool
    user: AuthUser | None = None
    expires_at: int | None = None
