import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/mock-api";

// #535: 一覧の絞り込みの検索は 3 製品で同じ部品（SearchField）と規則で動く。
// メモリの一覧: 入力に合わせて絞り込み（debounce）、検索ボタンなし、IME の変換中は問い合わせない。

const entries = [
  { id: "memory-1", kind: "note", content: "請求の締め日は月末", metadata: {}, created_at: "2026-06-19T00:00:00Z" },
  { id: "memory-2", kind: "note", content: "人事の申請は前日まで", metadata: {}, created_at: "2026-06-18T00:00:00Z" },
  { id: "memory-3", kind: "tool_learning", content: "SQL の上限は 100 行", metadata: {}, created_at: "2026-06-17T00:00:00Z" },
];

async function mockMemorySearch(page: Page): Promise<string[]> {
  const queries: string[] = [];
  await page.route("**/api/memory/search", async (route) => {
    const body = route.request().postDataJSON() as { query?: string };
    const query = body.query ?? "";
    queries.push(query);
    const matched = entries.filter((entry) => !query || entry.content.includes(query));
    await route.fulfill({ json: { data: { entries: matched }, error_messages: [], warning_messages: [] } });
  });
  return queries;
}

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile-375", width: 375, height: 812 },
]) {
  for (const theme of ["light", "dark"] as const) {
    test(`メモリの一覧は入力に合わせて絞り込み、検索ボタンを置かない (${viewport.name}, ${theme})`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      const queries = await mockMemorySearch(page);
      await page.goto("/memory");

      const search = page.getByRole("searchbox", { name: "検索" });
      const list = page.getByRole("table", { name: "メモリ一覧" });
      await expect(list.getByText("請求の締め日は月末")).toBeVisible();
      await expect(page.getByRole("button", { name: "検索", exact: true })).toHaveCount(0);

      await search.pressSequentially("人事", { delay: 30 });
      await expect(list.getByText("請求の締め日は月末")).toHaveCount(0);
      await expect(list.getByText("人事の申請は前日まで")).toBeVisible();
      expect(queries.filter((query) => query !== "")).toEqual(["人事"]);
      await expect(page.getByRole("status").filter({ hasText: "1 件が一致しました" })).toHaveCount(1);

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth
      );
      expect(overflow).toBeLessThanOrEqual(0);
      await page.screenshot({ path: testInfo.outputPath(`memory-search-${viewport.name}-${theme}.png`) });

      await page.getByRole("button", { name: "検索語をクリア" }).click();
      await expect(list.getByText("請求の締め日は月末")).toBeVisible();
      await expect(search).toBeFocused();
    });
  }
}

test("メモリの一覧は IME の変換中に問い合わせず、確定した値で絞り込む", async ({ page }) => {
  const queries = await mockMemorySearch(page);
  await page.goto("/memory");
  const list = page.getByRole("table", { name: "メモリ一覧" });
  await expect(list.getByText("請求の締め日は月末")).toBeVisible();

  await page.evaluate(() => {
    const input = document.querySelector<HTMLInputElement>("#memory-search")!;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    input.focus();
    input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true, data: "" }));
    for (const step of ["s", "せ", "せい", "せいきゅう", "請求"]) {
      setter.call(input, step);
      input.dispatchEvent(new InputEvent("input", { bubbles: true, isComposing: true, data: step }));
    }
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true, isComposing: true }));
  });
  await page.waitForTimeout(700);
  expect(queries.filter((query) => query !== "")).toEqual([]);

  await page.evaluate(() => {
    const input = document.querySelector<HTMLInputElement>("#memory-search")!;
    input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: "請求" }));
  });
  await expect(list.getByText("人事の申請は前日まで")).toHaveCount(0);
  await expect(list.getByText("請求の締め日は月末")).toBeVisible();
  expect(queries.filter((query) => query !== "")).toEqual(["請求"]);
});
