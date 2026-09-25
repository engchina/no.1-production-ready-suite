import { expect, type Page, test } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
  },
  error_messages: [],
  warning_messages: [],
};

interface HfSettings {
  endpoint: string;
  token_configured: boolean;
  config_source: "runtime";
}

const initial: HfSettings = {
  endpoint: "",
  token_configured: false,
  config_source: "runtime",
};

function envelope(data: unknown) {
  return { json: { data, error_messages: [], warning_messages: [] } };
}

async function mockHuggingFace(
  page: Page,
  state: { current: HfSettings; onPatch?: (payload: Record<string, unknown>) => HfSettings }
): Promise<void> {
  await page.route("**/api/settings/huggingface", async (route) => {
    if (route.request().method() === "PATCH") {
      const payload = JSON.parse(route.request().postData() || "{}");
      state.current = state.onPatch?.(payload) ?? state.current;
      await route.fulfill(envelope(state.current));
      return;
    }
    await route.fulfill(envelope(state.current));
  });
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
  await mockDatabaseReady(page);
});

test("HuggingFace 設定が表示され、保存で値とミラー/token を反映する", async ({ page }) => {
  const state = {
    current: { ...initial },
    onPatch: (payload: Record<string, unknown>): HfSettings => ({
      endpoint: String(payload.endpoint ?? ""),
      token_configured: payload.token ? true : Boolean(initial.token_configured),
      config_source: "runtime",
    }),
  };
  await mockHuggingFace(page, state);

  await page.goto("/settings/huggingface");

  await expect(page.locator("#hf-download-dir")).toHaveCount(0);
  await expect(page.getByText("Docker named volume")).toBeVisible();

  // ミラー endpoint と token を入力して保存。
  await page.locator("#hf-endpoint").fill("https://hf-mirror.com");
  await page.locator("#hf-token").fill("hf_secret_token");
  await page.getByRole("button", { name: "保存する" }).click();

  await expect(page.getByText("保存しました")).toBeVisible();
  // 保存後はステータスパネルに「設定済み」(token)とミラーが反映される。
  await expect(page.getByText("https://hf-mirror.com").first()).toBeVisible();
  await expectNoPageOverflow(page);
});

test("375px でも設定フォームと named volume の説明が横にはみ出さない", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockHuggingFace(page, { current: { ...initial } });

  await page.goto("/settings/huggingface");

  await expect(page.locator("#hf-endpoint")).toBeVisible();
  await expect(page.locator("#hf-token")).toBeVisible();
  await expectNoPageOverflow(page);
});

test("token はマスク入力で、表示トグルで切り替えられる", async ({ page }) => {
  await mockHuggingFace(page, { current: { ...initial, token_configured: true } });

  await page.goto("/settings/huggingface");

  const token = page.locator("#hf-token");
  await expect(token).toHaveAttribute("type", "password");
  await token.fill("hf_visible_check");
  await page.getByRole("button", { name: "token を表示" }).click();
  await expect(token).toHaveAttribute("type", "text");
  await expectNoPageOverflow(page);
});
