import type { Locator, Page } from "@playwright/test";

import { expect, test } from "./fixtures/mock-api";
import { chooseSelectFieldOption } from "./fixtures/select-field";

async function expectNoHorizontalOverflow(page: Page) {
  const hasNoOverflow = await page.evaluate(() => {
    return document.documentElement.scrollWidth <= document.documentElement.clientWidth;
  });
  expect(hasNoOverflow).toBe(true);
}

/** 行の操作メニュー（RowActionMenu）を開いて項目を選ぶ。 */
async function chooseRowAction(page: Page, name: string, item: string) {
  await page.getByRole("button", { name: `${name} の操作` }).click();
  await page.getByRole("menuitem", { name: item }).click();
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

/** テキストの欄が Vision の欄より先（同じ行なら左、縦積みなら上）にある（#566）。 */
async function expectTextBeforeVision(text: Locator, vision: Locator) {
  const textBox = await text.boundingBox();
  const visionBox = await vision.boundingBox();
  expect(textBox).not.toBeNull();
  expect(visionBox).not.toBeNull();
  const sameRow = Math.abs(textBox!.y - visionBox!.y) < 1;
  expect(sameRow ? textBox!.x < visionBox!.x : textBox!.y < visionBox!.y).toBe(true);
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
      // 保存などは e2e/fixtures/mock-api.ts が扱う（実 backend へは流さない）。
      await route.fallback();
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          backend: "local",
          local_storage_dir: "/u01/data/production-ready-agent",
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
      // 保存などは e2e/fixtures/mock-api.ts が扱う（実 backend へは流さない）。
      await route.fallback();
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
      // 保存などは e2e/fixtures/mock-api.ts が扱う（実 backend へは流さない）。
      await route.fallback();
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          backend: "local",
          local_storage_dir: "/u01/data/production-ready-agent",
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
      // 保存などは e2e/fixtures/mock-api.ts が扱う（実 backend へは流さない）。
      await route.fallback();
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
  test("RAG 由来のシステム設定 4 画面を表示・保存できる", async ({ page, mockApi }) => {
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
    await expect(page.getByText("PLATFORM_OCI_REGION=ap-osaka-1")).toHaveCount(0);
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
    expect(mockApi.lastRequest("PATCH", "/api/settings/oci")?.body).toMatchObject({
      user: "ocid1.user.oc1..aaaaaaaa",
      tenancy: "ocid1.tenancy.oc1..aaaaaaaa",
      fingerprint: "12:34:56:78:90:ab:cd:ef",
      region: "ap-osaka-1",
    });
    await page.getByRole("combobox", { name: "Object Storage リージョン" }).click();
    await page.getByRole("option", { name: "ap-osaka-1" }).click();
    await page.getByRole("button", { name: /Object Storage ネームスペース: 取得/ }).click();
    await expect(
      page.getByRole("textbox", { name: /Object Storage ネームスペース/ })
    ).toHaveValue(
      "mytenancynamespace"
    );
    await expect(page.getByText("namespace の取得に失敗しました。")).toHaveCount(0);
    await page.getByRole("button", { name: /Object Storage: 保存/ }).click();
    await expect(page.getByText("Object Storage 設定を保存しました。").first()).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.goto("/settings/upload-storage");
    await expect(page.getByRole("heading", { name: "アップロード保存先", level: 1 })).toBeVisible();
    await page.getByLabel("ローカル保存ディレクトリ").fill("/u01/data/production-ready-agent");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("保存しました")).toBeVisible();
    expect(mockApi.lastRequest("PATCH", "/api/settings/upload-storage")?.body).toMatchObject({
      backend: "local",
      local_storage_dir: "/u01/data/production-ready-agent",
    });
    await expectNoHorizontalOverflow(page);

    await page.goto("/settings/model");
    await expect(page.getByRole("heading", { name: "モデル設定", level: 1 })).toBeVisible();
    // OCI Enterprise AI の接続はプライマリ接続・セカンダリ接続のタブ。登録モデルごとに接続を選ぶ（#533 / #542）。
    const connectionTabs = page.getByRole("tablist", { name: "OCI Enterprise AI の接続" });
    await expect(connectionTabs.getByRole("tab", { name: "プライマリ接続" })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    await connectionTabs.getByRole("tab", { name: "セカンダリ接続" }).click();
    await expect(page.getByRole("button", { name: "セカンダリ接続を設定" })).toBeVisible();
    await connectionTabs.getByRole("tab", { name: "プライマリ接続" }).click();
    await expect(page.getByRole("combobox", { name: "モデル 1 の接続" })).toContainText(
      "プライマリ接続"
    );
    await page.getByRole("textbox", { name: "API key" }).fill("test-api-key");
    await page.getByRole("button", { name: "OCI Enterprise AI: 保存" }).click();
    await expect(page.getByText("OCI Enterprise AI 接続設定を保存しました。").first()).toBeVisible();
    expect(mockApi.lastRequest("PATCH", "/api/settings/model")?.body).toMatchObject({
      // 接続情報の節の保存は、既定のモデル 2 つを保存済みの値のまま送る（#499）。
      enterprise_ai: {
        connections: [{ connection_id: "primary", api_key: "test-api-key" }],
        default_text_model_id: "enterprise-llm",
        default_vision_model_id: "enterprise-llm",
      },
    });
    const textDefault = page.getByRole("combobox", { name: "既定のテキストモデル" });
    const visionDefault = page.getByRole("combobox", { name: "既定の画像対応モデル" });
    await expect(visionDefault).toContainText("業務 RAG 標準");
    await expect(textDefault).toContainText("業務 RAG 標準");
    // 並びはテキスト → Vision で、2 つとも必須（#566）。
    await expectTextBeforeVision(textDefault, visionDefault);
    await expect(textDefault).toHaveAttribute("aria-required", "true");
    await expect(visionDefault).toHaveAttribute("aria-required", "true");
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
      if (route === "/settings/model") {
        // 375px の縦積みでもテキスト → Vision の順（#566）。
        await expectTextBeforeVision(
          page.getByRole("combobox", { name: "既定のテキストモデル" }),
          page.getByRole("combobox", { name: "既定の画像対応モデル" })
        );
      }
    }
  });

  test("ツール権限を表示して保存できる", async ({ page }) => {
    await page.goto("/settings/tool-policy");

    await expect(page.getByRole("heading", { name: "ツール権限", level: 1 })).toBeVisible();
    await expect(page.getByLabel("未指定ツールの既定動作")).toBeVisible();
    await expect(page.getByText("external_rag_search")).toBeVisible();
    await expect(page.getByText("external_nl2sql_query")).toBeVisible();
    await expect(page.getByText("external_mcp_call")).toBeVisible();
    await expect(page.getByText("sandbox_command_run")).toBeVisible();
    // 権限レベルと side_effects は tool の分類（状態ではない）なので、StatusBadge のアイコンを付けない
    const badges = page.locator("main [data-status-variant]");
    await expect(badges.filter({ hasText: /^(read|write|sensitive|side_effects)$/ }).first()).toBeVisible();
    await expect(badges.filter({ hasText: /^(read|write|sensitive|side_effects)$/ }).locator("svg")).toHaveCount(0);

    const firstPolicy = page.getByRole("combobox", { name: "ポリシー", exact: true }).first();
    // 連続保存では前回のトーストが残るため、保存 API の成功を待ってから最新のトーストを確認する。
    const savePolicy = async () => {
      const saved = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/settings/tool-policy") &&
          response.request().method() === "PATCH" &&
          response.ok()
      );
      await page.getByRole("button", { name: "保存" }).click();
      await saved;
      await expect(page.getByText("設定を保存しました").last()).toBeVisible();
    };
    await chooseSelectFieldOption(firstPolicy, { label: "自動実行" });
    await savePolicy();

    await chooseSelectFieldOption(firstPolicy, { label: "既定に従う" });
    await savePolicy();
  });

  test("複数 MCP サーバーを登録・既定設定・削除し tool 探索できる", async ({ page }) => {
    await page.goto("/settings/external-mcp");

    await expect(page.getByRole("heading", { name: "外部 MCP", level: 1 })).toBeVisible();
    await expect(page.getByText("MCP tool は外部 JSON-RPC gateway として接続する")).toBeVisible();
    await expect(page.getByRole("heading", { name: "MCP サーバー" })).toBeVisible();
    await expectNoHorizontalOverflow(page);

    // サーバーを追加（全画面エディタ ?id=new。フォームと探索パネルで Server ID ラベルが重複するため id 指定）
    await page.getByRole("button", { name: "サーバーを追加" }).click();
    await expect(page).toHaveURL(/\/settings\/external-mcp\?id=new$/);
    await page.locator("#mcp-server-id").fill("crm");
    await page.locator("#mcp-server-label").fill("CRM Gateway");
    await page.locator("#mcp-server-base-url").fill("http://mcp.example.test/jsonrpc");
    await page.locator("#mcp-server-timeout").fill("7");
    await page.getByRole("button", { name: "作成" }).click();
    await expect(page.getByText("サーバーを追加しました")).toBeVisible();
    // 作成後は作成した対象のエディタへ移り、概要の ObjectActionBar で既定に切り替えられる
    await expect(page).toHaveURL(/\?id=crm$/);
    await expect(page.getByRole("heading", { name: "CRM Gateway", level: 1 })).toBeVisible();
    await page.getByTestId("mcp-server-object-actions").getByRole("button", { name: "既定にする" }).click();
    await expect(page.getByText("既定サーバーを変更しました")).toBeVisible();

    // tool 探索（一覧に戻って crm の mock gateway を引く）
    await page.getByTestId("editor-back").click();
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

    // 削除（行メニュー → 確認。crm を消すと既定は default へ戻る）
    await chooseRowAction(page, "crm", "削除");
    await page.getByRole("button", { name: "削除", exact: true }).click();
    await expect(page.getByText("サーバーを削除しました")).toBeVisible();
    await expect(page.getByRole("button", { name: "crm の操作" })).toHaveCount(0);
  });

  test("スキルを追加・編集・削除でき、ビルトインは保護される", async ({ page }) => {
    await page.goto("/skills");

    await expect(page.getByRole("heading", { name: "スキル", level: 1 })).toBeVisible();
    // ビルトインは詳細を開けるが、行メニュー（削除）を持たない
    await expect(page.getByRole("button", { name: "業務 RAG 調査 の操作" })).toHaveCount(0);
    await page.getByRole("link", { name: /業務 RAG 調査/ }).click();
    await expect(page.getByText("このスキルは読み取り専用です", { exact: false })).toBeVisible();
    await expect(page.getByRole("button", { name: "保存" })).toHaveCount(0);
    await page.getByTestId("editor-back").click();
    await expectNoHorizontalOverflow(page);

    // 追加
    await page.getByRole("button", { name: "スキルを追加" }).click();
    // 必須の欄はラベルの後ろに「必須」のタグ（aria-hidden）が付き、getByLabel の exact はタグの文字も含めて比べるため、
    // 支援技術と同じアクセシブルネームで探す（#531）。
    await page.getByRole("textbox", { name: "ID", exact: true }).fill("e2e_custom");
    await page.getByLabel("名前").fill("E2E カスタム");
    await page
      .getByLabel("MCP 依存 (JSON)")
      .fill('[{"server_id":"control-plane","tool_names":["external_rag_search"]}]');
    await page.getByLabel("Resource ID (JSON)").fill('["prompt.e2e"]');
    await page.getByRole("button", { name: "作成" }).click();
    await expect(page.getByText("スキルを追加しました")).toBeVisible();
    await expect(page).toHaveURL(/\/skills\?id=e2e_custom$/);
    // URL の更新は画面の切替（transition）より先に終わるため、編集のエディタが出たことを待つ。
    await expect(page.getByRole("heading", { name: "E2E カスタム", level: 1 })).toBeVisible();
    await expect(page.locator("#skill-id")).toBeDisabled();
    await expect(page.getByLabel("MCP 依存 (JSON)")).toHaveValue(/external_rag_search/);

    // 編集
    await page.getByLabel("名前").fill("E2E カスタム改");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("スキルを更新しました")).toBeVisible();

    // 宣言の再読込（一覧のページ操作）
    await page.getByTestId("editor-back").click();
    await page.getByRole("button", { name: "宣言を再読込" }).click();
    await expect(page.getByText("宣言スキルを再読込しました")).toBeVisible();

    // 削除（行メニュー → 確認）
    await chooseRowAction(page, "E2E カスタム改", "削除");
    await page.getByRole("button", { name: "削除", exact: true }).click();
    await expect(page.getByText("スキルを削除しました")).toBeVisible();
    await expect(page.getByRole("link", { name: /E2E カスタム改/ })).toHaveCount(0);

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
      mcp_servers: [{ server_id: "ui_plugin_mcp", base_url: "http://mcp.example.test/jsonrpc" }],
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
    // install 後は連携機能の詳細へ移り、manifest の内容を確認できる
    await expect(page).toHaveURL(/\/plugins\?id=ui_plugin$/);
    await expect(page.locator("pre").filter({ hasText: "ui_plugin_skill" })).toBeVisible();

    // 無効化（詳細の ObjectActionBar）
    await page.getByTestId("plugin-object-actions").getByRole("button", { name: "無効にする" }).click();
    await expect(page.getByText("連携機能の有効状態を更新しました")).toBeVisible();

    // アンインストール（詳細の「その他の操作」→ 確認。一覧へ戻る）
    await page.getByTestId("plugin-object-actions-more").click();
    await page.getByRole("menuitem", { name: "アンインストール" }).click();
    await page.getByRole("button", { name: "アンインストール", exact: true }).click();
    await expect(page.getByText("連携機能をアンインストールしました")).toBeVisible();
    await expect(page).toHaveURL(/\/plugins$/);
    await expect(page.getByRole("button", { name: "ui_plugin の操作" })).toHaveCount(0);
  });

  test("marketplace を追加・refresh・install できる", async ({ page }) => {
    await page.goto("/plugins/marketplaces");
    await expect(page.getByRole("heading", { name: "マーケットプレイス", level: 1 })).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.getByRole("button", { name: "マーケットプレイスを追加" }).click();
    await page.locator("#mkt-id").fill("fixture_market");
    await page.locator("#mkt-name").fill("Fixture Market");
    await page.locator("#mkt-url").fill("http://marketplace.example.test/marketplace");
    await page.getByRole("button", { name: "作成" }).click();
    await expect(page.getByText("マーケットプレイスを追加しました")).toBeVisible();
    await expect(page).toHaveURL(/\?id=fixture_market$/);
    await expect(page.getByRole("heading", { name: "Fixture Market", level: 1 })).toBeVisible();

    // リモート HTTP 取得（詳細の ObjectActionBar）→ 利用可能な連携機能を行メニューから install
    await expect(page.getByText("連携機能がありません。URL を更新してください。")).toBeVisible();
    await page.getByTestId("marketplace-object-actions").getByRole("button", { name: "更新" }).click();
    await expect(page.getByText("連携機能一覧を更新しました")).toBeVisible();
    await expect(page.getByRole("heading", { name: "利用可能な連携機能" })).toBeVisible();
    await expect(page.getByText("Fixture Plugin")).toBeVisible();
    await chooseRowAction(page, "fixture_plugin", "インストール");
    await expect(page.getByText("連携機能をインストールしました")).toBeVisible();

    // cleanup: plugins ページでアンインストール、marketplace を削除
    await page.goto("/plugins");
    await chooseRowAction(page, "fixture_plugin", "アンインストール");
    await page.getByRole("button", { name: "アンインストール", exact: true }).click();
    await expect(page.getByText("連携機能をアンインストールしました")).toBeVisible();

    await page.goto("/plugins/marketplaces");
    await chooseRowAction(page, "fixture_market", "削除");
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

  test("外部 RAG / NL2SQL は MCP の URL を保存し、サービス間認証の状態を表示する", async ({ page, mockApi }) => {
    await page.goto("/settings/external-rag");

    await expect(page.getByRole("heading", { name: "外部 RAG", level: 1 })).toBeVisible();
    // 未設定のあいだは、足りないものと直し方を warning の Banner で示す。API キー欄はない。
    const notice = page.getByRole("status").filter({ hasText: "この接続はまだ使えません" });
    await expect(notice).toContainText("MCP の URL が未設定です");
    await expect(notice).toContainText("PLATFORM_SERVICE_TOKEN_SECRET");
    await expect(page.getByText("API key")).toHaveCount(0);
    const authStatus = page.getByRole("list", { name: "サービス間認証の状態" });
    await expect(authStatus.getByRole("listitem")).toHaveCount(2);
    await expect(authStatus).toContainText("AGENT_MCP_SERVICE_USER_LOGIN_ID");
    await expect(page.getByLabel("タイムアウト秒")).toHaveValue("60");

    const url = page.getByLabel("MCP の URL");
    await expect(url).toHaveAccessibleDescription(/接続先の製品の \/api\/mcp/);
    await url.fill("rag.example.test");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("MCP の URL は http:// または https:// で始めてください。")).toBeVisible();

    await url.fill("http://rag-host/api/mcp");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("設定を保存しました")).toBeVisible();
    await expect(notice).not.toContainText("MCP の URL が未設定です");
    await expect(notice).toContainText("PLATFORM_SERVICE_TOKEN_SECRET");
    expect(mockApi.state.externalRag).toMatchObject({ mcp_url: "http://rag-host/api/mcp", configured: true });

    // すべて設定済みなら Banner は出さず、状態は StatusBadge で示す。
    Object.assign(mockApi.state.externalNl2Sql, {
      mcp_url: "http://nl2sql-host/api/mcp",
      configured: true,
      service_token_configured: true,
      service_user_configured: true,
    });
    await page.goto("/settings/external-nl2sql");
    await expect(page.getByRole("heading", { name: "外部 NL2SQL", level: 1 })).toBeVisible();
    await expect(page.getByLabel("MCP の URL")).toHaveValue("http://nl2sql-host/api/mcp");
    await expect(page.getByText("この接続はまだ使えません")).toHaveCount(0);
    await expect(
      page.getByRole("list", { name: "サービス間認証の状態" }).getByText("設定済み")
    ).toHaveCount(2);
    await page.getByLabel("既定取得件数").fill("50");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("設定を保存しました")).toBeVisible();
    expect(mockApi.state.externalNl2Sql).toMatchObject({ default_limit: 50 });

    await page.setViewportSize({ width: 375, height: 812 });
    await expect(page.getByLabel("MCP の URL")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  // 保存の失敗は保存ボタンの上の Banner ではなく、操作の行の FormStatus に出す（messaging.md §10.2。#725）。
  for (const viewport of [
    { name: "desktop", width: 1280, height: 800 },
    { name: "mobile", width: 375, height: 812 },
  ]) {
    test(`設定の保存の失敗は操作の行に出す (${viewport.name})`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/settings/external-rag");
      await expect(page.getByRole("heading", { name: "外部 RAG", level: 1 })).toBeVisible();
      await page.route("**/api/settings/external-rag", (route) =>
        route.request().method() === "PATCH"
          ? route.fulfill({
              status: 500,
              contentType: "application/json",
              body: JSON.stringify({ data: null, error_messages: ["設定ファイルに書き込めません。"], warning_messages: [] }),
            })
          : route.fallback()
      );
      await page.getByLabel("MCP の URL").fill("http://rag-host/api/mcp");
      await page.getByRole("button", { name: "保存", exact: true }).click();

      const actions = page.getByRole("group", { name: "外部 RAG の保存" });
      await expect(actions.getByRole("alert")).toHaveText("保存できませんでした。設定ファイルに書き込めません。");
      await expect(page.getByRole("alert")).toHaveCount(1);
      await expectNoHorizontalOverflow(page);
    });
  }

  // #411: 通知は主操作を覆わない。以前は画面の右下に出ていたため、ページの末尾（スクロールしきると画面の下端）の
  // 保存ボタンを覆い、ポインタが通知に乗ると自動の消去が止まって（#351）押せなくなっていた。
  for (const viewport of [
    { name: "desktop", width: 1280, height: 720 },
    { name: "mobile", width: 375, height: 812 },
  ]) {
    test(`通知を出したまま、ページの末尾の保存を押せる (${viewport.name})`, async ({ page, mockApi }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/settings/command-policy");
      await expect(page.getByRole("heading", { name: "Command Policy", level: 1 })).toBeVisible();
      const saves = () =>
        mockApi.requests.filter((request) => request.method === "PATCH" && request.path.endsWith("/settings/command-policy")).length;

      await page.getByLabel("既定タイムアウト秒").fill("4");
      const save = page.getByRole("button", { name: "保存", exact: true });
      await save.click();
      const notice = page.getByRole("region", { name: "通知" }).getByRole("status").filter({ hasText: "設定を保存しました" });
      await expect(notice).toBeVisible();
      await expect.poll(saves).toBe(1);

      // ページの末尾までスクロールすると、保存ボタンは画面の下端に来る（以前の通知の位置）。
      await page.getByLabel("既定タイムアウト秒").fill("5");
      await page.locator("main").evaluate((main) => {
        main.scrollTop = main.scrollHeight;
      });
      await expect(save).toBeInViewport();
      const box = (await save.boundingBox())!;
      expect(viewport.height - (box.y + box.height)).toBeLessThan(120);

      // 通知にポインタを乗せて止めたまま（一時停止は維持）、覆われていない保存ボタンを押せる。
      await notice.hover();
      await expect(notice).toBeVisible();
      const hit = await save.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return top !== null && element.contains(top);
      });
      expect(hit).toBe(true);
      await save.click({ timeout: 5_000 });
      await expect.poll(saves).toBe(2);
      await expectNoHorizontalOverflow(page);
    });
  }

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
    await chooseSelectFieldOption(page.getByRole("combobox", { name: "Artifact storage", exact: true }), {
      label: "Filesystem",
    });
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
    await page.getByRole("button", { name: "業務 Agent を作成" }).click();
    await expect(page).toHaveURL(/\/agents\?id=new$/);
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
    await expect(page.getByRole("heading", { name: "RAG Skill Agent", level: 1 })).toBeVisible();
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

    // 置換の確認語は共有の実行確認語欄（#379）。一致するまで置換を押せない。
    const confirmation = page.getByTestId("execution-confirmation-field");
    const confirmInput = confirmation.getByRole("textbox", { name: "確認入力" });
    const replace = confirmation.getByRole("button", { name: "置換" });
    await expect(confirmInput).toHaveAttribute("aria-required", "true");
    await expect(confirmInput).toHaveAccessibleDescription("置換するには REPLACE と入力してください");
    await expect(confirmation.getByText("入力条件: REPLACE")).toBeVisible();
    await expect(confirmation.getByText("未入力", { exact: true })).toBeVisible();
    await confirmInput.fill("replace");
    await expect(confirmation.getByText("不一致", { exact: true })).toBeVisible();
    await expect(confirmInput).toHaveAttribute("aria-invalid", "true");
    await expect(replace).toBeDisabled();
    await confirmInput.fill("REPLACE");
    await expect(confirmation.getByText("確認済み", { exact: true })).toBeVisible();
    await expect(confirmInput).not.toHaveAttribute("aria-invalid", "true");
    await expect(replace).toBeEnabled();
    await confirmInput.fill("");
    await expect(replace).toBeDisabled();

    await page.setViewportSize({ width: 375, height: 812 });
    await expectNoHorizontalOverflow(page);
  });
});
