import { expect, test, type Page } from "./fixtures/test";
import { expectNoPageOverflow, mockLocalAuth } from "./_helpers";

type ServiceStatus =
  | "running"
  | "degraded"
  | "starting"
  | "failed"
  | "stopped"
  | "not_installed"
  | "unconfigured";

interface ServiceRow {
  service_id: string;
  category:
    | "preprocess"
    | "parser"
    | "chunking"
    | "vector_index"
    | "graphrag"
    | "guardrail"
    | "evaluation";
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
      service_id: "parser-asr",
      category: "parser",
      profile: "gpu",
      label_key: "settings.services.item.parserAsr",
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
      service_id: "pipeline-graphrag",
      category: "graphrag",
      profile: "cpu",
      label_key: "settings.services.item.pipelineGraphrag",
      execution_policy: "in_process_when_disabled",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "pipeline-guardrail",
      category: "guardrail",
      profile: "cpu",
      label_key: "settings.services.item.pipelineGuardrail",
      execution_policy: "in_process_when_disabled",
      status: "stopped",
      configured: true,
    },
    {
      service_id: "pipeline-evaluation",
      category: "evaluation",
      profile: "cpu",
      label_key: "settings.services.item.pipelineEvaluation",
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
      // deployable=false の段は in_process 固定表示(操作ボタン非表示)。純 CPU の
      // pipeline 段(in_process_when_disabled)は demote 済み、それ以外は deployable。
      deployable: service.execution_policy !== "in_process_when_disabled",
      systemd_unit:
        service.execution_policy !== "in_process_when_disabled"
          ? `production-ready-rag-${service.service_id}.service`
          : null,
      model_cache:
        service.service_id === "parser-docling"
          ? { path: "/var/lib/production-ready-rag/.cache", editable: false }
          : null,
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
          source: "journald",
          lines: 200,
          content: `${serviceId} boot complete\nGET /health 200 OK`,
        },
        error_messages: [],
        warning_messages: [],
      },
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
  await page.route("**/api/services/*/restart", async (route) => {
    const id = route.request().url().match(/services\/([^/]+)\/restart/)?.[1] ?? "";
    const target = state.services.find((s) => s.service_id === decodeURIComponent(id));
    if (target) target.status = "running";
    await route.fulfill({
      json: {
        data: { service_id: id, action: "restart", status: "running" },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
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
    // 工程の見出しはサイドナビ（検索・回答設定）と同じ並び・名前（#638）。
    // ナビのリンクと区別するため heading role で限定する。
    const stageHeadings = [
      "ファイル準備",
      "文書解析(CPU)",
      "文書解析(GPU)",
      "文書解析(OCI)",
      "文書分割",
      "関係情報の構築",
      "安全チェック",
      "評価の基準",
    ];
    for (const name of stageHeadings) {
      await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
    }
    const headingTops = await Promise.all(
      stageHeadings.map(async (name) => {
        const box = await page.getByRole("heading", { name, exact: true }).boundingBox();
        return box?.y ?? -1;
      })
    );
    expect(headingTops).toEqual([...headingTops].sort((a, b) => a - b));
    for (const removed of ["前処理 (Preprocess)", "解析 (Parser)(CPU)", "品質評価", "関係情報"]) {
      await expect(page.getByRole("heading", { name: removed, exact: true })).toHaveCount(0);
    }
    // 工程の説明は、対応する設定画面の説明と同じ文（複数のグループに分かれる工程は最初のグループにだけ出す）。
    await expect(
      page.getByText("文書解析の前に原本を一度だけ整えるファイル準備方式を選択します。", { exact: true })
    ).toBeVisible();
    await expect(
      page.getByText("文書解析に使う方式を選び、必要な解析エンジンの現在状態を確認します。", { exact: true })
    ).toHaveCount(1);
    await expect(page.getByText("ASR(音声文字起こし)", { exact: true })).toBeVisible();
    for (const removed of ["Marker", "Unlimited-OCR", "MinerU", "Dots.OCR", "GLM-OCR"]) {
      await expect(page.getByText(removed, { exact: true })).toHaveCount(0);
    }
    // OCI クラウド parser は第 3 グループ「文書解析(OCI)」として表示。
    await expect(
      page.getByText("OCI Generative AI (Vision)", { exact: true })
    ).toBeVisible();
    await expect(page.getByText("OCI 認証はメイン設定を継承", { exact: false })).toBeVisible();
    // 単一プロファイルの工程は接尾辞なし（上の見出しの確認に含む）。
    await expect(page.getByText("選択時のみ使用").first()).toBeVisible();
    await expect(page.getByText("既定は backend 内処理").first()).toBeVisible();
    await expect(
      page.getByText("停止中です。backend 内処理で継続します", { exact: false }).first()
    ).toBeVisible();
    await expect(
      page.getByText("ファイル準備・文書解析の設定か処理レシピでこのサービスを選んだ場合だけ", { exact: false }).first()
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

    // ログは行の RowActionMenu に入っている（#158）。
    await page.getByRole("button", { name: "Docling の操作" }).click();
    await page.getByRole("menuitem", { name: "ログ" }).click();
    await expect(page.getByText("Docling のログ")).toBeVisible();
    await expect(
      page.getByText("journalctl -u production-ready-rag-parser-docling.service / 最新 200 行")
    ).toBeVisible();
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

  await expect(page.getByText("本番 (systemd)")).toBeVisible();
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
  // メニュー内の再起動も無効。
  await page.getByRole("button", { name: "Office→PDF の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "再起動" })).toBeDisabled();
});

test("dev モードは systemd バッジと有効化された制御を表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true, deploymentMode: "dev" });

  await page.goto("/settings/services");

  await expect(page.getByText("開発 (systemd)")).toBeVisible();
  await expect(
    page.getByText("開発モード", { exact: false })
  ).toBeVisible();
  // dev は制御が有効なので起動ボタンが押せる。
  await expect(
    page.getByRole("button", { name: "Docling 起動" })
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: /ASR\(音声文字起こし\) 起動/ })
  ).toBeEnabled();
});

test("稼働中のサービスは操作メニューから確認なしで再起動できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true, deploymentMode: "dev" });

  await page.goto("/settings/services");

  await page.getByRole("button", { name: "Office→PDF の操作" }).click();
  await page.getByRole("menuitem", { name: "再起動" }).click();
  await expect(page.getByText("Office→PDF を再起動しました。")).toBeVisible();
  // Docker のイメージ build / コンテナ削除の操作は出さない（#286）。
  await page.getByRole("button", { name: "Office→PDF の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "ビルド" })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: "削除" })).toHaveCount(0);
});

test("unit が未登録・起動失敗のサービスは状態と案内を出す", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  const services = defaultServices().map((service) =>
    service.service_id === "parser-asr"
      ? { ...service, status: "not_installed" as const }
      : service.service_id === "parser-docling"
        ? { ...service, status: "failed" as const }
        : service
  );
  await mockServices(page, { controlEnabled: true, deploymentMode: "prod", services });

  await page.goto("/settings/services");

  const asr = page.getByTestId("service-row-parser-asr");
  await expect(asr.getByText("未登録")).toBeVisible();
  await expect(asr.getByText("systemd の unit が登録されていません", { exact: false })).toBeVisible();
  // unit が無いので起動できない。
  await expect(page.getByRole("button", { name: /ASR\(音声文字起こし\) 起動/ })).toBeDisabled();
  const docling = page.getByTestId("service-row-parser-docling");
  await expect(docling.getByText("起動失敗")).toBeVisible();
  await expect(docling.getByText("ログで原因を確認", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Docling 起動" })).toBeEnabled();
  await expect(docling.getByText("/var/lib/production-ready-rag/.cache")).toBeVisible();
});

test("実行コマンドは既定で折りたたまれ、展開すると systemd のコマンドを表示する", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true, deploymentMode: "dev" });

  await page.goto("/settings/services");

  const status = "systemctl list-units --all 'production-ready-rag-*'";
  // 既定では閉じている(コマンドは描画されない)。
  await expect(page.getByText(status, { exact: false })).toHaveCount(0);

  await page.getByRole("button", { name: "実行コマンド" }).click();

  await expect(page.getByText(status, { exact: false }).first()).toBeVisible();
  await expect(
    page.getByText("sudo journalctl -u production-ready-rag-parser-docling.service -f").first()
  ).toBeVisible();
  // dev は unit と sudoers を登録するコマンドも出す。
  await expect(page.getByText("scripts/rag-services.sh install", { exact: true })).toBeVisible();
  await expect(
    page.getByText("scripts/rag-services.sh install --gpu", { exact: true })
  ).toBeVisible();
  await expect(page.getByText("docker", { exact: false })).toHaveCount(0);
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
  // 停止後は行の主操作が「起動」に切り替わる。
  await expect(page.getByRole("button", { name: "Office→PDF 起動" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Office→PDF 停止" })).toHaveCount(0);
});

test("制御有効時は確認なしで起動できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true });

  await page.goto("/settings/services");

  await page.getByRole("button", { name: "Docling 起動" }).click();
  await expect(page.getByText("Docling を起動しました。")).toBeVisible();
  // 起動後は行の主操作が「停止」に切り替わる。
  await expect(page.getByRole("button", { name: "Docling 停止" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Docling 起動" })).toHaveCount(0);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`起動の間は行に経過時間を出す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockServices(page, { controlEnabled: true });
    // 起動の応答を止めて、実行中の表示（#376）を確かめてから mockServices の応答へ渡す。
    let release: () => void = () => {};
    const released = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/services/*/start", async (route) => {
      await released;
      await route.fallback();
    });

    await page.goto("/settings/services");
    await page.getByRole("button", { name: "Docling 起動" }).click();

    const processing = page.getByTestId("service-processing-parser-docling");
    await expect(processing).toContainText("Docling を起動しています");
    await expect(processing.getByRole("timer")).toHaveAccessibleName(/経過時間 \d{2}:\d{2}/);
    await expect(processing).toHaveAttribute("data-processing-activity-icon", "none");
    await expectNoPageOverflow(page);

    release();
    await expect(page.getByText("Docling を起動しました。")).toBeVisible();
    await expect(processing).toHaveCount(0);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`各行は状態に応じた主操作 1 つと操作メニュー 1 つだけを出す (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockServices(page, { controlEnabled: true, deploymentMode: "dev" });

    await page.goto("/settings/services");
    await expect(page.getByRole("heading", { name: "マイクロサービス" })).toBeVisible();

    const expected: Record<string, "起動" | "停止" | null> = {
      "preprocess-office-to-pdf": "停止",
      "parser-docling": "起動",
      "parser-asr": "起動",
      "parser-oci-genai-vision": "起動",
      "parser-oci-document-understanding": "起動",
      // backend 内処理の段は操作を出さない。
      "pipeline-chunking": null,
      "pipeline-guardrail": null,
    };
    for (const [serviceId, primary] of Object.entries(expected)) {
      const row = page.getByTestId(`service-row-${serviceId}`);
      await expect(row).toBeVisible();
      const buttons = row.getByRole("button");
      if (primary === null) {
        await expect(buttons).toHaveCount(0);
        continue;
      }
      // 主操作のボタン + メニューのトリガーの 2 個だけ。
      await expect(buttons).toHaveCount(2);
      await expect(row.locator('[aria-haspopup="menu"]')).toHaveCount(1);
      await expect(row.getByTestId(`service-primary-action-${serviceId}`)).toHaveText(primary);
      for (const moved of ["ログ", "再起動"]) {
        await expect(row.getByRole("button", { name: moved, exact: true })).toHaveCount(0);
      }
    }
    await expectNoHorizontalOverflow(page);

    // 稼働中の行のメニューには ログ / 再起動 が入る（停止中はログだけ）。
    await page.getByRole("button", { name: "Office→PDF の操作" }).click();
    const menu = page.getByRole("menu");
    await expect(menu.getByRole("menuitem")).toHaveText(["ログ", "再起動"]);
    await expectNoHorizontalOverflow(page);
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Office→PDF の操作" })).toBeFocused();
    await page.getByRole("button", { name: "Docling の操作" }).click();
    await expect(page.getByRole("menu").getByRole("menuitem")).toHaveText(["ログ"]);
    await page.keyboard.press("Escape");
  });
}

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

test("別のサービスの操作を続けて始めても、先の操作の実行中の表示と結果を失わない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockServices(page, { controlEnabled: true });
  const releases = new Map<string, () => void>();
  await page.route("**/api/services/*/start", async (route) => {
    const id = decodeURIComponent(route.request().url().match(/services\/([^/]+)\/start/)?.[1] ?? "");
    await new Promise<void>((resolve) => releases.set(id, resolve));
    await route.fallback();
  });

  await page.goto("/settings/services");
  await page.getByRole("button", { name: "Docling 起動" }).click();
  await expect(page.getByTestId("service-processing-parser-docling")).toBeVisible();
  await page.getByRole("button", { name: "ASR(音声文字起こし) 起動" }).click();
  await expect(page.getByTestId("service-processing-parser-asr")).toBeVisible();
  // 先に始めた Docling の起動はまだ終わっていない。
  await expect(page.getByTestId("service-processing-parser-docling")).toBeVisible();
  await expect(page.getByRole("button", { name: "Docling 起動" })).toBeDisabled();

  releases.get("parser-docling")?.();
  await expect(page.getByText("Docling を起動しました。")).toBeVisible();
  await expect(page.getByTestId("service-processing-parser-docling")).toHaveCount(0);
  // 後から始めた ASR の起動は続いている。
  await expect(page.getByTestId("service-processing-parser-asr")).toBeVisible();
  releases.get("parser-asr")?.();
  await expect(page.getByText("ASR(音声文字起こし) を起動しました。")).toBeVisible();
  await expect(page.getByTestId("service-processing-parser-asr")).toHaveCount(0);
});

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);
}
