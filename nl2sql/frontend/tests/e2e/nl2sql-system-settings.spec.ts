import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";
import { dropFiles } from "./_helpers/file-dropzone";

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
    driver_mode: "thin",
    connection_security: "wallet_mtls",
    client_lib_dir: "",
    wallet_dir: "/u01/aipoc/wallet",
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

function adbInfoFixture(overrides: Record<string, unknown> = {}) {
  return {
    status: "success",
    message: "ADB OCID が設定されています。",
    error_code: null,
    id: "ocid1.autonomousdatabase.oc1.ap-osaka-1.example",
    display_name: "nl2sqldb",
    lifecycle_state: "AVAILABLE",
    db_name: "NL2SQLDB",
    cpu_core_count: 2,
    data_storage_size_in_tbs: 1,
    region: "ap-osaka-1",
    ...overrides,
  };
}

const WALLET_PENDING_MESSAGE = "OCI から Wallet を取得し、サーバーへ安全に設定しています…";

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

function createRequestGate() {
  let release: () => void = () => undefined;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
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
    local_storage_dir: "/u01/data/production-ready-nl2sql",
    object_storage_region: "ap-osaka-1",
    object_storage_namespace: "exampletenancy",
    object_storage_bucket: "nl2sql-originals",
    readiness: "ok",
    max_upload_bytes: 104857600,
    config_source: "runtime",
  };

  const modelSettings = modelSettingsFixture();

  const databaseSettings = databaseSettingsFixture();
  let selectAiCredential: {
    credential_name: "OCI_CRED";
    schema_name: string;
    exists: boolean;
    region: "ap-osaka-1" | "us-chicago-1";
    oci_auth_ready: boolean;
    missing_fields: string[];
    operation: "created" | "recreated" | null;
  } = {
    credential_name: "OCI_CRED" as const,
    schema_name: "ADMIN",
    exists: false,
    region: "ap-osaka-1",
    oci_auth_ready: true,
    missing_fields: [] as string[],
    operation: null as "created" | "recreated" | null,
  };

  const adbInfo = adbInfoFixture();

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
      elapsed_ms: 7,
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
  await page.route("**/api/settings/model/test", (route) => {
    const request = route.request().postDataJSON() as {
      target_type: "enterprise_text" | "enterprise_vision" | "embedding" | "rerank";
      model_id: string;
    };
    const details =
      request.target_type === "embedding"
        ? { vector_dim: 1536, input_count: 1 }
        : request.target_type === "rerank"
          ? { ranked_count: 1, top_score: 0.78962326 }
          : request.target_type === "enterprise_vision"
            ? { surface: "vision", response_chars: 1 }
            : { surface: "text", response_chars: 5 };
    return fulfillJson(route, {
      status: "success",
      target_type: request.target_type,
      model_id: request.model_id,
      message: `${request.model_id} の設定を確認しました。`,
      troubleshooting: [],
      raw_error: null,
      error_type: null,
      elapsed_ms: 12,
      checked_at: "2026-06-21T10:00:00.000Z",
      details,
    });
  });

  await page.route("**/api/settings/database", (route) =>
    fulfillJson(route, databaseSettings)
  );
  await page.route("**/api/settings/database/select-ai-credential", async (route) => {
    if (route.request().method() === "POST") {
      const request = route.request().postDataJSON() as {
        region: "ap-osaka-1" | "us-chicago-1";
        recreate: boolean;
      };
      selectAiCredential = {
        ...selectAiCredential,
        exists: true,
        region: request.region,
        operation: request.recreate ? "recreated" : "created",
      };
    }
    await fulfillJson(route, selectAiCredential);
  });
  await page.route("**/api/settings/database/password/reveal", (route) =>
    fulfillJson(route, { password: "database-secret-fixture" })
  );
  await page.route("**/api/settings/database/system-tables", (route) =>
    fulfillJson(route, {
      status: "ready",
      schema_head: 15,
      applied_versions: [0, 1, 2, 3, 5, 6, 7, 8, 9, 15],
      pending_versions: [],
      expected_object_count: 51,
      existing_object_count: 51,
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

async function expectNoElementOverlap(items: Array<{ label: string; locator: Locator }>) {
  const boxes = await Promise.all(
    items.map(async (item) => ({ ...item, box: await item.locator.boundingBox() }))
  );

  for (const item of boxes) {
    if (!item.box) {
      throw new Error(`${item.label} の位置を取得できません。`);
    }
  }

  for (let index = 0; index < boxes.length; index += 1) {
    for (let compareIndex = index + 1; compareIndex < boxes.length; compareIndex += 1) {
      const current = boxes[index];
      const next = boxes[compareIndex];
      const currentBox = current.box;
      const nextBox = next.box;
      if (!currentBox || !nextBox) continue;

      const horizontalOverlap =
        Math.min(currentBox.x + currentBox.width, nextBox.x + nextBox.width) -
        Math.max(currentBox.x, nextBox.x);
      const verticalOverlap =
        Math.min(currentBox.y + currentBox.height, nextBox.y + nextBox.height) -
        Math.max(currentBox.y, nextBox.y);

      expect(
        horizontalOverlap > 1 && verticalOverlap > 1,
        `${current.label} と ${next.label} が重なっています。`
      ).toBeFalsy();
    }
  }
}

async function expectAdbActionButtonsStableDuringOperation(
  page: Page,
  labels: { start: string; stop: string }
) {
  const adbCard = page.locator("#adb-management");
  const saveButton = adbCard.getByRole("button", { name: "保存", exact: true });
  const startButton = adbCard.getByRole("button", { name: labels.start, exact: true });
  const stopButton = adbCard.getByRole("button", { name: labels.stop, exact: true });

  await expect(saveButton).toBeDisabled();
  await expect(adbCard.getByRole("button", { name: "保存中…", exact: true })).toHaveCount(0);
  await expectNoAdbWalletPendingStatus(page);
  await expectNoElementOverlap([
    { label: "保存ボタン", locator: saveButton },
    { label: "起動ボタン", locator: startButton },
    { label: "停止ボタン", locator: stopButton },
  ]);
  await expectNoHorizontalOverflow(page);
}

async function expectNoAdbWalletPendingStatus(page: Page) {
  await expect(
    page.locator("#adb-management").getByRole("status").filter({
      hasText: WALLET_PENDING_MESSAGE,
    })
  ).toHaveCount(0);
}

async function expectModelPreviewPanelsAbsent(page: Page) {
  await expect(page.getByText(".env プレビュー", { exact: true })).toHaveCount(0);
  await expect(page.getByText("JSON プレビュー", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: ".env をコピー" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "JSON をコピー" })).toHaveCount(0);
}

async function expectDatabaseSupplementalPanelsAbsent(page: Page) {
  await expect(page.getByText(".env プレビュー", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel(".env プレビュー")).toHaveCount(0);
  await expect(page.getByRole("button", { name: ".env をコピー" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "接続状態" })).toHaveCount(0);
}

async function getSavedSecretBadgeStyle(page: Page, fieldId: string) {
  const badge = page
    .locator(`label[for="${fieldId}"]`)
    .locator("xpath=..")
    .getByText("保存済み", { exact: true });
  await expect(badge).toBeVisible();

  return badge.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      backgroundColor: style.backgroundColor,
      borderColor: style.borderColor,
      borderRadius: style.borderRadius,
      borderStyle: style.borderStyle,
      className: element.getAttribute("class") ?? "",
      color: style.color,
      fontSize: style.fontSize,
      fontWeight: style.fontWeight,
      text: element.textContent,
      whiteSpace: style.whiteSpace,
    };
  });
}

async function getActionButtonStyle(button: Locator) {
  await expect(button).toBeVisible();

  return button.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      backgroundColor: style.backgroundColor,
      borderColor: style.borderColor,
      className: element.getAttribute("class") ?? "",
      color: style.color,
    };
  });
}

async function expectModelSaveButtonsUsePrimaryStyle(page: Page) {
  const buttons = [
    page.getByRole("button", { name: "OCI Enterprise AI: 保存" }),
    page.getByRole("button", { name: "登録モデル: 保存" }),
    page.getByRole("button", { name: "OCI Generative AI: 保存" }),
  ];
  const styles = await Promise.all(buttons.map((button) => getActionButtonStyle(button)));
  const baseline = styles[0];

  for (const style of styles) {
    expect(style.className).toContain("bg-primary-fill");
    expect(style.className).toContain("text-primary-fill-foreground");
    expect(style.className).not.toContain("border-control-border");
    expect(style.backgroundColor).toBe(baseline.backgroundColor);
    expect(style.borderColor).toBe(baseline.borderColor);
    expect(style.color).toBe(baseline.color);
  }
}

async function expectNoOperationsMemoOrReadiness(page: Page) {
  await expect(page.getByRole("heading", { name: "運用メモ" })).toHaveCount(0);
  await expect(page.getByText(/readiness/i)).toHaveCount(0);
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

async function expectWalletAboveServiceDsn(page: Page) {
  const wallet = page.getByTestId("oracle-wallet-upload");
  const service = page.getByRole("combobox", { name: /サービス名 \/ DSN/ });

  await expect(wallet).toBeVisible();
  await expect(service).toBeVisible();

  const [walletBox, serviceBox] = await Promise.all([
    wallet.boundingBox(),
    service.boundingBox(),
  ]);

  if (!walletBox || !serviceBox) {
    throw new Error("データベース設定フォームの Wallet / DSN 位置を取得できません。");
  }

  expect(walletBox.y).toBeLessThan(serviceBox.y);
}

async function expectAdbManagementAboveDatabaseSettings(page: Page) {
  const adbHeading = page
    .locator("#adb-management")
    .getByRole("heading", { name: "Autonomous Database 管理" });
  const databaseHeading = page
    .locator("form")
    .getByRole("heading", { name: "データベース設定" });

  await expect(adbHeading).toBeVisible();
  await expect(databaseHeading).toBeVisible();

  const [adbBox, databaseBox] = await Promise.all([
    adbHeading.boundingBox(),
    databaseHeading.boundingBox(),
  ]);

  if (!adbBox || !databaseBox) {
    throw new Error("ADB 管理カード / データベース設定カードの位置を取得できません。");
  }

  expect(adbBox.y).toBeLessThan(databaseBox.y);
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
      local_storage_dir: "/u01/data/production-ready-nl2sql",
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
  await expect(page.getByRole("heading", { name: "設定チェック" })).toHaveCount(0);
  await expect(page.getByLabel(".env プレビュー")).toHaveCount(0);
});

test("NL2SQL のシステム設定画面を表示できる", async ({ page }) => {
  await page.goto("/settings/oci");
  await expect(page.getByRole("heading", { name: "OCI 認証設定" }).first()).toBeVisible();
  await expect(page.getByLabel("ユーザー OCID")).toBeVisible();
  await expect(page.getByRole("button", { name: "OCI 設定を保存" })).toBeVisible();
  await expect(page.getByRole("button", { name: "接続テスト" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "設定チェック" })).toHaveCount(0);
  await expect(page.getByLabel(".env プレビュー")).toHaveCount(0);
  await expect(page.getByRole("button", { name: ".env をコピー" })).toHaveCount(0);
  await expectNoOperationsMemoOrReadiness(page);
  await expectOciConfigFieldsAboveOcidFields(page);
  await expectNl2sqlShellFillsViewport(page);
  await expectNoExcessBottomWhitespace(page);
  await expectNoHorizontalOverflow(page);

  await page.goto("/settings/upload-storage");
  await expect(page.getByRole("heading", { name: "アップロード保存先" }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "保存先", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "保存先状態" })).toHaveCount(0);
  await expect(page.getByLabel(".env プレビュー")).toHaveCount(0);
  await expect(page.getByRole("button", { name: ".env をコピー" })).toHaveCount(0);
  await expect(page.getByLabel("ローカル保存ディレクトリ")).toHaveValue(
    "/u01/data/production-ready-nl2sql"
  );
  await expectNoOperationsMemoOrReadiness(page);
  await page.getByRole("radio", { name: /OCI Object Storage/ }).check();
  await expect(page.getByLabel("Object Storage バケット")).toHaveValue("nl2sql-originals");
  await expectNoHorizontalOverflow(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole("heading", { name: "保存先状態" })).toHaveCount(0);
  await expect(page.getByLabel(".env プレビュー")).toHaveCount(0);
  await expect(page.getByRole("button", { name: ".env をコピー" })).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  await page.setViewportSize({ width: 1280, height: 720 });

  await page.goto("/settings/model");
  await expect(page.getByRole("heading", { name: "モデル設定" }).first()).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "OCI Enterprise AI", exact: true })
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "登録モデル", exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "OCI Generative AI", exact: true })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "OCI Enterprise AI: 保存" })).toBeVisible();
  await expect(page.getByRole("button", { name: "登録モデル: 保存" })).toBeVisible();
  await expect(page.getByRole("button", { name: "OCI Generative AI: 保存" })).toBeVisible();
  await expectModelSaveButtonsUsePrimaryStyle(page);
  await expect(page.locator("#enterprise-api-path")).toHaveCount(0);
  await expect(page.locator("#enterprise-vlm-input-mode")).toHaveCount(0);
  await expect(page.locator("#enterprise-timeout")).toHaveCount(0);
  await expect(page.locator("#enterprise-retries")).toHaveCount(0);
  await expectNoOperationsMemoOrReadiness(page);
  await expectModelPreviewPanelsAbsent(page);
  const modelSavedSecretBadgeStyle = await getSavedSecretBadgeStyle(
    page,
    "enterprise-api-key"
  );
  expect(modelSavedSecretBadgeStyle.className).toContain("border-success/30");
  expect(modelSavedSecretBadgeStyle.className).toContain("bg-success-bg");
  expect(modelSavedSecretBadgeStyle.className).toContain("text-success");
  await expectNoHorizontalOverflow(page);

  await page.goto("/settings/database");
  await expect(page.getByRole("heading", { name: "データベース設定" }).first()).toBeVisible();
  await expect(page.getByLabel("データベースユーザー")).toBeVisible();
  await expectAdbManagementAboveDatabaseSettings(page);
  await expectWalletAboveServiceDsn(page);
  await expectDatabaseSupplementalPanelsAbsent(page);
  const databaseSavedSecretBadgeStyle = await getSavedSecretBadgeStyle(page, "oracle-password");
  expect(databaseSavedSecretBadgeStyle).toEqual(modelSavedSecretBadgeStyle);
  await expectNoOperationsMemoOrReadiness(page);
  await page.getByRole("button", { name: "DB接続テスト" }).click();
  await expect(page.getByText("入力値の形式のみ確認します。")).toBeVisible();
  await expect(page.getByTestId("settings-database-test-result")).toHaveAttribute(
    "data-tone",
    "warning"
  );
  await expectNoOperationsMemoOrReadiness(page);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("操作履歴")).toBeVisible();
  await expect(page.getByText("ADB OCID が設定されています。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("システム設定のテスト成功結果を共通パネルで表示する", async ({ page }, testInfo) => {
  await page.goto("/settings/oci");
  const ociTestButton = page.getByRole("button", { name: /接続テスト/ });
  await ociTestButton.focus();
  await expect(ociTestButton).toBeFocused();
  await ociTestButton.press("Enter");

  const ociResult = page.getByTestId("settings-oci-test-result");
  await expect(ociResult.getByRole("status")).toBeVisible();
  await expect(ociResult).toHaveAttribute("data-tone", "success");
  await expect(ociResult).toContainText("OCI config を確認しました。");
  await expect(ociResult).toContainText("所要時間: 7 ms");
  await expect(ociResult).toContainText("config_file_mode");
  await expectNoHorizontalOverflow(page);
  await page.locator("#oci-user-ocid").fill("ocid1.user.oc1..changed");
  await expect(ociResult).toHaveCount(0);

  await page.goto("/settings/model");
  await page
    .getByRole("button", { name: "enterprise-nl2sql-vlm をテスト", exact: true })
    .click();
  const enterpriseResult = page
    .getByText("enterprise-nl2sql-vlm の設定を確認しました。", { exact: true })
    .locator("xpath=ancestor::*[@data-settings-test-result][1]");
  await expect(enterpriseResult).toHaveAttribute("data-tone", "success");
  await expect(enterpriseResult).toContainText("surface");
  await expect(enterpriseResult).toContainText("vision");

  await page
    .getByRole("button", { name: "cohere.embed-v4.0 をテスト", exact: true })
    .click();
  const embeddingResult = page
    .getByText("cohere.embed-v4.0 の設定を確認しました。", { exact: true })
    .locator("xpath=ancestor::*[@data-settings-test-result][1]");
  await expect(embeddingResult).toContainText("vector_dim");
  await expect(embeddingResult).toContainText("1536");

  await page
    .getByRole("button", { name: "cohere.rerank-v4.0-fast をテスト", exact: true })
    .click();
  const rerankResult = page
    .getByText("cohere.rerank-v4.0-fast の設定を確認しました。", { exact: true })
    .locator("xpath=ancestor::*[@data-settings-test-result][1]");
  await expect(rerankResult).toContainText("top_score");
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: testInfo.outputPath("settings-model-test-result-success.png"),
    fullPage: true,
  });
  await page.getByRole("textbox", { name: "モデル ID 2" }).fill("enterprise-nl2sql-vlm-v2");
  await expect(enterpriseResult).toHaveCount(0);

  await page.unroute("**/api/settings/database/test");
  await page.route("**/api/settings/database/test", (route) =>
    fulfillJson(route, {
      status: "success",
      readiness: "ok",
      message: "Oracle 26ai への接続に成功しました。",
      elapsed_ms: 9,
      troubleshooting: [],
      details: { network_call: true },
      checked_at: "2026-06-21T10:00:00.000Z",
      error_type: null,
    })
  );
  await page.goto("/settings/database");
  await page.getByRole("button", { name: "DB接続テスト" }).click();
  const databaseResult = page.getByTestId("settings-database-test-result");
  await expect(databaseResult.getByRole("status")).toBeVisible();
  await expect(databaseResult).toHaveAttribute("data-tone", "success");
  await expect(databaseResult).toContainText("Oracle 26ai への接続に成功しました。");
  await expect(databaseResult).toContainText("所要時間: 9 ms");
  await expect(databaseResult).toContainText("確認時刻:");
  await expect(databaseResult).toContainText("network_call");
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: testInfo.outputPath("settings-test-result-success.png"),
    fullPage: true,
  });
  await page.locator("#oracle-user").fill("NL2SQL_APP_CHANGED");
  await expect(databaseResult).toHaveCount(0);
});

test("システム設定の失敗と API エラーを共通 danger パネルで表示する", async ({ page }) => {
  await page.unroute("**/api/settings/oci/config/test");
  await page.route("**/api/settings/oci/config/test", (route) =>
    fulfillJson(route, {
      status: "failed",
      profile: "DEFAULT",
      config_file: "~/.oci/config",
      key_file: "~/.oci/oci_api_key.pem",
      config_file_exists: true,
      key_file_exists: false,
      missing_fields: ["fingerprint"],
      permission_issues: ["OCI config ファイルは 0600 にしてください。"],
      oci_directory_mode: "0700",
      config_file_mode: "0644",
      key_file_mode: null,
      message: "OCI 認証ファイルを確認してください。",
      elapsed_ms: 4,
      checked_at: "2026-06-21T10:00:00.000Z",
      error_type: "InvalidOciConfig",
    })
  );
  await page.goto("/settings/oci");
  await page.getByRole("button", { name: /接続テスト/ }).click();
  const ociResult = page.getByTestId("settings-oci-test-result");
  await expect(ociResult.getByRole("alert")).toBeVisible();
  await expect(ociResult).toHaveAttribute("data-tone", "danger");
  await expect(ociResult).toContainText("確認ポイント");
  await expect(ociResult).toContainText("不足項目: fingerprint");
  await expectNoHorizontalOverflow(page);

  await page.unroute("**/api/settings/model/test");
  await page.route("**/api/settings/model/test", (route) =>
    fulfillJson(route, {
      status: "failed",
      target_type: "embedding",
      model_id: "cohere.embed-v4.0",
      message: "Embedding モデルのテストに失敗しました。",
      troubleshooting: ["OCI Generative AI の設定を確認してください。"],
      raw_error: "gateway timeout",
      error_type: "TimeoutError",
      elapsed_ms: 30000,
      checked_at: "2026-06-21T10:00:00.000Z",
      details: {},
    })
  );
  await page.goto("/settings/model");
  await page
    .getByRole("button", { name: "cohere.embed-v4.0 をテスト", exact: true })
    .click();
  const modelResult = page
    .getByText("Embedding モデルのテストに失敗しました。", { exact: true })
    .locator("xpath=ancestor::*[@data-settings-test-result][1]");
  await expect(modelResult.getByRole("alert")).toBeVisible();
  await expect(modelResult).toContainText("確認ポイント");
  await expect(modelResult).toContainText("TimeoutError");
  await expect(modelResult).not.toContainText("gateway timeout");
  await expectNoHorizontalOverflow(page);

  await page.unroute("**/api/settings/database/test");
  await page.route("**/api/settings/database/test", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "DB 接続サービスに到達できません。" }),
    })
  );
  await page.goto("/settings/database");
  await page.getByRole("button", { name: "DB接続テスト" }).click();
  const databaseResult = page.getByTestId("settings-database-test-result");
  await expect(databaseResult.getByRole("alert")).toBeVisible();
  await expect(databaseResult).toHaveAttribute("data-tone", "danger");
  await expect(databaseResult).toContainText("DB 接続サービスに到達できません。");
  await expect(databaseResult).toContainText("再試行してください。");
  await expectNoHorizontalOverflow(page);
});

test("データベース設定は Wallet ZIP の下にサービス名を置き、保存済みパスワードを明示表示する", async ({
  page,
}) => {
  let revealCount = 0;
  await page.unroute("**/api/settings/database/password/reveal");
  await page.route("**/api/settings/database/password/reveal", async (route) => {
    revealCount += 1;
    await fulfillJson(route, { password: "database-secret-fixture" });
  });

  await page.goto("/settings/database");
  await expect.poll(() => revealCount).toBe(0);

  await expectWalletAboveServiceDsn(page);
  await expectAdbManagementAboveDatabaseSettings(page);

  const password = page.getByLabel("データベースパスワード");
  await expect(password).toHaveValue("");
  await expect(password).toHaveAttribute("type", "password");
  await expectDatabaseSupplementalPanelsAbsent(page);

  await page.getByRole("button", { name: "DB パスワードを表示" }).click();
  await expect.poll(() => revealCount).toBe(1);
  await expect(password).toHaveValue("database-secret-fixture");
  await expect(password).toHaveAttribute("type", "text");
  await expectDatabaseSupplementalPanelsAbsent(page);
  await expect
    .poll(() =>
      page.evaluate(() => document.body.innerText.includes("database-secret-fixture"))
    )
    .toBeFalsy();

  await page.getByRole("button", { name: "DB パスワードを隠す" }).click();
  await expect(password).toHaveAttribute("type", "password");
  await expect(password).toHaveValue("database-secret-fixture");

  const revealAgain = page.getByRole("button", { name: "DB パスワードを表示" });
  await revealAgain.focus();
  await expect(revealAgain).toBeFocused();
  await revealAgain.press("Enter");
  await expect.poll(() => revealCount).toBe(1);
  await expect(password).toHaveAttribute("type", "text");
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 375, height: 812 });
  await expectAdbManagementAboveDatabaseSettings(page);
  await expectWalletAboveServiceDsn(page);
  await expectDatabaseSupplementalPanelsAbsent(page);
  await expectNoHorizontalOverflow(page);
});

test("データベース設定は Wallet パスワードを保存し、保存済み値を削除できる", async ({
  page,
}) => {
  const savedRequests: Array<Record<string, unknown>> = [];
  await page.unroute("**/api/settings/database");
  await page.route("**/api/settings/database", async (route) => {
    if (route.request().method() === "PATCH") {
      savedRequests.push(route.request().postDataJSON() as Record<string, unknown>);
      await fulfillJson(route, databaseSettingsFixture({ has_wallet_password: true }));
      return;
    }
    await fulfillJson(route, databaseSettingsFixture({ has_wallet_password: true }));
  });

  await page.goto("/settings/database");

  const walletPassword = page.getByLabel("Wallet パスワード", { exact: true });
  await expect(walletPassword).toBeVisible();
  await expect(walletPassword).toHaveValue("");
  await expect(walletPassword).toHaveAttribute("type", "password");
  const walletSavedSecretBadgeStyle = await getSavedSecretBadgeStyle(
    page,
    "oracle-wallet-password"
  );
  expect(walletSavedSecretBadgeStyle.text).toBe("保存済み");
  await expect(
    page.getByText("保存済み Wallet パスワードがあります。", { exact: false })
  ).toBeVisible();

  await walletPassword.fill("wallet-secret-fixture");
  await page.getByRole("button", { name: "Wallet パスワードを表示", exact: true }).click();
  await expect(walletPassword).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "Wallet パスワードを隠す", exact: true }).click();
  await expect(walletPassword).toHaveAttribute("type", "password");

  await page.getByRole("button", { name: "DB設定を保存" }).click();
  await expect.poll(() => savedRequests.length).toBe(1);
  expect(savedRequests[0]).toMatchObject({
    connection_security: "wallet_mtls",
    wallet_password: "wallet-secret-fixture",
  });
  expect(savedRequests[0]).not.toHaveProperty("clear_wallet_password");

  const clearWalletPassword = page.getByLabel("保存済み Wallet パスワードを削除する");
  await clearWalletPassword.check();
  await expect(walletPassword).toBeDisabled();
  await page.getByRole("button", { name: "DB設定を保存" }).click();
  await expect.poll(() => savedRequests.length).toBe(2);
  expect(savedRequests[1]).toMatchObject({
    connection_security: "wallet_mtls",
    clear_wallet_password: true,
  });
  expect(savedRequests[1]).not.toHaveProperty("wallet_password");
  await expectNoHorizontalOverflow(page);
});

test("DB パスワード表示ボタンの取得中 icon は上下に浮動しない StableLoadingIcon を使う", async ({
  page,
}) => {
  const revealGate = createRequestGate();
  let revealCount = 0;
  await page.unroute("**/api/settings/database/password/reveal");
  await page.route("**/api/settings/database/password/reveal", async (route) => {
    revealCount += 1;
    await revealGate.promise;
    await fulfillJson(route, { password: "database-secret-fixture" });
  });

  await page.goto("/settings/database");
  await expect.poll(() => revealCount).toBe(0);

  const revealButton = page.locator("#oracle-password + button");
  await expect(revealButton).toHaveAttribute("aria-label", "DB パスワードを表示");
  await revealButton.click();
  await expect.poll(() => revealCount).toBe(1);
  await expect(revealButton).toHaveAttribute("aria-label", "DB パスワードを取得中");
  await expect(revealButton).toBeDisabled();

  const loadingIcon = revealButton.locator('svg[data-loading-icon="true"]');
  await expect(loadingIcon).toBeVisible();
  await expect(loadingIcon.locator('[data-loading-icon-active="true"]')).toHaveCount(2);
  const metrics = await loadingIcon.evaluate((node) => {
    const icon = node.getBoundingClientRect();
    const button = (node.closest("button") as HTMLElement).getBoundingClientRect();
    const computed = getComputedStyle(node);
    return {
      width: computed.width,
      height: computed.height,
      transformBox: computed.transformBox,
      animationName: computed.animationName,
      iconCenterY: icon.y + icon.height / 2,
      buttonCenterY: button.y + button.height / 2,
    };
  });
  expect(metrics.width).toBe("16px");
  expect(metrics.height).toBe("16px");
  expect(metrics.transformBox).toBe("view-box");
  expect(metrics.animationName).not.toBe("none");
  expect(Math.abs(metrics.iconCenterY - metrics.buttonCenterY)).toBeLessThan(0.02);

  revealGate.release();
  await expect(page.locator("#oracle-password")).toHaveAttribute("type", "text");
  await expectNoHorizontalOverflow(page);
});

test("データベース設定は接続セキュリティで Wallet mTLS と Walletless TLS を切り替える", async ({
  page,
}) => {
  await page.goto("/settings/database");

  const securityMode = page.getByRole("combobox", { name: "接続セキュリティ" });
  await expect(securityMode).toBeVisible();
  await expect(securityMode).toContainText("Wallet mTLS");
  await expect(page.getByTestId("oracle-wallet-upload")).toBeVisible();
  await expect(page.getByLabel("Wallet パスワード", { exact: true })).toBeVisible();

  await securityMode.click();
  await page.getByRole("option", { name: /Walletless TLS/ }).click();

  await expect(page.getByText("Walletless TLS では Wallet", { exact: false })).toBeVisible();
  await expect(page.getByTestId("oracle-wallet-upload")).toHaveCount(0);
  await expect(page.getByLabel("Wallet パスワード", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("接続 DSN")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("アップロード保存先は右側情報カラムを表示しない", async ({
  page,
}) => {
  await page.goto("/settings/upload-storage");

  await expect(page.getByRole("heading", { name: "アップロード保存先" }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "保存先", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "保存先状態" })).toHaveCount(0);
  await expect(page.getByLabel(".env プレビュー")).toHaveCount(0);
  await expect(page.getByRole("button", { name: ".env をコピー" })).toHaveCount(0);
  await expect(page.getByLabel("ローカル保存ディレクトリ")).toHaveValue(
    "/u01/data/production-ready-nl2sql"
  );
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole("heading", { name: "保存先状態" })).toHaveCount(0);
  await expect(page.getByLabel(".env プレビュー")).toHaveCount(0);
  await expect(page.getByRole("button", { name: ".env をコピー" })).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});

test("データベース設定は右側情報カラムを表示しない", async ({
  page,
}) => {
  await page.unroute("**/api/settings/database");
  await page.route("**/api/settings/database", (route) =>
    fulfillJson(
      route,
      databaseSettingsFixture({
        driver_mode: "thin",
        client_lib_dir: "",
        wallet_dir: "/u01/aipoc/wallet",
      })
    )
  );

  await page.goto("/settings/database");

  await expect(page.getByRole("heading", { name: "データベース設定" }).first()).toBeVisible();
  await expectAdbManagementAboveDatabaseSettings(page);
  await expectWalletAboveServiceDsn(page);
  await expectDatabaseSupplementalPanelsAbsent(page);
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 375, height: 812 });
  await expectDatabaseSupplementalPanelsAbsent(page);
  await expectNoHorizontalOverflow(page);
});

test("OCI 秘密鍵と Wallet ZIP をドラッグ＆ドロップで即時アップロードできる", async ({
  page,
}) => {
  let keyUploadCount = 0;
  let walletUploadCount = 0;
  let keyUploadBody = "";
  let walletUploadBody = "";
  const keyUploadGate = createRequestGate();

  await page.unroute("**/api/settings/oci/key-file");
  await page.route("**/api/settings/oci/key-file", async (route) => {
    keyUploadCount += 1;
    keyUploadBody = route.request().postDataBuffer()?.toString("utf8") ?? "";
    await keyUploadGate.promise;
    await fulfillJson(route, { key_file: "~/.oci/oci_api_key.pem", saved: true });
  });
  await page.unroute("**/api/settings/database/wallet");
  await page.route("**/api/settings/database/wallet", async (route) => {
    walletUploadCount += 1;
    walletUploadBody = route.request().postDataBuffer()?.toString("utf8") ?? "";
    await fulfillJson(route, databaseSettingsFixture());
  });

  await page.goto("/settings/oci");
  const keyDropzone = page.getByTestId("oci-key-file-upload-dropzone");
  await expect(keyDropzone).toHaveCSS("height", "44px");
  await dropFiles(page, keyDropzone, [
    {
      name: "private.pem",
      type: "application/x-pem-file",
      content: "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
    },
  ]);
  try {
    await expect(keyDropzone).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("oci-key-file-upload-input")).toBeDisabled();
  } finally {
    keyUploadGate.release();
  }
  await expect(page.getByText("秘密鍵ファイルをアップロードしました。")).toBeVisible();
  await expect.poll(() => keyUploadCount).toBe(1);
  expect(keyUploadBody).toContain('filename="private.pem"');

  await page.goto("/settings/database");
  const walletInput = page.getByTestId("oracle-wallet-upload-input");
  await walletInput.focus();
  await expect(walletInput).toBeFocused();
  await dropFiles(page, page.getByTestId("oracle-wallet-upload-dropzone"), [
    {
      name: "wallet.zip",
      type: "application/zip",
      content: "PK\u0003\u0004test",
    },
  ]);
  await expect(page.getByText("Wallet ZIP をアップロードしました: wallet.zip")).toBeVisible();
  await expect.poll(() => walletUploadCount).toBe(1);
  expect(walletUploadBody).toContain('filename="wallet.zip"');
  await expectNoHorizontalOverflow(page);
});

test("データベース設定の可視読込は共通スケルトン内に経過時間を表示する", async ({
  page,
}) => {
  const gate = createRequestGate();
  await page.unroute("**/api/settings/database");
  await page.route("**/api/settings/database", async (route) => {
    await gate.promise;
    await fulfillJson(route, databaseSettingsFixture());
  });

  await page.goto("/settings/database");
  const loading = page.getByTestId("settings-database-loading");
  await expect(loading).toBeVisible();
  await expect(loading).toContainText("データベース設定を読み込んでいます");
  await expect(loading.getByRole("timer")).toHaveAccessibleName("経過時間 00:00");
  await expect(loading.getByRole("timer")).toHaveAttribute("aria-live", "off");

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoHorizontalOverflow(page);
  gate.release();
  await expect(page.getByLabel("データベースユーザー")).toBeVisible();
  await expect(loading).toHaveCount(0);
});

test("非正常な readiness 値も設定画面には表示しない", async ({ page }) => {
  await page.unroute("**/api/settings/upload-storage");
  await page.route("**/api/settings/upload-storage", (route) =>
    fulfillJson(route, {
      backend: "local",
      local_storage_dir: "/u01/data/production-ready-nl2sql",
      object_storage_region: "ap-osaka-1",
      object_storage_namespace: "exampletenancy",
      object_storage_bucket: "nl2sql-originals",
      readiness: "missing_credentials",
      max_upload_bytes: 104857600,
      config_source: "runtime",
    })
  );

  await page.goto("/settings/upload-storage");
  await expect(page.getByRole("heading", { name: "保存先", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "保存先状態" })).toHaveCount(0);
  await expect(page.getByLabel(".env プレビュー")).toHaveCount(0);
  await expect(
    page.getByText("backend/.env + 現在のプロセス設定", { exact: true })
  ).toHaveCount(0);
  await expectNoOperationsMemoOrReadiness(page);

  await page.unroute("**/api/settings/database");
  await page.route("**/api/settings/database", (route) =>
    fulfillJson(route, databaseSettingsFixture({ readiness: "invalid" }))
  );
  await page.unroute("**/api/settings/database/test");
  await page.route("**/api/settings/database/test", (route) =>
    fulfillJson(route, {
      status: "failed",
      readiness: "error",
      message: "接続設定を確認してください。",
      elapsed_ms: 3,
      troubleshooting: ["DSN と Wallet を確認してください。"],
      details: { network_call: false },
      checked_at: "2026-06-21T10:00:00.000Z",
      error_type: "InvalidConfiguration",
    })
  );

  await page.goto("/settings/database");
  await expectDatabaseSupplementalPanelsAbsent(page);
  await expectNoOperationsMemoOrReadiness(page);
  await page.getByRole("button", { name: "DB接続テスト" }).click();
  await expect(page.getByText("接続設定を確認してください。")).toBeVisible();
  await expect(page.getByText("所要時間: 3 ms")).toBeVisible();
  await expect(page.getByTestId("settings-database-test-result")).toHaveAttribute(
    "data-tone",
    "danger"
  );
  await expect(page.getByTestId("settings-database-test-result")).toContainText("network_call");
  await expectNoOperationsMemoOrReadiness(page);
  await expectNoHorizontalOverflow(page);
});

test("モデル設定を3カードごとに独立保存し、非表示設定と未保存入力を保持する", async ({
  page,
}) => {
  await page.unroute("**/api/settings/model");
  const base = modelSettingsFixture();
  let persisted = modelSettingsFixture({
    settings: {
      ...base.settings,
      enterprise_ai: {
        ...base.settings.enterprise_ai,
        api_path: "/custom-responses",
        vlm_input_mode: "inline_image",
        timeout_seconds: 177,
        max_retries: 4,
      },
    },
  });
  const requests: Array<Record<string, unknown>> = [];
  const firstSaveGate = createRequestGate();
  let holdFirstSave = true;
  let rejectNextSave = false;

  await page.route("**/api/settings/model", async (route) => {
    if (route.request().method() !== "PATCH") {
      await fulfillJson(route, persisted);
      return;
    }
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    if (holdFirstSave) {
      holdFirstSave = false;
      await firstSaveGate.promise;
    }
    if (rejectNextSave) {
      rejectNextSave = false;
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "登録モデルを保存できませんでした。" }),
      });
      return;
    }
    persisted = modelSettingsFixture({ settings: body });
    await fulfillJson(route, persisted);
  });

  await page.goto("/settings/model");
  await expect(page.locator("#enterprise-api-path")).toHaveCount(0);
  await expect(page.locator("#enterprise-vlm-input-mode")).toHaveCount(0);
  await expect(page.locator("#enterprise-timeout")).toHaveCount(0);
  await expect(page.locator("#enterprise-retries")).toHaveCount(0);

  await page.locator("#enterprise-endpoint").fill("https://changed.example.com");
  await page.getByRole("textbox", { name: "モデル ID 1" }).fill("enterprise-unsaved-model");
  await page.locator("#genai-embedding-model").fill("cohere.unsaved-embed");

  await page.getByRole("button", { name: "OCI Enterprise AI: 保存" }).click();
  await expect(
    page.getByRole("button", { name: "OCI Enterprise AI: 保存中…" })
  ).toBeDisabled();
  await expect(page.getByRole("button", { name: "登録モデル: 保存" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "OCI Generative AI: 保存" })).toBeDisabled();
  firstSaveGate.release();
  await expect(page.getByText("OCI Enterprise AI 接続設定を保存しました。")).toBeVisible();

  const connectionRequest = requests[0];
  const connectionEnterprise = connectionRequest.enterprise_ai as Record<string, unknown>;
  expect(connectionEnterprise.endpoint).toBe("https://changed.example.com");
  expect(connectionEnterprise.api_path).toBe("/custom-responses");
  expect(connectionEnterprise.vlm_input_mode).toBe("inline_image");
  expect(connectionEnterprise.timeout_seconds).toBe(177);
  expect(connectionEnterprise.max_retries).toBe(4);
  expect(
    ((connectionEnterprise.models as Array<Record<string, unknown>>)[0]).model_id
  ).toBe("enterprise-nl2sql-llm");
  expect(
    (connectionRequest.generative_ai as Record<string, unknown>).embedding_model
  ).toBe("cohere.embed-v4.0");
  await expect(page.getByRole("textbox", { name: "モデル ID 1" })).toHaveValue(
    "enterprise-unsaved-model"
  );
  await expect(page.locator("#genai-embedding-model")).toHaveValue("cohere.unsaved-embed");

  await page.getByRole("button", { name: "登録モデル: 保存" }).click();
  await expect(page.getByText("登録モデルを保存しました。")).toBeVisible();
  const modelsRequest = requests[1];
  const modelsEnterprise = modelsRequest.enterprise_ai as Record<string, unknown>;
  expect(modelsEnterprise.endpoint).toBe("https://changed.example.com");
  expect(modelsEnterprise.api_path).toBe("/custom-responses");
  expect(modelsEnterprise.vlm_input_mode).toBe("inline_image");
  expect(modelsEnterprise.timeout_seconds).toBe(177);
  expect(modelsEnterprise.max_retries).toBe(4);
  expect(((modelsEnterprise.models as Array<Record<string, unknown>>)[0]).model_id).toBe(
    "enterprise-unsaved-model"
  );
  expect((modelsRequest.generative_ai as Record<string, unknown>).embedding_model).toBe(
    "cohere.embed-v4.0"
  );
  await expect(page.locator("#genai-embedding-model")).toHaveValue("cohere.unsaved-embed");

  const generativeSave = page.getByRole("button", { name: "OCI Generative AI: 保存" });
  await generativeSave.focus();
  await expect(generativeSave).toBeFocused();
  await generativeSave.press("Enter");
  await expect(page.getByText("OCI Generative AI 設定を保存しました。")).toBeVisible();
  const generativeRequest = requests[2];
  expect(
    (generativeRequest.generative_ai as Record<string, unknown>).embedding_model
  ).toBe("cohere.unsaved-embed");
  expect(
    ((generativeRequest.enterprise_ai as Record<string, unknown>).models as Array<
      Record<string, unknown>
    >)[0].model_id
  ).toBe("enterprise-unsaved-model");

  rejectNextSave = true;
  await page.getByRole("textbox", { name: "モデル ID 1" }).fill("enterprise-failed-model");
  await page.getByRole("button", { name: "登録モデル: 保存" }).click();
  const modelsForm = page
    .getByRole("button", { name: "登録モデル: 保存" })
    .locator("xpath=ancestor::form[1]");
  await expect(modelsForm.getByRole("alert")).toContainText(
    "登録モデルを保存できませんでした。"
  );
  await expect(page.getByRole("textbox", { name: "モデル ID 1" })).toHaveValue(
    "enterprise-failed-model"
  );

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoHorizontalOverflow(page);
  await expect(page.getByRole("button", { name: "OCI Enterprise AI: 保存" })).toBeVisible();
  await expect(page.getByRole("button", { name: "登録モデル: 保存" })).toBeVisible();
  await expect(page.getByRole("button", { name: "OCI Generative AI: 保存" })).toBeVisible();
  await expectModelSaveButtonsUsePrimaryStyle(page);
});

test("モデル API Key を .env に新規保存して削除でき、プレビュー欄を表示しない", async ({
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
  await expectModelPreviewPanelsAbsent(page);
  await expect(page.locator("body")).not.toContainText("new-key-fixture");
  await page.getByRole("button", { name: "OCI Enterprise AI: 保存" }).click();
  const notificationRegion = page.getByRole("region", { name: "通知" });
  await expect(notificationRegion).toContainText("OCI Enterprise AI 接続設定を保存しました。");
  await expect(page.getByText("OCI Enterprise AI 接続設定を保存しました。")).toHaveCount(1);
  expect((requests[0].enterprise_ai as Record<string, unknown>).api_key).toBe(
    "new-key-fixture"
  );

  await page.getByLabel("保存済み API key を削除する").check();
  await page.getByRole("button", { name: "OCI Enterprise AI: 保存" }).click();
  expect((requests[1].enterprise_ai as Record<string, unknown>).clear_api_key).toBe(true);
  await expect(page.getByText("未設定", { exact: true })).toBeVisible();
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
  await page.getByRole("button", { name: "OCI Enterprise AI: 保存" }).click();

  expect(savedRequests).toHaveLength(1);
  const enterprise = savedRequests[0]?.enterprise_ai as Record<string, unknown>;
  expect(enterprise.api_key).toBe("");
  expect(enterprise.has_api_key).toBe(true);
  expect(enterprise.clear_api_key).toBe(false);
  await expect(page.getByText("旧 JSON に API Key が残っています")).toHaveCount(0);
  await expect(page.getByText("保存済み", { exact: true })).toBeVisible();
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
  await expect(page.getByTestId("oracle-wallet-upload-input")).toBeDisabled();
  await expect(page.getByTestId("oracle-wallet-upload-dropzone")).toContainText(
    "ドラッグ＆ドロップまたは選択"
  );

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
  await expect(page.getByTestId("oracle-wallet-upload-dropzone")).toContainText(
    "ドラッグ＆ドロップまたは選択"
  );
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

test("ADB 起動中は保存ボタンを無効化して保存表示のままにする", async ({ page }) => {
  const settingsGate = createRequestGate();
  const startGate = createRequestGate();
  let settingsCount = 0;
  let startCount = 0;

  await page.unroute("**/api/settings/database/adb");
  await page.route("**/api/settings/database/adb", (route) =>
    fulfillJson(route, adbInfoFixture({ lifecycle_state: "STOPPED" }))
  );
  await page.unroute("**/api/settings/database/adb/settings");
  await page.route("**/api/settings/database/adb/settings", async (route) => {
    settingsCount += 1;
    await settingsGate.promise;
    await fulfillJson(route, adbInfoFixture({ lifecycle_state: "STOPPED" }));
  });
  await page.unroute("**/api/settings/database/adb/start");
  await page.route("**/api/settings/database/adb/start", async (route) => {
    startCount += 1;
    await startGate.promise;
    await fulfillJson(
      route,
      adbInfoFixture({
        lifecycle_state: "STARTING",
        message: "データベース 'NL2SQLDB' の起動を開始しました。",
      })
    );
  });

  await page.goto("/settings/database");
  const adbCard = page.locator("#adb-management");
  await adbCard.getByRole("button", { name: "起動", exact: true }).click();

  await expect.poll(() => settingsCount).toBe(1);
  await expectAdbActionButtonsStableDuringOperation(page, {
    start: "起動中…",
    stop: "停止",
  });

  settingsGate.release();
  await expect.poll(() => startCount).toBe(1);
  await expectAdbActionButtonsStableDuringOperation(page, {
    start: "起動中…",
    stop: "停止",
  });

  await page.setViewportSize({ width: 375, height: 812 });
  await expectAdbActionButtonsStableDuringOperation(page, {
    start: "起動中…",
    stop: "停止",
  });

  startGate.release();
  await expect(page.getByText("データベース 'NL2SQLDB' の起動を開始しました。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("ADB 停止中は保存ボタンを無効化して保存表示のままにする", async ({ page }) => {
  const settingsGate = createRequestGate();
  const stopGate = createRequestGate();
  let settingsCount = 0;
  let stopCount = 0;

  await page.unroute("**/api/settings/database/adb/settings");
  await page.route("**/api/settings/database/adb/settings", async (route) => {
    settingsCount += 1;
    await settingsGate.promise;
    await fulfillJson(route, adbInfoFixture({ lifecycle_state: "AVAILABLE" }));
  });
  await page.unroute("**/api/settings/database/adb/stop");
  await page.route("**/api/settings/database/adb/stop", async (route) => {
    stopCount += 1;
    await stopGate.promise;
    await fulfillJson(
      route,
      adbInfoFixture({
        lifecycle_state: "STOPPING",
        message: "データベース 'NL2SQLDB' の停止を開始しました。",
      })
    );
  });

  await page.goto("/settings/database");
  const adbCard = page.locator("#adb-management");
  await adbCard.getByRole("button", { name: "停止", exact: true }).click();

  await expect.poll(() => settingsCount).toBe(1);
  await expectAdbActionButtonsStableDuringOperation(page, {
    start: "起動",
    stop: "停止中…",
  });

  settingsGate.release();
  await expect.poll(() => stopCount).toBe(1);
  await expectAdbActionButtonsStableDuringOperation(page, {
    start: "起動",
    stop: "停止中…",
  });

  await page.setViewportSize({ width: 375, height: 812 });
  await expectAdbActionButtonsStableDuringOperation(page, {
    start: "起動",
    stop: "停止中…",
  });

  stopGate.release();
  await expect(page.getByText("データベース 'NL2SQLDB' の停止を開始しました。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("ADB 情報の status=error は lifecycle がなくても表示する", async ({ page }) => {
  const safeMessage =
    "ADB 情報を取得できませんでした。OCI 認証、リージョン、ADB OCID を確認して再試行してください。";

  await page.unroute("**/api/settings/database/adb");
  await page.route("**/api/settings/database/adb", (route) =>
    fulfillJson(
      route,
      adbInfoFixture({
        status: "error",
        message: safeMessage,
        error_code: "ADB_INFO_UNAVAILABLE",
        display_name: null,
        lifecycle_state: null,
        db_name: null,
        cpu_core_count: null,
        data_storage_size_in_tbs: null,
      })
    )
  );

  await page.goto("/settings/database");

  const adbCard = page.locator("#adb-management");
  const alert = adbCard.getByRole("alert").filter({ hasText: safeMessage });
  await expect(alert).toBeVisible();
  await expect(adbCard.getByText("raw-sdk-detail")).toHaveCount(0);
  await expect(adbCard.getByText("OCI ADB")).toHaveCount(0);
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 375, height: 812 });
  await expect(alert).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("ADB 情報の not_configured は lifecycle がなくても表示する", async ({ page }) => {
  await page.unroute("**/api/settings/database");
  await page.route("**/api/settings/database", (route) =>
    fulfillJson(route, databaseSettingsFixture({ adb_ocid: "" }))
  );
  await page.unroute("**/api/settings/database/adb");
  await page.route("**/api/settings/database/adb", (route) =>
    fulfillJson(
      route,
      adbInfoFixture({
        status: "not_configured",
        message: "ADB OCID が設定されていません。",
        error_code: "ADB_NOT_CONFIGURED",
        id: null,
        display_name: null,
        lifecycle_state: null,
        db_name: null,
        cpu_core_count: null,
        data_storage_size_in_tbs: null,
      })
    )
  );

  await page.goto("/settings/database");

  const adbCard = page.locator("#adb-management");
  await expect(adbCard.getByRole("status")).toContainText("ADB OCID が設定されていません。");
  await expectNoHorizontalOverflow(page);
});

test("ADB 情報の query error をカード内に表示する", async ({ page }) => {
  const apiError =
    "ADB 情報を取得できませんでした。OCI 認証、リージョン、ADB OCID を確認して再試行してください。";

  await page.unroute("**/api/settings/database/adb");
  await page.route("**/api/settings/database/adb", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({
        data: null,
        error_messages: [apiError],
        warning_messages: [],
        error_code: "ADB_INFO_UNAVAILABLE",
        request_id: "req-adb-info",
      }),
    })
  );

  await page.goto("/settings/database");

  const adbCard = page.locator("#adb-management");
  const alert = adbCard.getByRole("alert").filter({ hasText: apiError });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("req-adb-info");
  await expectNoHorizontalOverflow(page);
});

test("情報を再取得は ADB 情報更新後に Wallet 取得も実行し進行メッセージは出さない", async ({
  page,
}) => {
  const walletGate = createRequestGate();
  let adbRefreshCount = 0;
  let walletDownloadCount = 0;

  await page.unroute("**/api/settings/database/adb/settings");
  await page.route("**/api/settings/database/adb/settings", async (route) => {
    adbRefreshCount += 1;
    await fulfillJson(route, adbInfoFixture());
  });
  await page.unroute("**/api/settings/database/wallet/download");
  await page.route("**/api/settings/database/wallet/download", async (route) => {
    walletDownloadCount += 1;
    await walletGate.promise;
    await fulfillJson(route, {
      status: "downloaded",
      settings: databaseSettingsFixture(),
    });
  });

  await page.goto("/settings/database");
  await expect.poll(() => walletDownloadCount).toBe(0);

  await page.getByRole("button", { name: "情報を再取得" }).click();
  await expect.poll(() => adbRefreshCount).toBe(1);
  await expect.poll(() => walletDownloadCount).toBe(1);
  await expectNoAdbWalletPendingStatus(page);

  walletGate.release();

  await expectNoAdbWalletPendingStatus(page);
  await expect(page.getByText("ADB OCID が設定されています。")).toBeVisible();
  await expect(page.getByText("設定済み", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Oracle Wallet を OCI から取得し、サーバーへ設定しました。")
  ).toBeVisible();
  await expect(
    page.getByText("Oracle Wallet を OCI から取得し、サーバーへ設定しました。")
  ).toHaveCount(1);
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoAdbWalletPendingStatus(page);
  await expectNoHorizontalOverflow(page);
});

test("保存は ADB 情報更新後に Wallet 取得も実行し進行メッセージは出さない", async ({
  page,
}) => {
  const walletGate = createRequestGate();
  let adbRefreshCount = 0;
  let walletDownloadCount = 0;

  await page.unroute("**/api/settings/database/adb/settings");
  await page.route("**/api/settings/database/adb/settings", async (route) => {
    adbRefreshCount += 1;
    await fulfillJson(route, adbInfoFixture());
  });
  await page.unroute("**/api/settings/database/wallet/download");
  await page.route("**/api/settings/database/wallet/download", async (route) => {
    walletDownloadCount += 1;
    await walletGate.promise;
    await fulfillJson(route, {
      status: "downloaded",
      settings: databaseSettingsFixture(),
    });
  });

  await page.goto("/settings/database");
  await expect.poll(() => walletDownloadCount).toBe(0);

  const adbCard = page.locator("#adb-management");
  await adbCard.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => adbRefreshCount).toBe(1);
  await expect.poll(() => walletDownloadCount).toBe(1);
  await expectNoAdbWalletPendingStatus(page);
  await expectNoHorizontalOverflow(page);

  walletGate.release();

  await expectNoAdbWalletPendingStatus(page);
  await expect(page.getByText("ADB OCID が設定されています。")).toBeVisible();
  await expect(
    page.getByText("Oracle Wallet を OCI から取得し、サーバーへ設定しました。")
  ).toBeVisible();
  await expect(
    page.getByText("Oracle Wallet を OCI から取得し、サーバーへ設定しました。")
  ).toHaveCount(1);

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoAdbWalletPendingStatus(page);
  await expectNoHorizontalOverflow(page);
});

test("情報を再取得の Wallet 取得失敗は ADB 操作フィードバックとして表示する", async ({
  page,
}) => {
  let adbRefreshCount = 0;
  let walletDownloadCount = 0;
  const walletError =
    "Wallet 保存領域を使用できません。管理者に保存領域の書き込み権限を確認するよう依頼してから再試行してください。";

  await page.unroute("**/api/settings/database/adb/settings");
  await page.route("**/api/settings/database/adb/settings", async (route) => {
    adbRefreshCount += 1;
    await fulfillJson(route, adbInfoFixture());
  });
  await page.unroute("**/api/settings/database/wallet/download");
  await page.route("**/api/settings/database/wallet/download", async (route) => {
    walletDownloadCount += 1;
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      headers: { "X-Request-ID": "wallet-storage-unavailable-request" },
      body: JSON.stringify({
        data: null,
        error_messages: [walletError],
        warning_messages: [],
        error_code: "WALLET_STORAGE_UNAVAILABLE",
        problem: {
          type: "urn:nl2sql:problem:wallet-storage-unavailable",
          title: "サーバー内部でエラーが発生しました",
          status: 500,
          detail: walletError,
          code: "WALLET_STORAGE_UNAVAILABLE",
          request_id: "wallet-storage-unavailable-request",
          retryable: true,
          field_errors: [],
        },
      }),
    });
  });

  await page.goto("/settings/database");
  await page.getByRole("button", { name: "情報を再取得" }).click();

  await expect.poll(() => adbRefreshCount).toBe(1);
  await expect.poll(() => walletDownloadCount).toBe(1);
  const adbAlert = page
    .locator("#adb-management")
    .getByRole("alert")
    .filter({ hasText: /保存領域の書き込み権限を確認/ });
  await expect(adbAlert).toBeVisible();
  await expect(
    page.getByRole("alert").filter({ hasText: /保存領域の書き込み権限を確認/ })
  ).toHaveCount(1);
  await expect(adbAlert).toContainText("リクエストID: wallet-storage-unavailable-request");
  await expect(page.getByRole("button", { name: "OCI から Wallet を再取得" })).toHaveCount(
    0
  );
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 375, height: 812 });
  await expect(adbAlert).toBeVisible();
  await expectNoHorizontalOverflow(page);
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
  await expect(page.getByTestId("oracle-wallet-upload-dropzone")).toContainText(
    "ドラッグ＆ドロップまたは選択"
  );
  await expect.poll(() => downloadCount).toBe(0);
  await expectNoHorizontalOverflow(page);
});

test("Select AI Credential を明示確認で作成し、成功状態だけを表示する", async ({
  page,
}) => {
  let posted: Record<string, unknown> | null = null;
  let credential = {
    credential_name: "OCI_CRED",
    schema_name: "ADMIN",
    exists: false,
    region: "ap-osaka-1",
    oci_auth_ready: true,
    missing_fields: [],
    operation: null as string | null,
  };
  await page.unroute("**/api/settings/database/select-ai-credential");
  await page.route("**/api/settings/database/select-ai-credential", async (route) => {
    if (route.request().method() === "POST") {
      posted = route.request().postDataJSON() as Record<string, unknown>;
      credential = { ...credential, exists: true, region: "us-chicago-1", operation: "created" };
    }
    await fulfillJson(route, credential);
  });

  await page.goto("/settings/database#select-ai-credential");
  const card = page.getByTestId("select-ai-credential-card");
  await expect(card.getByRole("heading", { name: "Select AI Credential" })).toBeVisible();
  await expect(card.getByText("OCI_CRED", { exact: true })).toBeVisible();
  await expect(card.getByText("ADMIN", { exact: true })).toBeVisible();
  await card.getByRole("combobox", { name: "Select AI 既定リージョン" }).click();
  await page.getByRole("option", { name: "us-chicago-1" }).click();
  const confirmation = card.getByTestId("execution-confirmation-field").getByRole("textbox");
  const create = card.getByRole("button", { name: "Credential を作成" });
  await expect(create).toBeDisabled();
  await confirmation.fill("ADMIN_EXECUTE");
  await expect(create).toBeEnabled();
  await create.click();

  await expect.poll(() => posted).not.toBeNull();
  expect(posted).toEqual({
    region: "us-chicago-1",
    confirmation: "ADMIN_EXECUTE",
    recreate: false,
  });
  expect(posted).not.toHaveProperty("private_key");
  const success = card.getByTestId("select-ai-credential-success");
  await expect(success).toContainText("Select AI Credential を作成");
  await expect(success.getByRole("link")).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});

test("既存 Select AI Credential の再作成は確認語だけで直接実行する", async ({
  page,
}) => {
  let recreateRequest: Record<string, unknown> | null = null;
  const existing = {
    credential_name: "OCI_CRED",
    schema_name: "ADMIN",
    exists: true,
    region: "ap-osaka-1",
    oci_auth_ready: true,
    missing_fields: [],
    operation: null,
  };
  await page.unroute("**/api/settings/database/select-ai-credential");
  await page.route("**/api/settings/database/select-ai-credential", async (route) => {
    if (route.request().method() === "POST") {
      recreateRequest = route.request().postDataJSON() as Record<string, unknown>;
      await fulfillJson(route, { ...existing, operation: "recreated" });
      return;
    }
    await fulfillJson(route, existing);
  });

  await page.goto("/settings/database#select-ai-credential");
  const card = page.getByTestId("select-ai-credential-card");
  await expect(card.getByText("作成済み", { exact: true })).toBeVisible();
  await card.getByTestId("execution-confirmation-field").getByRole("textbox").fill(
    "ADMIN_EXECUTE"
  );
  await card.getByRole("button", { name: "Credential を再作成" }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect.poll(() => recreateRequest).not.toBeNull();
  expect(recreateRequest).toEqual({
    region: "ap-osaka-1",
    confirmation: "ADMIN_EXECUTE",
    recreate: true,
  });
  await expect(card.getByTestId("select-ai-credential-success")).toBeVisible();
});

test("OCI 認証材料不足を 375px で案内し、作成操作を無効化する", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  let statusRequests = 0;
  await page.unroute("**/api/settings/database/select-ai-credential");
  await page.route("**/api/settings/database/select-ai-credential", (route) => {
    statusRequests += 1;
    return fulfillJson(route, {
      credential_name: "OCI_CRED",
      schema_name: "ADMIN",
      exists: false,
      region: "ap-osaka-1",
      oci_auth_ready: false,
      missing_fields: ["fingerprint", "key_file_permissions"],
      operation: null,
    });
  });

  await page.goto("/settings/database#select-ai-credential");
  const card = page.getByTestId("select-ai-credential-card");
  const readinessStatus = card
    .getByRole("status")
    .filter({ hasText: "OCI 認証材料を準備できません" });
  await expect(readinessStatus).toContainText("Fingerprint");
  await expect(readinessStatus).toContainText("秘密鍵のファイル権限 (0600)");
  await expect(readinessStatus).toContainText(
    "OCI 認証設定を確認して、修正後に状態を再取得してください。"
  );
  await expect(card.getByRole("link", { name: "OCI 認証設定を開く" })).toHaveCount(0);
  const initialStatusRequests = statusRequests;
  expect(initialStatusRequests).toBeGreaterThan(0);
  await card.getByRole("button", { name: "状態を再取得" }).click();
  await expect.poll(() => statusRequests).toBe(initialStatusRequests + 1);
  await expect(card.getByRole("button", { name: "Credential を作成" })).toBeDisabled();
  await expect(card.getByTestId("execution-confirmation-field").getByRole("textbox")).toBeDisabled();
  await expectNoHorizontalOverflow(page);
});

test("Select AI Credential 状態の初回取得失敗は標準 ErrorState で再取得できる", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const apiError =
    "Select AI Credential の状態を Oracle から取得できませんでした。データベース接続を確認して再試行してください。";
  let statusRequests = 0;
  await page.unroute("**/api/settings/database/select-ai-credential");
  await page.route("**/api/settings/database/select-ai-credential", (route) => {
    statusRequests += 1;
    return route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({
        data: null,
        error_messages: [apiError],
        warning_messages: [],
        error_code: "SELECT_AI_CREDENTIAL_STATUS_FAILED",
        request_id: "req-select-ai-status-initial",
      }),
    });
  });

  await page.goto("/settings/database#select-ai-credential");
  const card = page.getByTestId("select-ai-credential-card");
  const alert = card.getByRole("alert");
  await expect(alert).toHaveCount(1);
  await expect(alert).toContainText(apiError);
  await expect(alert).toContainText("req-select-ai-status-initial");
  const retry = card.getByRole("button", { name: "状態を再取得" });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect.poll(() => statusRequests).toBeGreaterThanOrEqual(2);
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});

test("Select AI Credential 状態の再取得失敗は標準 Banner で知らせ、旧データを保持する", async ({
  page,
}) => {
  const apiError =
    "Select AI Credential の状態を Oracle から取得できませんでした。データベース接続を確認して再試行してください。";
  let statusRequests = 0;
  let failNextRequest = false;
  await page.unroute("**/api/settings/database/select-ai-credential");
  await page.route("**/api/settings/database/select-ai-credential", (route) => {
    statusRequests += 1;
    if (failNextRequest) {
      failNextRequest = false;
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          data: null,
          error_messages: [apiError],
          warning_messages: [],
          error_code: "SELECT_AI_CREDENTIAL_STATUS_FAILED",
          request_id: "req-select-ai-status-refresh",
        }),
      });
    }
    return fulfillJson(route, {
      credential_name: "OCI_CRED",
      schema_name: "ADMIN",
      exists: false,
      region: "ap-osaka-1",
      oci_auth_ready: false,
      missing_fields: ["fingerprint"],
      operation: null,
    });
  });

  await page.goto("/settings/database#select-ai-credential");
  const card = page.getByTestId("select-ai-credential-card");
  await expect(card.getByText("OCI_CRED", { exact: true })).toBeVisible();
  await expect(card.getByText("ADMIN", { exact: true })).toBeVisible();
  const initialStatusRequests = statusRequests;
  failNextRequest = true;
  await card.getByRole("button", { name: "状態を再取得" }).click();
  await expect.poll(() => statusRequests).toBeGreaterThan(initialStatusRequests);
  const alert = card.getByRole("alert");
  await expect(alert).toHaveCount(1);
  await expect(alert).toContainText(apiError);
  await expect(alert).toContainText("req-select-ai-status-refresh");
  await expect(card.getByText("OCI_CRED", { exact: true })).toBeVisible();
  await expect(card.getByText("ADMIN", { exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: "状態を再取得" })).toHaveCount(1);
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});

test("Select AI Credential API 失敗は固定 alert だけに表示し Toast を重複させない", async ({
  page,
}) => {
  const apiError = "Select AI Credential を Oracle に作成できませんでした。";
  await page.unroute("**/api/settings/database/select-ai-credential");
  await page.route("**/api/settings/database/select-ai-credential", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({
          data: null,
          error_messages: [apiError],
          warning_messages: [],
          error_code: "SELECT_AI_CREDENTIAL_CREATE_FAILED",
        }),
      });
      return;
    }
    await fulfillJson(route, {
      credential_name: "OCI_CRED",
      schema_name: "ADMIN",
      exists: false,
      region: "ap-osaka-1",
      oci_auth_ready: true,
      missing_fields: [],
      operation: null,
    });
  });

  await page.goto("/settings/database#select-ai-credential");
  const card = page.getByTestId("select-ai-credential-card");
  await card.getByTestId("execution-confirmation-field").getByRole("textbox").fill(
    "ADMIN_EXECUTE"
  );
  await card.getByRole("button", { name: "Credential を作成" }).click();

  await expect(card.getByRole("alert").filter({ hasText: apiError })).toBeVisible();
  await expect(card.getByRole("link", { name: "OCI 認証設定を開く" })).toHaveCount(0);
  await expect(page.getByText(apiError, { exact: true })).toHaveCount(1);
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
});

test("外観設定でダーク/ライト/自動テーマを切り替えられる", async ({ page }) => {
  await page.goto("/settings/appearance");
  await expect(page.getByRole("heading", { name: "外観" })).toBeVisible();
  const html = page.locator("html");
  const bgVar = () =>
    page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--background").trim());
  const sidebarBgVar = () =>
    page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--sidebar").trim());
  const tokenVar = (name: string) =>
    page.evaluate(
      (tokenName) => getComputedStyle(document.documentElement).getPropertyValue(tokenName).trim(),
      name
    );

  // 既定はライト。
  await expect(html).not.toHaveClass(/dark/);
  expect(await bgVar()).toBe("#f7f8fa");

  const toggle = page.getByTestId("appearance-theme-toggle");
  await toggle.getByRole("button", { name: "ダーク" }).click();
  await expect(html).toHaveClass(/dark/);
  // 冷調 blue-gray の semantic token。純黒・大面積の純白は使わない。
  expect(await bgVar()).toBe("#101318");
  expect(await sidebarBgVar()).toBe("#0b0e13");
  expect(await tokenVar("--foreground")).toBe("#f2f4f7");
  expect(await tokenVar("--muted")).toBe("#b2bac5");
  expect(await tokenVar("--control-border")).toBe("#5d6878");
  expect(await tokenVar("--primary-fill")).toBe("#286abd");
  await expect(toggle.getByRole("button", { name: "ダーク" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("heading", { name: "外観" })).toHaveCSS(
    "color",
    "rgb(242, 244, 247)"
  );

  // 再読込しても永続化される。
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);

  // 自動テーマは実行中の OS 設定変更にも追従する。
  await page.emulateMedia({ colorScheme: "dark" });
  await page.getByTestId("appearance-theme-toggle").getByRole("button", { name: "自動（OS 設定）" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  expect(await bgVar()).toBe("#101318");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  expect(await bgVar()).toBe("#f7f8fa");

  await page.getByTestId("appearance-theme-toggle").getByRole("button", { name: "ライト" }).click();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  expect(await bgVar()).toBe("#f7f8fa");
});
