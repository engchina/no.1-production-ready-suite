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

async function mockRagSettingsApi(page: Page) {
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
            model_id: "enterprise-rag-llm",
            display_name: "業務 RAG 標準",
            vision_enabled: false,
          },
          {
            model_id: "enterprise-rag-vlm",
            display_name: "OCR / Vision",
            vision_enabled: true,
          },
        ],
        default_model_id: "enterprise-rag-llm",
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
    user: "RAG_APP",
    dsn: "ragdb_high",
    wallet_dir: "/u01/aipoc/instantclient_23_26/network/admin",
    wallet_uploaded: true,
    available_services: ["ragdb_high", "ragdb_low"],
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
    display_name: "ragdb",
    lifecycle_state: "AVAILABLE",
    db_name: "RAGDB",
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
      model_id: "enterprise-rag-llm",
      message: "enterprise-rag-llm の設定を確認しました。",
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

async function expectRagStyleShellFillsViewport(page: Page) {
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

test.beforeEach(async ({ page }) => {
  await mockRagSettingsApi(page);
});

test("RAG 由来の 4 つのシステム設定画面を表示できる", async ({ page }) => {
  await page.goto("/settings/oci");
  await expect(page.getByRole("heading", { name: "OCI 認証設定" }).first()).toBeVisible();
  await expect(page.getByLabel("ユーザー OCID")).toBeVisible();
  await expectRagStyleShellFillsViewport(page);
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
  await expectNoHorizontalOverflow(page);
});

test("旧 NL2SQL 設定は名前を変えたルートに残っている", async ({ page }) => {
  await page.goto("/settings/nl2sql-connection");
  await expect(page.getByRole("heading", { name: "NL2SQL 接続診断" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
