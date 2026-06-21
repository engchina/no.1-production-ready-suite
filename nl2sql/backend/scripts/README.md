# NL2SQL Manual Integration

`nl2sql_manual_integration.py` is a local smoke test for real Oracle Select AI /
Select AI Agent wiring. It uses `backend/.env` through the existing FastAPI
settings and does not print secret values.

Safe preview-only check:

```bash
uv run python scripts/nl2sql_manual_integration.py
```

Oracle diagnostics has an outer timeout guard. If the driver/network does not
return quickly, the script stops before previewing Oracle engines:

```bash
uv run python scripts/nl2sql_manual_integration.py --require-oracle --diagnostics-timeout 8
```

Oracle-required check with asset refresh:

```bash
uv run python scripts/nl2sql_manual_integration.py --require-oracle --refresh-catalog --refresh-assets
```

When the default profile still points at local mock tables, pass one or more
real Oracle tables. The script creates/updates a `manual_integration` profile
for the smoke test:

```bash
uv run python scripts/nl2sql_manual_integration.py --require-oracle --refresh-catalog --refresh-assets --allowed-table YOUR_TABLE
```

Full smoke including generated SQL execution:

```bash
uv run python scripts/nl2sql_manual_integration.py --require-oracle --refresh-assets --execute
```

Cleanup dry-run for manual Oracle assets:

```bash
uv run python scripts/nl2sql_manual_integration.py --cleanup-assets --profile-id manual_integration
```

Confirmed cleanup:

```bash
uv run python scripts/nl2sql_manual_integration.py --require-oracle --cleanup-assets --confirm-cleanup --profile-id manual_integration
```

`--refresh-assets` creates/replaces Oracle Select AI profile and Select AI Agent
profile/tool/agent/task/team assets. `--execute` runs the generated `SELECT`
SQL and writes a history item through the normal NL2SQL job path.
`--cleanup-assets` only prints the target assets unless `--confirm-cleanup` is
also passed.
