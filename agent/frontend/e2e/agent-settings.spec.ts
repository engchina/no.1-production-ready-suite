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

async function expectElementAbove(page: Page, upperSelector: string, lowerSelector: string) {
  const upperBox = await page.locator(upperSelector).boundingBox();
  const lowerBox = await page.locator(lowerSelector).boundingBox();

  expect(upperBox).not.toBeNull();
  expect(lowerBox).not.toBeNull();
  expect(upperBox!.y).toBeLessThan(lowerBox!.y);
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
          object_storage_region: "",
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
          region: "",
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
          object_storage_region: "",
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
          adb_ocid: "ocid1.autonomousdatabase.oc1.ap-osaka-1.agent",
          region: "ap-osaka-1",
          config_source: "runtime",
        },
      }),
    });
  });
  await page.route("**/api/settings/database/adb/settings", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          status: "success",
          message: "ADB OCID を保存しました。",
          id: "ocid1.autonomousdatabase.oc1.ap-osaka-1.agent",
          display_name: null,
          lifecycle_state: "AVAILABLE",
          db_name: null,
          cpu_core_count: null,
          data_storage_size_in_tbs: null,
          region: "ap-osaka-1",
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
    await expectElementAbove(page, "#oci-config-file", "#oci-user-ocid");
    await expectElementAbove(page, "#oci-config-profile", "#oci-tenancy-ocid");
    await expect(page.getByLabel("ユーザー OCID")).toHaveValue("");
    await expect(page.getByLabel("テナンシ OCID")).toHaveValue("");
    await expect(page.getByLabel("フィンガープリント")).toHaveValue("");
    await expect(page.getByRole("combobox", { name: "リージョン", exact: true })).toContainText("選択してください");
    await expect(page.getByText("OCI_REGION=ap-osaka-1")).toHaveCount(0);
    await expect(page.getByText(/=None/)).toHaveCount(0);
    await page.locator("main").evaluate((main) => {
      main.scrollTop = main.scrollHeight;
    });
    await expectDocumentScrollLocked(page);
    await page.getByLabel("ユーザー OCID").fill("ocid1.user.oc1..aaaaaaaa");
    await page.getByLabel("テナンシ OCID").fill("ocid1.tenancy.oc1..aaaaaaaa");
    await page.getByLabel("フィンガープリント").fill("12:34:56:78:90:ab:cd:ef");
    await page.getByRole("combobox", { name: "リージョン", exact: true }).click();
    await page.getByRole("option", { name: "ap-osaka-1" }).click();
    await page.getByRole("button", { name: /OCI 設定を保存/ }).click();
    await expect(page.getByText("保存しました").first()).toBeVisible();
    await page.getByRole("combobox", { name: "Object Storage リージョン" }).click();
    await page.getByRole("option", { name: "ap-osaka-1" }).click();
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
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("操作履歴")).toBeVisible();
    await expect(page.getByText("ADB OCID を保存しました。")).toBeVisible();
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

  test("複数 MCP サーバーを登録・既定設定・削除し tool 探索できる", async ({ page }) => {
    await page.goto("/settings/external-mcp");

    await expect(page.getByRole("heading", { name: "外部 MCP", level: 1 })).toBeVisible();
    await expect(page.getByText("MCP tool は外部 JSON-RPC gateway として接続する")).toBeVisible();
    await expect(page.getByRole("heading", { name: "MCP サーバー" })).toBeVisible();
    await expectNoHorizontalOverflow(page);

    // サーバーを追加(フォームと探索パネルで Server ID ラベルが重複するため id 指定)
    await page.getByRole("button", { name: "サーバーを追加" }).click();
    await page.locator("#mcp-server-id").fill("crm");
    await page.locator("#mcp-server-label").fill("CRM Gateway");
    await page.locator("#mcp-server-base-url").fill("http://127.0.0.1:8052/jsonrpc");
    await page.locator("#mcp-server-timeout").fill("7");
    await page.getByRole("button", { name: "作成" }).click();
    await expect(page.getByText("サーバーを追加しました")).toBeVisible();

    // crm 行が描画され、既定に切り替えられる
    await page.getByRole("button", { name: "既定にする crm" }).click();
    await expect(page.getByText("既定サーバーを変更しました")).toBeVisible();

    // tool 探索(crm の mock gateway を引く)
    await expect(page.getByRole("heading", { name: "MCP tools/list" })).toBeVisible();
    await page.locator("#mcp-discovery-server-id").fill("crm");
    await page.locator("#mcp-discovery-trace-id").fill("trace-ui-mcp-list");
    await page.getByRole("button", { name: "取得" }).click();
    await expect(page.getByRole("cell", { name: "lookup_customer" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "search_orders" })).toBeVisible();
    await expectNoHorizontalOverflow(page);

    // モバイル幅でも崩れない
    await page.setViewportSize({ width: 375, height: 812 });
    await expectNoHorizontalOverflow(page);
    await page.setViewportSize({ width: 1280, height: 800 });

    // 削除(crm を消すと既定は default へ戻る)
    await page.getByRole("button", { name: "削除 crm" }).click();
    await page.getByRole("button", { name: "削除", exact: true }).click();
    await expect(page.getByText("サーバーを削除しました")).toBeVisible();
    await expect(page.getByRole("button", { name: "削除 crm" })).toHaveCount(0);
  });

  test("スキルを追加・編集・削除でき、ビルトインは保護される", async ({ page }) => {
    await page.goto("/skills");

    await expect(page.getByRole("heading", { name: "スキル", level: 1 })).toBeVisible();
    // ビルトインは詳細はあるが編集・削除は持たない
    await expect(
      page.getByRole("button", { name: "詳細 business_rag_research" })
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "編集 business_rag_research" })
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "削除 business_rag_research" })
    ).toHaveCount(0);
    await expectNoHorizontalOverflow(page);

    // 追加
    await page.getByRole("button", { name: "スキルを追加" }).click();
    await page.getByLabel("ID", { exact: true }).fill("e2e_custom");
    await page.getByLabel("名前").fill("E2E カスタム");
    await page
      .getByLabel("MCP 依存 (JSON)")
      .fill('[{"server_id":"control-plane","tool_names":["external_rag_search"]}]');
    await page.getByLabel("Resource ID (JSON)").fill('["prompt.e2e"]');
    await page.getByRole("button", { name: "作成" }).click();
    await expect(page.getByText("スキルを追加しました")).toBeVisible();
    await expect(page.getByRole("button", { name: "詳細 e2e_custom" })).toBeVisible();

    // 詳細(progressive disclosure: MCP/resource 依存を表示)
    await page.getByRole("button", { name: "詳細 e2e_custom" }).click();
    await expect(page.locator("pre").filter({ hasText: "external_rag_search" })).toBeVisible();

    // 編集
    await page.getByRole("button", { name: "編集 e2e_custom" }).click();
    await page.getByLabel("名前").fill("E2E カスタム改");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("スキルを更新しました")).toBeVisible();

    // 宣言の再読込
    await page.getByRole("button", { name: "宣言を再読込" }).click();
    await expect(page.getByText("宣言スキルを再読込しました")).toBeVisible();

    // 削除
    await page.getByRole("button", { name: "削除 e2e_custom" }).click();
    await page.getByRole("button", { name: "削除", exact: true }).click();
    await expect(page.getByText("スキルを削除しました")).toBeVisible();
    await expect(page.getByRole("button", { name: "詳細 e2e_custom" })).toHaveCount(0);

    await page.setViewportSize({ width: 375, height: 812 });
    await expectNoHorizontalOverflow(page);
  });

  test("plugin を manifest から install・無効化・アンインストールできる", async ({ page }) => {
    await page.goto("/plugins");
    await expect(
      page.getByRole("heading", { name: "インストール済み連携", level: 1 })
    ).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.getByRole("button", { name: "manifest から install" }).click();
    const manifest = JSON.stringify({
      id: "ui_plugin",
      name: "UI Plugin",
      version: "0.1.0",
      skills: [
        {
          id: "ui_plugin_skill",
          name: "UI Skill",
          mcp_requirements: [],
          resource_ids: ["ui_plugin_prompt"],
        },
      ],
      mcp_servers: [{ server_id: "ui_plugin_mcp", base_url: "http://127.0.0.1:8052/jsonrpc" }],
      resources: [
        {
          id: "ui_plugin_prompt",
          kind: "prompt",
          name: "UI prompt",
          content: "日本語で回答する",
        },
      ],
    });
    await page.locator("#plugin-manifest").fill(manifest);
    await page.getByRole("button", { name: "install", exact: true }).click();
    await expect(page.getByText("連携機能をインストールしました")).toBeVisible();
    await expect(page.getByRole("button", { name: "アンインストール ui_plugin" })).toBeVisible();

    // 無効化(switch トグル)
    await page.getByRole("switch", { name: "有効 ui_plugin" }).click();
    await expect(page.getByText("連携機能の有効状態を更新しました")).toBeVisible();

    // アンインストール
    await page.getByRole("button", { name: "アンインストール ui_plugin" }).click();
    await page.getByRole("button", { name: "アンインストール", exact: true }).click();
    await expect(page.getByText("連携機能をアンインストールしました")).toBeVisible();
    await expect(page.getByRole("button", { name: "アンインストール ui_plugin" })).toHaveCount(0);
  });

  test("marketplace を追加・refresh・install できる", async ({ page }) => {
    await page.goto("/plugins/marketplaces");
    await expect(page.getByRole("heading", { name: "マーケットプレイス", level: 1 })).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.getByRole("button", { name: "マーケットプレイスを追加" }).click();
    await page.locator("#mkt-id").fill("fixture_market");
    await page.locator("#mkt-name").fill("Fixture Market");
    await page.locator("#mkt-url").fill("http://127.0.0.1:8052/marketplace");
    await page.getByRole("button", { name: "作成" }).click();
    await expect(page.getByText("マーケットプレイスを追加しました")).toBeVisible();

    // リモート HTTP 取得
    await page.getByRole("button", { name: "更新 fixture_market" }).click();
    await expect(page.getByText("連携機能一覧を更新しました")).toBeVisible();

    // 閲覧 → install
    await page.getByRole("button", { name: "連携機能を見る fixture_market" }).click();
    await expect(page.getByRole("heading", { name: "利用可能な連携機能" })).toBeVisible();
    await expect(page.getByText("Fixture Plugin")).toBeVisible();
    await page.getByRole("button", { name: "install fixture_plugin" }).click();
    await expect(page.getByText("連携機能をインストールしました")).toBeVisible();

    // cleanup: plugins ページでアンインストール、marketplace を削除
    await page.goto("/plugins");
    await page.getByRole("button", { name: "アンインストール fixture_plugin" }).click();
    await page.getByRole("button", { name: "アンインストール", exact: true }).click();
    await expect(page.getByText("連携機能をアンインストールしました")).toBeVisible();

    await page.goto("/plugins/marketplaces");
    await page.getByRole("button", { name: "削除 fixture_market" }).click();
    await page.getByRole("button", { name: "削除", exact: true }).click();
    await expect(page.getByText("マーケットプレイスを削除しました")).toBeVisible();
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

  test("業務 Agent は Skill だけを選択して保存できる", async ({ page }) => {
    await page.goto("/agents");

    await expect(page.getByRole("heading", { name: "業務 Agent", level: 1 })).toBeVisible();
    await page.locator("#new-agent-name").fill("RAG Skill Agent");
    await page.locator("#new-agent-description").fill("Skill で能力を選択する");
    await page
      .locator("label")
      .filter({ hasText: "業務 RAG 調査" })
      .first()
      .locator("input")
      .check();
    await page.getByRole("button", { name: "作成" }).first().click();
    await expect(page.getByText("Agent を作成しました")).toBeVisible();
    await expect(page.getByText("Command allowed prefixes")).toHaveCount(0);
    await expectNoHorizontalOverflow(page);

    await page.setViewportSize({ width: 375, height: 812 });
    await expect(page.getByText("業務 RAG 調査").first()).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("Control Plane バックアップを検証できる", async ({ page }) => {
    await page.goto("/settings/runtime-snapshot");

    await expect(
      page.getByRole("heading", { name: "Control Plane バックアップ", level: 1 })
    ).toBeVisible();
    const exportTextarea = page.locator("#runtime-snapshot-export");
    await expect(exportTextarea).toHaveValue(/agent-control-plane\.snapshot\.v2/);
    const exportText = await exportTextarea.inputValue();
    const snapshot = JSON.parse(exportText) as {
      version: string;
      agents: Array<Record<string, unknown>>;
    };
    expect(snapshot.version).toBe("agent-control-plane.snapshot.v2");

    await page.getByRole("button", { name: "現在値を入力へ反映" }).click();
    await expect(page.locator("#runtime-snapshot-import")).toHaveValue(
      /agent-control-plane\.snapshot\.v2/
    );
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
