import { expect, test, type Page } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

// 作業画面（lib/page-layout.ts の WIDE_PAGE_ROUTES）は画面幅いっぱい、読む・入力する画面は 1440px。
// PageHeader と PageBody の wide がずれると、1920px 以上でタイトルと本文の左端がずれる（#96）。

const CONTENT_MAX_WIDTH = 1440;

const emptyPage = { items: [], total: 0, limit: 50, offset: 0, has_next: false };
const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });
const emptySummary = {
  total: 0,
  helpful_count: 0,
  not_helpful_count: 0,
  helpful_rate: 0,
  answer_total: 0,
  answer_helpful_rate: 0,
  citation_total: 0,
  citation_helpful_rate: 0,
  reason_counts: [],
};

test.beforeEach(async ({ page }) => {
  // 画面固有でない API は空の一覧で応答する（後から登録した route が優先される）。
  await page.route("**/api/**", (route) => route.fulfill({ json: envelope(emptyPage) }));
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: envelope({ mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null }),
    })
  );
  await page.route("**/api/feedback?**", (route) =>
    route.fulfill({
      json: envelope({ summary: emptySummary, previous_summary: emptySummary, items: emptyPage }),
    })
  );
});

const PAGES = [
  { path: "/file-list", title: "文書インデックス", wide: true },
  { path: "/feedback", title: "利用者フィードバック", wide: true },
  { path: "/settings/pipeline", title: "設定の概要", wide: false },
] as const;

/** ページタイトル（h1）と、PageHeader 直後の PageBody の中身の左端・幅を測る。 */
async function measureLayout(page: Page, title: string) {
  const heading = page.getByRole("heading", { level: 1, name: title, exact: true });
  await expect(heading).toBeVisible();
  return heading.evaluate((h1) => {
    const header = h1.closest("header");
    const body = header?.nextElementSibling;
    if (!body) throw new Error("PageHeader の直後に PageBody がない");
    const rect = body.getBoundingClientRect();
    const style = getComputedStyle(body);
    return {
      titleLeft: h1.getBoundingClientRect().left,
      bodyContentLeft: rect.left + parseFloat(style.paddingLeft),
      bodyWidth: rect.width,
    };
  });
}

for (const width of [1920, 2560]) {
  for (const target of PAGES) {
    test(`${width}px: ${target.title} は ${target.wide ? "画面幅いっぱい" : "1440px"} で、タイトルと本文の左端が一致する`, async ({
      page,
    }, testInfo) => {
      test.skip(testInfo.project.name !== "desktop", "広い画面の計測は desktop プロジェクトで行う");
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(target.path);

      const layout = await measureLayout(page, target.title);
      expect(Math.abs(layout.titleLeft - layout.bodyContentLeft)).toBeLessThanOrEqual(1);
      if (target.wide) {
        expect(layout.bodyWidth).toBeGreaterThan(CONTENT_MAX_WIDTH);
      } else {
        expect(Math.round(layout.bodyWidth)).toBe(CONTENT_MAX_WIDTH);
      }
      await expectNoPageOverflow(page);
    });
  }
}

for (const target of PAGES.filter((item) => item.wide)) {
  test(`375px: ${target.title} はタイトルと本文の左端が一致し、横にはみ出さない`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "狭い画面の計測は mobile プロジェクトで行う");
    await page.goto(target.path);

    const layout = await measureLayout(page, target.title);
    expect(Math.abs(layout.titleLeft - layout.bodyContentLeft)).toBeLessThanOrEqual(1);
    await expectNoPageOverflow(page);
  });
}
