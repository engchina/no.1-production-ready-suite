import { expect, type Page, test } from "@playwright/test";

interface UploadStorageSettingsData {
  backend: "local" | "oci";
  local_storage_dir: string;
  object_storage_region: string;
  object_storage_namespace: string;
  object_storage_bucket: string;
  readiness: string;
  max_upload_bytes: number;
  config_source: "runtime";
}

const localStorageSettings: UploadStorageSettingsData = {
  backend: "local",
  local_storage_dir: "/u01/production-ready-rag",
  object_storage_region: "ap-osaka-1",
  object_storage_namespace: "",
  object_storage_bucket: "",
  readiness: "ok",
  max_upload_bytes: 200 * 1024 * 1024,
  config_source: "runtime",
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      json: {
        data: {
          mode: "local",
          auth_required: false,
          authenticated: true,
          user: null,
          expires_at: null,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
});

test("アップロード保存先設定で OCI Object Storage に切り替えられる", async ({
  page,
}) => {
  let current = { ...localStorageSettings };
  let lastPayload: unknown = null;
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.oci-settings.v1",
      JSON.stringify({
        objectStorageNamespace: "oci-page-namespace",
      })
    );
  });
  await mockUploadStorageSettings(page, () => current, async (payload) => {
    lastPayload = payload;
    current = {
      ...current,
      ...(payload as Partial<UploadStorageSettingsData>),
      readiness: "ok",
    };
  });

  await page.goto("/settings/upload-storage");

  await expect(
    page.getByRole("heading", { name: "アップロード保存先" })
  ).toBeVisible();
  await expect(page.getByText("200.0 MB")).toBeVisible();
  await expect(page.getByRole("button", { name: ".env をコピー" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "JSON プレビュー" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "運用メモ" })).toBeVisible();
  const envPreview = page.getByLabel(".env プレビュー");
  await expect(envPreview).toContainText("UPLOAD_STORAGE_BACKEND=local");
  await expect(envPreview).toContainText("LOCAL_STORAGE_DIR=/u01/production-ready-rag");
  await page.getByRole("radio", { name: /OCI Object Storage/ }).check();
  await expect(page.getByLabel("Object Storage ネームスペース")).toHaveCount(0);
  await page.getByLabel("Object Storage バケット").fill("rag-originals");
  await expect(envPreview).toContainText("UPLOAD_STORAGE_BACKEND=oci");
  await expect(envPreview).toContainText("OBJECT_STORAGE_REGION=ap-osaka-1");
  await expect(envPreview).toContainText(
    "OBJECT_STORAGE_NAMESPACE=oci-page-namespace"
  );
  await expect(envPreview).toContainText("OBJECT_STORAGE_BUCKET=rag-originals");
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("保存しました")).toBeVisible();
  await expect(page.getByText("oci-page-namespace/rag-originals")).toBeVisible();
  expect(lastPayload).toMatchObject({
    backend: "oci",
    object_storage_namespace: "oci-page-namespace",
    object_storage_bucket: "rag-originals",
  });
});

test("アップロード保存先は OCI の未設定項目があっても保存できる", async ({ page }) => {
  let current = { ...localStorageSettings };
  let lastPayload: unknown = null;
  await mockUploadStorageSettings(page, () => current, async (payload) => {
    lastPayload = payload;
    current = {
      ...current,
      ...(payload as Partial<UploadStorageSettingsData>),
      readiness: "missing",
    };
  });

  await page.goto("/settings/upload-storage");

  await page.getByRole("radio", { name: /OCI Object Storage/ }).check();
  const memo = operationMemoCard(page);
  await expect(memo.getByText("値を入力してください。")).toBeVisible();
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("保存しました")).toBeVisible();
  await expect(page.getByText("Readiness: 未設定")).toBeVisible();
  expect(lastPayload).toMatchObject({
    backend: "oci",
    object_storage_namespace: "",
    object_storage_bucket: "",
  });
});

test("アップロード画面から現在の保存先と設定導線を確認できる", async ({ page }) => {
  await mockUploadStorageSettings(page, () => ({
    ...localStorageSettings,
    backend: "oci",
    object_storage_namespace: "example-namespace",
    object_storage_bucket: "rag-originals",
  }));

  await page.goto("/upload");

  await expect(page.getByText("現在の保存先")).toBeVisible();
  await expect(page.getByText("最大 200 MB")).toBeVisible();
  await expect(page.getByText("example-namespace/rag-originals")).toBeVisible();
  await page.getByRole("link", { name: "保存先設定" }).click();
  await expect(page).toHaveURL(/\/settings\/upload-storage$/);
});

async function mockUploadStorageSettings(
  page: Page,
  getCurrent: () => UploadStorageSettingsData,
  onPatch?: (payload: unknown) => Promise<void>
) {
  await page.route("**/api/settings/upload-storage", async (route) => {
    const request = route.request();
    if (request.method() === "PATCH") {
      const payload = request.postDataJSON();
      await onPatch?.(payload);
    }

    await route.fulfill({
      json: {
        data: getCurrent(),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

function operationMemoCard(page: Page) {
  return page
    .getByRole("heading", { name: "運用メモ" })
    .locator(
      "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-lg ')][1]"
    );
}
