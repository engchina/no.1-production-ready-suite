import { expect, test, type Locator, type Page } from "@playwright/test";
import { expectMainScrollEndsAtContent, expectNoPageOverflow } from "./_helpers";

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

type OciStageStatus = "success" | "failed" | "skipped";
const OCI_STAGE_KEYS = ["config_format", "key_file", "region", "authentication"] as const;
const OCI_TEST_SUCCESS_MESSAGE = "OCI へ認証付きで接続できました（Object Storage GetNamespace）。";

function ociStages(
  statuses: readonly OciStageStatus[],
  failure?: { message: string; action: string }
) {
  return OCI_STAGE_KEYS.map((key, index) => {
    const status = statuses[index] ?? "skipped";
    return {
      key,
      status,
      message:
        status === "failed" && failure
          ? failure.message
          : status === "skipped"
            ? "前の段階が失敗したため実施していません。"
            : `${key} を確認しました。`,
      action: status === "failed" && failure ? failure.action : null,
    };
  });
}

function ociConfigTestFixture(overrides: Record<string, unknown> = {}) {
  return {
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
    message: OCI_TEST_SUCCESS_MESSAGE,
    elapsed_ms: 412,
    checked_at: "2026-06-14T00:00:00Z",
    error_type: null,
    stages: ociStages(["success", "success", "success", "success"]),
    region: "ap-osaka-1",
    auth_check_operation: "Object Storage GetNamespace",
    http_status: null,
    service_code: null,
    request_id: null,
    ...overrides,
  };
}

interface MockApiOptions {
  onOciConfigRead?: (body: unknown) => void;
  onOciSettingsUpdate?: (body: unknown) => void;
  onOciConfigTest?: () => void;
  ociConfigTestResult?: Record<string, unknown>;
  ociConfigTestGate?: Promise<void>;
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
            region: options.ociSettings?.region ?? "",
            key_file: "~/.oci/oci_api_key.pem",
            key_file_exists: options.ociSettings?.key_file_exists ?? false,
            config_file_exists: Boolean(
              options.ociSettings?.user ||
                options.ociSettings?.fingerprint ||
                options.ociSettings?.tenancy ||
                options.ociSettings?.region
            ),
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
            region: body.region ?? "",
            key_file: "~/.oci/oci_api_key.pem",
            key_file_exists: options.ociSettings?.key_file_exists ?? false,
            config_file_exists: Boolean(
              body.user || body.fingerprint || body.tenancy || body.region
            ),
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
      await options.ociConfigTestGate;
      await route.fulfill({
        json: {
          data: options.ociConfigTestResult ?? ociConfigTestFixture(),
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
            local_storage_dir: "/u01/data/production-ready-rag",
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
            local_storage_dir: "/u01/data/production-ready-rag",
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
    await expectNoPageOverflow(page);
    await expectMainScrollEndsAtContent(page);
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
  await page.getByRole("combobox", { name: "リージョン", exact: true }).click();
  await page.getByRole("listbox", { name: "リージョン" }).getByRole("option", {
    name: "us-chicago-1",
  }).click();

  await page.getByRole("button", { name: "OCI 認証設定: OCI 設定を保存" }).click();

  await expect(
    page.getByRole("button", { name: "OCI 認証設定: 保存しました" })
  ).toBeVisible();
  await expect(page.getByText(OCI_TEST_SUCCESS_MESSAGE)).toHaveCount(0);
  expect(savedPayload).toEqual({
    user: "ocid1.user.oc1..profile",
    fingerprint: "12:34:56:78:90:ab:cd:ef",
    tenancy: "ocid1.tenancy.oc1..profile",
    region: "us-chicago-1",
  });
  expect(configTestCount).toBe(0);
  await expect(page.getByRole("alert")).toHaveCount(0);

  await page.getByRole("button", { name: "OCI 認証設定: 接続テスト" }).click();
  const ociResult = page.getByTestId("settings-oci-test-result");
  await expect(ociResult.getByText(OCI_TEST_SUCCESS_MESSAGE)).toBeVisible();
  await expect(ociResult).toHaveAttribute("data-tone", "success");
  const stagesList = ociResult.getByRole("list", { name: "確認段階" });
  await expect(stagesList.getByRole("listitem")).toHaveCount(4);
  await expect(stagesList.locator('[data-stage-status="success"]')).toHaveCount(4);
  for (const label of ["設定の形式", "鍵の読み取り", "リージョン到達", "認証（API 応答）"]) {
    await expect(stagesList).toContainText(label);
  }
  await expect(ociResult).toContainText("Object Storage GetNamespace");
  await expect(ociResult).not.toContainText("確認ポイント");
  expect(configTestCount).toBe(1);

  const stored = await page.evaluate(() =>
    JSON.parse(window.localStorage.getItem("production-ready-rag.oci-settings.v1") ?? "{}")
  );
  expect(stored.userOcid).toBeUndefined();
  expect(stored.configFile).toBeUndefined();
  expect(stored.configProfile).toBeUndefined();
  expect(stored.compartmentId).toBeUndefined();
  expect(stored.objectStorageRegion).toBeUndefined();
  expect(stored.objectStorageNamespace).toBeUndefined();
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
    region: "",
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
    object_storage_region: "",
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
  await expect(storageRegion).toContainText("選択してください");

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

test("OCI 設定の必須表示は共有の「必須」タグにそろい、記号の * を出さない", async ({ page }) => {
  await mockApi(page);
  await page.goto("/settings/oci");

  // 共有 TextField と独自入力（設定ファイルのパス / ネームスペース）で同じ表示・同じ読み上げ名になる
  for (const label of ["OCI 設定ファイルのパス", "ユーザー OCID", "Object Storage ネームスペース"]) {
    const input = page.getByRole("textbox", { name: label, exact: true });
    await expect(input).toHaveAttribute("aria-required", "true");
    await expect(page.locator(`label[for="${await input.getAttribute("id")}"]`)).toContainText(
      "必須"
    );
  }
  // 秘密鍵のドロップゾーンは button で aria-required を持てないため、タグを読み上げ名に含める
  await expect(page.getByRole("button", { name: /^秘密鍵\s*必須/ })).toBeVisible();
  await expect(page.locator("main label").filter({ hasText: "*" })).toHaveCount(0);
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
  await page.getByRole("combobox", { name: "Object Storage リージョン" }).click();
  await page.getByRole("listbox", { name: "Object Storage リージョン" }).getByRole("option", {
    name: "ap-osaka-1",
  }).click();
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

const OCI_CONNECTIVITY_FAILURES = [
  {
    name: "形式不正",
    statuses: ["failed", "skipped", "skipped", "skipped"],
    message: "OCI config の形式が正しくありません（fingerprint）。",
    action: "fingerprint は OCI コンソールの API キーに表示される 16 バイトのコロン区切りを設定してください。",
    extra: { auth_check_operation: null },
    detail: null,
  },
  {
    name: "認証失敗",
    statuses: ["success", "success", "success", "failed"],
    message: "OCI が認証を拒否しました（401 NotAuthenticated）。",
    action: "fingerprint が OCI コンソールの API キーと一致しているかを確認してください。",
    extra: {
      error_type: "ServiceError",
      http_status: 401,
      service_code: "NotAuthenticated",
      request_id: "E2EREQUEST401",
    },
    detail: "opc-request-id E2EREQUEST401",
  },
  {
    name: "権限不足",
    statuses: ["success", "success", "success", "failed"],
    message: "OCI がアクセスを許可しませんでした（404 NotAuthorizedOrNotFound）。",
    action: "IAM ポリシーでこのユーザーのグループに必要な権限が付与されているかを確認してください。",
    extra: {
      error_type: "ServiceError",
      http_status: 404,
      service_code: "NotAuthorizedOrNotFound",
      request_id: "E2EREQUEST404",
    },
    detail: "HTTP 404",
  },
  {
    name: "タイムアウト",
    statuses: ["success", "success", "failed", "skipped"],
    message: "ap-osaka-1 の OCI endpoint への接続が 5 秒以内に完了しませんでした。",
    action: "リージョン名（ap-osaka-1）と、バックエンドから OCI への HTTPS 通信を確認してください。",
    extra: { error_type: "ConnectTimeout" },
    detail: null,
  },
] as const;

for (const failure of OCI_CONNECTIVITY_FAILURES) {
  test(`OCI 接続テストの${failure.name}を段階・次の対処つきで表示する`, async ({ page }) => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let configTestCount = 0;
    await mockApi(page, {
      ociSettings: {
        user: "ocid1.user.oc1..saved",
        fingerprint: "12:34:56:78:90:ab:cd:ef:12:34:56:78:90:ab:cd:ef",
        tenancy: "ocid1.tenancy.oc1..saved",
        region: "ap-osaka-1",
        key_file_exists: true,
      },
      onOciConfigTest: () => {
        configTestCount += 1;
      },
      ociConfigTestGate: gate,
      ociConfigTestResult: ociConfigTestFixture({
        status: "failed",
        message: failure.message,
        stages: ociStages(failure.statuses, { message: failure.message, action: failure.action }),
        ...failure.extra,
      }),
    });
    await page.goto("/settings/oci");

    const testButton = page.getByRole("button", { name: "OCI 認証設定: 接続テスト" });
    await testButton.click();
    // loading 中もラベルは変えない（先頭アイコンだけがスピナーになる）。
    await expect(testButton).toHaveAccessibleName("OCI 認証設定: 接続テスト");
    await expect(testButton).toContainText("接続テスト");
    release();

    const ociResult = page.getByTestId("settings-oci-test-result");
    await expect(ociResult).toHaveAttribute("data-tone", "danger");
    await expect(ociResult).toHaveAttribute("role", "alert");
    await expect(ociResult).toContainText(failure.message);
    const stagesList = ociResult.getByRole("list", { name: "確認段階" });
    for (const [index, key] of OCI_STAGE_KEYS.entries()) {
      await expect(stagesList.locator(`[data-stage="${key}"]`)).toHaveAttribute(
        "data-stage-status",
        failure.statuses[index]
      );
    }
    await expect(stagesList.locator('[data-stage-status="failed"]')).toContainText("失敗");
    if ((failure.statuses as readonly OciStageStatus[]).includes("skipped")) {
      await expect(stagesList.locator('[data-stage-status="skipped"]').first()).toContainText(
        "未実施"
      );
    }
    await expect(ociResult).toContainText("確認ポイント");
    await expect(ociResult).toContainText(failure.action);
    if (failure.detail) await expect(ociResult).toContainText(failure.detail);
    expect(configTestCount).toBe(1);
    await expectNoPageOverflow(page);
  });
}
