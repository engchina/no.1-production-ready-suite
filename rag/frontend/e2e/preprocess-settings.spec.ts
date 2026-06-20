import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow } from "./_helpers";

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

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`前処理設定はプロファイルを表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await page.addInitScript(() => {
        window.localStorage.setItem(
          "production-ready-rag.ui",
          JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
        );
      });
    }
    await mockPreprocessSettings(page);

    await page.goto("/settings/preprocess");

    await expect(page.getByRole("heading", { name: "前処理プロファイル" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /原本をそのまま解析/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /文字コード→UTF-8/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /Office を PDF へ変換/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /CSV をヘッダ列キー/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /Excel\(\.xls\/\.xlsx\)/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /自動/ })).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  });
}

test("前処理設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/preprocess", async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        data: null,
        error_messages: ["前処理設定を取得できませんでした。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/preprocess");

  await expect(page.getByRole("alert")).toContainText("前処理設定を取得できませんでした。");
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("前処理設定はプロファイルを保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let savedPayload: unknown = null;
  await page.route("**/api/settings/preprocess", async (route) => {
    if (route.request().method() === "PATCH") {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({ json: preprocessEnvelope({ profile: "office_to_pdf" }) });
      return;
    }
    await route.fulfill({ json: preprocessEnvelope() });
  });

  await page.goto("/settings/preprocess");

  const office = page.getByRole("radio", { name: /Office を PDF へ変換/ });
  await office.click();
  await expect(office).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("前処理設定を保存しました。")).toBeVisible();
  expect(savedPayload).toEqual({ profile: "office_to_pdf" });
  await expectNoHorizontalOverflow(page);
});

type PreprocessOverrides = { profile?: string; service_enabled?: boolean };

function preprocessEnvelope(overrides: PreprocessOverrides = {}) {
  const profile = overrides.profile ?? "passthrough";
  const serviceEnabled = overrides.service_enabled ?? false;
  const specs: {
    name: string;
    origin: string;
    recommended_for: string[];
    in_process: boolean;
    requires_service: boolean;
  }[] = [
    { name: "passthrough", origin: "baseline_no_conversion", recommended_for: ["any"], in_process: true, requires_service: false },
    { name: "text_normalize", origin: "unstructured_text_cleaning", recommended_for: ["text"], in_process: true, requires_service: false },
    { name: "office_to_pdf", origin: "libreoffice_headless", recommended_for: ["office"], in_process: false, requires_service: true },
    { name: "pdf_to_page_images", origin: "no1_pdfparser_page_images", recommended_for: ["pdf"], in_process: false, requires_service: true },
    { name: "csv_to_json", origin: "no1_csv2json_records", recommended_for: ["csv"], in_process: false, requires_service: true },
    { name: "excel_to_json", origin: "no1_excel2json_records", recommended_for: ["excel"], in_process: false, requires_service: true },
  ];
  return {
    data: {
      profile,
      service_enabled: serviceEnabled,
      service_url: "http://preprocess-office-to-pdf:8000",
      canonical_artifact_prefix: "artifacts/canonical",
      profiles: specs.map((spec) => ({
        ...spec,
        selected: spec.name === profile,
        available: spec.in_process || serviceEnabled,
      })),
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockPreprocessSettings(page: Page) {
  await page.route("**/api/settings/preprocess", async (route) => {
    await route.fulfill({ json: preprocessEnvelope() });
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  await expectNoPageOverflow(page);
}
