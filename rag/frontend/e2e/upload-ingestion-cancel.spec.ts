import { expect, type Page, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
  },
  error_messages: [],
  warning_messages: [],
};

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
  await page.route("**/api/settings/upload-storage", async (route) => {
    await route.fulfill({
      json: {
        data: {
          backend: "local",
          local_storage_dir: "/tmp/rag-uploads",
          object_storage_namespace: null,
          object_storage_bucket: null,
          object_storage_region: null,
          readiness: "ready",
          source: "runtime",
          max_upload_bytes: 209715200,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: { items: [], total: 0, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
});

test("取込ジョブ一覧から実行中 job をキャンセルできる", async ({ page }) => {
  let cancelRequested = false;
  let jobStatus = "RUNNING";
  await page.route("**/api/documents/ingestion-jobs**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/documents/ingestion-jobs/job-running/cancel") {
      cancelRequested = true;
      jobStatus = "CANCELLED";
      await route.fulfill({
        json: {
          data: ingestionJob(jobStatus),
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (url.pathname === "/api/documents/ingestion-jobs") {
      await route.fulfill({
        json: {
          data: {
            items: [ingestionJob(jobStatus)],
            total: 1,
            limit: 5,
            offset: 0,
            has_next: false,
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({
      status: 404,
      json: { data: null, error_messages: ["not found"], warning_messages: [] },
    });
  });

  await page.goto("/upload");

  await expect(page.getByText("取込中")).toBeVisible();
  await page.getByRole("button", { name: "キャンセル" }).click();

  await expect.poll(() => cancelRequested).toBe(true);
  await expect(page.getByText("キャンセル済み")).toBeVisible();
  await expect(page.getByRole("button", { name: "キャンセル" })).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});

function ingestionJob(status: string) {
  return {
    id: "job-running",
    document_id: "doc-1",
    status,
    parser_profile: "local_text_structure",
    quality_warnings: [],
    skip_reason: null,
    error_message: status === "CANCELLED" ? "利用者によりキャンセルされました。" : null,
    attempt_count: 1,
    max_attempts: 3,
    queued_at: "2026-06-16T00:00:00Z",
    started_at: "2026-06-16T00:00:02Z",
    finished_at: status === "CANCELLED" ? "2026-06-16T00:00:05Z" : null,
  };
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
