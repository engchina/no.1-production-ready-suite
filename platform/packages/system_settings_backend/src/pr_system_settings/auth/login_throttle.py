"""ログインの試行の回数の制限（#1087）。

構成管理者（`system_admin`）は DB のユーザーではないため、DB ユーザーのロック
（`FAILED_LOGIN_COUNT` / `LOCKED_UNTIL`）を使えない。代わりに、共通のログインの入口
（`AuthService.login`）で、失敗の回数を次の 2 つの単位で数え、上限に達したら一定時間 429 を返す。

- ログイン ID と送信元 IP の組: 1 つの端末から 1 つのログイン ID のパスワードを
  何回も試すのを止める。
  ログイン ID だけで数えると、攻撃者が別の IP から失敗を重ねて構成管理者（緊急用の全権限の管理者）を
  常に締め出せるため、送信元 IP と組にする（Issue #1087 の案 1 の副作用を避ける）。
- 送信元 IP: 1 つの端末から多くのログイン ID を試す（password spraying）のを止める。

ユーザーの有無・状態は数え方と応答に影響しない（存在しないユーザーも同じ。#1105 と同じ考え方）。
成功したら、そのログイン ID と送信元 IP の組の回数を消す。

失敗は直近の窓（`window_seconds`）の中の時刻で数える（sliding log）。上限に達したら、窓の中で
最も古い失敗が窓から出るまで拒否する。カウンタはプロセスのメモリに持つ（3 製品は別プロセスで、
gunicorn の worker ごとにも別。再起動で消える）。送信元 IP は `request.client.host` で、
Nginx の後ろでは uvicorn の proxy headers（gunicorn の `forwarded_allow_ips` の既定は
127.0.0.1 / ::1）が `X-Forwarded-For` の右端の信頼しない host に置き換える。
アプリは `X-Forwarded-For` を直接読まない。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil

# 鍵の数がこれを超えたら、窓を過ぎた鍵を掃除する（多くの IP からの試行でメモリが増え続けないよう）。
_PRUNE_THRESHOLD = 10_000


@dataclass(frozen=True)
class LoginThrottleLimits:
    """上限。0 以下の上限はその単位の制限を無効にする。"""

    per_login_and_ip: int
    per_ip: int
    window_seconds: float


class LoginThrottle:
    """ログインの失敗の回数をプロセスのメモリで数える。時計は差し替えられる（テスト用）。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[tuple[str, ...], deque[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(login_user_id: str, client_ip: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        normalized = login_user_id.strip().casefold()
        return ("login", normalized, client_ip), ("ip", client_ip)

    def retry_after_seconds(
        self, login_user_id: str, client_ip: str, limits: LoginThrottleLimits
    ) -> int | None:
        """拒否するなら、次に試せるまでの秒数（1 以上）。試してよいなら None。"""
        now = self._clock()
        waits: list[float] = []
        with self._lock:
            for key, limit in self._limited_keys(login_user_id, client_ip, limits):
                failures = self._recent(key, now, limits.window_seconds)
                if len(failures) >= limit:
                    # 上限から数えて最も古い失敗が窓から出れば、もう 1 回試せる。
                    waits.append(failures[-limit] + limits.window_seconds - now)
        if not waits:
            return None
        return max(1, ceil(max(waits)))

    def record_failure(
        self, login_user_id: str, client_ip: str, limits: LoginThrottleLimits
    ) -> None:
        now = self._clock()
        with self._lock:
            if len(self._failures) >= _PRUNE_THRESHOLD:
                self._prune(now, limits.window_seconds)
            for key, _ in self._limited_keys(login_user_id, client_ip, limits):
                failures = self._recent(key, now, limits.window_seconds)
                failures.append(now)
                self._failures[key] = failures

    def _limited_keys(
        self, login_user_id: str, client_ip: str, limits: LoginThrottleLimits
    ) -> list[tuple[tuple[str, ...], int]]:
        login_key, ip_key = self._keys(login_user_id, client_ip)
        return [
            (key, limit)
            for key, limit in ((login_key, limits.per_login_and_ip), (ip_key, limits.per_ip))
            if limit > 0
        ]

    def record_success(self, login_user_id: str, client_ip: str) -> None:
        """成功したログイン ID と送信元 IP の組の回数を消す（送信元 IP の回数は残す）。"""
        login_key, _ = self._keys(login_user_id, client_ip)
        with self._lock:
            self._failures.pop(login_key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()

    def _recent(self, key: tuple[str, ...], now: float, window_seconds: float) -> deque[float]:
        failures = self._failures.get(key)
        if failures is None:
            return deque()
        while failures and failures[0] + window_seconds <= now:
            failures.popleft()
        if not failures:
            del self._failures[key]
        return failures

    def _prune(self, now: float, window_seconds: float) -> None:
        for key in list(self._failures):
            self._recent(key, now, window_seconds)
