import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`回答スタイル設定は回答スタイルを表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockGeneration(page);

    await page.goto("/settings/generation");

    await expect(page.getByRole("heading", { name: "回答スタイル" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /根拠重視・簡潔/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /構造化 JSON/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /逐句出典付与/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /カスタム/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "回答スタイル" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoHorizontalOverflow(page);
  });

  test(`安全チェック設定は安全チェックを表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockGuardrail(page);

    await page.goto("/settings/guardrail");

    await expect(page.getByRole("heading", { name: "安全チェック" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /標準/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /規制対応/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "安全チェック" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoHorizontalOverflow(page);
  });
}

test("回答スタイル設定は回答スタイルを保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let saved: unknown = null;
  await page.route("**/api/settings/generation", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: generationEnvelope("detailed_cited") });
      return;
    }
    await route.fulfill({ json: generationEnvelope("grounded_concise") });
  });

  await page.goto("/settings/generation");
  const detailed = page.getByRole("radio", { name: /詳細・出典明示/ });
  await detailed.click();
  await expect(detailed).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("回答スタイルを保存しました。")).toBeVisible();
  expect(saved).toEqual({ profile: "detailed_cited" });
  await expectNoHorizontalOverflow(page);
});

test("カスタム回答スタイル選択でプロンプト版管理への導線が出る", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/generation", async (route) => {
    await route.fulfill({ json: generationEnvelope("grounded_concise") });
  });

  await page.goto("/settings/generation");
  await page.getByRole("radio", { name: /カスタム/ }).click();

  const manageLink = page.getByRole("link", { name: "プロンプト版を管理 →" });
  await expect(manageLink).toBeVisible();
  await expect(manageLink).toHaveAttribute("href", "/settings/prompts");
});

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
  const strict = page.getByRole("radio", { name: /閾値を高め/ });
  await strict.click();
  await expect(strict).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("安全チェックを保存しました。")).toBeVisible();
  expect(saved).toEqual({ policy: "strict" });
  await expectNoHorizontalOverflow(page);
});

test("回答スタイル設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/generation", async (route) => {
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["回答スタイル設定を取得できませんでした。"], warning_messages: [] },
    });
  });

  await page.goto("/settings/generation");

  await expect(page.getByRole("alert")).toContainText("回答スタイル設定を取得できませんでした。");
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
});

async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}

function generationEnvelope(profile: string) {
  const specs = [
    { name: "grounded_concise", structured_output: false },
    { name: "detailed_cited", structured_output: false },
    { name: "strict_extractive", structured_output: false },
    { name: "structured_json", structured_output: true },
    { name: "bilingual_ja_en", structured_output: false },
    { name: "inline_cited", structured_output: false },
    { name: "custom", structured_output: false },
  ];
  const selected = specs.find((s) => s.name === profile) ?? specs[0];
  return {
    data: {
      profile,
      structured_output: selected.structured_output,
      profiles: specs.map((s) => ({
        ...s,
        origin: "x",
        recommended_for: ["general"],
        selected: s.name === profile,
      })),
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

function guardrailEnvelope(policy: string) {
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
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockGeneration(page: Page) {
  await page.route("**/api/settings/generation", async (route) => {
    await route.fulfill({ json: generationEnvelope("grounded_concise") });
  });
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
