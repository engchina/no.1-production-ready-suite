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

## [ERR-20260614-019] uv_cache_readonly_root

**Logged**: 2026-06-14T13:11:46+09:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
`uv run` failed in the managed sandbox because the default cache under `/root/.cache/uv` is read-only.

### Error
```text
error: Could not acquire lock
  Caused by: Could not create temporary file
  Caused by: Read-only file system (os error 30) at path "/root/.cache/uv/.tmp..."
```

### Context
- Commands attempted: `uv run ruff check ...` and `uv run pytest ...`
- Re-running with `UV_CACHE_DIR=/tmp/uv-cache` allowed ruff and pytest to complete.

### Suggested Fix
Use `UV_CACHE_DIR=/tmp/uv-cache uv run ...` for backend validation commands in this sandbox.

### Metadata
- Reproducible: yes
- Related Files: backend/pyproject.toml

### Resolution
- **Resolved**: 2026-06-14T13:11:46+09:00
- **Notes**: Re-ran backend ruff and targeted pytest with `UV_CACHE_DIR=/tmp/uv-cache`; both passed.

---

## [ERR-20260614-014] stale_settings_route_probe

**Logged**: 2026-06-14T11:22:04+09:00
**Priority**: low
**Status**: pending
**Area**: frontend

### Summary
An exploratory read used the stale `frontend/src/app/settings/page.tsx` path even though the current settings UI lives under component files.

### Error
```text
sed: can't read frontend/src/app/settings/page.tsx: No such file or directory
```

### Context
- Command attempted: `sed -n '1,220p' frontend/src/app/settings/page.tsx`
- The actual Oracle settings implementation being debugged is `frontend/src/components/settings/OciSettingsClient.tsx`.

### Suggested Fix
Use `rg --files frontend/src | rg 'settings|OciSettings'` before assuming App Router paths in this Vite-based frontend.

### Metadata
- Reproducible: yes
- Related Files: frontend/src/components/settings/OciSettingsClient.tsx

---

## [ERR-20260614-013] full_validation_existing_regressions

**Logged**: 2026-06-14T10:30:00+09:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
Backend full validation surfaced two current-worktree regressions: OCI URI reads incorrectly routed to local storage, and a chunking test metadata value lacked type narrowing for mypy.

### Error
```text
ValueError: ローカルモードでは local:// URI のみ取得できます。
tests/test_chunking.py:61: error: Unsupported operand types for <= ("int" and "None")
```

### Context
- Commands attempted: `uv run pytest` and `uv run mypy .`
- Failing pytest case: `tests/test_object_storage.py::test_get_oci_uri_uses_oci_even_when_upload_storage_is_local`
- Failing mypy location: `tests/test_chunking.py:61`

### Suggested Fix
Route explicit `oci://` URIs through OCI Object Storage even when upload defaults are local, and narrow metadata value types in tests before numeric comparisons.

### Metadata
- Reproducible: yes
- Related Files: backend/app/clients/object_storage.py, backend/tests/test_chunking.py

---

## [ERR-20260614-012] evaluation_no_results_failure_reason

**Logged**: 2026-06-14T09:05:33+09:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
Evaluation failure-reason aggregation initially counted an expected no-results case as `guardrail_warning` because the RAG pipeline returns a no-results warning even when the golden set expects no relevant documents.

### Error
```text
AssertionError: assert {'guardrail_warning': 1} == {}
```

### Context
- Command attempted: `uv --cache-dir /tmp/uv-cache run pytest tests/test_evaluation.py tests/test_evaluation_cli.py tests/test_evaluation_fixture.py`
- Case had `relevant_document_ids=[]` and retrieved no citations, so precision/recall/keyword/groundedness all passed.
- The warning represented the expected no-results path, not a failure.

### Suggested Fix
When assigning evaluation `failure_reasons`, suppress `guardrail_warning` for expected no-results cases where both relevant documents and retrieved documents are empty.

### Metadata
- Reproducible: yes
- Related Files: backend/app/rag/evaluation.py, backend/tests/test_evaluation.py

---

## [ERR-20260614-011] staging_smoke_default_drift

**Logged**: 2026-06-14T08:36:11+09:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
`staging_smoke` の CLI default query を marker template に変えたが、直接呼び出し用 `run_staging_smoke()` の default が旧値のままで単体テストが失敗した。

### Error
```text
AssertionError: assert 'SMOKE-...' in 'staging smoke 文書の確認用キーワードは？'
```

### Context
- Command attempted: `uv --cache-dir /tmp/uv-cache run pytest tests/test_oci_enterprise_ai.py tests/test_staging_smoke.py`
- CLI parser と callable function の default が二重管理になっていた。

### Suggested Fix
CLI default と function default は同じ module constant を参照させる。

### Metadata
- Reproducible: yes
- Related Files: backend/app/rag/staging_smoke.py, backend/tests/test_staging_smoke.py

---

## [ERR-20260614-011] npm_run_dev_argument_forwarding

**Logged**: 2026-06-14T08:33:30+09:00
**Priority**: low
**Status**: pending
**Area**: frontend

### Summary
`npm run dev -p 5173` forwarded arguments incorrectly in this environment and Next.js treated `5173` as a project directory.

### Error
```text
Invalid project directory provided, no such directory: /u01/workspace/no.1-production-ready-rag/frontend/5173
```

### Context
- Command attempted: `npm run dev -p 5173`
- Correct command: `npm run dev -- -p 5173`
- This matters when restarting the local Next.js dev server for browser verification.

### Suggested Fix
Use `--` when passing Next.js CLI flags through npm scripts, for example `npm run dev -- -p 5173`.

### Metadata
- Reproducible: yes
- Related Files: frontend/package.json

---

## [ERR-20260614-011] next_dev_listen_and_build_manifest_race

**Logged**: 2026-06-14T08:28:19+09:00
**Priority**: low
**Status**: pending
**Area**: frontend

### Summary
Next.js dev server startup can require sandbox escalation for local port listening, and `next build` may transiently fail during static generation before succeeding on rerun/debug.

### Error
```text
Error: listen EPERM: operation not permitted 0.0.0.0:3000
Error: listen EADDRINUSE: address already in use 127.0.0.1:3000
Error: ENOENT: no such file or directory, open '.next/server/pages-manifest.json'
```

### Context
- Commands attempted: `npm run dev -- -p 3000`, `npm run dev -- -p 3010 -H 127.0.0.1`, `npm run build`
- Sandbox blocked the first local listen attempt; escalated dev server startup was required.
- Ports 3000 and 3001 were already occupied, while 3010 started successfully.
- `./node_modules/.bin/next build --debug` and `NODE_OPTIONS=--trace-uncaught ./node_modules/.bin/next build` both succeeded; a later normal `npm run build` also succeeded.

### Suggested Fix
When validating locally in this sandbox, request escalation for `npm run dev`, try an alternate port such as 3010 if 3000/3001 are occupied, and rerun `next build --debug` if the static-generation worker exits without a useful stack.

### Metadata
- Reproducible: unknown
- Related Files: frontend/src/components/layout/Sidebar.tsx, frontend/next.config.ts
- See Also: ERR-20260614-010

---

## [ERR-20260614-008] groundedness_test_assertions

**Logged**: 2026-06-14T08:05:00+09:00
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
`pytest` failed after adding groundedness evaluation because tests treated a ratio score as always `1.0`, and one fixture used citation text that was too short to provide grounding features.

### Error
```text
AssertionError: assert 0.3333 == 1.0
AssertionError: assert 0.0 == 1.0
```

### Context
- Command attempted: `uv --cache-dir /tmp/uv-cache run pytest tests/test_evaluation.py tests/test_guardrails.py tests/test_evaluation_fixture.py`
- `groundedness_score` is an overlap ratio unless a high-signal numeric/ID feature overlaps.
- A citation text like `"A"` is intentionally ignored by the tokenizer because single-character features are too noisy.

### Suggested Fix
Assert pass/fail separately from exact score unless the case intentionally includes a high-signal numeric/ID overlap. Use realistic citation text in evaluation fixtures.

### Metadata
- Reproducible: yes
- Related Files: backend/tests/test_evaluation.py, backend/app/rag/guardrails.py

---

## [ERR-20260613-001] sandbox_cache_and_generated_typeinfo

**Logged**: 2026-06-13T22:38:59Z
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
Backend `uv` verification failed in the sandbox when it tried to write `/root/.cache/uv`, and a generated TypeScript build-info restore attempt via Node hit `spawnSync git EPERM`.

### Error
```text
error: Could not acquire lock
  Caused by: Could not create temporary file
  Caused by: Read-only file system (os error 30) at path "/root/.cache/uv/..."

Error: spawnSync git EPERM
```

### Context
- Commands attempted: `uv run pytest ...`, `uv run ruff ...`, and a Node helper that called `git show HEAD:frontend/tsconfig.tsbuildinfo`.
- `uv` succeeded after rerunning with escalated permissions.
- `npm run build` regenerated stale `.next/types`, after which `npm run typecheck` passed.

### Suggested Fix
Prefer an approved `uv run ...` prefix or an explicit writable `UV_CACHE_DIR` for sandboxed verification. Avoid restoring generated build-info through nested `git` calls from Node in this environment.

### Metadata
- Reproducible: unknown
- Related Files: frontend/tsconfig.tsbuildinfo

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

## [ERR-20260614-009] uv_cache_read_only_root

**Logged**: 2026-06-14T08:22:08+09:00
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
`uv run pytest` failed because the default uv cache under `/root/.cache/uv` was read-only in the sandbox.

### Error
```text
error: Could not acquire lock
  Caused by: Could not create temporary file
  Caused by: Read-only file system (os error 30) at path "/root/.cache/uv/.tmp..."
```

### Context
- Command attempted: `uv run pytest tests/test_settings_api.py`
- The workspace allows writes under `/u01/workspace/no.1-production-ready-rag` and `/tmp`, but not `/root/.cache`.

### Suggested Fix
Run uv with a workspace-writable cache directory, for example `uv --cache-dir /tmp/uv-cache run pytest ...`.

### Metadata
- Reproducible: yes
- Related Files: backend/tests/test_settings_api.py

---

## [ERR-20260614-010] next_build_worker_static_generation

**Logged**: 2026-06-14T08:24:42+09:00
**Priority**: low
**Status**: pending
**Area**: frontend

### Summary
`npm run build` failed during Next.js static generation when the default build worker was enabled, but succeeded with `NEXT_PRIVATE_BUILD_WORKER=0`.

### Error
```text
Build error occurred
ENOENT: no such file or directory, rename '.next/export/500.html' -> '.next/server/pages/500.html'

Next.js build worker exited with code: 1 and signal: null
```

### Context
- Command attempted: `npm run build`
- Follow-up command succeeded: `NEXT_PRIVATE_BUILD_WORKER=0 npm run build`
- The compile, lint, typecheck, and route generation steps completed before the worker failure.

### Suggested Fix
For this sandbox, use `NEXT_PRIVATE_BUILD_WORKER=0 npm run build` when validating Next.js builds. If it recurs in CI, investigate Next.js worker filesystem behavior around `.next/export`.

### Metadata
- Reproducible: yes
- Related Files: frontend/next.config.ts

---

## [ERR-20260614-011] sandbox_socket_bind_permission

**Logged**: 2026-06-14T08:36:23+09:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
Starting a local uvicorn dev server inside the managed sandbox failed because socket creation was not permitted.

### Error
```text
PermissionError: [Errno 1] Operation not permitted
```

### Context
- Command attempted: `uv --cache-dir /tmp/uv-cache run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- The uv cache issue was avoided with `/tmp/uv-cache`, but the sandbox still blocked creating a listening socket.
- Running the same startup command with approved sandbox escalation reached the host network namespace.

### Suggested Fix
For local dev server startup in this environment, use a writable uv cache and approved sandbox escalation when binding ports.

### Metadata
- Reproducible: yes
- Related Files: backend/README.md
- See Also: ERR-20260614-009

---

## [ERR-20260614-012] dev_server_persistence_and_vite_args

**Logged**: 2026-06-14T10:07:02+09:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
Foreground dev server sessions and `nohup` background launches did not persist reliably after the Codex command turn; a systemd transient service did. The current frontend dev script is Vite and rejects Next-style `--hostname`.

### Error
```text
curl: (7) Failed to connect to 127.0.0.1 port 56765
CACError: Unknown option `--hostname`
```

### Context
- Commands attempted:
  - `npm run dev -- --hostname 0.0.0.0 --port 56765`
  - `nohup uv --cache-dir /tmp/uv-cache run uvicorn ... &`
- `npm run dev` currently expands to `vite --host 0.0.0.0 --port 3000`.
- The stable startup path was:
  - backend: `systemd-run --unit=production-ready-rag-backend ... uv --cache-dir /tmp/uv-cache run uvicorn ... --port 8000`
  - frontend: `systemd-run --unit=production-ready-rag-frontend ... frontend/node_modules/.bin/vite --host 0.0.0.0 --port 56765`

### Suggested Fix
Use `systemd-run` for dev servers that must remain available after the assistant turn, and match frontend CLI flags to the current framework (`--host` for Vite, `--hostname` for Next).

### Metadata
- Reproducible: yes
- Related Files: frontend/package.json, frontend/vite.config.ts, backend/README.md
- See Also: ERR-20260614-009, ERR-20260614-011

---

## [ERR-20260614-013] vitest_jest_runinband_option

**Logged**: 2026-06-14T02:15:32Z
**Priority**: low
**Status**: pending
**Area**: frontend

### Summary
Vitest rejected the Jest-specific `--runInBand` option during frontend test validation.

### Error
```text
CACError: Unknown option `--runInBand`
```

### Context
- Command attempted: `npm run test -- --runInBand`
- This project uses Vitest (`vitest run`), whose CLI does not accept Jest's serial execution flag.

### Suggested Fix
Use `npm run test` for the project default, or Vitest-supported flags such as `--pool` / `--maxWorkers` only when needed.

### Metadata
- Reproducible: yes
- Related Files: frontend/package.json

---

## [ERR-20260614-016] rg_missing_optional_env_example

**Logged**: 2026-06-14T12:10:36+09:00
**Priority**: low
**Status**: resolved
**Area**: docs

### Summary
Repository-wide docs scan failed because the command included `.env.example`, which is not present in this repository.

### Error
```text
rg: .env.example: No such file or directory (os error 2)
```

### Context
- Command attempted: `rg -n "staging_smoke|preflight-only|cleanup|Object Storage|staging smoke" backend/README.md docs/deployment.md docs/rag-architecture.md .env.example`
- The project currently has docs under `backend/README.md` and `docs/`, but no top-level `.env.example`.

### Suggested Fix
Before including optional files in targeted `rg` commands, either confirm they exist with `rg --files` or omit them from the fixed file list.

### Metadata
- Reproducible: yes
- Related Files: backend/README.md, docs/deployment.md

### Resolution
- **Resolved**: 2026-06-14T12:10:36+09:00
- **Notes**: Removed `.env.example` from subsequent targeted docs scans.

---

## [ERR-20260614-014] oci_settings_i18n_typecheck

**Logged**: 2026-06-14T11:57:53+09:00
**Priority**: low
**Status**: pending
**Area**: frontend

### Summary
Frontend typecheck failed after OCI settings UI changes because newly referenced i18n keys were missing, then duplicate key additions caused `TS1117`.

### Error
```text
TS2345: Argument of type '"settings.oci.actions.selectKeyFile"' is not assignable to parameter of type I18nKey.
TS1117: An object literal cannot have multiple properties with the same name.
```

### Context
- Command attempted: `npm run typecheck`
- `OciSettingsClient.tsx` referenced key-file picker labels while `frontend/src/lib/i18n.ts` did not yet expose those keys in the typed `ja` object.
- Adding the missing keys without first checking the surrounding block introduced duplicates because related keys already existed later in the same object.

### Suggested Fix
When adding typed i18n keys, search the locale object for existing related keys first, add each key once, and rerun `npm run typecheck` before build.

### Metadata
- Reproducible: yes
- Related Files: frontend/src/lib/i18n.ts, frontend/src/components/settings/OciSettingsClient.tsx

---

## [ERR-20260614-015] enterprise_ai_settings_contract_drift

**Logged**: 2026-06-14T12:06:36+09:00
**Priority**: medium
**Status**: resolved
**Area**: config

### Summary
Full backend tests failed after adding Enterprise AI response path settings because readiness, settings API schemas, and test fixtures were not updated together.

### Error
```text
FAILED tests/test_health.py::test_readiness_oci_complete_config_is_ok
FAILED tests/test_settings_api.py::test_update_model_settings_mutates_runtime_settings
FAILED tests/test_staging_smoke.py::test_staging_smoke_uses_unique_marker_query_and_document_filter
AssertionError: assert 'missing' == 'ok'
```

### Context
- Command attempted: `uv run pytest`
- Enterprise AI readiness currently requires endpoint, project OCID, paths, model/template, and auth mode.
- Adding new settings fields also required updating the model settings API schema, runtime apply path, frontend API type, and complete OCI test fixtures.

### Suggested Fix
When adding new runtime settings, update Settings, API schemas, readiness/status checks, UI/API types, `.env.example`, docs, and complete-config fixtures in one pass before full test runs.

### Metadata
- Reproducible: yes
- Related Files: backend/app/config.py, backend/app/schemas/settings.py, backend/app/api/routes/settings.py, backend/app/readiness.py, frontend/src/lib/api.ts

### Resolution
- **Resolved**: 2026-06-14T12:06:36+09:00
- **Notes**: Added response path fields to settings schemas/API types, propagated runtime apply/readback, updated OCI readiness fixtures, and reran full backend/frontend validation.

---

## [ERR-20260614-017] git_index_sandbox_readonly

**Logged**: 2026-06-14T13:02:00+09:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
`git add -A` failed because the managed sandbox could not create `.git/index.lock`.

### Error
```text
fatal: Unable to create '/u01/workspace/no.1-production-ready-rag/.git/index.lock': Read-only file system
```

### Context
- Command attempted: `git add -A`
- The workspace allows source-file edits, but `.git` writes may require sandbox escalation in this environment.

### Suggested Fix
When staging, committing, or pushing from this desktop sandbox, rerun Git operations that write `.git` with approved sandbox escalation.

### Metadata
- Reproducible: yes
- Related Files: .git/index

---

## [ERR-20260614-018] settings_namespace_threadpool_hang

**Logged**: 2026-06-14T13:00:00+09:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
The Object Storage namespace settings endpoint hung in the ASGI test client when the OCI SDK call was wrapped with `asyncio.to_thread()` / Starlette threadpool helpers.

### Error
```text
tests/test_settings_api.py::test_read_object_storage_namespace_uses_oci_sdk
Timeout (0:00:10)!
... asyncio/runners.py line 72 in close
```

### Context
- Commands attempted: `uv --cache-dir /tmp/uv-cache run pytest`, targeted `pytest -vv`, and targeted `pytest -o faulthandler_timeout=10`.
- The endpoint is an explicit admin/settings action and the test uses mocked OCI imports.
- Direct synchronous invocation avoids the executor shutdown wait and keeps the API response contract unchanged.

### Suggested Fix
For this endpoint, keep `_read_object_storage_namespace()` synchronous inside the async route unless a production timeout-aware SDK wrapper is added.

### Metadata
- Reproducible: yes
- Related Files: backend/app/api/routes/settings.py, backend/tests/test_settings_api.py

### Resolution
- **Resolved**: 2026-06-14T13:00:00+09:00
- **Notes**: Replaced the threadpool call with direct `_read_object_storage_namespace(payload)` and reran backend full pytest successfully.

---
