import assert from "node:assert/strict";
import test from "node:test";

import { apiGet, isAbortError, isTimeoutError } from "../src/lib/api.ts";

function abortableFetch(
  calls: { count: number },
): typeof fetch {
  return async (_input, init) => {
    calls.count += 1;
    return await new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal;
      if (signal?.aborted) {
        reject(signal.reason);
        return;
      }
      signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
    });
  };
}

test("apiGet reports a timeout without treating it as a database readiness failure", async () => {
  const originalFetch = globalThis.fetch;
  const calls = { count: 0 };
  // AbortSignal.timeout の timer は Node では unref されるため、検証中だけ event loop を保持する。
  const keepAlive = setTimeout(() => undefined, 1_000);
  globalThis.fetch = abortableFetch(calls);
  try {
    await assert.rejects(
      apiGet("/api/nl2sql/db-admin/tables/TABLE_01", { timeoutMs: 10 }),
      (cause) => isTimeoutError(cause) && !isAbortError(cause),
    );
    assert.equal(calls.count, 1, "timeout must not trigger a database readiness probe");
  } finally {
    clearTimeout(keepAlive);
    globalThis.fetch = originalFetch;
  }
});

test("apiGet preserves an explicit user cancellation as AbortError", async () => {
  const originalFetch = globalThis.fetch;
  const calls = { count: 0 };
  globalThis.fetch = abortableFetch(calls);
  const controller = new AbortController();
  try {
    const request = apiGet("/api/nl2sql/db-admin/views/VIEW_01", {
      signal: controller.signal,
      timeoutMs: 15_000,
    });
    controller.abort();
    await assert.rejects(
      request,
      (cause) => isAbortError(cause) && !isTimeoutError(cause),
    );
    assert.equal(calls.count, 1, "cancellation must not trigger a database readiness probe");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
