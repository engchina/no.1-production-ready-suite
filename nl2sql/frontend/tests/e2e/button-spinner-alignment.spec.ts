import { expect, test, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

/**
 * ボタンの loading スピナーが「一定の中心」で回転することを実画面で検証する。
 *
 * 旧実装は lucide `Loader2`（288 度の欠けた円弧）をそのまま回していたため、
 * インクの重心とシルエットが回転角ごとに動き、中心がずれて上下に揺れて見えていた。
 * 現在は 180 度対称の `StableLoadingIcon` を使い、外接矩形だけでなく active arc の
 * 見た目の重心も中央に保つ。
 */

const LOADING_ICON_SELECTOR = 'svg[data-loading-icon="true"]';

const profile = {
  id: "default",
  name: "既定プロファイル",
  category: "既定",
  description: "請求を扱うプロファイル",
  allowed_tables: ["APP.INVOICES"],
  allowed_views: [],
  glossary: {},
  sql_rules: [],
  default_row_limit: 100,
  safety_policy: "select_only",
  few_shot_examples: [],
  select_ai_config: {
    profile_name: "NL2SQL_DEFAULT_PROFILE",
    region: "ap-osaka-1",
    model: "cohere.command-r-plus",
    embedding_model: "cohere.embed-v4.0",
    max_tokens: 32000,
    enforce_object_list: true,
    comments: true,
    annotations: false,
    constraints: false,
    role: "",
    additional_instructions: "",
  },
  archived: false,
  version: 1,
  etag: "profile-etag",
  updated_at: "2026-08-16T00:00:00.000Z",
};

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

/** ジョブを running のまま返し続け、実行ボタンを loading 状態で固定する。 */
async function mockWorkbenchWithPendingJob(page: Page) {
  await mockDatabaseGateReady(page);
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: [
        {
          id: profile.id,
          name: profile.name,
          category: profile.category,
          description: profile.description,
          archived: false,
          allowed_table_count: 1,
          allowed_view_count: 0,
          glossary_count: 0,
          few_shot_count: 0,
          version: 1,
          etag: profile.etag,
          updated_at: profile.updated_at,
        },
      ],
      next_cursor: null,
      total: 1,
      change_token: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/default", (route) => fulfillJson(route, profile));
  await page.route("**/api/nl2sql/profiles/default/usage-context", (route) =>
    fulfillJson(route, {
      id: profile.id,
      name: profile.name,
      category: profile.category,
      description: profile.description,
      allowed_tables: profile.allowed_tables,
      allowed_views: profile.allowed_views,
      archived: false,
      object_scope_version: 1,
      version: profile.version,
      etag: profile.etag,
      updated_at: profile.updated_at,
    })
  );
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfillJson(route, {
      catalog_version: 1,
      schema_fingerprint: "schema-fixture",
      refreshed_at: "2026-08-16T00:00:00.000Z",
      object_count: 1,
      column_count: 1,
      change_token: 1,
      etag: "schema-etag",
    })
  );
  await page.route("**/api/schema/objects?*", (route) =>
    fulfillJson(route, { items: [], next_cursor: null, total: 0, catalog_version: 1 })
  );
  await page.route("**/api/nl2sql/history", (route) => fulfillJson(route, { items: [], total: 0 }));
  await page.route("**/api/nl2sql/similar-history", (route) => fulfillJson(route, { items: [] }));
  await page.route("**/api/nl2sql/recommend-profile", (route) =>
    fulfillJson(route, {
      recommended_profile_id: "default",
      recommended_profile_name: profile.name,
      confidence: 0.2,
      recommendation_source: "deterministic",
      reasons: [],
      recommended_allowed_objects: { table_names: [], columns: {} },
    })
  );
  await page.route("**/api/nl2sql/rewrite", (route) =>
    fulfillJson(route, {
      original_question: "請求金額を確認したい",
      rewritten_question: "請求金額を確認したい",
      source: "deterministic",
      model: "",
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/jobs", (route) =>
    fulfillJson(route, {
      job_id: "job-spinner-001",
      status: "pending",
      created_at: "2026-08-16T00:00:00.000Z",
      steps: [],
    })
  );
  // 完了させず running を返し続けることでスピナーを表示したままにする
  await page.route("**/api/nl2sql/jobs/job-spinner-001", (route) =>
    fulfillJson(route, {
      job_id: "job-spinner-001",
      status: "running",
      created_at: "2026-08-16T00:00:00.000Z",
      started_at: "2026-08-16T00:00:00.010Z",
      finished_at: null,
      elapsed_ms: null,
      error_message: null,
      timing: null,
      steps: [{ stage: "generate_sql", status: "running", elapsed_ms: null }],
      result: null,
    })
  );
}

async function startPendingRun(page: Page) {
  await mockWorkbenchWithPendingJob(page);
  await page.goto("/query");
  await page.locator("#nl2sql-question-input").fill("請求金額を確認したい");
  await page.getByRole("button", { name: "検索を実行" }).click();

  const button = page.getByRole("button", { name: "検索を実行" });
  const spinner = button.locator(LOADING_ICON_SELECTOR);
  await expect(spinner).toBeVisible();
  return { button, spinner };
}

async function activeArcCentroidSamples(spinner: ReturnType<Page["locator"]>) {
  return spinner.evaluate((node) => {
    const activeArcs = Array.from(
      node.querySelectorAll<SVGPathElement>('[data-loading-icon-active="true"]')
    );
    const points: { x: number; y: number }[] = [];

    for (const arc of activeArcs) {
      const length = arc.getTotalLength();
      for (let index = 0; index <= 48; index += 1) {
        const point = arc.getPointAtLength((length * index) / 48);
        points.push({ x: point.x, y: point.y });
      }
    }

    const center = { x: 12, y: 12 };
    const centroid = (items: typeof points) => ({
      x: items.reduce((sum, point) => sum + point.x, 0) / items.length,
      y: items.reduce((sum, point) => sum + point.y, 0) / items.length,
    });
    const rotate = (point: { x: number; y: number }, degrees: number) => {
      const radians = (degrees * Math.PI) / 180;
      const cos = Math.cos(radians);
      const sin = Math.sin(radians);
      const dx = point.x - center.x;
      const dy = point.y - center.y;
      return {
        x: center.x + dx * cos - dy * sin,
        y: center.y + dx * sin + dy * cos,
      };
    };

    return [0, 45, 90, 135, 180, 225, 270, 315].map((angle) => ({
      angle,
      ...centroid(points.map((point) => rotate(point, angle))),
    }));
  });
}

test("実行ボタンのスピナーは 16px で回転レイヤーが固定されている", async ({ page }) => {
  const { spinner } = await startPendingRun(page);

  const style = await spinner.evaluate((node) => {
    const computed = getComputedStyle(node);
    return {
      width: computed.width,
      height: computed.height,
      willChange: computed.willChange,
      transformBox: computed.transformBox,
      animationName: computed.animationName,
    };
  });

  // children 側の先頭アイコン（Play size=16）と同寸法。loading 切替で寸法が変わらない
  expect(style.width).toBe("16px");
  expect(style.height).toBe("16px");
  // 継続回転する要素として独立レイヤーで合成し、回転原点を図形中心へ固定する
  expect(style.willChange).toContain("transform");
  expect(style.transformBox).toBe("view-box");
  expect(style.animationName).not.toBe("none");
});

test("スピナーは対称 active arc を持ちシルエットが回転角に依存しない", async ({ page }) => {
  const { spinner } = await startPendingRun(page);

  const shape = await spinner.evaluate((node) => {
    const track = node.querySelector("circle");
    const arcs = Array.from(node.querySelectorAll('[data-loading-icon-active="true"]'));
    return {
      hasTrack: Boolean(track),
      trackRadius: track?.getAttribute("r") ?? null,
      activeArcCount: arcs.length,
      arcPaths: arcs.map((arc) => arc.getAttribute("d")),
      viewBox: node.getAttribute("viewBox"),
    };
  });

  // 2 本の active arc を 180 度対称に置き、濃い筆画の見た目重心が上下へ流れないようにする。
  expect(shape.hasTrack).toBe(true);
  expect(shape.trackRadius).toBe("8.25");
  expect(shape.activeArcCount).toBe(2);
  expect(shape.arcPaths).toEqual([
    "M12 3.75A8.25 8.25 0 0 1 20.25 12",
    "M12 20.25A8.25 8.25 0 0 1 3.75 12",
  ]);
  expect(shape.viewBox).toBe("0 0 24 24");
});

test("active arc の見た目重心は複数の回転角でも中央に残る", async ({ page }) => {
  const { spinner } = await startPendingRun(page);

  const samples = await activeArcCentroidSamples(spinner);

  for (const sample of samples) {
    expect(Math.abs(sample.x - 12), `angle ${sample.angle} x`).toBeLessThan(0.02);
    expect(Math.abs(sample.y - 12), `angle ${sample.angle} y`).toBeLessThan(0.02);
  }
});

test("スピナーはボタンの垂直中心に配置される", async ({ page }) => {
  const { button, spinner } = await startPendingRun(page);

  const metrics = await spinner.evaluate((node) => {
    const svg = node.getBoundingClientRect();
    const host = (node.closest("button") as HTMLElement).getBoundingClientRect();
    return {
      spinnerCenterY: svg.y + svg.height / 2,
      buttonCenterY: host.y + host.height / 2,
    };
  });

  expect(Math.abs(metrics.spinnerCenterY - metrics.buttonCenterY)).toBeLessThan(0.02);
  await expect(button).toBeDisabled();
});

test("回転中もスピナーの中心座標がフレーム間でドリフトしない", async ({ page }, testInfo) => {
  const { button, spinner } = await startPendingRun(page);

  const samples = await spinner.evaluate(
    (node) =>
      new Promise<{ x: number; y: number }[]>((resolve) => {
        const collected: { x: number; y: number }[] = [];
        const tick = () => {
          const rect = node.getBoundingClientRect();
          collected.push({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 });
          if (collected.length >= 8) {
            resolve(collected);
            return;
          }
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      })
  );

  const spread = (values: number[]) => Math.max(...values) - Math.min(...values);
  // 外接矩形は回転角で変わるが、中心は固定でなければならない
  expect(spread(samples.map((sample) => sample.x))).toBeLessThan(0.05);
  expect(spread(samples.map((sample) => sample.y))).toBeLessThan(0.05);

  await testInfo.attach(`spinner-${testInfo.project.name}.png`, {
    body: await button.screenshot(),
    contentType: "image/png",
  });
});

test("prefers-reduced-motion ではスピナーが回転しない", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const { spinner } = await startPendingRun(page);

  await expect
    .poll(() => spinner.evaluate((node) => getComputedStyle(node).animationName))
    .toBe("none");
});

test("sm/md/lg と icon-only の loading button でもスピナーは固定寸法で中央に残る", async ({
  page,
}) => {
  const { spinner } = await startPendingRun(page);
  const iconHtml = await spinner.evaluate((node) => node.outerHTML);

  await page.evaluate((stableIconHtml) => {
    const fixture = document.createElement("section");
    fixture.setAttribute("data-testid", "loading-button-size-fixture");
    fixture.className = "fixed left-4 top-4 z-50 flex flex-wrap items-center gap-2 bg-background p-2";
    fixture.innerHTML = [
      { id: "sm", label: "small", height: 32, width: 92 },
      { id: "md", label: "medium", height: 36, width: 112 },
      { id: "lg", label: "large", height: 40, width: 120 },
      { id: "icon", label: "", height: 36, width: 36 },
    ]
      .map(
        (item) => `
          <button
            type="button"
            disabled
            data-testid="loading-button-fixture-${item.id}"
            data-expected-height="${item.height}"
            class="rounded-md border border-border bg-disabled-bg text-sm font-medium leading-5 text-disabled"
            style="display:inline-flex;align-items:center;justify-content:center;gap:6px;height:${item.height}px;width:${item.width}px;overflow:hidden;white-space:nowrap;"
            aria-label="${item.label || "icon-only"}"
          >
            ${stableIconHtml}
            ${item.label ? `<span>${item.label}</span>` : ""}
          </button>
        `
      )
      .join("");
    document.body.appendChild(fixture);
  }, iconHtml);

  const buttons = page.locator("[data-testid^='loading-button-fixture-']");
  await expect(buttons).toHaveCount(4);

  for (const id of ["sm", "md", "lg", "icon"]) {
    const button = page.getByTestId(`loading-button-fixture-${id}`);
    const icon = button.locator(LOADING_ICON_SELECTOR);
    const metrics = await icon.evaluate((node) => {
      const svg = node.getBoundingClientRect();
      const host = (node.closest("button") as HTMLElement).getBoundingClientRect();
      const computed = getComputedStyle(node);
      return {
        width: computed.width,
        height: computed.height,
        spinnerCenterY: svg.y + svg.height / 2,
        buttonCenterY: host.y + host.height / 2,
        buttonHeight: host.height,
        expectedHeight: Number((node.closest("button") as HTMLElement).dataset.expectedHeight),
      };
    });

    expect(metrics.width).toBe("16px");
    expect(metrics.height).toBe("16px");
    expect(metrics.buttonHeight).toBe(metrics.expectedHeight);
    expect(Math.abs(metrics.spinnerCenterY - metrics.buttonCenterY)).toBeLessThan(0.02);
  }
});
