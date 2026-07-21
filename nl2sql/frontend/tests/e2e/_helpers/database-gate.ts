import type { Page, Route } from "@playwright/test";

function envelope(data: unknown) {
  return { data, error_messages: [], warning_messages: [] };
}

async function fulfill(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(envelope(data)),
  });
}

export const systemAdminMe = {
  user_id: "00000000-0000-0000-0000-000000000001",
  login_name: "SYSTEM",
  display_name: "システム管理者",
  status: "ACTIVE",
  force_password_change: false,
  role_codes: ["SYSTEM_ADMIN"],
  permissions: [],
  data_entitlements: [],
  debug_mode: false,
};

/** 通常の E2E は Oracle snapshot が利用可能な状態から開始する。 */
export async function mockDatabaseGateReady(page: Page) {
  await page.route("**/api/auth/me", (route) => fulfill(route, systemAdminMe));
  await page.route("**/api/ready/database", (route) =>
    fulfill(route, { status: "ok", check: "ok", detail: null })
  );
  await page.route("**/api/nl2sql/persistence", (route) =>
    fulfill(route, {
      mode: "oracle",
      ready: true,
      durable: true,
      writable: true,
      snapshot_loaded: true,
      reason_code: null,
      checked_at: "2026-07-19T00:00:00Z",
    })
  );
  await page.route("**/api/nl2sql/persistence/recover", (route) =>
    fulfill(route, {
      mode: "oracle",
      ready: true,
      durable: true,
      writable: true,
      snapshot_loaded: true,
      reason_code: null,
      checked_at: "2026-07-19T00:00:00Z",
    })
  );
  await page.route("**/api/settings/database/system-tables", (route) =>
    fulfill(route, {
      status: "ready",
      schema_head: 6,
      applied_versions: [0, 1, 2, 3, 5, 6],
      pending_versions: [],
      expected_object_count: 49,
      existing_object_count: 49,
      missing_objects: [],
      tables: [],
      operation_state: {
        status: "idle",
        operation_kind: null,
        lease_expires_at: null,
        last_error_code: null,
        schema_epoch: 1,
        updated_at: "2026-07-19T00:00:00Z",
      },
    })
  );
}
