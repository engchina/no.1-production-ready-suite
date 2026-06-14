import { expect, test, type Page } from "@playwright/test";

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

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());

    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: authStatus });
      return;
    }

    if (url.pathname === "/api/settings/oci") {
      await route.fulfill({
        json: {
          data: {
            config_file: "~/.oci/config",
            profile: "DEFAULT",
            user: "ocid1.user.oc1..example",
            fingerprint: "08:04:52:3f:da:bc:00:ed:06:e5:e1:88:08:90:54:1e",
            tenancy: "ocid1.tenancy.oc1..example",
            region: "ap-osaka-1",
            key_file: "~/.oci/oci_api_key.pem",
            key_file_exists: true,
            config_file_exists: true,
            config_source: "runtime",
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (url.pathname === "/api/settings/upload-storage") {
      await route.fulfill({
        json: {
          data: {
            backend: "oci",
            local_storage_dir: "/u01/production-ready-rag",
            object_storage_region: "ap-osaka-1",
            object_storage_namespace: "idqcucnenh88",
            object_storage_bucket: "rag-originals",
            readiness: "ok",
            max_upload_bytes: 209715200,
            config_source: "runtime",
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    await route.fulfill({
      json: { data: null, error_messages: [], warning_messages: [] },
    });
  });
}

test("sidebar route changes reset the main pane while browser back restores it", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/settings/oci");

  const main = page.getByRole("main", { name: "メイン領域" });
  await expect(page.getByRole("heading", { name: "OCI 認証設定" })).toBeVisible();

  const ociScrollTop = await main.evaluate((element) => {
    element.scrollTo({
      top: Math.min(620, element.scrollHeight - element.clientHeight),
      left: 0,
      behavior: "auto",
    });
    element.dispatchEvent(new Event("scroll"));
    return element.scrollTop;
  });
  expect(ociScrollTop).toBeGreaterThan(100);

  await page
    .getByRole("complementary", { name: "サイドナビゲーション" })
    .getByRole("link", { name: "アップロード保存先" })
    .click();

  await expect(page).toHaveURL(/\/settings\/upload-storage$/);
  await expect(page.getByRole("heading", { name: "アップロード保存先" })).toBeVisible();
  await expect.poll(() => main.evaluate((element) => element.scrollTop)).toBe(0);

  await page.goBack();

  await expect(page).toHaveURL(/\/settings\/oci$/);
  await expect(page.getByRole("heading", { name: "OCI 認証設定" })).toBeVisible();
  await expect
    .poll(() => main.evaluate((element) => element.scrollTop))
    .toBeGreaterThanOrEqual(ociScrollTop - 1);
});
