import { expect, test, type Page } from "./fixtures/test";
import { expectNoPageOverflow, mockLocalAuth } from "./_helpers";

// 言語資源（#2181）。項目の定義と組み込みの値は backend の一部（画面の確認に足りる分）。
const FIELDS = [
  { section: "numbers", key: "article_units", kind: "words", choices: [], ingest: true },
  { section: "numbers", key: "kanji_multipliers", kind: "char_values", choices: [], ingest: true },
  { section: "symbols", key: "normalization", kind: "choice", choices: ["NFKC", "NFC", "none"], ingest: true },
  { section: "symbols", key: "bracket_pairs", kind: "pairs", choices: [], ingest: true },
  { section: "references", key: "cue_words", kind: "words", choices: [], ingest: true },
  { section: "titles", key: "edition_words", kind: "words", choices: [], ingest: true },
  {
    section: "english",
    key: "en_section_words",
    kind: "enum_map",
    choices: ["chapter", "section", "article", "appendix"],
    ingest: true,
  },
];
const BUILTIN = {
  numbers: { article_units: ["条"], kanji_multipliers: ["十=10", "百=100"] },
  symbols: { normalization: "NFKC", bracket_pairs: ["()", "【】"] },
  references: { cue_words: ["を参照", "による"] },
  titles: { edition_words: ["改訂版"] },
  english: { en_section_words: ["section=section"] },
};

function envelope(data: unknown) {
  return { data, error_messages: [], warning_messages: [] };
}

async function mockLanguagePack(page: Page) {
  const saved: { overrides: Record<string, Record<string, unknown>> | null } = { overrides: null };
  const previews: Record<string, unknown>[] = [];
  await page.route("**/api/settings/language-pack/preview", async (route) => {
    previews.push(route.request().postDataJSON() as Record<string, unknown>);
    const result = (headingKeys: string[]) => ({
      references: [{ label: "第二款", kind: "article", key: "2", document_title: null }],
      title_keys: ["第二款届出"],
      heading_keys: headingKeys,
    });
    await route.fulfill({ json: envelope({ saved: result([]), draft: result(["article:2"]) }) });
  });
  await page.route("**/api/settings/language-pack", async (route) => {
    if (route.request().method() === "PUT") {
      saved.overrides = (route.request().postDataJSON() as { overrides: Record<string, Record<string, unknown>> })
        .overrides;
      const effective = structuredClone(BUILTIN) as Record<string, Record<string, unknown>>;
      for (const [section, values] of Object.entries(saved.overrides)) Object.assign(effective[section], values);
      await route.fulfill({
        json: envelope({ fields: FIELDS, builtin: BUILTIN, overrides: saved.overrides, effective, hash: "b" }),
      });
      return;
    }
    await route.fulfill({
      json: envelope({ fields: FIELDS, builtin: BUILTIN, overrides: {}, effective: BUILTIN, hash: "a" }),
    });
  });
  return { saved, previews };
}

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`言語資源は節のタブで項目を編集し、組み込みと違う項目だけを保存する（#2181, ${viewport.name}）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const { saved } = await mockLanguagePack(page);

    await page.goto("/settings/language-pack");

    await expect(page.getByRole("heading", { name: "言語資源", level: 1 })).toBeVisible();
    const tabs = page.getByRole("tablist", { name: "言語資源" });
    await expect(tabs.getByRole("tab")).toHaveText([
      "番号の書き方",
      "記号と文字",
      "参照の書き方",
      "文書名の照合",
      "英語の参照の書き方",
    ]);
    const units = page.getByLabel("条の単位", { exact: true });
    await expect(units).toHaveValue("条");
    await units.fill("条\n款");
    await expect(page.getByText("既定から変更")).toBeVisible();
    await page.getByRole("button", { name: "既定に戻す" }).click();
    await expect(units).toHaveValue("条");
    await units.fill("条\n款");

    // 別の節の形の誤り: 保存を押すとその節を開き、欄の直下に出してその欄へ移す。
    await tabs.getByRole("tab", { name: "記号と文字" }).click();
    const pairs = page.getByLabel("括弧書きの括弧", { exact: true });
    await pairs.fill("()\n(");
    await tabs.getByRole("tab", { name: "番号の書き方" }).click();
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("開きと閉じの 2 文字で書いてください（例: 「」）。")).toBeVisible();
    await expect(pairs).toBeFocused();
    expect(saved.overrides).toBeNull();

    await pairs.fill("()\n【】");
    await page.getByRole("combobox", { name: "文字の正規化" }).click();
    await page.getByRole("option", { name: "NFC（全角と半角をそろえない）" }).click();
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("言語資源を保存しました。")).toBeVisible();
    expect(saved.overrides).toEqual({
      numbers: { article_units: ["条", "款"] },
      symbols: { normalization: "NFC" },
    });
    await expect(page.getByRole("button", { name: "保存" })).toBeDisabled();
    await expectNoPageOverflow(page);
  });
}

test("言語資源の試すは、保存した値と編集中の値で例の文を読み比べる（保存しない。#2181）", async ({ page }) => {
  const { saved, previews } = await mockLanguagePack(page);
  await page.goto("/settings/language-pack");

  await page.getByLabel("条の単位", { exact: true }).fill("条\n款");
  await page.getByLabel("例の文", { exact: true }).fill("第二款 届出\n第二款を参照");
  await page.getByRole("button", { name: "試す" }).click();

  const result = page.getByTestId("language-preview-result");
  await expect(result.getByRole("region", { name: "編集中の言語資源" })).toContainText("article:2");
  await expect(result.getByRole("region", { name: "保存した言語資源" })).toContainText("なし");
  expect(previews).toEqual([
    { text: "第二款 届出\n第二款を参照", overrides: { numbers: { article_units: ["条", "款"] } } },
  ]);
  expect(saved.overrides).toBeNull();
});
