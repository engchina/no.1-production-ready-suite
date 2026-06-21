import { expect, test, type Page } from "@playwright/test";

async function expectNoHorizontalOverflow(page: Page) {
  const hasNoOverflow = await page.evaluate(() => {
    return document.documentElement.scrollWidth <= document.documentElement.clientWidth;
  });
  expect(hasNoOverflow).toBe(true);
}

async function expectDocumentScrollLocked(page: Page) {
  await page.evaluate(() => window.scrollTo(0, 1000));
  const metrics = await page.evaluate(() => ({
    windowScrollY: window.scrollY,
    documentClientHeight: document.documentElement.clientHeight,
    documentScrollHeight: document.documentElement.scrollHeight,
    rootClientHeight: document.getElementById("root")?.clientHeight ?? 0,
    rootScrollHeight: document.getElementById("root")?.scrollHeight ?? 0,
  }));

  expect(metrics.windowScrollY).toBe(0);
  expect(metrics.documentScrollHeight).toBeLessThanOrEqual(metrics.documentClientHeight + 1);
  expect(metrics.rootScrollHeight).toBeLessThanOrEqual(metrics.rootClientHeight + 1);
}

async function fillOrSelectDsn(page: Page, value: string) {
  const dsnControl = page.getByLabel("サービス名 / DSN");
  const tagName = await dsnControl.evaluate((element) => element.tagName.toLowerCase());
  if (tagName === "button") {
    await dsnControl.click();
    await page.getByRole("option", { name: value }).click();
    return;
  }
  await dsnControl.fill(value);
}

async function mockMissingOciRuntimeSettings(page: Page) {
  await page.route("**/api/settings/oci/object-storage/namespace", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          namespace: "mytenancynamespace",
        },
      }),
    });
  });
  await page.route("**/api/settings/oci/object-storage", async (route) => {
    if (route.request().method() !== "PATCH") {
      await route.continue();
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          backend: "local",
          local_storage_dir: "/u01/production-ready-rag",
          object_storage_region: "ap-osaka-1",
          object_storage_namespace: "mytenancynamespace",
          object_storage_bucket: "",
          readiness: "ok",
          max_upload_bytes: 104857600,
          config_source: "runtime",
        },
      }),
    });
  });
  await page.route("**/api/settings/oci", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          config_file: "~/.oci/config",
          profile: "DEFAULT",
          user: "",
          fingerprint: "",
          tenancy: "",
          region: "ap-osaka-1",
          key_file: "~/.oci/oci_api_key.pem",
          key_file_exists: false,
          config_file_exists: false,
          config_source: "runtime",
        },
      }),
    });
  });
  await page.route("**/api/settings/upload-storage", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          backend: "local",
          local_storage_dir: "/u01/production-ready-rag",
          object_storage_region: "ap-osaka-1",
          object_storage_namespace: "",
          object_storage_bucket: "",
          readiness: "ok",
          max_upload_bytes: 104857600,
          config_source: "runtime",
        },
      }),
    });
  });
  await page.route("**/api/settings/database", async (route) => {
    const method = route.request().method();
    if (!["GET", "PATCH"].includes(method)) {
      await route.continue();
      return;
    }
    const body =
      method === "PATCH"
        ? ((route.request().postDataJSON() ?? {}) as { user?: string; dsn?: string })
        : {};
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          user: body.user ?? "",
          dsn: body.dsn ?? "ragdb_high",
          wallet_dir: "/u01/aipoc/instantclient_23_26/network/admin",
          wallet_uploaded: true,
          available_services: ["ragdb_high"],
          has_password: method === "PATCH",
          has_wallet_password: false,
          readiness: "ok",
          embedding_dimension: 1536,
          vector_column: "VECTOR(1536, FLOAT32)",
          adb_ocid: "",
          region: "ap-osaka-1",
          config_source: "runtime",
        },
      }),
    });
  });
}

test.describe("Agent Runtime settings", () => {
  test("RAG 由来のシステム設定 4 画面を表示・保存できる", async ({ page }) => {
    await mockMissingOciRuntimeSettings(page);
    await page.goto("/settings/oci");
    await expect(page.getByRole("heading", { name: "OCI 認証設定", level: 1 })).toBeVisible();
    await expectDocumentScrollLocked(page);
    await expect(page.getByLabel("ユーザー OCID")).toHaveValue("");
    await expect(page.getByLabel("テナンシ OCID")).toHaveValue("");
    await expect(page.getByLabel("フィンガープリント")).toHaveValue("");
    await expect(page.getByRole("combobox", { name: "リージョン", exact: true })).toContainText("ap-osaka-1");
    await expect(page.getByText("OCI_REGION=ap-osaka-1")).toBeVisible();
    await expect(page.getByText(/=None/)).toHaveCount(0);
    await page.locator("main").evaluate((main) => {
      main.scrollTop = main.scrollHeight;
    });
    await expectDocumentScrollLocked(page);
    await page.getByLabel("ユーザー OCID").fill("ocid1.user.oc1..aaaaaaaa");
    await page.getByLabel("テナンシ OCID").fill("ocid1.tenancy.oc1..aaaaaaaa");
    await page.getByLabel("フィンガープリント").fill("12:34:56:78:90:ab:cd:ef");
    await page.getByRole("button", { name: /OCI 設定を保存/ }).click();
    await expect(page.getByText("保存しました").first()).toBeVisible();
    await page.getByRole("button", { name: /Object Storage ネームスペース: 取得/ }).click();
    await expect(
      page.getByRole("textbox", { name: /Object Storage ネームスペース/ })
    ).toHaveValue(
      "mytenancynamespace"
    );
    await expect(page.getByText("9/9 入力済み")).toBeVisible();
    await expect(page.getByText("namespace の取得に失敗しました。")).toHaveCount(0);
    await page.getByRole("button", { name: /Object Storage: 保存/ }).click();
    await expect(page.getByRole("button", { name: /Object Storage: 保存しました/ })).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.goto("/settings/upload-storage");
    await expect(page.getByRole("heading", { name: "アップロード保存先", level: 1 })).toBeVisible();
    await page.getByLabel("ローカル保存ディレクトリ").fill("/u01/production-ready-rag");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("保存しました")).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.goto("/settings/model");
    await expect(page.getByRole("heading", { name: "モデル設定", level: 1 })).toBeVisible();
    await page.getByRole("textbox", { name: "API key" }).fill("test-api-key");
    await page.getByRole("button", { name: /保存/ }).click();
    await expect(page.getByText("モデル設定を保存しました")).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.goto("/settings/database");
    await expect(page.getByRole("heading", { name: "データベース設定", level: 1 })).toBeVisible();
    await page.getByLabel("データベースユーザー").fill("rag_app");
    await fillOrSelectDsn(page, "ragdb_high");
    await page.getByLabel("データベースパスワード").fill("secret-password");
    await page.getByRole("button", { name: /DB設定を保存/ }).click();
    await expect(page.getByText("保存しました")).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.setViewportSize({ width: 375, height: 812 });
    for (const route of [
      "/settings/oci",
      "/settings/upload-storage",
      "/settings/model",
      "/settings/database",
    ]) {
      await page.goto(route);
      await expectNoHorizontalOverflow(page);
    }
  });

  test("ツール権限を表示して保存できる", async ({ page }) => {
    await page.goto("/settings/tool-policy");

    await expect(page.getByRole("heading", { name: "ツール権限" })).toBeVisible();
    await expect(page.getByLabel("未指定ツールの既定動作")).toBeVisible();
    await expect(page.getByText("external_rag_search")).toBeVisible();
    await expect(page.getByText("external_nl2sql_query")).toBeVisible();
    await expect(page.getByText("external_mcp_call")).toBeVisible();
    await expect(page.getByText("sandbox_command_run")).toBeVisible();

    const firstPolicy = page.getByLabel("ポリシー").first();
    await firstPolicy.selectOption({ label: "自動実行" });
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("設定を保存しました")).toBeVisible();

    await firstPolicy.selectOption({ label: "既定に従う" });
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("設定を保存しました")).toBeVisible();
  });

  test("外部 MCP gateway 設定を保存できる", async ({ page }) => {
    await page.goto("/settings/external-mcp");

    await expect(page.getByRole("heading", { name: "外部 MCP", level: 1 })).toBeVisible();
    await expect(page.getByText("MCP tool は外部 JSON-RPC gateway として接続する")).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.getByLabel("Base URL").fill("http://127.0.0.1:8052/jsonrpc");
    await page.getByLabel("タイムアウト秒").fill("7");
    await page.getByLabel("MCP Session ID").fill("session-ui-1");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("設定を保存しました")).toBeVisible();
    await expect(page.getByText("設定済み")).toBeVisible();
    await expect(page.getByPlaceholder("設定済み（値は表示しません）")).toBeVisible();

    await expect(page.getByRole("heading", { name: "MCP tools/list" })).toBeVisible();
    await page.getByLabel("Server ID").fill("crm");
    await page.getByLabel("Trace ID").fill("trace-ui-mcp-list");
    await page.getByRole("button", { name: "取得" }).click();
    await expect(page.getByRole("cell", { name: "lookup_customer" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "search_orders" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "顧客情報を検索する" })).toBeVisible();
    await expect(page.getByText("object / 1 fields").first()).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.setViewportSize({ width: 375, height: 812 });
    await expect(page.locator("p").filter({ hasText: "lookup_customer" })).toBeVisible();
    await expect(page.locator("p").filter({ hasText: "search_orders" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("Runtime Safety をモバイル幅でも操作できる", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/settings/runtime-safety");

    await expect(page.getByRole("heading", { name: "Runtime Safety", level: 1 })).toBeVisible();
    await expect(page.getByText("上限を超えた Run は安全に停止し")).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.getByLabel("Run あたり最大ツール呼び出し").fill("20");
    await page.getByLabel("Run あたり最大承認待ち").fill("5");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("設定を保存しました")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("Command Policy を保存してモバイル幅でも確認できる", async ({ page }) => {
    await page.goto("/settings/command-policy");

    await expect(page.getByRole("heading", { name: "Command Policy", level: 1 })).toBeVisible();
    await expect(page.locator("header").getByText("sandbox command の実行許可")).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.getByLabel("sandbox command を有効化").check();
    await page.getByLabel("Workspace root").fill(".");
    await page.getByLabel("Global allowed prefixes").fill("echo\npwd");
    await page.getByLabel("既定タイムアウト秒").fill("4");
    await page.getByLabel("最大タイムアウト秒").fill("6");
    await page.getByLabel("出力上限 bytes").fill("2048");
    await page.getByLabel("Artifact storage", { exact: true }).selectOption({ label: "Filesystem" });
    await page.getByLabel("Artifact storage path").fill(".agent-artifacts-ui");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("設定を保存しました")).toBeVisible();
    await expect(page.getByLabel("Global allowed prefixes")).toHaveValue("echo\npwd");
    await expectNoHorizontalOverflow(page);

    await page.setViewportSize({ width: 375, height: 812 });
    await expect(page.getByLabel("Global allowed prefixes")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("Agent の command prefix policy を保存できる", async ({ page }) => {
    await page.goto("/agents");

    await expect(page.getByRole("heading", { name: "エージェント一覧", level: 1 })).toBeVisible();
    await page.locator("#new-agent-name").fill("Command Policy Agent");
    await page.locator("#new-agent-description").fill("sandbox command の prefix を限定する");
    await page.locator("#new-agent-command-prefixes").fill("echo allowed\npwd");
    await page
      .locator("label")
      .filter({ hasText: "sandbox_command_run" })
      .first()
      .locator("input")
      .check();
    await page.getByRole("button", { name: "作成" }).first().click();
    await expect(page.getByText("Agent を作成しました")).toBeVisible();
    await expect(page.locator("#new-agent-command-prefixes")).toHaveValue("echo allowed\npwd");
    await expectNoHorizontalOverflow(page);

    await page.setViewportSize({ width: 375, height: 812 });
    await expect(page.getByLabel("Command allowed prefixes").first()).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("Runtime Snapshot を検証できる", async ({ page }) => {
    await page.goto("/settings/runtime-snapshot");

    await expect(page.getByRole("heading", { name: "Runtime Snapshot", level: 1 })).toBeVisible();
    const exportTextarea = page.locator("#runtime-snapshot-export");
    await expect(exportTextarea).toHaveValue(/agent-runtime\.snapshot\.v1/);
    const exportText = await exportTextarea.inputValue();
    const snapshot = JSON.parse(exportText) as {
      version: string;
      agents: Array<Record<string, unknown>>;
    };
    expect(snapshot.version).toBe("agent-runtime.snapshot.v1");

    await page.getByRole("button", { name: "現在値を入力へ反映" }).click();
    await expect(page.locator("#runtime-snapshot-import")).toHaveValue(/agent-runtime\.snapshot\.v1/);
    await expect(page.getByRole("button", { name: "置換" })).toBeDisabled();

    await page.getByRole("button", { name: "検証" }).click();
    await expect(page.getByText("Snapshot を検証しました")).toBeVisible();
    await expect(page.getByText("有効")).toBeVisible();
    await expect(page.getByText("検証エラーはありません")).toBeVisible();

    const invalidSnapshot = {
      ...snapshot,
      version: "unsupported",
      agents: [...snapshot.agents, { ...snapshot.agents[0] }],
    };
    await page.getByLabel("インポート JSON").fill(JSON.stringify(invalidSnapshot, null, 2));
    await page.getByRole("button", { name: "検証" }).click();
    await expect(page.getByText("無効")).toBeVisible();
    await expect(page.getByText(/unsupported snapshot version/)).toBeVisible();
    await expect(page.getByText(/duplicate agent id/)).toBeVisible();

    await page.setViewportSize({ width: 375, height: 812 });
    await expectNoHorizontalOverflow(page);
  });
});
