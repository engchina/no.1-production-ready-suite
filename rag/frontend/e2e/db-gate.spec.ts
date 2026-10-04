import { expect, test, type Page } from "./fixtures/test";
import {
  SYSTEM_TABLES_STATUS_OK,
  expectNoPageOverflow,
  mockAuthUser,
  mockLocalAuth,
} from "./_helpers";

/**
 * DB ゲート（3製品共通の部品。#325）: システム設定の 5 画面以外は、DB 接続不可/未設定のとき
 * エラー画面ではなく落ち着いた案内を表示し、状態ごとの導線（未設定・接続できない → データベース設定、
 * 初期化が必要 → システムテーブル）と再試行を出す。設定を開けない利用者には導線を出さず、
 * システム管理者への連絡を案内する（#820）。
 */

type DbStatus = "ok" | "not_configured" | "unreachable" | "setup_required";

function dbStatus(status: DbStatus, check?: string, adbLifecycleState: string | null = null) {
  return {
    data: {
      status,
      check: check ?? (status === "not_configured" ? "missing" : "ok"),
      detail: status === "unreachable" ? "Oracle connection probe failed (ORA-12514)." : null,
      adb_lifecycle_state: adbLifecycleState,
    },
    error_messages: [],
    warning_messages: [],
  };
}

const SETTINGS_HINT =
  "OCI 認証・アップロード保存先・モデル・データベース・外観の各設定ページは引き続き利用できます。";

async function routeAuth(page: Page) {
  await mockLocalAuth(page);
}

async function expectGate(
  page: Page,
  {
    title,
    actionName = "データベース設定を開く",
    actionHref = "/settings/database#adb-management",
    settingsHint = SETTINGS_HINT,
  }: { title: string; actionName?: string; actionHref?: string; settingsHint?: string }
) {
  const card = page.locator('section[aria-labelledby="database-unavailable-title"]');
  await expect(card.getByRole("heading", { level: 1, name: title })).toBeVisible();
  const link = card.getByRole("link", { name: actionName });
  await expect(link).toHaveAttribute("href", actionHref);
  // 全状態で再試行を出す（NL2SQL と同じ）。
  await expect(card.getByRole("button", { name: "再試行" })).toBeVisible();
  await expect(card.getByText(settingsHint, { exact: true })).toBeVisible();
  // 接続先に関わる ORA コードは出さない（#320）。
  await expect(page.getByText(/ORA-12514/)).toHaveCount(0);
  await expectNoPageOverflow(page);
  return link;
}

test("DB 接続済みでも schema 未作成ならシステムテーブルへ案内する", async ({ page }) => {
  await routeAuth(page);
  await page.route("**/api/ready/database", (route) =>
    route.fulfill({ json: dbStatus("setup_required") })
  );

  await page.goto("/file-list");

  const link = await expectGate(page, {
    title: "システムテーブルの作成・更新が必要です",
    actionName: "システムテーブルを開く",
    actionHref: "/settings/system-tables",
    settingsHint:
      "OCI 認証・アップロード保存先・モデル・データベース・システムテーブル・外観の各設定ページは引き続き利用できます。",
  });

  // システムテーブルの画面（運用設定。#658）は未初期化でもゲートに塞がれずに開ける。
  await page.route("**/api/settings/database/system-tables", (route) =>
    route.fulfill({ json: SYSTEM_TABLES_STATUS_OK })
  );
  await link.click();
  await expect(page).toHaveURL(/\/settings\/system-tables$/);
  await expect(page.getByRole("heading", { name: "システムテーブル管理" })).toBeVisible();
  await expect(page.locator('section[aria-labelledby="database-unavailable-title"]')).toHaveCount(0);
});

test("DB 接続不可時、機能ページはエラーではなく起動の案内を表示する", async ({ page }) => {
  await routeAuth(page);
  await page.route("**/api/ready/database", (route) =>
    route.fulfill({ json: dbStatus("unreachable") })
  );
  // 画面本体の API は叩かれない想定だが、保険で 500 を返しておく
  await page.route("**/api/documents**", (route) =>
    route.fulfill({ status: 500, json: { data: null, error_messages: ["boom"], warning_messages: [] } })
  );

  await page.goto("/file-list");

  const link = await expectGate(page, { title: "データベースに接続できません" });
  // 全画面エラー(サーバー内部エラー)ではないこと
  await expect(page.getByText("サーバー内部でエラーが発生しました")).toHaveCount(0);
  // 設定を開くリンク → 再試行の順に Tab で移る。
  await link.focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "再試行" })).toBeFocused();
});

test("DB 未設定時も再試行を出し、Wallet の不備は診断コードと補足で示す", async ({ page }) => {
  await routeAuth(page);
  await page.route("**/api/ready/database", (route) =>
    route.fulfill({ json: dbStatus("not_configured", "wallet_password_invalid") })
  );

  await page.goto("/file-list");

  await expectGate(page, { title: "データベースの接続情報が未設定です", actionHref: "/settings/database" });
  await expect(page.getByText(/RAG 機能\(取込・検索・索引\)を使うには/)).toBeVisible();
  await expect(page.getByText("診断コード: wallet_password_invalid", { exact: true })).toBeVisible();
  await expect(
    page.getByText(
      "暗号化された Wallet を現在の Wallet パスワードで復号できません。Wallet パスワードを確認してください。",
      { exact: true }
    )
  ).toBeVisible();
});

test("再試行で DB が使えるようになったら本来のページを表示する", async ({ page }) => {
  await routeAuth(page);
  let available = false;
  await page.route("**/api/ready/database", (route) =>
    route.fulfill({ json: dbStatus(available ? "ok" : "unreachable") })
  );
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: {
        data: { items: [], total: 0, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );

  await page.goto("/knowledge-bases");
  await expectGate(page, { title: "データベースに接続できません" });

  available = true;
  await page.getByRole("button", { name: "再試行" }).click();
  await expect(page.getByRole("heading", { name: "ナレッジベース", exact: true })).toBeVisible();
  await expect(page.locator('section[aria-labelledby="database-unavailable-title"]')).toHaveCount(0);
});

test("状態の確認中は経過時間付きの読み込み表示を出す", async ({ page }) => {
  await routeAuth(page);
  let release: () => void = () => undefined;
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/ready/database", async (route) => {
    await released;
    await route.fulfill({ json: dbStatus("unreachable") });
  });

  await page.goto("/file-list");

  const loading = page.getByTestId("database-gate-loading");
  await expect(loading).toBeVisible();
  await expect(loading).toContainText("データベースの状態を確認しています");
  release();
  await expect(loading).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "データベースに接続できません" })).toBeVisible();
});

test("システム設定の 5 画面は DB が無くてもゲートを通さずに開ける", async ({ page }) => {
  await routeAuth(page);
  await page.route("**/api/ready/database", (route) =>
    route.fulfill({ json: dbStatus("unreachable") })
  );

  await page.goto("/settings/database");

  // ゲートに塞がれず、データベース設定ページ自体が表示される
  await expect(page.getByRole("heading", { name: "データベース設定" })).toBeVisible();
  await expect(page.locator('section[aria-labelledby="database-unavailable-title"]')).toHaveCount(0);
});

test("RAG 固有の設定・ユーザー管理・権限管理は DB が無いとゲートの案内を出す", async ({ page }) => {
  await routeAuth(page);
  await page.route("**/api/ready/database", (route) =>
    route.fulfill({ json: dbStatus("not_configured") })
  );

  // RAG 固有の設定（取込・検索・回答の設定）は DB に設定を持つ（#325 で NL2SQL と同じ範囲にした）。
  // ユーザーとロールも DB に持つ（#214）。
  for (const path of ["/settings/pipeline", "/settings/retrieval", "/settings/security/users", "/settings/security/permissions"]) {
    await page.goto(path);
    await expect(
      page.getByRole("heading", { name: "データベースの接続情報が未設定です" })
    ).toBeVisible();
  }
});

test("DB 利用可能時は本来のページを表示する", async ({ page }) => {
  await routeAuth(page);
  await page.route("**/api/ready/database", (route) => route.fulfill({ json: dbStatus("ok") }));
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: {
        data: { items: [], total: 0, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );

  await page.goto("/knowledge-bases");

  await expect(page.getByRole("heading", { name: "ナレッジベース", exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "データベースに接続できません" })
  ).toHaveCount(0);
});

test("ADB が停止中なら停止の案内と ADB 管理への導線を出す", async ({ page }) => {
  await routeAuth(page);
  await page.route("**/api/ready/database", (route) =>
    route.fulfill({ json: dbStatus("unreachable", "ok", "STOPPED") })
  );

  await page.goto("/file-list");

  await expectGate(page, { title: "Autonomous Database が停止しています" });
  await expect(page.getByText("Autonomous Database: 停止済み", { exact: true })).toBeVisible();
});

test("設定を開けない利用者には導線を出さず、システム管理者への連絡を案内する", async ({ page }) => {
  await mockAuthUser(page, { permissions: ["menu.file_list"] });
  let status: DbStatus = "unreachable";
  await page.route("**/api/ready/database", (route) => route.fulfill({ json: dbStatus(status) }));

  await page.goto("/file-list");

  const card = page.locator('section[aria-labelledby="database-unavailable-title"]');
  await expect(card.getByRole("heading", { level: 1, name: "データベースに接続できません" })).toBeVisible();
  await expect(card.getByText(/システム管理者に連絡して、データベースの起動と接続の確認を依頼してください。$/)).toBeVisible();
  await expect(card.getByRole("link")).toHaveCount(0);
  await expect(card.getByRole("button", { name: "再試行" })).toBeVisible();
  await expectNoPageOverflow(page);

  status = "setup_required";
  await card.getByRole("button", { name: "再試行" }).click();
  await expect(card.getByRole("heading", { level: 1, name: "システムテーブルの作成・更新が必要です" })).toBeVisible();
  await expect(card.getByText(/システム管理者に連絡して、システムテーブルの作成・更新を依頼してください。$/)).toBeVisible();
  await expect(card.getByRole("link")).toHaveCount(0);
});
