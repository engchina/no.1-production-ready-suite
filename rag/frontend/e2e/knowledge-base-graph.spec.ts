import { findLoadingStandardViolations } from "../../../platform/packages/ui/e2e/loading-checks";
import { expect, type Page, type TestInfo, test } from "./fixtures/test";
import { expectedControlHeight, expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

// KB 詳細の Wiki の図（#5502）。文書構造索引のカードは KB 詳細から外し、図は Wiki ナビゲーションのカードの最下部に置く。

function ok(json: unknown) {
  return { data: json, error_messages: [], warning_messages: [] };
}

const nullIngestion = {
  preprocess_profile: null, parser_adapter_backend: null, parser_docling_enabled: null, parser_unstructured_enabled: null,
  chunking_strategy: null, chunk_size: null, chunk_overlap: null, chunk_min_chars: null, chunk_context_header_enabled: null,
  field_extraction_enabled: null, vision_enabled: null, navigation_summary_enabled: null, auto_parse_after_preprocess_enabled: null,
  auto_chunk_after_extract_enabled: null, auto_index_after_chunk_enabled: null,
};
const kbDetail = {
  id: "kb-1", name: "合成のナレッジベース", description: "合成の説明", status: "ACTIVE", default_search_mode: "hybrid",
  document_count: 2, indexed_document_count: 2, error_document_count: 0, searchable_chunk_count: 10,
  created_at: "2026-06-15T00:00:00Z", updated_at: "2026-06-15T00:00:00Z", archived_at: null, retrieval_config: {},
  adapter_config: { version: 1, ingestion: nullIngestion, query: { guardrail_policy: null } },
};

const ID = (value: string) => value.padEnd(64, "0");
const EMPTY = { status: "empty", documents: [] };
const OVERVIEW = {
  status: "ok",
  documents: [
    { id: "doc-a", document_id: "doc-a", name: "合成文書A.pdf", unit_count: 3, outgoing_links: 1, incoming_links: 0 },
    { id: "doc-b", document_id: "doc-b", name: "合成文書B.pdf", unit_count: 1, outgoing_links: 0, incoming_links: 1 },
  ],
};
const S1 = ID("a1"), UA1 = ID("aa1"), UA2 = ID("aa2"), UA3 = ID("aa3"), SB = ID("b1"), UB1 = ID("bb1");
const node = (id: string, kind: "section" | "unit", label: string, parent: string | null, documentId: string, extra: Record<string, unknown> = {}) => ({
  id, kind, label, path: kind === "section" ? [label] : ["第 1 章"], document_id: documentId, document_name: `${documentId}.pdf`, parent_id: parent,
  child_count: 0, unit_count: kind === "unit" ? 1 : 0, outgoing_links: 0, incoming_links: 0, outgoing: [], summary: "", ...extra,
});
// 文書 A: 第 1 章 > 単位 a1（a2 は a1 の例外、b1 は a1 の前提）・a2（a3 は a2 の続く手順）・a3。文書 B: 付録 > 単位 b1。
const TREES: Record<string, ReturnType<typeof node>[]> = {
  "doc-a:root": [node(S1, "section", "第 1 章", null, "doc-a", { child_count: 3, unit_count: 3, outgoing_links: 1 })],
  [`doc-a:${S1}`]: [
    node(UA1, "unit", "第 1 章", S1, "doc-a", { summary: "合成の手続の原則の案内。", outgoing_links: 1, outgoing: [
      { target: UA2, relation: "exception", scope: "same_document" },
      { target: UB1, relation: "prerequisite", scope: "cross_document" },
    ] }),
    node(UA2, "unit", "第 1 章", S1, "doc-a", { summary: "合成の手続の例外の案内。", outgoing: [{ target: UA3, relation: "continuation", scope: "same_document" }] }),
    node(UA3, "unit", "第 1 章", S1, "doc-a", { summary: "合成の手続の後続の案内。" }),
  ],
  "doc-b:root": [node(SB, "section", "付録", null, "doc-b", { child_count: 1, unit_count: 1, incoming_links: 1 })],
  [`doc-b:${SB}`]: [node(UB1, "unit", "付録", SB, "doc-b", { summary: "合成の前提の案内。", incoming_links: 1, path: ["付録"] })],
};
const LOCATIONS: Record<string, { id: string; index: number }[]> = {
  [UA3]: [{ id: S1, index: 0 }, { id: UA3, index: 2 }],
  [UB1]: [{ id: SB, index: 0 }, { id: UB1, index: 0 }],
};
const UNIT_A1 = {
  id: UA1, label: "第 1 章", path: ["第 1 章"], document_id: "doc-a", document_name: "合成文書A.pdf",
  context: "合成の手続の原則の案内。", possible_questions: ["合成の手続はいつ使えますか？"], unresolved: ["合成の期限は本文にありません。"],
  source_chunks: [{ chunk_id: "chunk-a1", chunk_index: 0 }],
  relations: [
    { direction: "outgoing", relation: "exception", scope: "same_document", unit: { id: UA2, label: "第 1 章", path: ["第 1 章"], document_id: "doc-a", document_name: "合成文書A.pdf" } },
    { direction: "outgoing", relation: "prerequisite", scope: "cross_document", reason: "合成の判定の理由。", unit: { id: UB1, label: "付録", path: ["付録"], document_id: "doc-b", document_name: "合成文書B.pdf" } },
  ],
};

interface Calls { requests: string[] }

async function mockKb(page: Page, overview: unknown): Promise<Calls> {
  const calls: Calls = { requests: [] };
  page.on("request", (request) => {
    const url = request.url();
    if (/\/api\/knowledge-bases\/kb-1\/(graph|structure-index|wiki\/graph)/.test(url)) calls.requests.push(new URL(url).pathname);
  });
  await page.route("**/api/documents**", (route) => route.fulfill({ json: ok({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) }));
  // generic を先に、具体ルートを後に登録（Playwright は後勝ち）。
  await page.route("**/api/knowledge-bases**", (route) => route.fulfill({ json: ok({ items: [kbDetail], total: 1, limit: 20, offset: 0, has_next: false }) }));
  await page.route("**/api/knowledge-bases/kb-1", (route) => route.fulfill({ json: ok(kbDetail) }));
  await page.route("**/api/knowledge-bases/kb-1/extraction-fields", (route) => route.fulfill({ json: ok({ inherits_default: true, fields: [] }) }));
  await page.route(/\/api\/knowledge-bases\/kb-1\/wiki(\?.*)?$/, (route) => route.fulfill({ json: ok({
    enabled: false, counts: { READY: 2 }, blocked_reason: null,
    documents: { items: [], total: 2, limit: 25, offset: 0, has_next: false },
  }) }));
  await page.route("**/api/knowledge-bases/kb-1/wiki/graph", (route) => route.fulfill({ json: ok(overview) }));
  await page.route(/\/api\/knowledge-bases\/kb-1\/wiki\/graph\/documents\/[^/]+\/tree/, (route) => {
    const url = new URL(route.request().url());
    const documentId = url.pathname.split("/").at(-2)!;
    const all = TREES[`${documentId}:${url.searchParams.get("parent") ?? "root"}`] ?? [];
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 100);
    return route.fulfill({ json: ok({ items: all.slice(offset, offset + limit), total: all.length, offset }) });
  });
  await page.route(/\/api\/knowledge-bases\/kb-1\/wiki\/graph\/documents\/[^/]+\/locate/, (route) => {
    const nodeId = new URL(route.request().url()).searchParams.get("node") ?? "";
    return route.fulfill({ json: ok({ path: LOCATIONS[nodeId] ?? [] }) });
  });
  await page.route("**/api/knowledge-bases/kb-1/wiki/graph/search**", (route) => {
    const q = new URL(route.request().url()).searchParams.get("q") ?? "";
    const items = q.includes("後続") ? [{ id: UA3, label: "第 1 章", document_id: "doc-a", document_name: "合成文書A.pdf" }] : [];
    return route.fulfill({ json: ok({ items, total: items.length, offset: 0 }) });
  });
  await page.route("**/api/knowledge-bases/kb-1/wiki/graph/units/**", (route) => {
    const unitId = new URL(route.request().url()).pathname.split("/").at(-1);
    const detail = unitId === UA1 ? UNIT_A1 : { ...UNIT_A1, id: unitId, context: "合成の別の案内。", relations: [], possible_questions: [], unresolved: [] };
    return route.fulfill({ json: ok(detail) });
  });
  await page.route("**/api/documents/doc-a/chunks/chunk-a1", (route) => route.fulfill({ json: ok({
    chunk_id: "chunk-a1", document_id: "doc-a", file_name: "合成文書A.pdf", text: "合成の原文の本文。", score: null, metadata: {},
  }) }));
  return calls;
}

function disclosure(page: Page) {
  return page.getByTestId("wiki-graph-disclosure");
}

/** 合成の mock の画面を ./tmp/wiki-graph/ に集めるための出力（WIKI_GRAPH_SHOTS があるときだけ）。 */
async function shot(page: Page, testInfo: TestInfo, name: string) {
  const path = process.env.WIKI_GRAPH_SHOTS ? `${process.env.WIKI_GRAPH_SHOTS}/${testInfo.project.name}-${name}.png` : testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path, fullPage: false });
}

async function openGraph(page: Page) {
  await disclosure(page).locator("summary").click();
  const graph = page.getByRole("region", { name: "Wiki の図" });
  await expect(graph).toBeVisible();
  return graph;
}

/** 図の節点を押す（React Flow の節点の ID）。 */
async function clickNode(page: Page, id: string) {
  await page.locator(`#wiki-graph .react-flow__node[data-id="${id}"]`).click();
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
});

// 開閉の領域は共有の Disclosure の card（カードの中なので sunken）で、見出しは開閉で変えない固定の名詞（#397 / #5480）。
test("Wiki の図は Wiki のカードの最下部の sunken の card で、開いたときだけ読み、キーボードで開閉する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const calls = await mockKb(page, EMPTY);
  await page.goto("/knowledge-bases/kb-1");

  // 文書構造索引のカードは無く、旧の /graph/*・/structure-index/* を呼ばない。
  await expect(page.getByText("文書構造索引の構築")).toHaveCount(0);
  const card = page.getByTestId("knowledge-base-wiki");
  const summary = card.getByTestId("wiki-graph-disclosure").locator("summary");
  await expect(summary).toBeVisible();
  await expect(summary).toContainText("Wiki の図");
  await expect(summary).toContainText("保存済みの案内の文書・章節・原文単位と、一緒に読む単位のつながり");
  const closedSummaryText = await summary.innerText();
  const surface = await disclosure(page).evaluate((element) => {
    const probe = document.createElement("div");
    probe.className = "bg-surface-sunken";
    document.body.append(probe);
    const sunken = getComputedStyle(probe).backgroundColor;
    probe.remove();
    const style = getComputedStyle(element);
    return { sunken: style.backgroundColor === sunken, border: parseFloat(style.borderTopWidth) };
  });
  expect(surface).toEqual({ sunken: true, border: 1 });
  // カードの最下部（文書ごとの構築状態の後）に置く。
  const [documentsBox, summaryBox, cardBox] = await Promise.all([
    card.getByTestId("knowledge-base-wiki-documents").boundingBox(), summary.boundingBox(), card.boundingBox(),
  ]);
  expect(documentsBox!.y + documentsBox!.height).toBeLessThan(summaryBox!.y);
  expect(summaryBox!.y + summaryBox!.height).toBeLessThan(cardBox!.y + cardBox!.height);
  expect(Math.round(summaryBox!.height)).toBeGreaterThanOrEqual(await expectedControlHeight(page, "lg"));
  await page.waitForLoadState("networkidle");
  expect(calls.requests).toEqual([]);
  await expectNoPageOverflow(page);

  // キーボード（Enter）で開閉でき、見出しの文言は開いても変わらない。空のときは構築の案内を出す。
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(disclosure(page)).toHaveAttribute("open", "");
  await expect(page.getByText("Wiki を構築すると、文書・章節・案内のつながりを図で見られます。")).toBeVisible();
  await expect(summary).toBeFocused();
  expect(await summary.innerText()).toBe(closedSummaryText);
  expect(calls.requests).toEqual(["/api/knowledge-bases/kb-1/wiki/graph"]);
  await summary.scrollIntoViewIfNeeded();
  await shot(page, testInfo, "wiki-graph-empty");
  await page.keyboard.press("Enter");
  await expect(page.locator("#wiki-graph")).toHaveCount(0);
  expect(await summary.innerText()).toBe(closedSummaryText);
});

// 読み取れないときは「まだありません」と区別して、原因と再試行を出す（#4217）。
test("Wiki の図を読めないときは空と区別して出し、再試行で取り直す", { tag: "@responsive" }, async ({ page }) => {
  await mockKb(page, EMPTY);
  let failing = true;
  await page.route("**/api/knowledge-bases/kb-1/wiki/graph", (route) => failing
    ? route.fulfill({ status: 500, json: { data: null, error_messages: ["合成の読み取りの失敗です。"], warning_messages: [] } })
    : route.fallback());
  await page.goto("/knowledge-bases/kb-1");
  await disclosure(page).locator("summary").click();
  const section = page.locator("#wiki-graph");
  await expect(section.getByRole("alert")).toContainText("合成の読み取りの失敗です。", { timeout: 15_000 });
  await expect(section.getByText("Wiki を構築すると")).toHaveCount(0);
  await expectNoPageOverflow(page);
  failing = false;
  await section.getByRole("button", { name: "再試行" }).click();
  await expect(section.getByText("Wiki を構築すると、文書・章節・案内のつながりを図で見られます。")).toBeVisible();
});

test("文書 → 章節 → 原文単位を段階的に開き、関係を矢印と種類の文字で表示中の単位の間に引く", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const calls = await mockKb(page, OVERVIEW);
  await page.goto("/knowledge-bases/kb-1");
  const graph = await openGraph(page);

  // 初めは文書だけ。文書をまたぐ関連は節点に件数で示し、線は引かない。
  await expect(graph.getByText("合成文書A", { exact: true })).toBeVisible();
  await expect(graph.getByText("他文書へ 1")).toBeVisible();
  await expect(graph.locator(".react-flow__edge")).toHaveCount(0);
  expect(calls.requests.some((path) => path.includes("/tree"))).toBe(false);

  await clickNode(page, "doc-a");
  await expect(graph.getByText("第 1 章", { exact: true })).toBeVisible();
  expect(calls.requests.filter((path) => path.includes("/tree"))).toHaveLength(1);
  await clickNode(page, S1);
  await expect(graph.getByText("合成の手続の原則の案内。")).toBeVisible();
  // 同じ文書の依存は種類の文字（例外・続く手順）で、向きは矢印（終点の marker）。文書 B は閉じているので前提の線は無い。
  const labels = graph.locator(".react-flow__edge-text");
  await expect(labels.filter({ hasText: "例外" })).toHaveCount(1);
  await expect(labels.filter({ hasText: "続く手順" })).toHaveCount(1);
  await expect(labels.filter({ hasText: "前提" })).toHaveCount(0);
  await expect(graph.locator(".react-flow__edge-path[marker-end]").first()).toBeAttached();
  // 凡例は関係の種類をアイコンと文字で示し、押すとその種類の線を隠す。
  const legend = page.getByTestId("wiki-graph-legend");
  await expect(legend.getByRole("button", { name: "例外" }).locator("svg")).toBeAttached();
  await legend.getByRole("button", { name: "例外" }).click();
  await expect(labels.filter({ hasText: "例外" })).toHaveCount(0);
  await legend.getByRole("button", { name: "例外" }).click();

  // 文書 B を開くと、文書をまたぐ関連の線も引く。
  await clickNode(page, "doc-b");
  await clickNode(page, SB);
  await expect(labels.filter({ hasText: "前提" })).toHaveCount(1);
  // 旧の文書構造索引の API は呼ばない。
  expect(calls.requests.every((path) => path.startsWith("/api/knowledge-bases/kb-1/wiki/graph"))).toBe(true);
  await expectNoPageOverflow(page);
  // 開いても今の見え方を保つ。「全体表示」で開いた節点をすべて収める。
  await graph.getByRole("button", { name: "全体表示", exact: true }).click();
  await graph.scrollIntoViewIfNeeded();
  await shot(page, testInfo, "wiki-graph-expanded");
});

test("単位を押すと案内と関係を横に出し、原文を開き、関係から図の位置へ移る", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockKb(page, OVERVIEW);
  await page.goto("/knowledge-bases/kb-1");
  const graph = await openGraph(page);
  await clickNode(page, "doc-a");
  await clickNode(page, S1);
  await clickNode(page, UA1);

  const panel = page.getByTestId("wiki-graph-unit");
  await expect(panel.getByText("AI が作った案内です。回答の根拠ではありません。")).toBeVisible();
  await expect(panel.getByText("合成の手続の原則の案内。")).toBeVisible();
  await expect(panel.getByText("合成の手続はいつ使えますか？")).toBeVisible();
  await expect(panel.getByText("合成の期限は本文にありません。")).toBeVisible();
  const relations = panel.getByRole("table", { name: "関係" });
  await expect(relations.getByText("別の文書：合成文書B.pdf")).toBeVisible();
  await expect(relations.locator('[data-relation="prerequisite"] svg')).toBeAttached();
  const loading = await findLoadingStandardViolations(page);
  expect(loading).toEqual([]);
  // 広い画面では図の横、狭い画面（375px）では図の下に出す。
  const [graphBox, panelBox] = await Promise.all([graph.boundingBox(), panel.boundingBox()]);
  if (testInfo.project.name === "desktop") expect(panelBox!.x).toBeGreaterThanOrEqual(graphBox!.x + graphBox!.width);
  else expect(panelBox!.y).toBeGreaterThanOrEqual(graphBox!.y + graphBox!.height);
  await expectNoPageOverflow(page);
  await panel.scrollIntoViewIfNeeded();
  await shot(page, testInfo, "wiki-graph-unit");

  await panel.getByRole("button", { name: "原文を開く" }).click();
  // 既存の原文の表示（根拠の要約と原文のプレビュー）で、単位の原文を開く。
  const sheet = page.getByRole("dialog", { name: "「第 1 章」の原文" });
  await expect(sheet.getByRole("region", { name: "根拠の要約" })).toContainText("合成文書A.pdf");
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);

  // 別の文書の単位の位置を確認すると、その文書の段を開いて選ぶ。
  await relations.getByRole("row").filter({ hasText: "合成文書B.pdf" }).getByRole("button", { name: "図で位置を確認" }).click();
  await expect(graph.getByText("合成の前提の案内。")).toBeVisible();
  await expect(page.getByTestId("wiki-graph-unit").getByText("合成の別の案内。")).toBeVisible();
});

test("検索欄で原文単位へ移り、段を開いて選ぶ", { tag: "@responsive" }, async ({ page }) => {
  await mockKb(page, OVERVIEW);
  await page.goto("/knowledge-bases/kb-1");
  const graph = await openGraph(page);
  await graph.page().getByRole("searchbox", { name: "図の中を検索", exact: true }).fill("後続");
  await expect(graph.getByText("合成の手続の後続の案内。")).toBeVisible();
  await expect(page.getByTestId("wiki-graph-unit")).toBeVisible();
  await expectNoPageOverflow(page);
});
