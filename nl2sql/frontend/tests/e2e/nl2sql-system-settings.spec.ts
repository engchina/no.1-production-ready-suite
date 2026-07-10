import { expect, test, type Page, type Route } from "@playwright/test";

function settingsEnvelope(data: unknown) {
  return {
    data,
    error_messages: [],
    warning_messages: [],
  };
}

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(settingsEnvelope(data)),
  });
}

async function mockNl2sqlSettingsApi(page: Page) {
  const ociSettings = {
    config_file: "~/.oci/config",
    profile: "DEFAULT",
    user: "ocid1.user.oc1..example",
    fingerprint: "12:34:56:78:90:ab:cd:ef",
    tenancy: "ocid1.tenancy.oc1..example",
    region: "ap-osaka-1",
    key_file: "~/.oci/oci_api_key.pem",
    key_file_exists: true,
    config_file_exists: true,
    config_source: "runtime",
  };

  const uploadStorage = {
    backend: "local",
    local_storage_dir: "/u01/production-ready-nl2sql",
    object_storage_region: "ap-osaka-1",
    object_storage_namespace: "exampletenancy",
    object_storage_bucket: "nl2sql-originals",
    readiness: "ok",
    max_upload_bytes: 104857600,
    config_source: "runtime",
  };

  const modelSettings = {
    settings: {
      enterprise_ai: {
        endpoint: "https://enterprise-ai.example.com",
        project_ocid: "ocid1.generativeaiproject.oc1.ap-osaka-1.example",
        api_key: "",
        has_api_key: true,
        clear_api_key: false,
        models: [
          {
            model_id: "enterprise-nl2sql-llm",
            display_name: "業務 NL2SQL 標準",
            vision_enabled: false,
          },
          {
            model_id: "enterprise-nl2sql-vlm",
            display_name: "OCR / Vision",
            vision_enabled: true,
          },
        ],
        default_model_id: "enterprise-nl2sql-llm",
        api_path: "/responses",
        vlm_input_mode: "auto",
        text_payload_template: "",
        vision_payload_template: "",
        text_response_path: "",
        vision_response_path: "",
        timeout_seconds: 120,
        max_retries: 3,
      },
      generative_ai: {
        embedding_model: "cohere.embed-v4.0",
        embedding_dim: 1536,
        rerank_model: "cohere.rerank-v4.0-fast",
      },
    },
    checks: {
      enterprise_ai: "ok",
      generative_ai: "ok",
      embedding_dim: "ok",
    },
    model_settings_file: "runtime-settings",
    source: "runtime",
  };

  const databaseSettings = {
    user: "NL2SQL_APP",
    dsn: "nl2sqldb_high",
    wallet_dir: "/u01/aipoc/instantclient_23_26/network/admin",
    wallet_uploaded: true,
    available_services: ["nl2sqldb_high", "nl2sqldb_low"],
    has_password: true,
    has_wallet_password: false,
    readiness: "ok",
    embedding_dimension: 1536,
    vector_column: "VECTOR(1536, FLOAT32)",
    adb_ocid: "ocid1.autonomousdatabase.oc1.ap-osaka-1.example",
    region: "ap-osaka-1",
    config_source: "runtime",
  };

  const adbInfo = {
    status: "success",
    message: "ADB OCID が設定されています。",
    id: "ocid1.autonomousdatabase.oc1.ap-osaka-1.example",
    display_name: "nl2sqldb",
    lifecycle_state: "AVAILABLE",
    db_name: "NL2SQLDB",
    cpu_core_count: 2,
    data_storage_size_in_tbs: 1,
    region: "ap-osaka-1",
  };

  await page.route("**/api/settings/oci", (route) => fulfillJson(route, ociSettings));
  await page.route("**/api/settings/oci/config/test", (route) =>
    fulfillJson(route, {
      status: "success",
      profile: "DEFAULT",
      config_file: "~/.oci/config",
      key_file: "~/.oci/oci_api_key.pem",
      config_file_exists: true,
      key_file_exists: true,
      missing_fields: [],
      permission_issues: [],
      oci_directory_mode: "700",
      config_file_mode: "600",
      key_file_mode: "600",
      message: "OCI config を確認しました。",
      checked_at: "2026-06-21T10:00:00.000Z",
      error_type: null,
    })
  );
  await page.route("**/api/settings/oci/object-storage", (route) =>
    fulfillJson(route, uploadStorage)
  );
  await page.route("**/api/settings/oci/object-storage/namespace", (route) =>
    fulfillJson(route, { namespace: "exampletenancy" })
  );
  await page.route("**/api/settings/oci/config/read", (route) =>
    fulfillJson(route, {
      profile: "DEFAULT",
      user: ociSettings.user,
      fingerprint: ociSettings.fingerprint,
      tenancy: ociSettings.tenancy,
      region: ociSettings.region,
      key_file: ociSettings.key_file,
      applied_fields: ["user", "fingerprint", "tenancy", "region", "key_file"],
    })
  );
  await page.route("**/api/settings/oci/key-file", (route) =>
    fulfillJson(route, { key_file: "~/.oci/oci_api_key.pem", saved: true })
  );

  await page.route("**/api/settings/upload-storage", (route) =>
    fulfillJson(route, uploadStorage)
  );
  await page.route("**/api/settings/model", (route) => fulfillJson(route, modelSettings));
  await page.route("**/api/settings/model/test", (route) =>
    fulfillJson(route, {
      status: "success",
      target_type: "enterprise_text",
      model_id: "enterprise-nl2sql-llm",
      message: "enterprise-nl2sql-llm の設定を確認しました。",
      troubleshooting: [],
      raw_error: null,
      error_type: null,
      elapsed_ms: 12,
      checked_at: "2026-06-21T10:00:00.000Z",
      details: { network_call: false },
    })
  );

  await page.route("**/api/settings/database", (route) =>
    fulfillJson(route, databaseSettings)
  );
  await page.route("**/api/settings/database/test", (route) =>
    fulfillJson(route, {
      status: "skipped",
      readiness: "ok",
      message: "入力値の形式のみ確認します。",
      elapsed_ms: 1,
      troubleshooting: [],
      details: { network_call: false },
      checked_at: "2026-06-21T10:00:00.000Z",
      error_type: null,
    })
  );
  await page.route("**/api/settings/database/wallet", (route) =>
    fulfillJson(route, databaseSettings)
  );
  await page.route("**/api/settings/database/adb", (route) => fulfillJson(route, adbInfo));
  await page.route("**/api/settings/database/adb/settings", (route) =>
    fulfillJson(route, adbInfo)
  );
  await page.route("**/api/settings/database/adb/start", (route) =>
    fulfillJson(route, adbInfo)
  );
  await page.route("**/api/settings/database/adb/stop", (route) =>
    fulfillJson(route, adbInfo)
  );

  await page.route("**/api/nl2sql/diagnostics", (route) =>
    fulfillJson(route, { readiness: [], checks: [] })
  );
}

async function expectNoHorizontalOverflow(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1
      )
    )
    .toBeTruthy();
}

async function expectNl2sqlShellFillsViewport(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const main = document.querySelector("main");
        const sidebar = document.querySelector(".sidebar-shell");
        if (!main || !sidebar) return false;

        const viewportBottom = window.innerHeight;
        const mainBottomGap = viewportBottom - main.getBoundingClientRect().bottom;
        const sidebarBottomGap = viewportBottom - sidebar.getBoundingClientRect().bottom;
        return (
          Math.abs(mainBottomGap) <= 1 &&
          Math.abs(sidebarBottomGap) <= 1
        );
      })
    )
    .toBeTruthy();
}

async function expectNoExcessBottomWhitespace(page: Page) {
  const metrics = await page.evaluate(() => {
    const main = document.querySelector("main");
    const scroller = main instanceof HTMLElement ? main : null;
    if (!scroller) {
      throw new Error("main scroller が見つかりません。");
    }

    scroller.scrollTo({ top: scroller.scrollHeight, behavior: "instant" });
    const mainBottom = scroller.getBoundingClientRect().bottom;
    const cards = Array.from(scroller.querySelectorAll("div")).filter((element) => {
      const className = element.getAttribute("class") ?? "";
      return className.includes("border-border") && className.includes("bg-card");
    });
    if (cards.length === 0) {
      throw new Error("設定カードが見つかりません。");
    }

    const lastVisibleCardBottom = Math.max(
      ...cards
        .map((card) => card.getBoundingClientRect())
        .filter((rect) => rect.width > 0 && rect.height > 0)
        .map((rect) => rect.bottom)
    );

    return {
      bottomWhitespace: Math.round(mainBottom - lastVisibleCardBottom),
      scrollHeight: scroller.scrollHeight,
      clientHeight: scroller.clientHeight,
    };
  });

  expect(metrics.bottomWhitespace).toBeGreaterThanOrEqual(0);
  expect(metrics.bottomWhitespace).toBeLessThanOrEqual(96);
}

async function expectOciConfigFieldsAboveOcidFields(page: Page) {
  const configFile = page.getByLabel("OCI 設定ファイルのパス");
  const configProfile = page.getByLabel("OCI プロファイル");
  const userOcid = page.getByLabel("ユーザー OCID");
  const tenancyOcid = page.getByLabel("テナンシ OCID");

  await expect(configFile).toBeVisible();
  await expect(configProfile).toBeVisible();
  await expect(userOcid).toBeVisible();
  await expect(tenancyOcid).toBeVisible();

  const boxes = await Promise.all([
    configFile.boundingBox(),
    configProfile.boundingBox(),
    userOcid.boundingBox(),
    tenancyOcid.boundingBox(),
  ]);

  if (boxes.some((box) => box === null)) {
    throw new Error("OCI 認証フォームの入力欄位置を取得できません。");
  }

  const [configFileBox, configProfileBox, userOcidBox, tenancyOcidBox] = boxes as [
    NonNullable<(typeof boxes)[number]>,
    NonNullable<(typeof boxes)[number]>,
    NonNullable<(typeof boxes)[number]>,
    NonNullable<(typeof boxes)[number]>,
  ];
  const configFieldsBottomRow = Math.max(configFileBox.y, configProfileBox.y);
  const ocidFieldsTopRow = Math.min(userOcidBox.y, tenancyOcidBox.y);

  expect(configFieldsBottomRow).toBeLessThan(ocidFieldsTopRow);
}

test.beforeEach(async ({ page }) => {
  await mockNl2sqlSettingsApi(page);
});

test("OCI 認証設定はブラウザ草稿のダミー値を runtime 空値で上書きする", async ({ page }) => {
  await page.addInitScript(() => {
    const staleDraft = JSON.stringify({
      userOcid: "ocid1.user.oc1..aaaaaaaa",
      fingerprint: "12:34:56:78:90:ab:cd:ef",
      tenancyOcid: "ocid1.tenancy.oc1..aaaaaaaa",
      region: "us-chicago-1",
      objectStorageRegion: "ap-osaka-1",
      objectStorageNamespace: "fake-namespace",
    });
    window.localStorage.setItem("production-ready-rag.oci-settings.v1", staleDraft);
    window.localStorage.setItem("production-ready-nl2sql.oci-settings.v1", staleDraft);
  });
  await page.route("**/api/settings/oci", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await fulfillJson(route, {
      config_file: "~/.oci/config",
      profile: "DEFAULT",
      user: "",
      fingerprint: "",
      tenancy: "",
      region: "",
      key_file: "~/.oci/oci_api_key.pem",
      key_file_exists: false,
      config_file_exists: false,
      config_source: "runtime",
    });
  });
  await page.route("**/api/settings/upload-storage", async (route) => {
    await fulfillJson(route, {
      backend: "local",
      local_storage_dir: "/u01/production-ready-nl2sql",
      object_storage_region: "",
      object_storage_namespace: "",
      object_storage_bucket: "nl2sql-originals",
      readiness: "ok",
      max_upload_bytes: 104857600,
      config_source: "runtime",
    });
  });

  await page.goto("/settings/oci");

  await expect(page.getByLabel("ユーザー OCID")).toHaveValue("");
  await expect(page.getByLabel("フィンガープリント")).toHaveValue("");
  await expect(page.getByLabel("テナンシ OCID")).toHaveValue("");
  await expect(
    page.getByRole("textbox", { name: /Object Storage ネームスペース/ })
  ).toHaveValue("");
  await expect(page.getByLabel(".env プレビュー")).not.toContainText("aaaaaaaa");
  await expect(page.getByLabel(".env プレビュー")).not.toContainText("fake-namespace");
});

test("NL2SQL のシステム設定画面を表示できる", async ({ page }) => {
  await page.goto("/settings/oci");
  await expect(page.getByRole("heading", { name: "OCI 認証設定" }).first()).toBeVisible();
  await expect(page.getByLabel("ユーザー OCID")).toBeVisible();
  await expectOciConfigFieldsAboveOcidFields(page);
  await expectNl2sqlShellFillsViewport(page);
  await expectNoExcessBottomWhitespace(page);
  await expectNoHorizontalOverflow(page);

  await page.goto("/settings/upload-storage");
  await expect(page.getByRole("heading", { name: "アップロード保存先" }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "保存先", exact: true })).toBeVisible();
  await expect(page.getByLabel("ローカル保存ディレクトリ")).toHaveValue(
    "/u01/production-ready-nl2sql"
  );
  await page.getByRole("radio", { name: /OCI Object Storage/ }).check();
  await expect(page.getByLabel("Object Storage バケット")).toHaveValue("nl2sql-originals");
  await expectNoHorizontalOverflow(page);

  await page.goto("/settings/model");
  await expect(page.getByRole("heading", { name: "モデル設定" }).first()).toBeVisible();
  await expect(page.getByText("OCI Enterprise AI", { exact: true })).toBeVisible();
  await expect(page.getByText("OCI Generative AI", { exact: true })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.goto("/settings/database");
  await expect(page.getByRole("heading", { name: "データベース設定" }).first()).toBeVisible();
  await expect(page.getByLabel("データベースユーザー")).toBeVisible();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("操作履歴")).toBeVisible();
  await expect(page.getByText("ADB OCID が設定されています。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("旧 NL2SQL 接続診断ルートはエンジン運用に集約される", async ({ page }) => {
  await page.goto("/settings/nl2sql-connection");
  await expect(page).toHaveURL(/\/engine-operations$/);
  await expect(page.getByRole("heading", { name: "エンジン運用" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
