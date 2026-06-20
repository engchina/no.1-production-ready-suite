import { expect, test, type Page } from "@playwright/test";

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

type ServiceStatus = "running" | "degraded" | "stopped" | "unconfigured";

interface ServiceRow {
  service_id: string;
  category:
    | "preprocess"
    | "parser"
    | "chunking"
    | "vector_index"
    | "retrieval"
    | "grounding"
    | "generation"
    | "guardrail"
    | "evaluation"
    | "graphrag"
    | "agentic";
  profile: "cpu" | "gpu";
  label_key: string;
  status: ServiceStatus;
  configured: boolean;
}

function defaultServices(): ServiceRow[] {
  return [
    {
      service_id: "preprocess-office-to-pdf",
      category: "preprocess",
      profile: "cpu",
      label_key: "settings.services.item.preprocessOfficeToPdf",
      status: "running",
      configured: true,
    },
    {
      service_id: "parser-docling",
      category: "parser",
      profile: "cpu",
      label_key: "settings.services.item.parserDocling",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "parser-mineru",
      category: "parser",
      profile: "gpu",
      label_key: "settings.services.item.parserMineru",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "pipeline-chunking",
      category: "chunking",
      profile: "cpu",
      label_key: "settings.services.item.pipelineChunking",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "pipeline-retrieval",
      category: "retrieval",
      profile: "cpu",
      label_key: "settings.services.item.pipelineRetrieval",
      status: "stopped",
      configured: true,
    },
  ];
}

async function mockServices(
  page: Page,
  options: {
    controlEnabled?: boolean;
    deploymentMode?: "dev" | "prod";
    services?: ServiceRow[];
  } = {}
) {
  const state = {
    control_enabled: options.controlEnabled ?? false,
    deployment_mode: options.deploymentMode ?? "prod",
    services: options.services ?? defaultServices(),
  };
  await page.route("**/api/services", async (route) => {
    await route.fulfill({
      json: { data: state, error_messages: [], warning_messages: [] },
    });
  });
  await page.route("**/api/services/*/start", async (route) => {
    const id = route.request().url().match(/services\/([^/]+)\/start/)?.[1] ?? "";
    const target = state.services.find((s) => s.service_id === decodeURIComponent(id));
    if (target) target.status = "running";
    await route.fulfill({
      json: {
        data: { service_id: id, action: "start", status: "running" },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/services/*/stop", async (route) => {
    const id = route.request().url().match(/services\/([^/]+)\/stop/)?.[1] ?? "";
    const target = state.services.find((s) => s.service_id === decodeURIComponent(id));
    if (target) target.status = "stopped";
    await route.fulfill({
      json: {
        data: { service_id: id, action: "stop", status: "stopped" },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`サービス管理は稼働状態を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockServices(page);

    await page.goto("/settings/services");

    await expect(page.getByRole("heading", { name: "マイクロサービス" })).toBeVisible();
    await expect(page.getByText("前処理サービス", { exact: true })).toBeVisible();
    await expect(page.getByText("Parser サービス(CPU)", { exact: true })).toBeVisible();
    await expect(page.getByText("Parser サービス(GPU)", { exact: true })).toBeVisible();
    // RAG パイプライン ステージ群(chunking/retrieval 等のプラグイン)。
    await expect(page.getByText("RAG パイプライン ステージ", { exact: true })).toBeVisible();
    await expect(page.getByText("Chunking(分割)", { exact: true })).toBeVisible();
    await expect(page.getByText("Retrieval(検索)", { exact: true })).toBeVisible();
    // 稼働状態バッジ。
    await expect(page.getByText("稼働中").first()).toBeVisible();
    await expect(page.getByText("停止").first()).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}

test("制御無効時(prod)は起動/停止ボタンが disabled", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: false, deploymentMode: "prod" });

  await page.goto("/settings/services");

  await expect(page.getByText("本番 (docker)")).toBeVisible();
  await expect(page.getByText("無効(可視化のみ)")).toBeVisible();
  await expect(
    page.getByText("起動/停止は無効です。", { exact: false })
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Docling 起動" })
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Office→PDF 停止" })
  ).toBeDisabled();
});

test("dev モードは uv バッジと有効化された制御を表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true, deploymentMode: "dev" });

  await page.goto("/settings/services");

  await expect(page.getByText("開発 (uv)")).toBeVisible();
  await expect(
    page.getByText("開発モード", { exact: false })
  ).toBeVisible();
  // dev は制御が有効なので起動ボタンが押せる。
  await expect(
    page.getByRole("button", { name: "Docling 起動" })
  ).toBeEnabled();
});

test("制御有効時は確認ダイアログを経て停止できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true });

  await page.goto("/settings/services");

  // 起動中の Office→PDF を停止 → 確認ダイアログ → トースト。
  await page.getByRole("button", { name: "Office→PDF 停止" }).click();
  await expect(page.getByRole("heading", { name: "サービスを停止しますか?" })).toBeVisible();
  await page.getByRole("button", { name: "停止する" }).click();
  await expect(page.getByText("Office→PDF を停止しました。")).toBeVisible();
});

test("制御有効時は確認なしで起動できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true });

  await page.goto("/settings/services");

  await page.getByRole("button", { name: "Docling 起動" }).click();
  await expect(page.getByText("Docling を起動しました。")).toBeVisible();
});

test("取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/services", async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        data: null,
        error_messages: ["サービス一覧を取得できませんでした。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/services");

  await expect(page.getByRole("alert")).toContainText("サービス一覧を取得できませんでした。");
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
});

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);
}
