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

// 画面は platform の共有パッケージ（NL2SQL と同じ画面。#100）。画面の詳細な挙動は NL2SQL の e2e が確認するため、
// ここでは RAG の API mock とつないだときの主要な流れと、desktop / 375px のレイアウトを確認する。

const VALID_AUTH = {
  user: "ocid1.user.oc1..aaaaaaaa",
  fingerprint: "12:34:56:78:90:ab:cd:ef",
  tenancy: "ocid1.tenancy.oc1..aaaaaaaa",
  region: "ap-osaka-1",
  key_file_exists: true,
};

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`OCI 認証設定は runtime の値を表示し、プレビュー欄を持たない (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockApi(page, {
      ociSettings: VALID_AUTH,
      uploadStorageSettings: { object_storage_region: "ap-tokyo-1", object_storage_namespace: "env-namespace" },
    });

    await page.goto("/settings/oci");

    await expect(page.getByLabel("ユーザー OCID")).toHaveValue(VALID_AUTH.user);
    await expect(page.getByLabel("テナンシ OCID")).toHaveValue(VALID_AUTH.tenancy);
    await expect(page.getByRole("textbox", { name: "Object Storage ネームスペース" })).toHaveValue("env-namespace");
    await expect(page.getByRole("button", { name: ".env をコピー" })).toHaveCount(0);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test("OCI 認証設定を保存すると入力値を PATCH で送る", async ({ page }) => {
  let saved: unknown = null;
  await mockApi(page, {
    ociSettings: VALID_AUTH,
    onOciSettingsUpdate: (body) => {
      saved = body;
    },
  });

  await page.goto("/settings/oci");
  await page.getByLabel("ユーザー OCID").fill("ocid1.user.oc1..bbbbbbbb");
  await page.getByRole("button", { name: "OCI 認証設定: OCI 設定を保存" }).click();

  await expect.poll(() => saved).toMatchObject({
    user: "ocid1.user.oc1..bbbbbbbb",
    fingerprint: VALID_AUTH.fingerprint,
    tenancy: VALID_AUTH.tenancy,
    region: VALID_AUTH.region,
  });
});

test("OCI 認証設定は必須項目が空なら保存前に止める", async ({ page }) => {
  let patchCount = 0;
  await mockApi(page, {
    onOciSettingsUpdate: () => {
      patchCount += 1;
    },
  });

  await page.goto("/settings/oci");
  await expect(page.getByLabel("ユーザー OCID")).toHaveValue("");
  await page.getByRole("button", { name: "OCI 認証設定: OCI 設定を保存" }).click();

  await expect(page.getByText("値を入力してください。").first()).toBeVisible();
  expect(patchCount).toBe(0);
});

test("Object Storage ネームスペースを OCI API から取得できる", async ({ page }) => {
  let requested: unknown = null;
  await mockApi(page, {
    ociSettings: VALID_AUTH,
    uploadStorageSettings: { object_storage_region: "ap-osaka-1" },
    onObjectStorageNamespaceRead: (body) => {
      requested = body;
    },
  });

  await page.goto("/settings/oci");
  await page.getByRole("button", { name: "Object Storage ネームスペース: 取得" }).click();

  await expect.poll(() => requested).toMatchObject({ region: "ap-osaka-1" });
  await expect(page.getByRole("textbox", { name: "Object Storage ネームスペース" })).not.toHaveValue("");
});

test("OCI config から認証項目を反映できる", async ({ page }) => {
  let requested: unknown = null;
  await mockApi(page, {
    onOciConfigRead: (body) => {
      requested = body;
    },
  });

  await page.goto("/settings/oci");
  await page.getByRole("button", { name: /config から反映/ }).click();

  await expect.poll(() => requested).toMatchObject({ config_file: "~/.oci/config", profile: "DEFAULT" });
  await expect(page.getByLabel("ユーザー OCID")).not.toHaveValue("");
});

test("OCI 接続テストの失敗を段階つきで表示する", async ({ page }) => {
  await mockApi(page, {
    ociSettings: VALID_AUTH,
    ociConfigTestResult: ociConfigTestFixture({
      status: "failed",
      message: "OCI config の必須項目が不足しています。",
      stages: ociStages(["failed"], {
        message: "OCI config の必須項目が不足しています。",
        action: "不足している項目を入力して認証設定を保存してください。",
      }),
    }),
  });

  await page.goto("/settings/oci");
  await page.getByRole("button", { name: /接続テスト/ }).click();

  await expect(page.getByTestId("settings-oci-test-stages")).toBeVisible();
  await expect(page.getByText("OCI config の必須項目が不足しています。").first()).toBeVisible();
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
