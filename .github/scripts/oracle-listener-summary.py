"""CI の Oracle の listener.log から、接続の拒否の診断の数値だけを出す（#5156）。

- listener が handler を止めた・戻した行（時刻と load の値）。
- 接続（establish）の数・1 秒の最大・10 秒の窓の最大と、その窓の始まりの時刻。

接続の行の CONNECT_DATA（利用者・host・program）は出さない。
"""

import collections
import datetime
import re
import sys

TIMESTAMP = re.compile(r"(\d{2}-[A-Z]{3}-\d{4} \d{2}:\d{2}:\d{2})")
HANDLER = re.compile(
    r"\A(\d{2}-[A-Z]{3}-\d{4} \d{2}:\d{2}:\d{2}):\d{3} \* "
    r"(DEDICATED handler (?:blocked by listener|unblocked): load = \d+)"
)
WINDOW_SECONDS = 10


def summarize(lines: list[str]) -> list[str]:
    handlers: list[str] = []
    per_second: collections.Counter[datetime.datetime] = collections.Counter()
    for line in lines:
        handler = HANDLER.match(line)
        if handler:
            handlers.append(f"{handler.group(1)} {handler.group(2)}")
            continue
        if " * establish * " not in line:
            continue
        timestamp = TIMESTAMP.match(line)
        if timestamp:
            second = datetime.datetime.strptime(timestamp.group(1), "%d-%b-%Y %H:%M:%S")
            per_second[second] += 1
    rows = handlers[-20:]
    if not per_second:
        return [*rows, "establish: 0"]
    window_max, window_start = 0, min(per_second)
    for start in per_second:
        count = sum(
            per_second[start + datetime.timedelta(seconds=offset)]
            for offset in range(WINDOW_SECONDS)
        )
        if count > window_max:
            window_max, window_start = count, start
    rows.append(
        f"establish: {sum(per_second.values())} max_per_second: {max(per_second.values())} "
        f"max_{WINDOW_SECONDS}s: {window_max} at {window_start:%H:%M:%S}"
    )
    return rows


if __name__ == "__main__":
    print("\n".join(summarize(sys.stdin.readlines())))
