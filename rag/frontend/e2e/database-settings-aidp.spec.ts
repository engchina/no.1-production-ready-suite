import {
  AIDP_CARD_TEST_ID,
  AIDP_CATALOG_GUIDE,
  AIDP_DB_USER_EXISTS_BADGE,
  AIDP_DB_USER_UNKNOWN_BADGE,
  AIDP_INTEGRATION,
  AIDP_OPEN_LINK_TEST_ID,
  AIDP_READONLY_FIELDS,
} from "../../../platform/packages/system-settings/e2e/aidp-integration";
import { expect, type Page, test } from "./fixtures/test";

import { expectNoPageOverflow, mockLocalAuth } from "./_helpers";

// #5931: システム設定 › データベースの「Oracle AI Data Platform」の区画（3 製品共通。読み取り専用）。

const databaseSettings = {
  user: "rag_app",
  dsn: "appdb_high",
  wallet_dir: "/u01/aipoc/wallet",
  wallet_uploaded: true,
  available_services: ["appdb_high"],
  has_password: true,
  has_wallet_password: false,
  readiness: "ok",
  embedding_dimension: 1536,
  vector_column: "VECTOR(1536, FLOAT32)",
  adb_ocid: "",
  region: "ap-osaka-1",
  driver_mode: "thin" as const,
  connection_security: "wallet_mtls" as const,
  client_lib_dir: "",
  config_source: "runtime" as const,
};

const adbNotConfigured = {
  status: "not_configured",
  message: "ADB OCID が設定されていません。",
  error_code: "ADB_NOT_CONFIGURED",
  id: null,
  display_name: null,
  lifecycle_state: null,
  db_name: null,
  cpu_core_count: null,
  data_storage_size_in_tbs: null,
  region: "ap-osaka-1",
};

/** データベース設定の API を合成の応答にする。戻り値は AIDP の API が呼ばれた回数。 */
async function mockDatabase(
  page: Page,
  options: { aidpConfigured: boolean; aidp?: Record<string, unknown> },
): Promise<() => number> {
  let aidpCalls = 0;
  const envelope = (data: unknown) => ({
    json: { data, error_messages: [], warning_messages: [] },
  });
  await page.route("**/api/settings/database**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/settings/database/aidp") {
      aidpCalls += 1;
      await route.fulfill(envelope(options.aidp ?? AIDP_INTEGRATION));
      return;
    }
    if (url.pathname === "/api/settings/database/adb") {
      await route.fulfill(envelope(adbNotConfigured));
      return;
    }
    await route.fulfill(
      envelope({ ...databaseSettings, aidp_configured: options.aidpConfigured }),
    );
  });
  return () => aidpCalls;
}

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

test(
  "Oracle AI Data Platform の区画に、URL のリンクと External Catalog の登録に使う値が読み取り専用で出る",
  { tag: "@responsive" },
  async ({ page }) => {
    await mockDatabase(page, { aidpConfigured: true });
    await page.goto("/settings/database");

    const card = page.getByTestId(AIDP_CARD_TEST_ID);
    await expect(card.getByRole("heading", { name: "Oracle AI Data Platform" })).toBeVisible();
    await expect(card.getByText(AIDP_DB_USER_EXISTS_BADGE)).toBeVisible();

    // URL は新しいタブで開くリンク。
    const link = card.getByTestId(AIDP_OPEN_LINK_TEST_ID);
    await expect(link).toHaveAttribute("href", AIDP_INTEGRATION.url);
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");

    for (const [id, value] of AIDP_READONLY_FIELDS) {
      const field = card.locator(`#${id}`);
      await expect(field).toHaveValue(value);
      await expect(field).toHaveJSProperty("readOnly", true);
    }
    // ADB の情報が取れないときは、接続のサービス名から読んだ DB 名を出す。
    await expect(card.locator("#aidp-catalog-db-name")).toHaveValue(AIDP_INTEGRATION.adb_db_name);

    // 読み取り専用: パスワードの欄も、登録・編集の操作も無い（External Catalog の登録は AIDP の画面で行う）。
    await expect(card.getByText(AIDP_CATALOG_GUIDE)).toBeVisible();
    await expect(card.locator('input[type="password"]')).toHaveCount(0);
    await expect(card.getByRole("button")).toHaveText(["情報を再取得"]);

    await expectNoPageOverflow(page);
  },
);

test("DB に接続できずユーザーを確かめられないときも、区画と画面は出たままにする", async ({ page }) => {
  await mockDatabase(page, {
    aidpConfigured: true,
    aidp: { ...AIDP_INTEGRATION, db_user_status: "unknown" },
  });
  await page.goto("/settings/database");

  const card = page.getByTestId(AIDP_CARD_TEST_ID);
  await expect(card.getByText(AIDP_DB_USER_UNKNOWN_BADGE)).toBeVisible();
  await expect(card.getByText("データベースに接続できないため")).toBeVisible();
  // 接続設定の区画（h2）は出たまま。
  await expect(page.getByRole("heading", { name: "データベース設定", level: 2 })).toBeVisible();
});

test("Oracle AI Data Platform が未設定なら、区画を出さず API も呼ばない", async ({ page }) => {
  const aidpCalls = await mockDatabase(page, { aidpConfigured: false });
  await page.goto("/settings/database");

  // データベース設定の読み込みが終わり、接続設定の区画（h2）が出てから確かめる。
  await expect(page.getByRole("heading", { name: "データベース設定", level: 2 })).toBeVisible();
  await expect(page.getByTestId(AIDP_CARD_TEST_ID)).toHaveCount(0);
  expect(aidpCalls()).toBe(0);
});
