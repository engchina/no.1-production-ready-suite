import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { isAbortError, isTimeoutError } from "../src/lib/api.ts";
import { waitForSchemaRefreshJob } from "../src/features/nl2sql/schemaRefreshJob.ts";
import type { SchemaRefreshJob } from "../src/features/nl2sql/types.ts";

function jsonResponse(data: SchemaRefreshJob): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function job(status: SchemaRefreshJob["status"], errorCode = ""): SchemaRefreshJob {
  return {
    job_id: "schema-refresh-test",
    status,
    created_at: "2026-08-12T00:00:00.000Z",
    scanned_objects: status === "done" ? 1 : 0,
    changed_objects: status === "done" ? 1 : 0,
    deleted_objects: 0,
    catalog_version: status === "done" ? 2 : 0,
    error_code: errorCode,
  };
}

test("waitForSchemaRefreshJob resolves when the durable job reaches done", async () => {
  const originalFetch = globalThis.fetch;
  const responses = [job("running"), job("done")];
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return jsonResponse(responses.shift() ?? job("done"));
  };
  try {
    const completed = await waitForSchemaRefreshJob("schema-refresh-test", undefined, {
      pollIntervalMs: 1,
      maxWaitMs: 1_000,
    });
    assert.equal(completed.status, "done");
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("waitForSchemaRefreshJob rejects when the durable job reaches error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => jsonResponse(job("error", "schema_refresh_failed"));
  try {
    await assert.rejects(
      waitForSchemaRefreshJob("schema-refresh-test", undefined, { pollIntervalMs: 1 }),
      /schema_refresh_failed/u,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("waitForSchemaRefreshJob reports bounded waits as TimeoutError", async () => {
  const originalFetch = globalThis.fetch;
  const keepAlive = setTimeout(() => undefined, 1_000);
  globalThis.fetch = async () => jsonResponse(job("running"));
  try {
    await assert.rejects(
      waitForSchemaRefreshJob("schema-refresh-test", undefined, {
        pollIntervalMs: 1,
        maxWaitMs: 5,
      }),
      (cause) => isTimeoutError(cause),
    );
  } finally {
    clearTimeout(keepAlive);
    globalThis.fetch = originalFetch;
  }
});

test("waitForSchemaRefreshJob preserves caller aborts", async () => {
  const originalFetch = globalThis.fetch;
  const keepAlive = setTimeout(() => undefined, 1_000);
  const controller = new AbortController();
  globalThis.fetch = async () => jsonResponse(job("running"));
  try {
    const pending = waitForSchemaRefreshJob("schema-refresh-test", controller.signal, {
      pollIntervalMs: 50,
      maxWaitMs: 1_000,
    });
    controller.abort(new DOMException("user_cancelled", "AbortError"));
    await assert.rejects(pending, (cause) => isAbortError(cause));
  } finally {
    clearTimeout(keepAlive);
    globalThis.fetch = originalFetch;
  }
});

test("DDL mutation pages do not block SQL results on schema refresh completion", () => {
  const sources = [
    "../src/features/nl2sql/pages/TableManagementPage.tsx",
    "../src/features/nl2sql/pages/ViewManagementPage.tsx",
    "../src/features/nl2sql/pages/MetadataSqlManagementPage.tsx",
  ].map((path) => readFileSync(new URL(path, import.meta.url), "utf8"));

  for (const source of sources) {
    assert.doesNotMatch(source, /reloadAfterMutation\s*=\s*async/u);
    assert.doesNotMatch(source, /await\s+waitForSchemaRefreshJob\(result\.schema_refresh_job_id/u);
    assert.match(source, /setSchemaRefreshJobId\(/u);
    assert.match(source, /void refreshObjects\(\)/u);
  }
});
