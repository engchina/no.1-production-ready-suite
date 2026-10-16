import { expect, test, type Page } from "./fixtures/test";
import { expectNoPageOverflow, mockLocalAuth } from "./_helpers";

// 言語資源（#2181）。項目の定義と組み込みの値は backend の一部（画面の確認に足りる分）。
const FIELDS = [
  { section: "numbers", key: "article_units", kind: "words", choices: [], ingest: true, max_chars: 40 },
  { section: "numbers", key: "kanji_multipliers", kind: "char_values", choices: [], ingest: true, max_chars: 40 },
  { section: "symbols", key: "normalization", kind: "choice", choices: ["NFKC", "NFC", "none"], ingest: true, max_chars: 40 },
  { section: "symbols", key: "bracket_pairs", kind: "pairs", choices: [], ingest: true, max_chars: 40 },
  { section: "references", key: "cue_words", kind: "words", choices: [], ingest: true, max_chars: 40 },
  { section: "titles", key: "edition_words", kind: "words", choices: [], ingest: true, max_chars: 40 },
  {
    section: "english",
    key: "en_section_words",
    kind: "enum_map",
    choices: ["chapter", "section", "article", "appendix"],
    ingest: true,
    max_chars: 40,
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
  const restored: string[] = [];
  await page.route("**/api/settings/language-pack/history", async (route) => {
    await route.fulfill({
      json: envelope({
        versions: [
          {
            id: "v2",
            saved_at: "2026-10-17T09:00:00Z",
            saved_by: "管理者",
            hash: "a",
            ingest_hash: "ia",
            restored_from: null,
            changed_fields: 0,
          },
          {
            id: "v1",
            saved_at: "2026-10-16T09:00:00Z",
            saved_by: "管理者",
            hash: "b",
            ingest_hash: "ib",
            restored_from: null,
            changed_fields: 2,
          },
        ],
      }),
    });
  });
  await page.route("**/api/settings/language-pack/history/*/restore", async (route) => {
    restored.push(route.request().url().split("/history/")[1].split("/")[0]);
    const overrides = { numbers: { article_units: ["条", "款"] } };
    const effective = structuredClone(BUILTIN) as Record<string, Record<string, unknown>>;
    Object.assign(effective.numbers, overrides.numbers);
    await route.fulfill({ json: envelope({ fields: FIELDS, builtin: BUILTIN, overrides, effective, hash: "b" }) });
  });
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
      json: envelope({
        fields: FIELDS,
        builtin: BUILTIN,
        overrides: {},
        effective: BUILTIN,
        hash: "a",
        outdated_documents: 3,
      }),
    });
  });
  return { saved, previews, restored };
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
    // 今の言語資源で分割していない文書の数を案内する（#2181）。
    await expect(page.getByText("今の言語資源で分割していない文書が 3 件あります")).toBeVisible();
    const tabs = page.getByRole("tablist", { name: "言語資源", exact: true });
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

test("言語資源の保存の履歴から前の版に戻す。編集中は戻せない（#2181）", async ({ page }) => {
  const { restored } = await mockLanguagePack(page);
  await page.goto("/settings/language-pack");

  const history = page.getByRole("table", { name: "保存の履歴" });
  await expect(history.getByRole("row")).toHaveCount(3);
  const currentRow = history.getByRole("row").filter({ hasText: "今の版" });
  await expect(currentRow.getByRole("button", { name: "この版に戻す" })).toHaveCount(0);
  const olderRow = history.getByRole("row").filter({ hasText: "既定と違う項目 2 件" });
  const restore = olderRow.getByRole("button", { name: "この版に戻す" });

  // 編集中の変更があるあいだは戻さない。
  const units = page.getByLabel("条の単位", { exact: true });
  await units.fill("条\n款");
  await expect(restore).toBeDisabled();
  await page.getByRole("button", { name: "変更を破棄" }).click();
  await expect(restore).toBeEnabled();

  await restore.click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("取込の項目が今と違うため");
  await dialog.getByRole("button", { name: "この版に戻す" }).click();
  await expect(page.getByText("前の版に戻しました。")).toBeVisible();
  expect(restored).toEqual(["v1"]);
  await expect(units).toHaveValue("条\n款");
});

test("言語資源は使い道の組と節の 2 段のタブで開き、項目を名前で探せる（#2181）", { tag: "@responsive" }, async ({ page }) => {
  const fields = [
    ...FIELDS,
    { section: "search_terms", key: "stopwords", kind: "words", choices: [], ingest: false, max_chars: 40 },
    { section: "answer_labels", key: "citation_prefix", kind: "text", choices: [], ingest: false, max_chars: 200 },
  ];
  const builtin = { ...BUILTIN, search_terms: { stopwords: ["こと"] }, answer_labels: { citation_prefix: "根拠：" } };
  await mockLanguagePack(page);
  await page.route("**/api/settings/language-pack", (route) =>
    route.fulfill({ json: envelope({ fields, builtin, overrides: {}, effective: builtin, hash: "a" }) }),
  );
  await page.goto("/settings/language-pack");

  const groups = page.getByRole("tablist", { name: "言語資源の使い道" });
  await expect(groups.getByRole("tab")).toHaveText(["文書を読む", "質問を読む", "回答を確かめて表す"]);
  await groups.getByRole("tab", { name: "質問を読む" }).click();
  await expect(groups.getByRole("tab", { name: "質問を読む" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tablist", { name: "言語資源", exact: true }).getByRole("tab")).toHaveText(["全文検索の質問の語"]);
  await expect(page.getByLabel("止め語", { exact: true })).toHaveValue("こと");

  await page.getByRole("searchbox", { name: "項目を探す" }).fill("出典");
  const results = page.getByTestId("language-search-results");
  await expect(results.getByRole("heading", { name: "回答の表示" })).toBeVisible();
  await expect(page.getByLabel("出典の前置き", { exact: true })).toHaveValue("根拠：");
  await expect(page.getByRole("tablist", { name: "言語資源の使い道" })).toHaveCount(0);
  await page.getByRole("searchbox", { name: "項目を探す" }).fill("見つからない語");
  await expect(page.getByText("検索語に当たる項目はありません。")).toBeVisible();
  await expectNoPageOverflow(page);
});
