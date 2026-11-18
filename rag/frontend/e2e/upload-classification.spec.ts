import { expect, test, type Page } from "./fixtures/test";

import { apiEnvelope, expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

// #5464: アップロードの画面で、この回のすべてのファイルに分類と有効期間を設定する。最初は閉じ、
// 保存ボタンは無く、「アップロードを開始」で一緒に送る（選ばなければ送らない）。

function category(id: string, name: string, level: number, parent: string | null, children: unknown[] = []) {
  return { id, name, level, parent_id: parent, document_count: 0, children };
}

async function mockUploadPage(page: Page) {
  const bodies: string[] = [];
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/settings/upload-storage", (route) =>
    route.fulfill({
      json: apiEnvelope({
        backend: "local",
        local_storage_dir: "/tmp/rag-uploads",
        object_storage_namespace: null,
        object_storage_bucket: null,
        object_storage_region: null,
        readiness: "ready",
        source: "runtime",
        max_upload_bytes: 209715200,
      }),
    })
  );
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({ json: apiEnvelope({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) })
  );
  await page.route("**/api/document-categories", (route) =>
    route.fulfill({
      json: apiEnvelope([
        category("l1", "分類A", 1, null, [category("m1", "分類A-1", 2, "l1")]),
        category("l2", "分類B", 1, null),
      ]),
    })
  );
  // 送信内容だけを確かめる。結果の画面へは進めない。
  await page.route(/\/api\/documents\/(batch-)?upload$/, async (route) => {
    bodies.push(route.request().postData() ?? "");
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["テスト用にアップロードを止めました。"], warning_messages: [] },
    });
  });
  return bodies;
}

const FILES = [
  { name: "a.txt", mimeType: "text/plain", buffer: Buffer.from("A") },
  { name: "b.txt", mimeType: "text/plain", buffer: Buffer.from("B") },
];

function classificationField(body: string): unknown {
  const match = body.match(/name="classification"\r\n\r\n(.*)\r\n/);
  return match ? JSON.parse(match[1]) : undefined;
}

test("選んだ分類と有効期間を、この回のすべてのファイルに付けて送る", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const bodies = await mockUploadPage(page);
  await page.goto("/upload");

  // ナレッジベースの欄の下に、閉じた状態で置く。閉じたままでも今の値（未設定）を見せる。
  const disclosure = page.getByTestId("upload-classification");
  await expect(disclosure).not.toHaveAttribute("open", "");
  await expect(page.getByTestId("upload-classification-current")).toHaveText("未設定");
  const pickerBox = (await page.getByTestId("upload-knowledge-base-picker").boundingBox())!;
  expect((await disclosure.boundingBox())!.y).toBeGreaterThan(pickerBox.y + pickerBox.height - 1);

  await disclosure.locator("summary").click();
  await page.getByRole("button", { name: /^大分類 / }).click();
  await page.getByRole("option", { name: "分類A", exact: true }).click();
  await page.getByRole("button", { name: /^中分類 / }).click();
  await page.getByRole("option", { name: "分類A-1" }).click();
  await page.getByLabel("有効期間の開始日").fill("2026-04-01");
  await page.getByLabel("有効期間の終了日").fill("2027-04-01");
  await expect(page.getByTestId("upload-classification-current")).toHaveText(
    "分類A / 分類A-1・有効期間 2026-04-01〜2027-04-01"
  );
  // 保存ボタンは置かない（開始の時に一緒に送る）。
  await expect(disclosure.getByRole("button", { name: /保存/ })).toHaveCount(0);
  await expectNoPageOverflow(page);
  await disclosure.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("upload-classification-open.png"), fullPage: true });

  await page.locator('input[type="file"]').setInputFiles(FILES);
  await page.getByRole("button", { name: /アップロードを開始/ }).click();
  await expect.poll(() => bodies.length).toBe(1);
  expect(bodies[0]).toContain('filename="a.txt"');
  expect(bodies[0]).toContain('filename="b.txt"');
  expect(classificationField(bodies[0])).toEqual({
    large_category: "分類A",
    middle_category: "分類A-1",
    small_category: null,
    effective_from: "2026-04-01",
    effective_to: "2027-04-01",
  });
});

test("分類と有効期間を選ばなければ送らない", async ({ page }) => {
  const bodies = await mockUploadPage(page);
  await page.goto("/upload");

  await page.locator('input[type="file"]').setInputFiles(FILES[0]);
  await page.getByRole("button", { name: /アップロードを開始/ }).click();
  await expect.poll(() => bodies.length).toBe(1);
  expect(bodies[0]).not.toContain('name="classification"');
});

test("終了日が開始日より前なら送らず、閉じていても開いて終了日の欄の下で知らせる", async ({ page }) => {
  const bodies = await mockUploadPage(page);
  await page.goto("/upload");

  const disclosure = page.getByTestId("upload-classification");
  await disclosure.locator("summary").click();
  await page.getByLabel("有効期間の開始日").fill("2026-04-01");
  const effectiveTo = page.getByLabel("有効期間の終了日");
  await effectiveTo.fill("2026-04-01");
  await disclosure.locator("summary").click();
  await expect(disclosure).not.toHaveAttribute("open", "");

  await page.locator('input[type="file"]').setInputFiles(FILES[0]);
  await page.getByRole("button", { name: /アップロードを開始/ }).click();
  await expect(disclosure).toHaveAttribute("open", "");
  await expect(page.getByText("終了日は開始日より後の日付にしてください。")).toBeVisible();
  await expect(effectiveTo).toBeFocused();
  expect(bodies).toHaveLength(0);

  // 直すと送れる。
  await effectiveTo.fill("2026-04-02");
  await expect(page.getByText("終了日は開始日より後の日付にしてください。")).toHaveCount(0);
  await page.getByRole("button", { name: /アップロードを開始/ }).click();
  await expect.poll(() => bodies.length).toBe(1);
});
