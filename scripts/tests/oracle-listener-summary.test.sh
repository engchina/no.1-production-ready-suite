#!/usr/bin/env bash
# 公開する listener の診断には、handler の状態・load と接続の数以外を含めない（#5156）。
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
python3 - "${root}" <<'PY'
import importlib.util
import sys
from pathlib import Path

path = Path(sys.argv[1]) / '.github/scripts/oracle-listener-summary.py'
spec = importlib.util.spec_from_file_location('summary', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
connect = (
    '16-NOV-2026 12:31:{second:02d}:100 * (CONNECT_DATA=(SERVICE_NAME=freepdb1)(CID=(PROGRAM=private-program)'
    '(HOST=private-host)(USER=private-user))) * (ADDRESS=(PROTOCOL=tcp)(HOST=192.0.2.1)(PORT=1)) * establish * freepdb1 * 0'
)
lines = [connect.format(second=second) for second in (0, 0, 0, 5, 9, 20)]
lines += [
    '16-NOV-2026 12:31:19:950 * DEDICATED handler blocked by listener: load = 199 * FREE',
    '16-NOV-2026 12:31:21:309 * DEDICATED handler unblocked: load = 100 * FREE',
    '16-NOV-2026 12:31:22:000 * service_update * FREE * 0',
]
rows = module.summarize(lines)
assert rows == [
    '16-NOV-2026 12:31:19 DEDICATED handler blocked by listener: load = 199',
    '16-NOV-2026 12:31:21 DEDICATED handler unblocked: load = 100',
    'establish: 6 max_per_second: 3 max_10s: 5 at 12:31:00',
], rows
assert not any('private' in row or '192.0.2.1' in row for row in rows)
assert module.summarize(['16-NOV-2026 12:31:22:000 * service_update * FREE * 0']) == ['establish: 0']
print('ok - handler の状態と接続の数だけを出し、接続の利用者・host を出さない')
PY
