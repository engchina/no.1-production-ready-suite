import { expect, type Page, test } from "./fixtures/test";
import { expectToastPlacement } from "./_toast-placement";
import { expectedControlHeight, expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

function ok(json: unknown) {
  return { data: json, error_messages: [], warning_messages: [] };
}

const nullIngestion = {
  preprocess_profile: null,
  parser_adapter_backend: null,
  parser_docling_enabled: null,
  parser_unstructured_enabled: null,
  chunking_strategy: null,
  chunk_size: null,
  chunk_overlap: null,
  chunk_min_chars: null,
  chunk_context_header_enabled: null,
  graph_profile: null,
  field_extraction_enabled: null,
  vision_enabled: null,
  navigation_summary_enabled: null,
  auto_parse_after_preprocess_enabled: null,
  auto_chunk_after_extract_enabled: null,
  auto_index_after_chunk_enabled: null,
};
const nullQuery = {
  guardrail_policy: null,
};
const kbDetail = {
  id: "kb-1",
  name: "社内規程",
  description: "就業規則",
  status: "ACTIVE",
  default_search_mode: "hybrid",
  document_count: 1,
  indexed_document_count: 1,
  error_document_count: 0,
  searchable_chunk_count: 10,
  created_at: "2026-06-15T00:00:00Z",
  updated_at: "2026-06-15T00:00:00Z",
  archived_at: null,
  retrieval_config: {},
  adapter_config: { version: 1, ingestion: nullIngestion, query: nullQuery },
};

const EMPTY_OVERVIEW = { status: "empty", documents: [], edges: [] };
const ID = (value: string) => value.padEnd(64, "0");
const structureDocument = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id, document_id: `doc-${id}`, chunk_set_id: `set-${id}`, name, has_sections: true, outgoing_count: 0, incoming_count: 0, ...extra,
});
const treeNode = (id: string, label: string, parent: { id: string } | null, documentId: string, extra: Record<string, unknown> = {}) => ({
  id, name: label, label, type: "section", document_id: `doc-${documentId}`, document_name: `${documentId}.pdf`, chunk_set_id: `set-${documentId}`,
  parent_id: parent?.id ?? null, virtual: false, child_count: 0, ...extra,
});

/** 章節の木の合成の応答（親 → 子。parent が無ければ文書の直下）。 */
async function mockTree(page: Page, trees: Record<string, ReturnType<typeof treeNode>[]>, references: Record<string, unknown[]> = {}) {
  await page.route(/\/api\/knowledge-bases\/kb-1\/graph\/documents\/[^/]+\/tree/, (route) => {
    const url = new URL(route.request().url());
    const key = url.searchParams.get("parent") ?? `root:${url.searchParams.get("chunk_set_id")}`;
    const all = trees[key] ?? [];
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 100);
    return route.fulfill({ json: ok({ items: all.slice(offset, offset + limit), total: all.length, offset }) });
  });
  await page.route(/\/api\/knowledge-bases\/kb-1\/graph\/documents\/[^/]+\/references/, (route) =>
    route.fulfill({ json: ok({ items: references[new URL(route.request().url()).searchParams.get("chunk_set_id") ?? ""] ?? [] }) })
  );
}

async function mockKb(
  page: Page,
  graph: unknown,
  warnings: () => string[] = () => []
): Promise<void> {
  await page.route("**/api/documents**", (route) =>
    route.fulfill({ json: ok({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) })
  );
  // generic を先に、具体ルートを後に登録(Playwright は後勝ち)。
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({ json: ok({ items: [kbDetail], total: 1, limit: 20, offset: 0, has_next: false }) })
  );
  await page.route("**/api/knowledge-bases/kb-1/graph**", (route) =>
    route.fulfill({ json: { ...ok(graph), warning_messages: warnings() } })
  );
  await page.route("**/api/knowledge-bases/kb-1/graph/search**", (route) => route.fulfill({ json: ok({ items: [], total: 0, offset: 0 }) }));
  await page.route("**/api/knowledge-bases/kb-1/graph/references**", (route) => route.fulfill({ json: ok({ items: [], offset: 0, has_more: false, direction: "outgoing" }) }));
  await page.route("**/api/knowledge-bases/kb-1/structure-index/job**", (route) => route.fulfill({ json: ok(null) }));
  await page.route("**/api/knowledge-bases/kb-1", (route) => route.fulfill({ json: ok(kbDetail) }));
  // KB の抽出する項目（#548）。上の wildcard の一覧を返さない。
  await page.route("**/api/knowledge-bases/kb-1/extraction-fields", (route) =>
    route.fulfill({ json: ok({ inherits_default: true, fields: [] }) })
  );
}

// 文書構造グラフの開閉の見出し（共有の Disclosure の <summary>。#1135）。
function disclosureSummary(page: Page, name: string) {
  return page.locator("summary", { hasText: name });
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
});

test("文書構造グラフは KB の全文書を出す", async ({ page }) => {
  await mockKb(page, {
    status: "ok",
    documents: [structureDocument("a", "合成文書A.pdf"), structureDocument("b", "合成文書B.pdf")],
    edges: [{ id: "a:b", source: "a", target: "b", count: 3 }],
  });

  await page.goto("/knowledge-bases/kb-1");
  await disclosureSummary(page, "文書構造グラフを表示").click();

  const graph = page.getByRole("region", { name: "文書構造グラフ" });
  await expect(graph).toBeVisible();
  await expect(graph.getByText("合成文書A", { exact: true })).toBeVisible();
  await expect(graph.getByText("合成文書B", { exact: true })).toBeVisible();
  // 文書の間の参照は操作なしで見え、件数を出す。
  await expect(graph.locator(".react-flow__edge-text")).toHaveText("参照 3 件");

  // ホイールは NL2SQL の図と同じく図の拡大・縮小で、ページを動かさない。
  const main = page.locator("main");
  await graph.scrollIntoViewIfNeeded();
  const mainScrollTop = await main.evaluate((element) => element.scrollTop);
  const viewport = graph.locator(".react-flow__viewport");
  const before = await viewport.getAttribute("style");
  await graph.hover();
  await page.mouse.wheel(0, 600);
  await expect.poll(() => viewport.getAttribute("style")).not.toBe(before);
  expect(await main.evaluate((element) => element.scrollTop)).toBe(mainScrollTop);
});

test("関係情報が無い KB は空状態を出す", async ({ page }) => {
  await mockKb(page, EMPTY_OVERVIEW);

  await page.goto("/knowledge-bases/kb-1");
  await disclosureSummary(page, "文書構造グラフを表示").click();

  await expect(page.getByText("文書構造索引がまだありません。")).toBeVisible();
  // 画面に無い操作（再取込）ではなく、文書の詳細の処理レシピの「再処理」を案内する。
  const hint = page.locator("#knowledge-base-graph");
  await expect(hint).toContainText("「既存文書に構築」");
  await expect(hint).toContainText("再処理");
  await expect(hint).not.toContainText("再取込");
});

// 開閉の見出しは共有の Disclosure の高さ（lg 40px・タッチ端末 44px）。以前は手書きの min-h-11 で 38.5px（#1135）。
test("文書構造グラフは抽出項目と検索テストの間にあり、標準の高さとキーボードで開閉する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockKb(page, EMPTY_OVERVIEW);
  await page.goto("/knowledge-bases/kb-1");

  const expected = await expectedControlHeight(page, "lg");
  const summary = disclosureSummary(page, "文書構造グラフを表示");
  await expect(summary).toBeVisible();
  const fields = page.getByRole("heading", { name: "抽出する項目", exact: true });
  const search = page.getByRole("heading", { name: "このナレッジで検索テスト", exact: true });
  await expect(fields).toBeVisible();
  await expect(search).toBeVisible();
  const [fieldsBox, graphBox, searchBox] = await Promise.all([
    fields.boundingBox(), summary.boundingBox(), search.boundingBox(),
  ]);
  expect(fieldsBox!.y + fieldsBox!.height).toBeLessThan(graphBox!.y);
  expect(graphBox!.y + graphBox!.height).toBeLessThan(searchBox!.y);
  expect(Math.round((await summary.boundingBox())?.height ?? 0)).toBe(expected);

  // キーボード（Enter）で開閉でき、開くと見出しの文言が「隠す」になる。
  const graph = disclosureSummary(page, "文書構造グラフを表示");
  await graph.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("details").filter({ has: page.locator("#knowledge-base-graph") })).toHaveAttribute("open", "");
  await expect(page.getByText("文書構造索引がまだありません。")).toBeVisible();
  await expect(disclosureSummary(page, "文書構造グラフを隠す")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#knowledge-base-graph")).toHaveCount(0);
  await expect(disclosureSummary(page, "文書構造グラフを表示")).toBeVisible();
  await summary.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("knowledge-base-section-order.png"), fullPage: false });
});

// 関係情報を読み取れないときは、「関係情報がまだありません」と区別して、原因と再試行を出す（#4217）。
for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`関係情報を取得できないときは空と区別して出し、再試行で取り直す (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.clock.install();
    await mockKb(page, EMPTY_OVERVIEW);
    let failing = true;
    let graphRequests = 0;
    await page.route("**/api/knowledge-bases/kb-1/graph**", async (route) => {
      graphRequests += 1;
      if (!failing) return route.fallback();
      return route.fulfill({
        status: 503,
        json: {
          data: null,
          error_messages: ["データの読み取りがタイムアウトしました。"],
          warning_messages: [],
          error_code: "database_read_timeout",
        },
      });
    });

    await page.goto("/knowledge-bases/kb-1");
    await disclosureSummary(page, "文書構造グラフを表示").click();
    await expect.poll(() => graphRequests).toBe(1);
    // 自動の再試行（間隔を空けて 2 回）を進める。
    await page.clock.runFor(5_100);
    await expect.poll(() => graphRequests).toBe(2);
    await page.clock.runFor(15_100);
    await expect.poll(() => graphRequests).toBe(3);

    const section = page.locator("#knowledge-base-graph");
    await expect(section.getByRole("alert")).toContainText("読み取りがタイムアウトしました");
    await expect(section.getByText("文書構造索引がまだありません。")).toHaveCount(0);
    await expectNoPageOverflow(page);

    // 復旧したら再試行で取り直し、本当に空なら空の状態を出す。
    failing = false;
    await section.getByRole("button", { name: "再試行" }).click();
    await expect(section.getByText("文書構造索引がまだありません。")).toBeVisible();
    await expect(section.getByRole("alert")).toHaveCount(0);
  });
}

// React Flow の操作部と辺のラベルはテーマに合わせ、帰属リンクは表示しない。
// React Flow の既定の配色は colorMode="dark" のときだけダークになり、ダークテーマでも白い地のまま出ていた。
// platform/packages/ui の integrations/react-flow.css が React Flow の変数をトークンに結び付ける。
for (const theme of ["light", "dark"] as const) {
  test(`文書構造グラフの操作はテーマに合わせ帰属リンクを表示しない (${theme})`, { tag: "@responsive" }, async ({
    page,
  }, testInfo) => {
    await page.addInitScript((value) => {
      window.localStorage.setItem(
        "production-ready-rag.ui",
        JSON.stringify({ state: { theme: value }, version: 0 })
      );
    }, theme);
    await mockKb(page, {
      status: "ok",
      documents: [structureDocument("a", "合成文書A.pdf"), structureDocument("b", "合成文書B.pdf")],
      edges: [{ id: "a:b", source: "a", target: "b", count: 2 }],
    });

    await page.goto("/knowledge-bases/kb-1");
    await expect
      .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
      .toBe(theme === "dark");
    await disclosureSummary(page, "文書構造グラフを表示").click();

    const region = page.getByRole("region", { name: "文書構造グラフ" });
    await expect(region.getByText("合成文書A", { exact: true })).toBeVisible();

    // トークンの色を同じ場所（図の中）で解決して、React Flow の部品の色と比べる。
    const colors = await region.evaluate((element) => {
      const probe = document.createElement("div");
      element.append(probe);
      const token = (name: string) => {
        probe.style.backgroundColor = `var(${name})`;
        return getComputedStyle(probe).backgroundColor;
      };
      const tokens = {
        surface: token("--color-surface"),
        raised: token("--color-surface-raised"),
        fg: token("--color-fg"),
        fgMuted: token("--color-fg-muted"),
      };
      probe.remove();
      const style = (selector: string) => {
        const target = element.querySelector(selector);
        return target ? getComputedStyle(target) : null;
      };
      const buttons = [...element.querySelectorAll("button")].filter(button => ["拡大", "縮小", "全体表示"].includes(button.getAttribute("aria-label") ?? ""));
      return {
        tokens,
        buttonCount: buttons.length,
        buttonBackgrounds: buttons.map((button) => getComputedStyle(button).backgroundColor),
        buttonColors: buttons.map((button) => getComputedStyle(button).color),
        edgeLabelBackground: style(".react-flow__edge-textbg")?.fill ?? null,
      };
    });
    expect(colors.buttonCount).toBe(3);
    // 共通 ghost Button は透明な面を通して親の surface を表示する。
    expect(new Set(colors.buttonBackgrounds)).toEqual(new Set(["rgba(0, 0, 0, 0)"]));
    expect(new Set(colors.buttonColors)).toEqual(new Set([colors.tokens.fg]));
    await region.screenshot({ path: testInfo.outputPath(`graph-attribution-${theme}.png`) });
    await expect(region.locator(".react-flow__attribution")).toHaveCount(0);
    const viewport = region.locator(".react-flow__viewport");
    const beforeZoom = await viewport.getAttribute("style");
    await region.getByRole("button", { name: "拡大", exact: true }).click();
    await expect(viewport).not.toHaveAttribute("style", beforeZoom!);
    await region.getByRole("button", { name: "全体表示", exact: true }).click();
    if (colors.edgeLabelBackground !== null) {
      expect(colors.edgeLabelBackground).toBe(colors.tokens.surface);
    }
    // 参照の辺のラベルは常に出す。
    await expect(region.locator(".react-flow__edge-textbg")).toHaveCount(1);
    await expectNoPageOverflow(page);
  });
}


for (const count of [30, 80]) {
  test(`${count} 文書の総覧を全体表示できる`, { tag: "@responsive" }, async ({ page }) => {
    await mockKb(page, {
      status: "ok",
      documents: Array.from({ length: count }, (_, index) => structureDocument(`node-${index}`, `合成文書 ${index}.pdf`)),
      edges: [{ id: "node-0:node-1", source: "node-0", target: "node-1", count: 4 }],
    });
    await page.goto("/knowledge-bases/kb-1");
    await disclosureSummary(page, "文書構造グラフを表示").click();
    const graph = page.getByRole("region", { name: "文書構造グラフ" });
    await expect(graph.locator(".react-flow__node")).toHaveCount(count);
    const fits = () => graph.evaluate((region) => {
      const bounds = region.getBoundingClientRect();
      return [...region.querySelectorAll(".react-flow__node")].every((node) => {
        const box = node.getBoundingClientRect();
        return box.left >= bounds.left && box.right <= bounds.right && box.top >= bounds.top && box.bottom <= bounds.bottom;
      });
    });
    await expect.poll(fits).toBe(true);
    await graph.getByRole("button", { name: "全体表示", exact: true }).click();
    await expect.poll(fits).toBe(true);
    // 英語の内部名を出さない（#3028）。
    await expect(graph.locator(".react-flow__edge-text").first()).toHaveText("参照 4 件");
    // 小地図の viewBox は有限の値で、ノードが描かれる（#4078）。375px は小地図を出さない。
    const minimap = graph.locator(".react-flow__minimap svg");
    await expect(graph.locator(".react-flow__minimap-node")).toHaveCount(count);
    const viewBox = (await minimap.getAttribute("viewBox"))!.split(" ").map(Number);
    expect(viewBox).toHaveLength(4);
    expect(viewBox.every(Number.isFinite)).toBe(true);
    expect(viewBox[2]).toBeGreaterThan(0);
    expect(viewBox[3]).toBeGreaterThan(0);
    if (page.viewportSize()!.width >= 640) await expect(minimap).toBeVisible();
    else await expect(minimap).toBeHidden();
    await graph.scrollIntoViewIfNeeded();
    await page.screenshot({ path: test.info().outputPath(`graph-${count}.png`) });
    await expectNoPageOverflow(page);
  });
}

test("長い共通の接頭辞を持つ章節は末尾の見出しで区別でき、完全な名前を詳細で読める", { tag: "@responsive" }, async ({ page }) => {
  // #4079: 文書名が長いと、先頭から出す表示は行数の上限で同じ文字列に見えていた。
  const common = "合成の長い文書名".repeat(40);
  const paths = [`${common} > 第1章 > 第1条`, `${common} > 第1章 > 第2条`, `${common} > 第2章 > 第1条`];
  const document = structureDocument("doc", `${common}.pdf`);
  await mockKb(page, { status: "ok", documents: [document], edges: [] });
  await mockTree(page, {
    "root:set-doc": paths.map((name, index) => ({
      ...treeNode(`section-${index}`, name.split(" > ").at(-1)!, null, "doc"),
      name,
    })),
  });
  await page.goto("/knowledge-bases/kb-1");
  await disclosureSummary(page, "文書構造グラフを表示").click();
  const graph = page.locator("#knowledge-base-graph");
  const documentNode = graph.locator('.react-flow__node[data-id="doc"]');
  await documentNode.focus();
  await page.keyboard.press("Enter");
  const node = (index: number) => graph.locator(`.react-flow__node[data-id="section-${index}"]`);
  await expect(node(0)).toBeVisible();

  const label = async (index: number) => (await node(index).innerText()).split("\n").slice(1).join(" / ");
  expect(await label(0)).toBe("第1条 / … > 第1章");
  expect(await label(1)).toBe("第2条 / … > 第1章");
  expect(await label(2)).toBe("第1条 / … > 第2章");

  // 完全な名前は title（ポインター）と選択の詳細（タッチ・キーボード）で読める。
  await expect(node(1).locator("[title]").first()).toHaveAttribute("title", paths[1]!);
  await expect(node(1)).toHaveAttribute("aria-label", `章・節：${paths[1]}`);
  await node(1).focus();
  await page.keyboard.press("Enter");
  await expect(graph.getByRole("region", { name: "選択したノード" }).getByText(paths[1]!, { exact: true })).toBeVisible();

  // 文書の節点はファイル名で（拡張子を外す）、階層のパスとして分けない。
  await expect(documentNode.locator(".font-semibold")).toHaveText(common);
  await expectNoPageOverflow(page);
});

test("構造索引は保存と補建を分け、停止・再開の状態を保持する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockKb(page, EMPTY_OVERVIEW);
  let enabled: boolean | null = null;
  let saves = 0;
  let starts = 0;
  let job: Record<string, unknown> | null = null;
  const items = [
    { document_id: "doc-a", name: "synthetic-a.txt", status: "MISSING", reason: null },
    { document_id: "doc-b", name: "synthetic-b.txt", status: "CURRENT", reason: null },
    { document_id: "doc-c", name: "synthetic-c.txt", status: "UNAVAILABLE", reason: "no_chunks" },
  ];
  const result = (status: string) => ({ job_id: "job-a", status, started_at: "2026-01-01T00:00:00Z", finished_at: null, items: [
    { ...items[0], index_status: status === "COMPLETED" ? "CURRENT" : "MISSING", index_quality: status === "COMPLETED" ? "PARTIAL" : null, status: status === "COMPLETED" ? "PARTIAL" : "QUEUED", result: status === "COMPLETED" ? { quality: "PARTIAL" } : null },
    { ...items[1], status: "SKIPPED", index_status: "CURRENT", reason: "current" }, { ...items[2], index_status: "UNAVAILABLE" },
  ], counts: { PARTIAL: status === "COMPLETED" ? 1 : 0, QUEUED: status === "COMPLETED" ? 0 : 1, SKIPPED: 1, UNAVAILABLE: 1 }, total: 3, offset: 0, limit: 25 });
  await page.route("**/api/knowledge-bases/kb-1", (route) => route.fulfill({ json: ok({ ...kbDetail, structure_index_enabled: enabled, structure_index_global_enabled: false }) }));
  await page.route("**/api/knowledge-bases/kb-1/structure-index", async (route) => {
    enabled = route.request().postDataJSON().enabled;
    saves++;
    await route.fulfill({ json: ok({ enabled, global_enabled: false }) });
  });
  await page.route("**/api/knowledge-bases/kb-1/structure-index/plan**", (route) => route.fulfill({ json: ok({ items, counts: { MISSING: 1, CURRENT: 1, UNAVAILABLE: 1 }, total: 3, offset: 0, limit: 25 }) }));
  await page.route("**/api/knowledge-bases/kb-1/structure-index/job**", async (route) => {
    if (route.request().method() === "POST") {
      if (new URL(route.request().url()).pathname.endsWith("/stop")) job = result("STOPPED");
      else { starts++; job = result(route.request().postDataJSON().mode === "continue" ? "COMPLETED" : "RUNNING"); }
    }
    await route.fulfill({ json: ok(job) });
  });
  await page.goto("/knowledge-bases/kb-1");
  const settings = page.getByTestId("knowledge-base-structure-settings");
  await settings.getByRole("combobox").click();
  await page.getByRole("option", { name: "構築する", exact: true }).click();
  await settings.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => saves).toBe(1);
  expect(starts).toBe(0);
  await settings.getByRole("button", { name: "既存文書に構築", exact: true }).click();
  await expect(settings.getByText("未構築 1 件・更新が必要 0 件・構築済み 1 件・準備不足 1 件")).toBeVisible();
  await settings.getByRole("button", { name: "不足・更新分を構築", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "不足・更新分を構築", exact: true }).click();
  await expect(page.getByText("構築を開始しました。ページを離れても処理は続きます。", { exact: true })).toBeVisible();
  await expect(settings.getByRole("button", { name: "停止", exact: true })).toBeVisible();
  await settings.getByRole("button", { name: "停止", exact: true }).click();
  await expect(settings.getByText("停止済み", { exact: true })).toBeVisible();
  await page.reload();
  await expect(settings.getByText("停止済み", { exact: true })).toBeVisible();
  await settings.getByRole("button", { name: "残りを続ける", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "残りを続ける", exact: true }).click();
  await expect(settings.getByText("処理終了。文書ごとの結果を確認してください。")).toBeVisible();
  await expect(settings.getByRole("cell", { name: "部分構築", exact: true })).toBeVisible();
  await expectNoPageOverflow(page);
  await settings.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("structure-build-completed.png"), fullPage: false });
  await settings.getByRole("button", { name: "既存文書に構築", exact: true }).click();
  await settings.getByRole("button", { name: "再構築", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByRole("heading", { name: "構築済みも含めて再構築しますか？" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "再構築", exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル", exact: true }).click();
  expect(starts).toBe(2);
});

test("構造索引の保存失敗を保持し、操作の再試行で要求を再送する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockKb(page, EMPTY_OVERVIEW);
  let saveAttempts = 0;
  let jobAttempts = 0;
  let job: Record<string, unknown> | null = null;
  const item = { document_id: "doc-a", name: "synthetic-a.txt", status: "MISSING", reason: null };
  await page.route("**/api/knowledge-bases/kb-1", (route) => route.fulfill({ json: ok({ ...kbDetail, structure_index_enabled: null, structure_index_global_enabled: false }) }));
  await page.route("**/api/knowledge-bases/kb-1/structure-index", async (route) => {
    saveAttempts++;
    await route.fulfill(saveAttempts === 1 ? { status: 503, json: { detail: "一時的に保存できません。" } } : { json: ok({ enabled: true, global_enabled: false }) });
  });
  await page.route("**/api/knowledge-bases/kb-1/structure-index/plan**", (route) => route.fulfill({ json: ok({ items: [item], counts: { MISSING: 1 }, total: 1, offset: 0, limit: 25 }) }));
  await page.route("**/api/knowledge-bases/kb-1/structure-index/job**", async (route) => {
    if (route.request().method() === "POST") {
      jobAttempts++;
      if (jobAttempts === 1) { await route.fulfill({ status: 503, json: { detail: "一時的に開始できません。" } }); return; }
      job = { job_id: "job-retry", status: "COMPLETED", started_at: "2026-01-01T00:00:00Z", finished_at: "2026-01-01T00:00:01Z", items: [{ ...item, status: "FAILED", index_status: "MISSING", reason: "processing_error" }], counts: { FAILED: 1 }, total: 1, offset: 0, limit: 25 };
    }
    await route.fulfill({ json: ok(job) });
  });
  await page.goto("/knowledge-bases/kb-1");
  const settings = page.getByTestId("knowledge-base-structure-settings");
  await settings.getByRole("combobox").click();
  await page.getByRole("option", { name: "構築する", exact: true }).click();
  await settings.getByRole("button", { name: "保存", exact: true }).click();
  await expect(settings.getByRole("alert")).toBeVisible();
  await expect(settings.getByRole("combobox")).toContainText("構築する");
  await settings.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("01-save-error.png") });
  await settings.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("文書構造索引の構築設定を保存しました。", { exact: true })).toBeVisible();
  await expect(settings.getByRole("alert")).toHaveCount(0);
  await page.getByRole("region", { name: "通知", exact: true }).evaluate(async (element) => { await Promise.all(element.getAnimations({ subtree: true }).map((animation) => animation.finished.catch(() => undefined))); });
  await expectToastPlacement(page);
  await page.screenshot({ path: testInfo.outputPath("02-save-toast.png") });
  await settings.getByRole("button", { name: "既存文書に構築", exact: true }).click();
  await settings.getByRole("button", { name: "不足・更新分を構築", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "不足・更新分を構築", exact: true }).click();
  await expect(settings.getByRole("button", { name: "再試行", exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("03-start-error.png") });
  await settings.getByRole("button", { name: "再試行", exact: true }).click();
  await expect.poll(() => jobAttempts).toBe(2);
  await expect(settings.getByRole("cell", { name: /^失敗：/ })).toBeVisible();
  await expect(settings.getByRole("button", { name: "失敗分を再試行", exact: true })).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("04-partial-failure.png") });
});

// 構造索引の構築の選択欄は、既定の長い文言「個別設定なし（…に従う）」を、選択中の値も一覧の選択肢も省略せず読める幅にする（#4227）。
test("構造索引の構築の選択欄は選択中の値と選択肢を省略せずに出す", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockKb(page, EMPTY_OVERVIEW);
  await page.route("**/api/knowledge-bases/kb-1", (route) => route.fulfill({ json: ok({ ...kbDetail, structure_index_enabled: null, structure_index_global_enabled: false }) }));
  await page.goto("/knowledge-bases/kb-1");
  const settings = page.getByTestId("knowledge-base-structure-settings");
  const select = settings.getByRole("combobox");
  await select.scrollIntoViewIfNeeded();
  await expect(select).toContainText("個別設定なし（文書レシピ・全体設定に従う）");

  // 文言が省略（text-overflow: ellipsis）で切れていない = 内容の幅が表示の幅に収まる。
  const clipped = (root: string) => page.evaluate((selector) => {
    return [...document.querySelectorAll<HTMLElement>(`${selector} .truncate`)]
      .filter((element) => element.scrollWidth > element.clientWidth + 1)
      .map((element) => element.textContent);
  }, root);
  // 375px はカードの内側（約 310px）が文言より狭く、閉じた状態の値は共有部品の既定どおり省略になる。全文は開いた一覧で折り返して読める。
  const mobile = page.viewportSize()!.width < 640;
  if (!mobile) expect(await clipped('[data-testid="knowledge-base-structure-settings"] [role="combobox"]')).toEqual([]);

  await select.click();
  const listbox = page.getByRole("listbox", { name: "文書構造索引の構築" });
  await expect(listbox.getByRole("option")).toHaveCount(3);
  await expect(listbox.getByRole("option", { name: "個別設定なし（文書レシピ・全体設定に従う）" })).toBeVisible();
  expect(await clipped('[role="listbox"]')).toEqual([]);
  const viewportWidth = page.viewportSize()!.width;
  const box = (await listbox.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewportWidth);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("structure-select-open.png") });
});

for (const width of [1280, 1920, 375]) {
  for (const theme of ["light", "dark"] as const) {
    test(`共通の文書構造画布で階層・検索・選択と種類切替を操作する ${width}px ${theme}`, { tag: "@responsive" }, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((state) => localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state, version: 0 })), { theme, sidebarCollapsed: width === 375 });
      const sectionA = treeNode("section-a", "合成章 A", null, "doc", { child_count: 2 });
      const sectionB = treeNode("section-b", "合成章 B", null, "doc");
      const table = treeNode("table", "合成表 A", sectionA, "doc", { type: "table_section" });
      const article = treeNode("article", "合成条 A", sectionA, "doc", { virtual: true });
      const extras = Array.from({ length: 10 }, (_, index) => treeNode(`extra-${index}`, `合成節 ${index}`, null, "doc"));
      await mockKb(page, {
        status: "ok",
        documents: [structureDocument("doc", "合成文書A.pdf", { outgoing_count: 2 }), structureDocument("other", "合成文書B.pdf", { incoming_count: 2 })],
        edges: [{ id: "doc:other", source: "doc", target: "other", count: 2 }],
      });
      await mockTree(page, { "root:set-doc": [sectionA, sectionB, ...extras], "section-a": [table, article] }, {
        "set-doc": [{ source_set: "set-doc", target_set: "set-other", source_chain: ["section-a", "article"], target_chain: [], count: 2 }],
      });
      await page.route("**/api/knowledge-bases/kb-1/graph/search**", (route) => {
        const q = new URL(route.request().url()).searchParams.get("q");
        return route.fulfill({ json: ok(q === "合成章 B"
          ? { items: [{ id: "section-b", name: "合成章 B", type: "section", document_id: "doc-doc", document_name: "doc.pdf", chunk_set_id: "set-doc" }], total: 1, offset: 0 }
          : { items: [], total: 0, offset: 0 }) });
      });
      await page.route(/\/graph\/documents\/[^/]+\/locate/, (route) => route.fulfill({ json: ok({ path: [{ id: "section-b", index: 1 }] }) }));
      await page.goto("/knowledge-bases/kb-1");
      await disclosureSummary(page, "文書構造グラフを表示").click();
      const graph = page.locator("#knowledge-base-graph");
      const nodeOf = (id: string) => graph.locator(`.react-flow__node[data-id="${id}"]`);
      const documentNode = nodeOf("doc");
      const sectionNode = nodeOf("section-a");
      const edgeTexts = graph.locator(".react-flow__edge-text");
      await expect(documentNode).toBeVisible();
      await expect(nodeOf("other")).toBeVisible();
      await expect(sectionNode).toHaveCount(0);
      await expect(edgeTexts).toHaveText("参照 2 件");
      // 文書を開くと章が出て、参照の辺は見えている章へ移り、件数を保つ。
      await documentNode.focus();
      await page.keyboard.press("Enter");
      await expect(sectionNode).toBeVisible();
      await expect(graph.getByRole("region", { name: "選択したノード" })).toContainText("合成文書A.pdf");
      await expect.poll(async () => (await documentNode.boundingBox())!.x < (await sectionNode.boundingBox())!.x).toBe(true);
      await expect(graph.locator('.react-flow__edge[data-id="section-a:other"]')).toHaveCount(1);
      await expect(edgeTexts).toHaveText("参照 2 件");
      // 下の段を開くと、辺はさらに下の段へ移る。実体の無い段は参照の一覧を出さない。
      await sectionNode.focus();
      await page.keyboard.press("Enter");
      await expect(nodeOf("article")).toBeVisible();
      await expect(graph.locator('.react-flow__edge[data-id="article:other"]')).toHaveCount(1);
      await nodeOf("article").focus();
      await page.keyboard.press("Enter");
      await expect(graph.getByText("この段に本文はありません。下の段を開いてください。", { exact: true })).toBeVisible();
      // 閉じると辺は文書へ戻る。
      await documentNode.focus();
      await page.keyboard.press("Enter");
      await expect(sectionNode).toHaveCount(0);
      await expect(graph.locator('.react-flow__edge[data-id="doc:other"]')).toHaveCount(1);
      await documentNode.focus();
      await page.keyboard.press("Enter");
      await expect(sectionNode).toBeVisible();
      const search = graph.getByRole("searchbox", { name: "グラフ内を検索", exact: true });
      await expect(search).toHaveAccessibleDescription("このナレッジベース全体の文書名と章節名を検索します。");
      await search.fill("合成章 B");
      await expect(graph.getByRole("region", { name: "選択したノード" })).toContainText("合成章 B");
      await expect(nodeOf("section-b")).toBeInViewport();
      await graph.getByRole("button", { name: "検索語をクリア", exact: true }).click();
      const edge = graph.locator('.react-flow__edge[data-id="contains:section-b"]');
      await edge.focus();
      await page.keyboard.press("Space");
      await expect(graph.getByRole("region", { name: "選択した関係" })).toContainText("合成文書A.pdf");
      await expect(graph.getByRole("region", { name: "選択した関係" }).locator(".font-mono")).toHaveText("contains:section-b");
      const kinds = graph.getByRole("combobox", { name: "種類", exact: true });
      await kinds.click();
      await page.getByRole("option", { name: "章・節", exact: true }).click();
      await expect(documentNode).toHaveCount(0);
      await expect(sectionNode).toBeVisible();
      await kinds.click();
      await page.getByRole("option", { name: "すべての種類", exact: true }).click();
      const sectionLegend = graph.getByRole("group", { name: "表示する種類" }).getByRole("button", { name: "章・節", exact: true });
      await sectionLegend.click();
      await expect(sectionLegend).toHaveAttribute("aria-pressed", "false");
      await expect(sectionNode).toHaveCount(0);
      await sectionLegend.click();
      await expect(sectionNode).toBeVisible();
      await expectNoPageOverflow(page);
      // 固定headerと長い親領域を避け、表示中の画布を確認してから撮る。
      const canvas = graph.getByRole("region", { name: "文書構造グラフ", exact: true });
      await page.setViewportSize({ width, height: 1200 });
      await canvas.evaluate(element => element.scrollIntoView({ block: "center" }));
      await expect(canvas.locator(".react-flow__node").first()).toBeVisible();
      await canvas.getByRole("button", { name: "全体表示", exact: true }).click();
      await canvas.screenshot({ path: testInfo.outputPath(`shared-structure-graph-${width}-${theme}.png`) });
    });
  }
}

test("明示参照の方向を分け、未表示の参照先を取得して位置を示す", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const sourceDocument = structureDocument("a".repeat(64), "合成文書A.pdf", { has_sections: false });
  const targetDocument = structureDocument("c".repeat(64), "合成文書B.pdf");
  const source = { id: sourceDocument.id, name: "合成文書A.pdf", type: "document", document_name: "合成文書A.pdf", document_id: sourceDocument.document_id, chunk_set_id: sourceDocument.chunk_set_id, section_path: null };
  const target = { id: "b".repeat(64), name: "合成章 B", type: "section", document_name: "合成文書B.pdf", document_id: targetDocument.document_id, chunk_set_id: targetDocument.chunk_set_id, section_path: "合成章 B" };
  await mockKb(page, { status: "ok", documents: [sourceDocument, targetDocument], edges: [{ id: "a:c", source: sourceDocument.id, target: targetDocument.id, count: 1 }] });
  await mockTree(page, { [`root:${targetDocument.chunk_set_id}`]: [treeNode(target.id, "合成章 B", null, targetDocument.id)] }, {
    [targetDocument.chunk_set_id]: [{ source_set: sourceDocument.chunk_set_id, target_set: targetDocument.chunk_set_id, source_chain: [], target_chain: [target.id], count: 1 }],
  });
  await page.route(/\/graph\/documents\/[^/]+\/locate/, (route) => route.fulfill({ json: ok({ path: [{ id: target.id, index: 0 }] }) }));
  const directions: string[] = [];
  await page.route("**/api/knowledge-bases/kb-1/graph/references**", (route) => {
    const url = new URL(route.request().url());
    const direction = url.searchParams.get("direction") ?? "outgoing";
    directions.push(direction);
    const isTarget = url.searchParams.get("node_id") === target.id;
    return route.fulfill({ json: ok({ items: isTarget && direction === "outgoing" ? [] : [{ id: "ref-a", label: "合成章 Bを参照", status: "resolved", source, target }, { id: "ref-b", label: "同名の参照", status: "ambiguous", source, target: null }], offset: 0, has_more: false, direction }) });
  });
  await page.goto("/knowledge-bases/kb-1");
  await disclosureSummary(page, "文書構造グラフを表示").click();
  const node = page.locator(`.react-flow__node[data-id="${source.id}"]`);
  await node.focus(); await page.keyboard.press("Enter");
  const references = page.getByTestId("graph-references");
  await expect(references.getByText("同名の候補が複数あります", { exact: true })).toBeVisible();
  await expect(references.getByRole("button", { name: "図で位置を確認" })).toHaveCount(1);
  await references.getByRole("tab", { name: "参照元", exact: true }).click();
  await expect.poll(() => directions.includes("incoming")).toBe(true);
  await references.getByRole("tab", { name: "参照先", exact: true }).click();
  await references.screenshot({ path: testInfo.outputPath("reference-directions.png") });
  await references.getByRole("button", { name: "図で位置を確認" }).click();
  const destination = page.locator(`.react-flow__node[data-id="${target.id}"]`);
  await expect(destination).toBeVisible();
  await expect(destination).toBeInViewport();
  await expect(destination).toHaveClass(/selected/);
  await expect(page.locator(`.react-flow__edge[data-id="${sourceDocument.id}:${target.id}"]`)).toHaveCount(1);
  await expect(page.locator(`.react-flow__edge[data-id="contains:${target.id}"]`)).toHaveCount(1);
  await expect(page.locator(".react-flow__edge-text")).toHaveText("参照 1 件");
  await expectNoPageOverflow(page);
  for (const theme of ["light", "dark"]) {
    await page.evaluate(value => {document.documentElement.dataset.theme=value;}, theme);
    await page.screenshot({ path: testInfo.outputPath(`reference-navigation-${theme}.png`), fullPage: true });
  }
});

test("1 段の 100 件を超える章節は続きを読み、読み込みの失敗は再試行できる", { tag: "@responsive" }, async ({ page }) => {
  const document = structureDocument("doc", "合成文書A.pdf");
  await mockKb(page, { status: "ok", documents: [document], edges: [] });
  const sections = Array.from({ length: 130 }, (_, index) => treeNode(`section-${index}`, `合成節 ${index}`, null, "doc"));
  await mockTree(page, { "root:set-doc": sections });
  let failed = false;
  const offsets: number[] = [];
  await page.route(/\/graph\/documents\/[^/]+\/tree/, (route) => {
    offsets.push(Number(new URL(route.request().url()).searchParams.get("offset")));
    if (failed) return route.fallback();
    failed = true;
    return route.fulfill({ status: 503, json: { error_messages: ["一時的に取得できません。"] } });
  });
  await page.goto("/knowledge-bases/kb-1");
  await disclosureSummary(page, "文書構造グラフを表示").click();
  const graph = page.locator("#knowledge-base-graph");
  const nodes = graph.locator(".react-flow__node");
  await graph.locator('.react-flow__node[data-id="doc"]').focus();
  await page.keyboard.press("Enter");
  await expect(graph.getByRole("alert")).toContainText("一時的に取得できません。");
  await graph.getByRole("button", { name: "再試行", exact: true }).click();
  await expect(nodes).toHaveCount(102);
  const more = graph.locator('.react-flow__node[data-id="more:doc"]');
  await expect(more).toHaveAttribute("aria-label", "さらに 30 件を表示");
  await more.focus();
  await page.keyboard.press("Enter");
  await expect(nodes).toHaveCount(131);
  await expect(more).toHaveCount(0);
  expect(offsets).toEqual([0, 0, 100]);
  await expectNoPageOverflow(page);
});

test("構造索引の現在状態と今回の結果を分け、版変更後に再確認する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockKb(page, EMPTY_OVERVIEW);
  let changed = false;
  await page.route("**/api/knowledge-bases/kb-1/structure-index/job**", (route) => route.fulfill({ json: ok({
    job_id: "synthetic-state-job", status: "COMPLETED", started_at: "2026-01-01T00:00:00Z", finished_at: "2026-01-01T00:00:01Z",
    items: [
      { document_id: "synthetic-skip", name: "synthetic-skip.txt", status: "SKIPPED", reason: "current", index_status: changed ? "STALE" : "CURRENT", index_quality: "COMPLETE" },
      { document_id: "synthetic-fail", name: "synthetic-fail.txt", status: "FAILED", reason: "processing_error", index_status: "CURRENT", index_quality: "COMPLETE" },
      { document_id: "synthetic-old", name: "synthetic-old.txt", status: "SUCCEEDED", index_status: "STALE" },
      { document_id: "synthetic-active", name: "synthetic-active.txt", status: "RUNNING", index_status: "MISSING" },
      { document_id: "synthetic-unready", name: "synthetic-unready.txt", status: "UNAVAILABLE", reason: "no_chunks", index_status: "UNAVAILABLE", index_reason: "no_chunks" },
      { document_id: "synthetic-pending", name: "synthetic-pending.txt", status: "QUEUED", index_status: "MISSING" },
    ], counts: { SKIPPED: 1, FAILED: 1, SUCCEEDED: 1, RUNNING: 1, UNAVAILABLE: 1, QUEUED: 1 }, total: 6, offset: 0, limit: 25,
  }) }));
  await page.goto("/knowledge-bases/kb-1");
  const build = page.getByTestId("knowledge-base-structure-build");
  const row = (name: string) => build.getByRole("row").filter({ has: page.getByRole("cell", { name, exact: true }) });
  await expect(row("synthetic-skip.txt").getByRole("cell").nth(1)).toHaveText("構築済み");
  await expect(row("synthetic-skip.txt").getByRole("cell").nth(2)).toContainText("スキップ：処理対象の版は構築済みでした。");
  await expect(row("synthetic-fail.txt").getByRole("cell").nth(1)).toHaveText("構築済み");
  await expect(row("synthetic-fail.txt").getByRole("cell").nth(2)).toContainText("失敗：");
  await expect(row("synthetic-old.txt").getByRole("cell").nth(1)).toHaveText("更新が必要");
  await expect(row("synthetic-old.txt").getByRole("cell").nth(2)).toHaveText("成功");
  await expect(row("synthetic-active.txt").getByRole("cell").nth(1)).toHaveText("未構築");
  await expect(row("synthetic-active.txt").getByRole("cell").nth(2)).toHaveText("構築中");
  await expect(row("synthetic-unready.txt").getByRole("cell").nth(1)).toHaveText("準備不足");
  await expect(row("synthetic-pending.txt").getByRole("cell").nth(2)).toHaveText("待機中");
  await build.scrollIntoViewIfNeeded();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("current-index-and-operation.png") });
  if (testInfo.project.name === "mobile") {
    await row("synthetic-skip.txt").getByRole("cell").nth(2).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("current-index-operation-details.png") });
  }
  changed = true;
  await page.reload();
  await expect(row("synthetic-skip.txt").getByRole("cell").nth(1)).toHaveText("更新が必要");
  await expect(row("synthetic-skip.txt").getByRole("cell").nth(2)).toContainText("スキップ：");
});

test("参照一覧の取得失敗を再試行でき、次のページだけを読む", { tag: "@responsive" }, async ({ page }) => {
  const document = structureDocument("a".repeat(64), "合成文書A.pdf", { has_sections: false });
  const source = { id: document.id, name: "合成文書A.pdf", type: "document", document_name: "合成文書A.pdf", document_id: document.document_id, chunk_set_id: document.chunk_set_id, section_path: null };
  await mockKb(page, { status: "ok", documents: [document], edges: [] });
  let attempts = 0;
  await page.route("**/api/knowledge-bases/kb-1/graph/references**", (route) => {
    attempts += 1;
    if (attempts === 1) return route.fulfill({ status: 503, json: { error_messages: ["一時的に取得できません。"] } });
    const offset = Number(new URL(route.request().url()).searchParams.get("offset"));
    return route.fulfill({ json: ok({ items: [{ id: `ref-${offset}`, label: offset ? "後続の参照" : "先頭の参照", status: "ambiguous", source, target: null }], offset, has_more: offset === 0, direction: "outgoing" }) });
  });
  await page.goto("/knowledge-bases/kb-1");
  await disclosureSummary(page, "文書構造グラフを表示").click();
  const node = page.locator(`.react-flow__node[data-id="${source.id}"]`);
  await node.focus(); await page.keyboard.press("Enter");
  const panel = page.getByTestId("graph-references");
  await expect(panel.getByRole("alert")).toContainText("一時的に取得できません。");
  await panel.getByRole("button", { name: "再試行", exact: true }).click();
  await expect(panel.getByText("先頭の参照", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "次へ", exact: true }).click();
  await expect(panel.getByText("後続の参照", { exact: true })).toBeVisible();
  await expect(panel.getByText("先頭の参照", { exact: true })).toHaveCount(0);
  await expectNoPageOverflow(page);
});
