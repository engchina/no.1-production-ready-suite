import { expect, test, type Locator, type Page } from "@playwright/test";

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

interface MockApiOptions {
  onOciConfigRead?: (body: unknown) => void;
  onObjectStorageNamespaceRead?: (body: unknown) => void;
  onOciPrivateKeyUpload?: (contentType: string) => void;
  uploadStorageSettings?: {
    object_storage_region?: string;
    object_storage_namespace?: string;
    object_storage_bucket?: string;
  };
}

async function mockApi(page: Page, options: MockApiOptions = {}) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: authStatus });
      return;
    }

    if (url.pathname === "/api/ready") {
      await route.fulfill({
        json: {
          data: {
            status: "ok",
            version: "0.1.0",
            message: "adapter=oci",
            checks: { config: "ok", oracle: "missing" },
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
            backend: "local",
            ai_service_adapter: "local",
            local_storage_dir: "/tmp/production-ready-rag",
            object_storage_region: options.uploadStorageSettings?.object_storage_region ?? "",
            object_storage_namespace:
              options.uploadStorageSettings?.object_storage_namespace ?? "",
            object_storage_bucket: options.uploadStorageSettings?.object_storage_bucket ?? "",
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

    if (url.pathname === "/api/settings/oci/config/read") {
      options.onOciConfigRead?.(route.request().postDataJSON());
      await route.fulfill({
        json: {
          data: {
            profile: "RAG_PROD",
            user: "ocid1.user.oc1..prod",
            fingerprint: "12:34:56:78",
            tenancy: "ocid1.tenancy.oc1..prod",
            region: "ap-osaka-1",
            key_file: "/home/app/.oci/prod.pem",
            applied_fields: [
              "user",
              "fingerprint",
              "tenancy",
              "region",
              "key_file",
            ],
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (url.pathname === "/api/settings/oci/key-file") {
      options.onOciPrivateKeyUpload?.(route.request().headers()["content-type"] ?? "");
      await route.fulfill({
        json: {
          data: {
            key_file: "~/.oci/oci_api_key.pem",
            saved: true,
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (url.pathname === "/api/settings/oci/object-storage/namespace") {
      options.onObjectStorageNamespaceRead?.(route.request().postDataJSON());
      await route.fulfill({
        json: {
          data: {
            namespace: "mytenancynamespace",
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

async function expectButtonTextContained(button: Locator) {
  await expect(button).toBeVisible();

  const metrics = await button.evaluate((element) => {
    const style = window.getComputedStyle(element);
    return {
      clientHeight: element.clientHeight,
      clientWidth: element.clientWidth,
      scrollHeight: element.scrollHeight,
      scrollWidth: element.scrollWidth,
      whiteSpace: style.whiteSpace,
    };
  });

  expect(metrics.whiteSpace).toBe("nowrap");
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
  expect(metrics.scrollHeight).toBeLessThanOrEqual(metrics.clientHeight + 1);
}

async function expectActionInsideCard(page: Page, heading: string, button: Locator) {
  const card = page
    .getByRole("heading", { name: heading })
    .locator(
      "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-lg ')][1]"
    );
  const cardBox = await card.boundingBox();
  const buttonBox = await button.boundingBox();

  expect(cardBox).not.toBeNull();
  expect(buttonBox).not.toBeNull();
  expect(buttonBox!.x + buttonBox!.width).toBeLessThanOrEqual(
    cardBox!.x + cardBox!.width + 1
  );
  expect(buttonBox!.y + buttonBox!.height).toBeLessThanOrEqual(
    cardBox!.y + cardBox!.height + 1
  );
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 720, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`OCI 設定カードのアクション文言が収まる (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await page.addInitScript(() => {
        window.localStorage.setItem(
          "production-ready-rag.ui",
          JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
        );
      });
    }
    await mockApi(page);
    await page.goto("/settings/oci");

    const authSaveButton = page.getByRole("button", {
      name: "OCI 認証設定: 下書きを保存",
    });
    const authResetButton = page.getByRole("button", {
      name: "OCI 認証設定: 既定値へ戻す",
    });
    const storageSaveButton = page.getByRole("button", {
      name: "Object Storage: 下書きを保存",
    });
    const storageResetButton = page.getByRole("button", {
      name: "Object Storage: 既定値へ戻す",
    });
    const readinessButton = page.getByRole("button", { name: "接続確認" });
    const copyButton = page.getByRole("button", { name: ".env をコピー" });

    await expectButtonTextContained(authSaveButton);
    await expectButtonTextContained(authResetButton);
    await expectButtonTextContained(storageSaveButton);
    await expectButtonTextContained(storageResetButton);
    await expectButtonTextContained(readinessButton);
    await expectButtonTextContained(copyButton);
    await expectActionInsideCard(page, "認証プロファイル", authSaveButton);
    await expectActionInsideCard(page, "認証プロファイル", authResetButton);
    await expectActionInsideCard(page, "Object Storage", storageSaveButton);
    await expectActionInsideCard(page, "Object Storage", storageResetButton);
    await expectActionInsideCard(page, "バックエンド readiness", readinessButton);
    await expectActionInsideCard(page, ".env プレビュー", copyButton);
  });
}

test("認証プロファイルの下書きは Object Storage 未入力でも保存できる", async ({ page }) => {
  await mockApi(page);
  await page.goto("/settings/oci");

  await page.getByLabel("ユーザー OCID").fill("ocid1.user.oc1..profile");
  await page.getByLabel("API キー fingerprint").fill("12:34:56:78:90:ab:cd:ef");
  await page.getByLabel("テナンシ OCID").fill("ocid1.tenancy.oc1..profile");

  await page
    .getByRole("button", { name: "OCI 認証設定: 下書きを保存" })
    .click();

  await expect(
    page.getByRole("button", { name: "OCI 認証設定: 保存しました" })
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);

  const stored = await page.evaluate(() =>
    JSON.parse(window.localStorage.getItem("production-ready-rag.oci-settings.v1") ?? "{}")
  );
  expect(stored.userOcid).toBe("ocid1.user.oc1..profile");
  expect(stored.configFile).toBe("~/.oci/config");
  expect(stored.configProfile).toBe("DEFAULT");
  expect(stored.compartmentId).toBeUndefined();
  expect(stored.objectStorageRegion).toBe("ap-osaka-1");
  expect(stored.objectStorageNamespace).toBe("");
  expect(stored.objectStorageBucket).toBe("");
});

test("Object Storage リージョンは認証プロファイルと同じ候補で保存できる", async ({ page }) => {
  await mockApi(page);
  await page.goto("/settings/oci");

  const storageRegion = page.getByRole("combobox", { name: "Object Storage リージョン" });
  await expect(storageRegion).toContainText("ap-osaka-1");

  await storageRegion.click();
  const listbox = page.getByRole("listbox", { name: "Object Storage リージョン" });
  await expect(listbox.getByRole("option")).toHaveText([
    "ap-tokyo-1",
    "ap-osaka-1",
    "us-chicago-1",
  ]);

  await listbox.getByRole("option", { name: "us-chicago-1" }).click();
  await expect(storageRegion).toContainText("us-chicago-1");
  await page.getByRole("button", { name: "Object Storage ネームスペース: 取得" }).click();
  await page.getByLabel("Object Storage バケット").fill("rag-originals");
  await page.getByRole("button", { name: "Object Storage: 下書きを保存" }).click();

  await expect(page.getByRole("button", { name: "Object Storage: 保存しました" })).toBeVisible();
  await expect(page.getByLabel(".env プレビュー")).toContainText(
    "OBJECT_STORAGE_REGION=us-chicago-1"
  );

  const stored = await page.evaluate(() =>
    JSON.parse(window.localStorage.getItem("production-ready-rag.oci-settings.v1") ?? "{}")
  );
  expect(stored.objectStorageRegion).toBe("us-chicago-1");
  expect(stored.objectStorageNamespace).toBe("mytenancynamespace");
  expect(stored.objectStorageBucket).toBe("rag-originals");
});

test("Object Storage 設定は runtime の .env 由来値を初期表示する", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.oci-settings.v1",
      JSON.stringify({
        objectStorageRegion: "ap-tokyo-1",
        objectStorageNamespace: "stale-browser-draft",
        objectStorageBucket: "stale-bucket",
      })
    );
  });
  await mockApi(page, {
    uploadStorageSettings: {
      object_storage_region: "us-chicago-1",
      object_storage_namespace: "env-namespace",
      object_storage_bucket: "env-bucket",
    },
  });
  await page.goto("/settings/oci");

  await expect(
    page.getByRole("textbox", { name: /Object Storage ネームスペース/ })
  ).toHaveValue("env-namespace");
  await expect(page.getByLabel("Object Storage バケット")).toHaveValue("env-bucket");
  await expect(
    page.getByRole("combobox", { name: "Object Storage リージョン" })
  ).toContainText("us-chicago-1");
  await expect(page.getByLabel(".env プレビュー")).toContainText(
    "OBJECT_STORAGE_NAMESPACE=env-namespace"
  );
});

test("Object Storage 入力欄はネームスペースとバケットを1行目、リージョンを2行目に表示する", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await mockApi(page);
  await page.goto("/settings/oci");

  const namespaceField = page.getByRole("textbox", {
    name: /Object Storage ネームスペース/,
  });
  const bucketField = page.getByLabel("Object Storage バケット");
  const regionField = page.getByRole("combobox", {
    name: "Object Storage リージョン",
  });

  const namespaceBox = await namespaceField.boundingBox();
  const bucketBox = await bucketField.boundingBox();
  const regionBox = await regionField.boundingBox();

  expect(namespaceBox).not.toBeNull();
  expect(bucketBox).not.toBeNull();
  expect(regionBox).not.toBeNull();
  expect(Math.abs(namespaceBox!.y - bucketBox!.y)).toBeLessThanOrEqual(2);
  expect(regionBox!.y).toBeGreaterThan(bucketBox!.y + bucketBox!.height);
});

test("Object Storage ネームスペースを OCI API から取得できる", async ({ page }) => {
  let namespaceRequest: unknown;
  await mockApi(page, {
    onObjectStorageNamespaceRead: (body) => {
      namespaceRequest = body;
    },
  });
  await page.goto("/settings/oci");

  await expect(
    page.getByRole("textbox", { name: /Object Storage ネームスペース/ })
  ).not.toBeEditable();
  await page.getByRole("button", { name: "Object Storage ネームスペース: 取得" }).click();

  expect(namespaceRequest).toEqual({
    config_file: "~/.oci/config",
    profile: "DEFAULT",
    region: "ap-osaka-1",
  });
  await expect(
    page.getByRole("textbox", { name: /Object Storage ネームスペース/ })
  ).toHaveValue("mytenancynamespace");
  await expect(
    page.getByRole("button", { name: "Object Storage ネームスペース: 取得しました" })
  ).toBeVisible();
  await expect(page.getByLabel(".env プレビュー")).toContainText(
    "OBJECT_STORAGE_NAMESPACE=mytenancynamespace"
  );
});

test("OCI config の path と profile から認証プロファイル項目へ反映できる", async ({ page }) => {
  const viewport = page.viewportSize();
  if (viewport && viewport.width <= 480) {
    await page.addInitScript(() => {
      window.localStorage.setItem(
        "production-ready-rag.ui",
        JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
      );
    });
  }

  let importRequest: unknown;
  await mockApi(page, {
    onOciConfigRead: (body) => {
      importRequest = body;
    },
  });
  await page.goto("/settings/oci");

  await expect(page.getByText("貼り付け内容")).toHaveCount(0);
  await expect(page.getByLabel("OCI config ファイルを選択", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("コンパートメント OCID")).toHaveCount(0);

  await expect(page.getByLabel("OCI config ファイル")).toHaveValue("~/.oci/config");
  await expect(page.getByLabel("OCI config ファイル")).not.toBeEditable();
  await expect(page.getByLabel("プロファイル")).toHaveValue("DEFAULT");
  await expect(page.getByLabel("プロファイル")).not.toBeEditable();
  await page.getByRole("button", { name: "config から反映" }).click();

  expect(importRequest).toEqual({ config_file: "~/.oci/config", profile: "DEFAULT" });
  await expect(page.getByRole("button", { name: "反映しました" })).toBeVisible();
  await expect(page.getByLabel("プロファイル")).toHaveValue("DEFAULT");
  await expect(page.getByLabel("ユーザー OCID")).toHaveValue("ocid1.user.oc1..prod");
  await expect(page.getByLabel("API キー fingerprint")).toHaveValue("12:34:56:78");
  await expect(page.getByLabel("テナンシ OCID")).toHaveValue("ocid1.tenancy.oc1..prod");
  await expect(page.getByRole("combobox", { name: "リージョン", exact: true })).toContainText("ap-osaka-1");
  await expect(page.locator("#oci-key-file")).toContainText("~/.oci/oci_api_key.pem");
  await expect(page.getByText("/home/app/.oci/prod.pem")).toHaveCount(0);
  await expect(page.getByLabel("コンパートメント OCID")).toHaveCount(0);
});

test("秘密鍵ファイルは固定 path へ上書きアップロードできる", async ({ page }) => {
  let uploadContentType = "";
  await mockApi(page, {
    onOciPrivateKeyUpload: (contentType) => {
      uploadContentType = contentType;
    },
  });
  await page.goto("/settings/oci");

  await expect(page.locator("#oci-key-file")).toContainText("~/.oci/oci_api_key.pem");
  await page.getByLabel("秘密鍵ファイルを選択").setInputFiles({
    name: "oci_api_key.pem",
    mimeType: "application/x-pem-file",
    buffer: Buffer.from("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"),
  });

  await expect(page.locator("#oci-key-file")).toHaveText("~/.oci/oci_api_key.pem");
  await expect(page.getByRole("button", { name: "上書きしました" })).toBeVisible();
  expect(uploadContentType).toContain("multipart/form-data");
});
