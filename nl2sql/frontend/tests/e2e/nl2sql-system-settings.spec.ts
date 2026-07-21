import { expect, test, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

function settingsEnvelope(data: unknown) {
  return {
    data,
    error_messages: [],
    warning_messages: [],
  };
}

function databaseSettingsFixture(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

function modelSettingsFixture(overrides: Record<string, unknown> = {}) {
  return {
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
    secret_source: "environment",
    legacy_secret_detected: false,
    ...overrides,
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

  const modelSettings = modelSettingsFixture();

  const databaseSettings = databaseSettingsFixture();

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
  await page.route("**/api/settings/database/system-tables", (route) =>
    fulfillJson(route, {
      status: "ready",
      schema_head: 6,
      applied_versions: [0, 1, 2, 3, 5, 6],
      pending_versions: [],
      expected_object_count: 49,
      existing_object_count: 49,
      missing_objects: [],
      tables: [
        {
          name: "NL2SQL_PROFILES",
          exists: true,
          estimated_rows: 3,
          created_at: "2026-07-19T00:00:00Z",
          last_analyzed_at: "2026-07-19T00:00:00Z",
        },
      ],
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
  await page.route("**/api/schema/owners", (route) =>
    fulfillJson(route, {
      current_owner: "APP",
      owners: [
        { owner: "APP", is_current: true, table_count: 8, view_count: 2 },
        { owner: "SH", is_current: false, table_count: 12, view_count: 1 },
      ],
      excluded_oracle_maintained_count: 29,
    })
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
  await page.route("**/api/settings/database/wallet/download", (route) =>
    fulfillJson(route, { status: "already_configured", settings: databaseSettings })
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
  await mockDatabaseGateReady(page);
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
  await expect(page.getByText("現在の接続ユーザー")).toBeVisible();
  await expect(page.getByText("APP", { exact: true })).toBeVisible();
  await expect(page.getByText("2 schema", { exact: true })).toBeVisible();
  await expect(
    page.getByText(/データベース接続ユーザーの権限がシステム上限/)
  ).toBeVisible();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("操作履歴")).toBeVisible();
  await expect(page.getByText("ADB OCID が設定されています。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("モデル API Key を .env に新規保存して削除でき、JSON preview に含めない", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.unroute("**/api/settings/model");
  const missing = modelSettingsFixture({
    settings: {
      ...modelSettingsFixture().settings,
      enterprise_ai: {
        ...modelSettingsFixture().settings.enterprise_ai,
        has_api_key: false,
      },
    },
    checks: {
      enterprise_ai: "missing",
      generative_ai: "ok",
      embedding_dim: "ok",
    },
    secret_source: "missing",
  });
  const requests: Array<Record<string, unknown>> = [];
  await page.route("**/api/settings/model", async (route) => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      requests.push(body);
      const enterprise = body.enterprise_ai as Record<string, unknown>;
      const cleared = enterprise.clear_api_key === true;
      await fulfillJson(
        route,
        modelSettingsFixture({
          settings: {
            ...missing.settings,
            enterprise_ai: {
              ...missing.settings.enterprise_ai,
              api_key: "",
              has_api_key: !cleared,
              clear_api_key: false,
            },
          },
          secret_source: cleared ? "missing" : "environment",
        })
      );
      return;
    }
    await fulfillJson(route, missing);
  });

  await page.goto("/settings/model");
  await page.getByLabel("API key", { exact: true }).fill("new-key-fixture");
  await expect(page.getByLabel(".env プレビュー")).toContainText(
    "OCI_ENTERPRISE_AI_API_KEY=\"<入力済み secret>\""
  );
  await expect(page.getByLabel("JSON プレビュー")).toContainText('"version": 2');
  await expect(page.getByLabel("JSON プレビュー")).not.toContainText("api_key");
  await expect(page.getByLabel("JSON プレビュー")).not.toContainText("new-key-fixture");
  await page.getByRole("button", { name: "モデル設定: 保存" }).click();
  await expect(page.getByText("モデル設定を保存しました。")).toBeVisible();
  expect((requests[0].enterprise_ai as Record<string, unknown>).api_key).toBe(
    "new-key-fixture"
  );

  await page.getByLabel("保存済み API key を削除する").check();
  await page.getByRole("button", { name: "モデル設定: 保存" }).click();
  expect((requests[1].enterprise_ai as Record<string, unknown>).clear_api_key).toBe(true);
  await expect(page.getByText("API Key 保存先: 未設定")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("legacy JSON の原因と復旧方法を表示し、保存時に既存 Key を保持して移行する", async ({
  page,
}) => {
  await page.unroute("**/api/settings/model");
  const legacy = modelSettingsFixture({
    secret_source: "legacy_json",
    legacy_secret_detected: true,
  });
  const savedRequests: Array<Record<string, unknown>> = [];
  await page.route("**/api/settings/model", async (route) => {
    if (route.request().method() === "PATCH") {
      savedRequests.push(route.request().postDataJSON() as Record<string, unknown>);
      await fulfillJson(route, modelSettingsFixture());
      return;
    }
    await fulfillJson(route, legacy);
  });

  await page.goto("/settings/model");
  await expect(page.getByText("旧 JSON に API Key が残っています")).toBeVisible();
  await expect(page.getByText(/原因: v1 の model-settings.json/)).toBeVisible();
  await expect(page.getByText("API Key 保存先: 旧 JSON（移行が必要）")).toBeVisible();
  await page.getByRole("button", { name: "モデル設定: 保存" }).click();

  expect(savedRequests).toHaveLength(1);
  const enterprise = savedRequests[0]?.enterprise_ai as Record<string, unknown>;
  expect(enterprise.api_key).toBe("");
  expect(enterprise.has_api_key).toBe(true);
  expect(enterprise.clear_api_key).toBe(false);
  await expect(page.getByText("旧 JSON に API Key が残っています")).toHaveCount(0);
  await expect(page.getByText("API Key 保存先: backend/.env")).toBeVisible();
});

test("Wallet 不足時はページ表示ごとに OCI 自動取得を一度だけ実行する", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const incompleteSettings = databaseSettingsFixture({
    wallet_uploaded: false,
    available_services: [],
    readiness: "wallet_not_found",
  });
  const downloadedSettings = databaseSettingsFixture();
  let downloadCount = 0;
  let notifyStarted: (() => void) | undefined;
  let releaseDownload: (() => void) | undefined;
  const started = new Promise<void>((resolve) => {
    notifyStarted = resolve;
  });
  const downloadGate = new Promise<void>((resolve) => {
    releaseDownload = resolve;
  });

  await page.route("**/api/settings/database", (route) =>
    fulfillJson(route, incompleteSettings)
  );
  await page.route("**/api/settings/database/wallet/download", async (route) => {
    downloadCount += 1;
    notifyStarted?.();
    await downloadGate;
    await fulfillJson(route, { status: "downloaded", settings: downloadedSettings });
  });

  await page.goto("/settings/database");
  await started;
  const pendingStatus = page
    .getByRole("status")
    .filter({ hasText: "OCI から Wallet を取得し、サーバーへ安全に設定しています…" });
  await expect(pendingStatus).toBeVisible();
  await expect(page.getByText("`.zip` Wallet ファイルをアップロード")).toBeVisible();

  releaseDownload?.();

  const successToast = page.getByText(
    "Oracle Wallet を OCI から取得し、サーバーへ設定しました。"
  );
  await expect(successToast).toBeVisible();
  await expect(
    successToast.locator("xpath=ancestor::*[@aria-live='polite'][1]")
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.activeElement?.closest("[aria-live]") != null)
  ).toBe(false);
  await expect(page.getByText("設定済み", { exact: true })).toBeVisible();
  await expect.poll(() => downloadCount).toBe(1);
  await expectNoHorizontalOverflow(page);
});

test("Wallet 自動取得の失敗を保持し、キーボードで再取得できる", async ({ page }) => {
  const incompleteSettings = databaseSettingsFixture({
    wallet_uploaded: false,
    available_services: [],
    readiness: "wallet_not_found",
  });
  let downloadCount = 0;
  await page.route("**/api/settings/database", (route) =>
    fulfillJson(route, incompleteSettings)
  );
  await page.route("**/api/settings/database/wallet/download", async (route) => {
    downloadCount += 1;
    if (downloadCount === 1) {
      await route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({
          detail:
            "OCI から Wallet を取得できませんでした。IAM 権限を確認して再試行するか、Wallet ZIP を手動アップロードしてください。",
        }),
      });
      return;
    }
    await fulfillJson(route, {
      status: "downloaded",
      settings: databaseSettingsFixture(),
    });
  });

  await page.goto("/settings/database");

  const retry = page.getByRole("button", { name: "OCI から Wallet を再取得" });
  await expect(retry).toBeVisible();
  await expect(
    page.getByRole("alert").filter({ hasText: /IAM 権限を確認して再試行/ })
  ).toBeVisible();
  await expect(page.getByText("`.zip` Wallet ファイルをアップロード")).toBeVisible();
  await retry.focus();
  await expect(retry).toBeFocused();
  await expect
    .poll(() => retry.evaluate((element) => getComputedStyle(element).outlineStyle))
    .not.toBe("none");
  await retry.press("Enter");

  await expect(
    page.getByText("Oracle Wallet を OCI から取得し、サーバーへ設定しました。")
  ).toBeVisible();
  await expect.poll(() => downloadCount).toBe(2);
  await expect(retry).toHaveCount(0);
});

test("有効な Wallet がある場合は OCI 自動取得を呼ばない", async ({ page }) => {
  let downloadCount = 0;
  await page.route("**/api/settings/database/wallet/download", async (route) => {
    downloadCount += 1;
    await fulfillJson(route, {
      status: "already_configured",
      settings: databaseSettingsFixture(),
    });
  });

  await page.goto("/settings/database");

  await expect(page.getByText("設定済み", { exact: true })).toBeVisible();
  await expect.poll(() => downloadCount).toBe(0);
  await expect(
    page.getByText("OCI から Wallet を取得し、サーバーへ安全に設定しています…")
  ).toHaveCount(0);
});

test("ADB OCID がない場合は自動取得せず手動アップロードを案内する", async ({ page }) => {
  let downloadCount = 0;
  await page.route("**/api/settings/database", (route) =>
    fulfillJson(
      route,
      databaseSettingsFixture({
        wallet_uploaded: false,
        available_services: [],
        readiness: "wallet_not_found",
        adb_ocid: "",
      })
    )
  );
  await page.route("**/api/settings/database/wallet/download", async (route) => {
    downloadCount += 1;
    await fulfillJson(route, {
      status: "downloaded",
      settings: databaseSettingsFixture(),
    });
  });

  await page.goto("/settings/database");

  await expect(page.getByText(/ADB OCID が未設定のため自動取得は行いません/)).toBeVisible();
  await expect(page.getByText("`.zip` Wallet ファイルをアップロード")).toBeVisible();
  await expect.poll(() => downloadCount).toBe(0);
  await expectNoHorizontalOverflow(page);
});

test("外観設定でダーク/ライト/自動テーマを切り替えられる", async ({ page }) => {
  await page.goto("/settings/appearance");
  await expect(page.getByRole("heading", { name: "外観" })).toBeVisible();
  const html = page.locator("html");
  const bgVar = () =>
    page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--background").trim());
  const sidebarBgVar = () =>
    page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--sidebar").trim());

  // 既定はライト。
  await expect(html).not.toHaveClass(/dark/);
  expect(await bgVar()).toBe("#f7f8fa");

  const toggle = page.getByTestId("appearance-theme-toggle");
  await toggle.getByRole("button", { name: "ダーク" }).click();
  await expect(html).toHaveClass(/dark/);
  // VS Code Dark+ の editor / sidebar に合わせ、純黒は使わない。
  expect(await bgVar()).toBe("#1e1e1e");
  expect(await sidebarBgVar()).toBe("#181818");
  await expect(toggle.getByRole("button", { name: "ダーク" })).toHaveAttribute("aria-pressed", "true");

  // 再読込しても永続化される。
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);

  await page.getByTestId("appearance-theme-toggle").getByRole("button", { name: "ライト" }).click();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  expect(await bgVar()).toBe("#f7f8fa");
});
