## [ERR-20260614-001] uv_cache_readonly

**Logged**: 2026-06-14T02:31:00+09:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
`uv run` failed because the sandbox could not write to the default `/root/.cache/uv` cache directory.

### Error
```text
error: Could not acquire lock
  Caused by: Could not create temporary file
  Caused by: Read-only file system (os error 30) at path "/root/.cache/uv/.tmp..."
```

### Context
- Command attempted: `uv run ruff check .`, `uv run mypy .`, `uv run pytest`
- Environment: managed workspace sandbox with writable project root and `/tmp`

### Suggested Fix
Use a writable cache directory for verification commands, for example `uv --cache-dir /tmp/uv-cache run pytest`.

### Metadata
- Reproducible: yes
- Related Files: backend/pyproject.toml

---

## [ERR-20260614-004] atomic_temp_filename_too_long

**Logged**: 2026-06-14T03:10:43+09:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
Atomic local object writes failed for long uploaded file names because the temporary file name appended a UUID to the already-long final file name.

### Error
```text
OSError: [Errno 36] File name too long: '.../.<long-original-name>.<uuid>.tmp'
```

### Context
- `ObjectStorageClient._atomic_write()` created a temp file with the full target name plus UUID.
- POSIX file name component limits can reject the temporary file even when the final target file name is valid.

### Suggested Fix
Use a short same-directory temporary component such as `.tmp-<uuid>` and then atomically replace the target path.

### Metadata
- Reproducible: yes
- Related Files: backend/app/clients/object_storage.py, backend/tests/test_rag_flow.py
- See Also: ERR-20260614-003

---

## [ERR-20260614-003] testclient_dependency_drift

**Logged**: 2026-06-14T02:55:00+09:00
**Priority**: high
**Status**: pending
**Area**: tests

### Summary
FastAPI/Starlette `TestClient` hung after dependency resolution drifted to newer Starlette/AnyIO/httpx combinations.

### Error
```text
TestClient(app).get("/api/health")
# hung until killed by timeout
```

### Context
- Observed combinations included `fastapi=0.136.3`, `starlette=1.3.1`, `httpx=0.28.1`, `anyio=4.13.0`.
- Pinning Starlette/FastAPI/AnyIO alone was not sufficient in this environment.
- `httpx.AsyncClient` with `httpx.ASGITransport` returned immediately and is enough for this backend's API tests.

### Suggested Fix
Use the local `tests.support.AsgiTestClient` helper for synchronous API tests and keep dependency upper bounds in `pyproject.toml` / `uv.lock` to avoid unreviewed framework drift.

### Metadata
- Reproducible: yes
- Related Files: backend/pyproject.toml, backend/uv.lock, backend/tests/support.py
- See Also: ERR-20260614-002

---

## [ERR-20260614-002] pytest_unhandled_exception_middleware_hang

**Logged**: 2026-06-14T02:34:00+09:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
Unhandled-error regression tests hung when exercising generic 500 handling through Starlette `TestClient`.

### Error
```text
pytest backend/tests/test_rag_flow.py::test_unhandled_error_uses_api_response_shape -vv
# collected 1 item, then hung at test execution
```

### Context
- First attempt converted exceptions from `call_next` into a JSON response inside HTTP middleware.
- Second attempt moved request metrics to a pure ASGI middleware and used `TestClient(app, raise_server_exceptions=False)`.
- Both approaches caused request tests to hang in this dependency set; even a simple `/ok` route hung with the experimental ASGI middleware.

### Suggested Fix
Keep the previously verified request-id/metrics middleware for now. Treat generic 500 ApiResponse handling as a separate spike with a minimal Starlette reproduction before reintroducing it.

### Metadata
- Reproducible: yes
- Related Files: backend/app/main.py, backend/tests/test_rag_flow.py
- See Also: ERR-20260614-001

---

## [ERR-20260614-005] non_ascii_bytes_literal

**Logged**: 2026-06-14T03:15:50+09:00
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
`pytest` collection failed because a test used Japanese text directly inside a Python bytes literal.

### Error
```text
SyntaxError: bytes can only contain ASCII literal characters
```

### Context
- Command attempted: `uv --cache-dir /tmp/uv-cache run pytest tests/test_dashboard.py tests/test_categories.py tests/test_health.py`
- The new dashboard test used `b"請求書..."`, which Python rejects before test execution.

### Suggested Fix
Use a Unicode string and call `.encode()` when test input bytes need Japanese text.

### Metadata
- Reproducible: yes
- Related Files: backend/tests/test_dashboard.py

---

## [ERR-20260614-006] logging_extra_mypy

**Logged**: 2026-06-14T03:19:21+09:00
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
`mypy` failed on tests that accessed a logging `extra` field as a direct `LogRecord` attribute, and on a string variable passed to a Literal-typed audit helper.

### Error
```text
tests/test_audit.py:62: error: "LogRecord" has no attribute "audit_event"  [attr-defined]
app/rag/pipeline.py:78: error: Argument "outcome" to "record_rag_search_audit" has incompatible type "str"; expected "Literal['success', 'blocked']"  [arg-type]
```

### Context
- Command attempted: `uv --cache-dir /tmp/uv-cache run mypy .`
- Python logging supports dynamic `extra` fields at runtime, but static typing does not know those attributes.
- The ternary expression for `outcome` was inferred as `str`, not the narrower Literal union.

### Suggested Fix
Use `getattr(record, "audit_event")` in tests and annotate the variable as the audit Literal alias at the call site.

### Metadata
- Reproducible: yes
- Related Files: backend/app/rag/pipeline.py, backend/tests/test_audit.py, backend/tests/test_rag_flow.py

---

## [ERR-20260614-007] logging_extra_ruff_getattr

**Logged**: 2026-06-14T03:20:28+09:00
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
`ruff` rejected constant-name `getattr` used to satisfy mypy for a dynamic logging `extra` field.

### Error
```text
B009 Do not call `getattr` with a constant attribute value.
```

### Context
- Command attempted: `uv --cache-dir /tmp/uv-cache run ruff check .`
- Direct `record.audit_event` satisfies ruff but fails mypy because `LogRecord` does not declare dynamic `extra` attributes.
- `getattr(record, "audit_event")` satisfies mypy but fails ruff B009.

### Suggested Fix
Use `cast(Any, record).audit_event` for test assertions that need dynamic logging extra fields.

### Metadata
- Reproducible: yes
- Related Files: backend/tests/test_audit.py, backend/tests/test_rag_flow.py
- See Also: ERR-20260614-006

---
