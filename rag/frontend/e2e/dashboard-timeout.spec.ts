import { expect, test } from "@playwright/test";

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

const degradedDashboardSummary = {
  data: {
    stats: {
      total_uploads: 0,
      uploads_this_month: 0,
      total_indexed: 0,
      indexed_this_month: 0,
      searchable_rows: 0,
    },
    ingestion_quality: {
      document_count: 0,
      structured_document_count: 0,
      element_count: 0,
      table_count: 0,
      list_count: 0,
      page_count: 0,
      chunk_profile_counts: {},
      content_kind_counts: {},
    },
    recent_activities: [],
    system: {
      status: "degraded",
      version: "0.1.0",
      searchable_rows: 0,
      checks: {
        oracle: "timeout",
        dashboard_data: "timeout",
      },
    },
  },
  error_messages: [],
  warning_messages: [
    "ダッシュボードのデータ取得が 8 秒以内に完了しませんでした。データベースの起動状態を確認して再試行してください。",
  ],
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __RAG_API_TIMEOUT_MS__?: number }).__RAG_API_TIMEOUT_MS__ = 250;
  });
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
  // DB ゲートは通過させ、Dashboard 自身の縮退/タイムアウト挙動を検証する。
  await page.route("**/api/ready/database", async (route) => {
    await route.fulfill({
      json: {
        data: { status: "ok", check: "ok", detail: null },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
});

test("Dashboard の初期取得がタイムアウトしたら再試行できるエラー状態を表示する", async ({
  page,
}) => {
  await page.route("**/api/dashboard/summary", async () => {
    await new Promise(() => undefined);
  });

  await page.goto("/dashboard");

  await expect(page.getByRole("alert")).toContainText("API の応答が 1 秒以内に返りませんでした。");
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
});

test("DB 停止時も Dashboard を縮退状態で開き、データベース設定へ移動できる", async ({
  page,
}) => {
  await page.route("**/api/dashboard/summary", async (route) => {
    await route.fulfill({ json: degradedDashboardSummary });
  });

  await page.goto("/dashboard");

  await expect(page.getByRole("heading", { name: "主要機能ハブ" })).toBeVisible();
  await expect(page.getByText("データベース機能は縮退中です")).toBeVisible();
  await expect(page.getByText("タイムアウト")).toBeVisible();

  const settingsLink = page.getByRole("link", { name: /データベース設定を開く/ });
  await expect(settingsLink).toBeVisible();
  await expect(settingsLink).toHaveAttribute("href", "/settings/database");
});
