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
  onOciSettingsUpdate?: (body: unknown) => void;
  onOciConfigTest?: () => void;
  onObjectStorageNamespaceRead?: (body: unknown) => void;
  onOciObjectStorageUpdate?: (body: unknown) => void;
  onOciPrivateKeyUpload?: (contentType: string) => void;
  ociSettings?: {
    user?: string;
    fingerprint?: string;
    tenancy?: string;
    region?: string;
    key_file_exists?: boolean;
  };
  uploadStorageSettings?: {
    object_storage_region?: string;
    object_storage_namespace?: string;
    object_storage_bucket?: string;
  };
}

async function mockApi(page: Page, options: MockApiOptions = {}) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: authStatus });
      return;
    }

    if (url.pathname === "/api/settings/oci" && method === "GET") {
      await route.fulfill({
        json: {
          data: {
            config_file: "~/.oci/config",
            profile: "DEFAULT",
            user: options.ociSettings?.user ?? "",
            fingerprint: options.ociSettings?.fingerprint ?? "",
            tenancy: options.ociSettings?.tenancy ?? "",
            region: options.ociSettings?.region ?? "us-chicago-1",
            key_file: "~/.oci/oci_api_key.pem",
            key_file_exists: options.ociSettings?.key_file_exists ?? true,
            config_file_exists: true,
            config_source: "runtime",
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (url.pathname === "/api/settings/oci" && method === "PATCH") {
      const body = route.request().postDataJSON() as {
        user?: string;
        fingerprint?: string;
        tenancy?: string;
        region?: string;
      };
      options.onOciSettingsUpdate?.(body);
      await route.fulfill({
        json: {
          data: {
            config_file: "~/.oci/config",
            profile: "DEFAULT",
            user: body.user ?? "",
            fingerprint: body.fingerprint ?? "",
            tenancy: body.tenancy ?? "",
            region: body.region ?? "ap-osaka-1",
            key_file: "~/.oci/oci_api_key.pem",
            key_file_exists: options.ociSettings?.key_file_exists ?? true,
            config_file_exists: true,
            config_source: "runtime",
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (url.pathname === "/api/settings/oci/config/test") {
      options.onOciConfigTest?.();
      await route.fulfill({
        json: {
          data: {
            status: "success",
            profile: "DEFAULT",
            config_file: "~/.oci/config",
            key_file: "~/.oci/oci_api_key.pem",
            config_file_exists: true,
            key_file_exists: true,
            missing_fields: [],
            permission_issues: [],
            oci_directory_mode: "0700",
            config_file_mode: "0600",
            key_file_mode: "0600",
            message: "OCI config と秘密鍵ファイルを確認できました。",
            checked_at: "2026-06-14T00:00:00Z",
            error_type: null,
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (url.pathname === "/api/settings/upload-storage" && method === "GET") {
      await route.fulfill({
        json: {
          data: {
            backend: "local",
            local_storage_dir: "/u01/production-ready-rag",
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

    if (url.pathname === "/api/settings/oci/object-storage" && method === "PATCH") {
      const body = route.request().postDataJSON() as {
        object_storage_region?: string;
        object_storage_namespace?: string;
      };
      options.onOciObjectStorageUpdate?.(body);
      await route.fulfill({
        json: {
          data: {
            backend: "local",
            local_storage_dir: "/u01/production-ready-rag",
            object_storage_region: body.object_storage_region ?? "",
            object_storage_namespace: body.object_storage_namespace ?? "",
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

async function expectActionInsideCard(_page: Page, heading: string, button: Locator) {
  const metrics = await button.evaluate((element, expectedHeading) => {
    const card = element.closest("div.rounded-lg, div.rounded-md");
    if (!card) return null;

    const cardBox = card.getBoundingClientRect();
    const buttonBox = element.getBoundingClientRect();
    const hasHeading = Array.from(card.querySelectorAll("h1,h2,h3,h4,h5,h6")).some(
      (node) => node.textContent?.trim() === expectedHeading
    );

    return {
      hasHeading,
      cardRight: cardBox.x + cardBox.width,
      cardBottom: cardBox.y + cardBox.height,
      buttonRight: buttonBox.x + buttonBox.width,
      buttonBottom: buttonBox.y + buttonBox.height,
    };
  }, heading);

  expect(metrics).not.toBeNull();
  expect(metrics!.hasHeading).toBe(true);
  expect(metrics!.buttonRight).toBeLessThanOrEqual(metrics!.cardRight + 1);
  expect(metrics!.buttonBottom).toBeLessThanOrEqual(metrics!.cardBottom + 1);
}

function operationMemoCard(page: Page) {
  return page
    .getByRole("heading", { name: "運用メモ" })
    .locator(
      "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-lg ')][1]"
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
      name: "OCI 認証設定: OCI 設定を保存",
    });
    const authTestButton = page.getByRole("button", {
      name: "OCI 認証設定: 接続テスト",
    });
    const storageSaveButton = page.getByRole("button", {
      name: "Object Storage: 保存",
    });
    const copyButton = page.getByRole("button", { name: ".env をコピー" });

    await expect(page.getByRole("button", { name: /既定値へ戻す/ })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "サーバー readiness" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "接続確認" })).toHaveCount(0);
    await expectButtonTextContained(authSaveButton);
    await expectButtonTextContained(authTestButton);
    await expectButtonTextContained(storageSaveButton);
    await expectButtonTextContained(copyButton);
    await expect(page.getByRole("button", { name: "JSON をコピー" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "JSON プレビュー" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "運用メモ" })).toBeVisible();
    await expectActionInsideCard(page, "OCI 認証設定", authSaveButton);
    await expectActionInsideCard(page, "OCI 認証設定", authTestButton);
    await expectActionInsideCard(page, "Object Storage", storageSaveButton);
    await expectActionInsideCard(page, ".env プレビュー", copyButton);
  });
}

test("OCI 認証設定の下書きは Object Storage 未入力でも保存できる", async ({ page }) => {
  let savedPayload: unknown;
  let configTestCount = 0;
  await mockApi(page, {
    onOciSettingsUpdate: (body) => {
      savedPayload = body;
    },
    onOciConfigTest: () => {
      configTestCount += 1;
    },
  });
  await page.goto("/settings/oci");

  await page.getByLabel("ユーザー OCID").fill("ocid1.user.oc1..profile");
  await page.getByLabel("フィンガープリント").fill("12:34:56:78:90:ab:cd:ef");
  await page.getByLabel("テナンシ OCID").fill("ocid1.tenancy.oc1..profile");

  await page.getByRole("button", { name: "OCI 認証設定: OCI 設定を保存" }).click();

  await expect(
    page.getByRole("button", { name: "OCI 認証設定: 保存しました" })
  ).toBeVisible();
  await expect(page.getByText("OCI config と秘密鍵ファイルを確認できました。")).toHaveCount(0);
  expect(savedPayload).toEqual({
    user: "ocid1.user.oc1..profile",
    fingerprint: "12:34:56:78:90:ab:cd:ef",
    tenancy: "ocid1.tenancy.oc1..profile",
    region: "us-chicago-1",
  });
  expect(configTestCount).toBe(0);
  await expect(page.getByRole("alert")).toHaveCount(0);

  await page.getByRole("button", { name: "OCI 認証設定: 接続テスト" }).click();
  await expect(page.getByText("OCI config と秘密鍵ファイルを確認できました。")).toBeVisible();
  expect(configTestCount).toBe(1);

  const stored = await page.evaluate(() =>
    JSON.parse(window.localStorage.getItem("production-ready-rag.oci-settings.v1") ?? "{}")
  );
  expect(stored.userOcid).toBe("ocid1.user.oc1..profile");
  expect(stored.configFile).toBe("~/.oci/config");
  expect(stored.configProfile).toBe("DEFAULT");
  expect(stored.compartmentId).toBeUndefined();
  expect(stored.objectStorageRegion).toBe("ap-osaka-1");
  expect(stored.objectStorageNamespace).toBe("");
  expect(stored.objectStorageBucket).toBeUndefined();
});

test("OCI 認証設定は未入力でも保存できる", async ({ page }) => {
  let savedPayload: unknown;
  await mockApi(page, {
    onOciSettingsUpdate: (body) => {
      savedPayload = body;
    },
  });
  await page.goto("/settings/oci");

  const memo = operationMemoCard(page);
  await expect(memo.getByText("ユーザー OCID: 値を入力してください。")).toBeVisible();
  await page.getByRole("button", { name: "OCI 認証設定: OCI 設定を保存" }).click();

  await expect(
    page.getByRole("button", { name: "OCI 認証設定: 保存しました" })
  ).toBeVisible();
  expect(savedPayload).toEqual({
    user: "",
    fingerprint: "",
    tenancy: "",
    region: "us-chicago-1",
  });
});

test("Object Storage 設定は namespace 未取得でも保存できる", async ({ page }) => {
  let savedPayload: unknown;
  await mockApi(page, {
    onOciObjectStorageUpdate: (body) => {
      savedPayload = body;
    },
  });
  await page.goto("/settings/oci");

  const memo = operationMemoCard(page);
  await expect(
    memo.getByText("Object Storage ネームスペース: 値を入力してください。")
  ).toBeVisible();
  await page.getByRole("button", { name: "Object Storage: 保存" }).click();

  await expect(page.getByRole("button", { name: "Object Storage: 保存しました" })).toBeVisible();
  expect(savedPayload).toEqual({
    object_storage_region: "ap-osaka-1",
    object_storage_namespace: "",
  });
});

test("Object Storage リージョンは OCI 認証設定と同じ候補で保存できる", async ({ page }) => {
  let savedPayload: unknown;
  await mockApi(page, {
    onOciObjectStorageUpdate: (body) => {
      savedPayload = body;
    },
  });
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
  await expect(page.getByLabel("Object Storage バケット")).toHaveCount(0);
  await page.getByRole("button", { name: "Object Storage: 保存" }).click();

  await expect(page.getByRole("button", { name: "Object Storage: 保存しました" })).toBeVisible();
  expect(savedPayload).toEqual({
    object_storage_region: "us-chicago-1",
    object_storage_namespace: "mytenancynamespace",
  });
  await expect(page.getByLabel(".env プレビュー")).toContainText(
    "OBJECT_STORAGE_REGION=us-chicago-1"
  );
  await expect(page.getByLabel(".env プレビュー")).not.toContainText("OBJECT_STORAGE_BUCKET");
});

test("Object Storage 設定は runtime の .env 由来値を初期表示する", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.oci-settings.v1",
      JSON.stringify({
        objectStorageRegion: "ap-tokyo-1",
        objectStorageNamespace: "stale-browser-draft",
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
  await expect(page.getByLabel("Object Storage バケット")).toHaveCount(0);
  await expect(
    page.getByRole("combobox", { name: "Object Storage リージョン" })
  ).toContainText("us-chicago-1");
  await expect(page.getByLabel(".env プレビュー")).toContainText(
    "OBJECT_STORAGE_NAMESPACE=env-namespace"
  );
  await expect(page.getByLabel(".env プレビュー")).not.toContainText("OBJECT_STORAGE_BUCKET");
});

test("OCI 認証設定は runtime 由来値を初期表示する", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.oci-settings.v1",
      JSON.stringify({
        userOcid: "ocid1.user.oc1..stale",
        fingerprint: "aa:bb:cc:dd",
        tenancyOcid: "ocid1.tenancy.oc1..stale",
        region: "us-chicago-1",
      })
    );
  });
  await mockApi(page, {
    ociSettings: {
      user: "ocid1.user.oc1..runtime",
      fingerprint: "12:34:56:78",
      tenancy: "ocid1.tenancy.oc1..runtime",
      region: "ap-osaka-1",
      key_file_exists: true,
    },
  });
  await page.goto("/settings/oci");

  await expect(page.getByLabel("ユーザー OCID")).toHaveValue("ocid1.user.oc1..runtime");
  await expect(page.getByLabel("フィンガープリント")).toHaveValue("12:34:56:78");
  await expect(page.getByLabel("テナンシ OCID")).toHaveValue("ocid1.tenancy.oc1..runtime");
  await expect(
    page.getByRole("combobox", { name: "リージョン", exact: true })
  ).toContainText("ap-osaka-1");
  await expect(page.getByLabel(".env プレビュー")).toContainText("OCI_REGION=ap-osaka-1");
});

test("秘密鍵ファイルが無い場合は固定 path の案内を表示する", async ({ page }) => {
  await mockApi(page, {
    ociSettings: {
      key_file_exists: false,
    },
  });
  await page.goto("/settings/oci");

  await expect(
    page.getByText("~/.oci/oci_api_key.pem が見つかりません。")
  ).toBeVisible();
  await page.getByLabel("秘密鍵ファイルを選択").setInputFiles({
    name: "oci_api_key.pem",
    mimeType: "application/x-pem-file",
    buffer: Buffer.from("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"),
  });

  await expect(page.getByText("秘密鍵を読み込みました")).toBeVisible();
  await expect(
    page.getByText("~/.oci/oci_api_key.pem が見つかりません。")
  ).toHaveCount(0);
});

test("Object Storage 入力欄はネームスペースとリージョンだけを表示する", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await mockApi(page);
  await page.goto("/settings/oci");

  const namespaceField = page.getByRole("textbox", {
    name: /Object Storage ネームスペース/,
  });
  const regionField = page.getByRole("combobox", {
    name: "Object Storage リージョン",
  });

  const namespaceBox = await namespaceField.boundingBox();
  const regionBox = await regionField.boundingBox();

  expect(namespaceBox).not.toBeNull();
  expect(regionBox).not.toBeNull();
  await expect(page.getByLabel("Object Storage バケット")).toHaveCount(0);
  expect(Math.abs(namespaceBox!.y - regionBox!.y)).toBeLessThanOrEqual(2);
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

test("OCI config の path と設定名から OCI 認証項目へ反映できる", async ({ page }) => {
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

  await expect(page.getByLabel("OCI 設定ファイルのパス")).toHaveValue("~/.oci/config");
  await expect(page.getByLabel("OCI 設定ファイルのパス")).not.toBeEditable();
  await expect(page.getByLabel("OCI 設定名")).toHaveValue("DEFAULT");
  await expect(page.getByLabel("OCI 設定名")).not.toBeEditable();
  await page.getByRole("button", { name: "config から反映" }).click();

  expect(importRequest).toEqual({ config_file: "~/.oci/config", profile: "DEFAULT" });
  await expect(page.getByRole("button", { name: "反映しました" })).toBeVisible();
  await expect(page.getByLabel("OCI 設定名")).toHaveValue("DEFAULT");
  await expect(page.getByLabel("ユーザー OCID")).toHaveValue("ocid1.user.oc1..prod");
  await expect(page.getByLabel("フィンガープリント")).toHaveValue("12:34:56:78");
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

  await expect(page.locator("#oci-key-file")).toContainText("~/.oci/oci_api_key.pem");
  await expect(page.getByText("秘密鍵を読み込みました")).toBeVisible();
  expect(uploadContentType).toContain("multipart/form-data");
});
