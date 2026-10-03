import { expect, test, type Page } from "./fixtures/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`安全チェック設定は安全チェックを表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockGuardrail(page);

    await page.goto("/settings/guardrail");

    await expect(page.getByRole("heading", { name: "安全チェック", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByRole("radio", { name: /標準/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /規制対応/ })).toBeVisible();
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    await expect((await openSidebarNav(page)).getByRole("link", { name: "安全チェック" })).toHaveAttribute("aria-current", "page");
    await expectNoHorizontalOverflow(page);
  });
}

test("安全チェック設定は方針を保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let saved: unknown = null;
  await page.route("**/api/settings/guardrail", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: guardrailEnvelope("strict") });
      return;
    }
    await route.fulfill({ json: guardrailEnvelope("standard") });
  });

  await page.goto("/settings/guardrail");
  const strict = page.locator("#guardrail-policy-strict");
  await page.getByText("厳格", { exact: true }).click();
  await expect(strict).toBeChecked();
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("安全チェックを保存しました。")).toBeVisible();
  expect(saved).toEqual({ policy: "strict", backend: "local" });
  await expectNoHorizontalOverflow(page);
});

test("安全チェック設定は OCI 検査方式を保存できる", async ({ page }) => {
  let saved: unknown = null;
  await page.route("**/api/settings/guardrail", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: guardrailEnvelope("standard", "oci_guardrails", true) });
      return;
    }
    await route.fulfill({ json: guardrailEnvelope("standard", "local", true) });
  });

  await page.goto("/settings/guardrail");
  await page.getByText("OCI Guardrails", { exact: true }).click();
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("安全チェックを保存しました。")).toBeVisible();
  expect(saved).toEqual({ policy: "standard", backend: "oci_guardrails" });
});

test("安全チェックの native radio は方向キーで移動できる", async ({ page }) => {
  await mockGuardrail(page);
  await page.goto("/settings/guardrail");

  const standard = page.locator("#guardrail-policy-standard");
  await standard.focus();
  await page.keyboard.press("ArrowRight");

  await expect(page.locator("#guardrail-policy-strict")).toBeChecked();
});

test("OCI 未設定時は保存エラーと OCI 認証導線を表示する", async ({ page }) => {
  await page.route("**/api/settings/guardrail", async (route) => {
    if (route.request().method() === "PATCH") {
      await route.fulfill({
        status: 422,
        json: {
          data: null,
          error_messages: ["OCI Guardrails を選択する前に OCI 認証を設定してください。"],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ json: guardrailEnvelope("standard") });
  });

  await page.goto("/settings/guardrail");
  await page.getByText("OCI Guardrails", { exact: true }).click();
  await page.getByRole("button", { name: "保存" }).click();

  await expect(
    page.getByText("OCI Guardrails を選択する前に OCI 認証を設定してください。")
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "OCI 認証設定を開く" })).toHaveAttribute(
    "href",
    "/settings/oci"
  );
});

test("OCI の注意は OCI Guardrails を選んだときだけ、未設定の理由どおりに表示する", async ({
  page,
}) => {
  // 保存中は oci_guardrails だが compartment が未設定(.env 直接編集などで起こる)。
  await page.route("**/api/settings/guardrail", async (route) => {
    await route.fulfill({
      json: guardrailEnvelope(
        "standard",
        "oci_guardrails",
        false,
        "oci_guardrails_compartment_missing"
      ),
    });
  });

  await page.goto("/settings/guardrail");
  const compartmentWarning = page.getByText("OCI Guardrails の compartment が未設定です。", {
    exact: false,
  });
  await expect(compartmentWarning).toBeVisible();
  await expect(page.getByText("OCI API キー認証を利用できません。", { exact: false })).toHaveCount(0);

  // Local を選ぶ(未保存)と、OCI の注意は消える。
  await page.getByText("Local", { exact: true }).click();
  await expect(compartmentWarning).toHaveCount(0);
  await expect(page.getByRole("link", { name: "OCI 認証設定を開く" })).toHaveCount(0);
});

async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}

function guardrailEnvelope(
  policy: string,
  backend: "local" | "oci_guardrails" = "local",
  ociConfigured = false,
  ociWarningCode: string | null = null
) {
  const specs = [
    { name: "standard", grounding_min_overlap: 3, grounding_min_ratio: 0.12, audit_emphasis: false },
    { name: "strict", grounding_min_overlap: 5, grounding_min_ratio: 0.3, audit_emphasis: false },
    { name: "lenient", grounding_min_overlap: 2, grounding_min_ratio: 0.05, audit_emphasis: false },
    { name: "regulated", grounding_min_overlap: 5, grounding_min_ratio: 0.3, audit_emphasis: true },
  ];
  const selected = specs.find((s) => s.name === policy) ?? specs[0];
  return {
    data: {
      policy,
      block_prompt_injection: true,
      mask_sensitive_identifiers: true,
      max_query_chars: 2000,
      grounding_min_overlap: selected.grounding_min_overlap,
      grounding_min_ratio: selected.grounding_min_ratio,
      audit_emphasis: selected.audit_emphasis,
      policies: specs.map((s) => ({
        ...s,
        origin: "x",
        recommended_for: ["general"],
        selected: s.name === policy,
      })),
      backend,
      oci_configured: ociConfigured,
      oci_warning_code: ociWarningCode,
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockGuardrail(page: Page) {
  await page.route("**/api/settings/guardrail", async (route) => {
    await route.fulfill({ json: guardrailEnvelope("standard") });
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
