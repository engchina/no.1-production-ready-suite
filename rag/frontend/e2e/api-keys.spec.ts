import { expect, test, type Page } from "./fixtures/test";

import {
  apiEnvelope,
  dbUser,
  expectNoPageOverflow,
  mockAuthUser,
  mockDatabaseReady,
  openSidebarNav,
} from "./_helpers";

// 外部の MCP クライアント向けの API キー（RAG / NL2SQL 共通の画面。#1805）。
// 自分のキーはアカウントのメニューから開き（メニュー権限なし）、全員のキーは「ユーザーとロール」の管理の画面。

const TOKEN = "prak_0123456789abcdef_" + "S".repeat(43);

interface KeyPayload {
  id: string;
  name: string;
  token_prefix: string;
  products: string[];
  owner_user_uuid: string;
  owner_display_name: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  expired: boolean;
}

function key(overrides: Partial<KeyPayload> = {}): KeyPayload {
  return {
    id: "0123456789abcdef",
    name: "会社の PC の Claude Code",
    token_prefix: "prak_0123456789abcdef_SSSS",
    products: ["rag"],
    owner_user_uuid: "11111111-1111-1111-1111-111111111111",
    owner_display_name: "利用者 一郎",
    created_at: "2026-10-14T00:00:00Z",
    expires_at: "2027-01-12T00:00:00Z",
    last_used_at: null,
    expired: false,
    ...overrides,
  };
}

async function mockMyKeys(page: Page) {
  const keys: KeyPayload[] = [];
  const created: Record<string, unknown>[] = [];
  const revoked: string[] = [];
  await mockDatabaseReady(page);
  await page.route("**/api/account/api-keys**", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      created.push(body);
      const item = key({ name: String(body.name), products: body.products as string[] });
      keys.unshift(item);
      return route.fulfill({ json: apiEnvelope({ key: item, token: TOKEN }) });
    }
    if (request.method() === "DELETE") {
      const id = new URL(request.url()).pathname.split("/").pop() ?? "";
      revoked.push(id);
      keys.splice(keys.findIndex((item) => item.id === id), 1);
      return route.fulfill({ json: apiEnvelope({ id }) });
    }
    return route.fulfill({ json: apiEnvelope({ keys }) });
  });
  return { created, revoked };
}

test.describe("API キー", { tag: "@responsive" }, () => {
  test("アカウントのメニューから自分のキーを発行し、秘密を 1 回だけ見て、失効できる（権限なし）", async ({ page }) => {
    const calls = await mockMyKeys(page);
    // 自分のキーの画面はメニュー権限を要しない（検索だけの利用者）。
    await mockAuthUser(page, { permissions: ["menu.search"] });

    await page.goto("/search");
    const sidebar = await openSidebarNav(page);
    await sidebar.getByRole("button", { name: "API キー" }).click();
    await expect(page).toHaveURL(/\/account\/api-keys$/);
    await expect(page.getByRole("heading", { name: "API キー", level: 1 })).toBeVisible();
    // 開発の dev server（base が /）では開いている製品の URL だけを出す。
    await expect(page.getByTestId("api-keys-endpoint-rag")).toContainText("/api/mcp");
    await expect(page.getByText("API キーはまだありません。")).toBeVisible();

    // 名前が空なら発行しない。
    await page.getByTestId("api-key-create").click();
    await expect(page.getByText("名前を入力してください。")).toBeVisible();
    expect(calls.created).toHaveLength(0);

    // 発行の既定の製品は、開いている製品によらず 3 製品のすべて（#1869・#1874）。
    await expect(page.getByTestId("api-key-product-rag")).toBeChecked();
    await expect(page.getByTestId("api-key-product-nl2sql")).toBeChecked();
    await expect(page.getByTestId("api-key-product-agent")).toBeChecked();
    // RAG の画面では業務 Agent を絞れない（Agent の画面で発行する）ことを案内する。
    await expect(page.getByTestId("api-key-agent-scope-elsewhere")).toBeVisible();
    await page.getByLabel("名前（用途）").fill("会社の PC の Claude Code");
    await page.getByTestId("api-key-product-nl2sql").uncheck();
    await page.getByTestId("api-key-product-agent").uncheck();
    await expect(page.getByTestId("api-key-agent-scope-elsewhere")).toHaveCount(0);
    await page.getByTestId("api-key-create").click();
    await expect(page.getByTestId("api-key-token")).toHaveValue(TOKEN);
    expect(calls.created).toEqual([{ name: "会社の PC の Claude Code", products: ["rag"], expires_in_days: 90 }]);
    // 発行の後は欄を既定（すべて）へ戻す。
    await expect(page.getByTestId("api-key-product-rag")).toBeChecked();
    await expect(page.getByTestId("api-key-product-nl2sql")).toBeChecked();
    await expect(page.getByTestId("api-key-product-agent")).toBeChecked();
    const list = page.getByRole("table", { name: "API キーの一覧" });
    await expect(list.getByText("会社の PC の Claude Code")).toBeVisible();
    // 一覧は秘密を出さない（見分けるための先頭だけ）。
    await expect(list).not.toContainText(TOKEN);
    await expectNoPageOverflow(page);

    // 「保管しました」で閉じたら二度と出さない。
    await page.getByRole("button", { name: "保管しました" }).click();
    await expect(page.getByTestId("api-key-token")).toHaveCount(0);

    await page.getByTestId("api-key-row-actions-0123456789abcdef-trigger").click();
    await page.getByRole("menuitem", { name: "失効" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "失効" }).click();
    await expect(page.getByText("API キーはまだありません。")).toBeVisible();
    expect(calls.revoked).toEqual(["0123456789abcdef"]);
  });

  test("管理の画面は全員のキーを利用者付きで出し、メニュー権限が無ければ開けない", async ({ page }) => {
    await mockDatabaseReady(page);
    await page.route("**/api/security/api-keys**", (route) =>
      route.fulfill({
        json: apiEnvelope({
          keys: [
            key(),
            key({ id: "fedcba9876543210", name: "CI", owner_display_name: "", expired: true }),
          ],
        }),
      })
    );
    const setUser = await mockAuthUser(page, { permissions: ["menu.security_api_keys"] });

    // 管理のメニュー権限だけの利用者は、ナビの先頭で開ける画面として全員のキーの画面を開く。
    await page.goto("/");
    await expect(page).toHaveURL(/\/settings\/security\/api-keys$/);
    // 開いている画面のセクション（ユーザーとロール）は自動で開く。
    const sidebar = await openSidebarNav(page);
    await expect(sidebar.getByRole("link", { name: "API キー" })).toHaveAttribute("aria-current", "page");
    if (await page.getByTestId("nav-drawer-trigger").isVisible()) await page.keyboard.press("Escape");
    const list = page.getByRole("table", { name: "API キーの一覧" });
    await expect(list.getByText("利用者 一郎")).toBeVisible();
    await expect(list.getByText("削除されたユーザー")).toBeVisible();
    await expect(list.getByText("期限切れ")).toBeVisible();
    // 管理の画面からはキーを発行しない。
    await expect(page.getByTestId("api-key-create")).toHaveCount(0);
    await expectNoPageOverflow(page);

    setUser(dbUser({ permissions: ["menu.search"] }));
    await page.goto("/settings/security/api-keys");
    await expect(page).toHaveURL(/\/forbidden$/);
  });
});
