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
  profile: "cpu" | "gpu" | "oci";
  label_key: string;
  execution_policy:
    | "required_no_fallback"
    | "in_process_when_disabled"
    | "selected_adapter";
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
      execution_policy: "selected_adapter",
      status: "running",
      configured: true,
    },
    {
      service_id: "parser-docling",
      category: "parser",
      profile: "cpu",
      label_key: "settings.services.item.parserDocling",
      execution_policy: "selected_adapter",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "parser-unlimited-ocr",
      category: "parser",
      profile: "gpu",
      label_key: "settings.services.item.parserUnlimitedOcr",
      execution_policy: "selected_adapter",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "parser-mineru",
      category: "parser",
      profile: "gpu",
      label_key: "settings.services.item.parserMineru",
      execution_policy: "selected_adapter",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "parser-dots-ocr",
      category: "parser",
      profile: "gpu",
      label_key: "settings.services.item.parserDotsOcr",
      execution_policy: "selected_adapter",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "parser-glm-ocr",
      category: "parser",
      profile: "gpu",
      label_key: "settings.services.item.parserGlmOcr",
      execution_policy: "selected_adapter",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "parser-oci-genai-vision",
      category: "parser",
      profile: "oci",
      label_key: "settings.services.item.parserOciGenaiVision",
      execution_policy: "selected_adapter",
      status: "stopped",
      configured: false,
    },
    {
      service_id: "parser-oci-document-understanding",
      category: "parser",
      profile: "oci",
      label_key: "settings.services.item.parserOciDocumentUnderstanding",
      execution_policy: "selected_adapter",
      status: "unconfigured",
      configured: false,
    },
    {
      service_id: "pipeline-chunking",
      category: "chunking",
      profile: "cpu",
      label_key: "settings.services.item.pipelineChunking",
      execution_policy: "in_process_when_disabled",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "pipeline-retrieval",
      category: "retrieval",
      profile: "cpu",
      label_key: "settings.services.item.pipelineRetrieval",
      execution_policy: "in_process_when_disabled",
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
  await page.route("**/api/services/catalog", async (route) => {
    const catalogServices = state.services.map((service) => ({
      service_id: service.service_id,
      category: service.category,
      profile: service.profile,
      label_key: service.label_key,
      execution_policy: service.execution_policy,
      configured: service.configured,
    }));
    await route.fulfill({
      json: {
        data: { ...state, services: catalogServices },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/services/*/status", async (route) => {
    const id = route.request().url().match(/services\/([^/]+)\/status/)?.[1] ?? "";
    const target = state.services.find((s) => s.service_id === decodeURIComponent(id));
    await route.fulfill({
      status: target ? 200 : 404,
      json: {
        data: target ?? null,
        error_messages: target ? [] : ["指定したサービスが見つかりません。"],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/services/*/logs**", async (route) => {
    const id = route.request().url().match(/services\/([^/]+)\/logs/)?.[1] ?? "";
    const serviceId = decodeURIComponent(id);
    await route.fulfill({
      json: {
        data: {
          service_id: serviceId,
          source: serviceId.startsWith("preprocess-") ? "uv" : "docker",
          lines: 200,
          content: `${serviceId} boot complete\nGET /health 200 OK`,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
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
  for (const action of ["build", "remove"] as const) {
    await page.route(`**/api/services/*/${action}`, async (route) => {
      const id =
        route.request().url().match(new RegExp(`services/([^/]+)/${action}`))?.[1] ?? "";
      const target = state.services.find((s) => s.service_id === decodeURIComponent(id));
      if (action === "remove" && target) target.status = "stopped";
      await route.fulfill({
        json: {
          data: { service_id: id, action, status: target?.status ?? "stopped" },
          error_messages: [],
          warning_messages: [],
        },
      });
    });
  }
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
    // セクション見出しは検索・回答フロー順(前処理→解析→分割→…)で表示。
    // ラベルはサイドナビと統一しているため heading role で限定する。
    await expect(
      page.getByRole("heading", { name: "前処理 (Preprocess)", exact: true })
    ).toBeVisible();
    // 解析は CPU/GPU 両方あるため Parser と同様に分割。
    await expect(
      page.getByRole("heading", { name: "解析 (Parser)(CPU)", exact: true })
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "解析 (Parser)(GPU)", exact: true })
    ).toBeVisible();
    // OCI クラウド parser は第 3 グループ「解析 (Parser)(OCI)」として表示。
    await expect(
      page.getByRole("heading", { name: "解析 (Parser)(OCI)", exact: true })
    ).toBeVisible();
    await expect(
      page.getByText("OCI Generative AI (Vision)", { exact: true })
    ).toBeVisible();
    await expect(page.getByText("OCI 認証はメイン設定を継承", { exact: false })).toBeVisible();
    // 単一プロファイルのステージは接尾辞なし。
    await expect(
      page.getByRole("heading", { name: "文書分割", exact: true })
    ).toBeVisible();
    await expect(page.getByText("選択時のみ使用").first()).toBeVisible();
    await expect(page.getByText("既定は backend 内処理").first()).toBeVisible();
    await expect(
      page.getByText("停止中です。backend 内処理で継続します", { exact: false }).first()
    ).toBeVisible();
    await expect(
      page.getByText("取込/解析設定でこのサービスを選択した場合のみ", { exact: false }).first()
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "検索方法", exact: true })
    ).toBeVisible();
    // 稼働状態バッジ。
    await expect(page.getByText("稼働中").first()).toBeVisible();
    await expect(page.getByText("停止").first()).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`サービスログを行内で確認できる (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockServices(page);

    await page.goto("/settings/services");

    await page.getByRole("button", { name: "Docling ログ" }).click();
    await expect(page.getByText("Docling のログ")).toBeVisible();
    await expect(page.getByText("docker compose logs / 最新 200 行")).toBeVisible();
    await expect(page.getByText("parser-docling boot complete")).toBeVisible();
    await expect(page.getByRole("button", { name: "再取得" })).toBeVisible();
    await expect(page.getByRole("button", { name: "コピー" })).toBeVisible();
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

test("dev モードは docker バッジと有効化された制御を表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true, deploymentMode: "dev" });

  await page.goto("/settings/services");

  await expect(page.getByText("開発 (docker)")).toBeVisible();
  await expect(
    page.getByText("開発モード", { exact: false })
  ).toBeVisible();
  // dev は制御が有効なので起動ボタンが押せる。
  await expect(
    page.getByRole("button", { name: "Docling 起動" })
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Dots.OCR 起動" })
  ).toBeEnabled();
});

test("各サービスにビルドとコンテナ削除のボタンがあり操作できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true, deploymentMode: "dev" });

  await page.goto("/settings/services");

  // ビルドは確認なしで実行 → トースト。
  await page.getByRole("button", { name: "Docling ビルド" }).click();
  await expect(page.getByText("Docling のイメージをビルドしました。")).toBeVisible();

  // 削除は破壊的なので確認ダイアログを経て実行 → トースト。
  await page.getByRole("button", { name: "Docling 削除" }).click();
  await page.getByRole("button", { name: "削除する" }).click();
  await expect(page.getByText("Docling のコンテナを削除しました。")).toBeVisible();
});

test("実行コマンドは既定で折りたたまれ、展開すると dev のビルドコマンドを表示する", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true, deploymentMode: "dev" });

  await page.goto("/settings/services");

  const devBuild = "docker compose -f docker-compose.yml -f docker-compose.dev.yml build";
  // 既定では閉じている(コマンドは描画されない)。
  await expect(page.getByText(devBuild, { exact: false })).toHaveCount(0);

  await page.getByRole("button", { name: "実行コマンド" }).click();

  // 展開すると dev の override 付きビルドコマンドが見える。
  await expect(page.getByText(devBuild, { exact: false }).first()).toBeVisible();
  await expect(
    page.getByText("--profile gpu build parser-glm-ocr", { exact: false }).first()
  ).toBeVisible();
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
  await page.route("**/api/services/catalog", async (route) => {
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
