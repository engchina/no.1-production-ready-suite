import { expect, test, type Locator, type Page, type TestInfo } from "./fixtures/test";
import {
  apiEnvelope as ok,
  dbUser,
  expectNoPageOverflow,
  mockLocalAuth,
  selectSearchAnswerProfile,
} from "./_helpers";

// Wiki の図（Cytoscape の共有画布。#5922）。画布の中は canvas なので、描いた数・縮尺・全体が収まっているか・
// 辺の文字の出し方は画布の属性（data-graph-nodes / data-graph-edges / data-graph-zoom / data-graph-contained /
// data-graph-edge-labels）で、操作はキーボードと一覧・詳細で確かめる。

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
  field_extraction_enabled: null,
  vision_enabled: null,
  navigation_summary_enabled: null,
  auto_parse_after_preprocess_enabled: null,
  auto_chunk_after_extract_enabled: null,
  auto_index_after_chunk_enabled: null,
};
const kbDetail = {
  id: "kb-1",
  name: "合成のナレッジベース",
  description: "合成の説明",
  status: "ACTIVE",
  default_search_mode: "hybrid",
  document_count: 30,
  indexed_document_count: 30,
  error_document_count: 0,
  searchable_chunk_count: 10,
  created_at: "2026-06-15T00:00:00Z",
  updated_at: "2026-06-15T00:00:00Z",
  archived_at: null,
  retrieval_config: {},
  adapter_config: { version: 1, ingestion: nullIngestion, query: { guardrail_policy: null } },
};

const files = Array.from({ length: 30 }, (_, i) => ({
  id: "doc-" + i,
  document_id: "doc-" + i,
  name: "合成資料 " + String(i + 1).padStart(2, "0") + (i === 1 ? "_" + "Reference".repeat(8) : "") + ".md",
}));
const kbs = [
  { id: "kb-1", name: "ナレッジベース A" },
  { id: "kb-2", name: "ナレッジベース B" },
];
const profiles = ["A", "B"].map((n, i) => ({
  id: "p" + i,
  name: "合成プロファイル " + n,
  description: "",
  status: "ACTIVE",
  knowledge_base_count: 2,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}));
const UNITS_PER_DOC = 40;
const hash = (n: number) => n.toString(16).padStart(64, "0");
function unit(index: number) {
  const file = files[Math.floor(index / UNITS_PER_DOC) % files.length];
  return {
    id: hash(index + 1),
    kind: "unit",
    label: "合成の原文 " + String(index + 1),
    document_id: file.id,
    document_name: file.name,
    path: ["第 1 章"],
    knowledge_base_ids: [kbs[Math.floor(index / UNITS_PER_DOC) % 2].id],
  };
}
const hitCount = 60;
// 原文 1 は 30 件の関係を持つ（25 件ずつ展開し、残りは「さらに展開」）。
const related = Array.from({ length: 30 }, (_, i) => ({
  unit: unit(i + 1),
  direction: i % 2 ? "outgoing" : "incoming",
  scope: i < 20 ? "same_document" : "cross_document",
  relation: i < 2 ? "exception" : "reference",
}));
// 文書の間の参照（文書 0 を中心に 1〜5、2→3）。参照の多い順に文書 0 が先頭。
const docLinks = [
  ...[1, 2, 3, 4, 5].map((n) => ({ source_id: "doc-0", target_id: "doc-" + n, relation: "reference", count: 2 })),
  { source_id: "doc-2", target_id: "doc-3", relation: "prerequisite", count: 1 },
];
// 密な図の合成（配備した環境で測った密度。#5953）: 25 文書・関係 448 本・相異なる文書の組 283・1 組あたり最大 5 本・
// 種類は参照 409／例外 17／前提 12／続く手順 10。
function denseDocLinks() {
  const pairs: [number, number][] = [];
  for (let i = 0; i < 25; i++) for (let j = i + 1; j < 25; j++) pairs.push([i, j]);
  const links: { source_id: string; target_id: string; relation: string; count: number }[] = [];
  // 300 組のうち 283 組を使い、種類の多い組が 1 つの文書に偏らないように散らす。
  pairs.filter((_, index) => index % 18 !== 17 || index > 17 * 18).slice(0, 283).forEach(([i, j], index) => {
    const a = "doc-" + i, b = "doc-" + j;
    const spread = (index * 7) % 283;
    links.push({ source_id: a, target_id: b, relation: "reference", count: 1 + (index % 4) });
    if (spread < 126) links.push({ source_id: b, target_id: a, relation: "reference", count: 1 });
    if (spread < 17) links.push({ source_id: a, target_id: b, relation: "exception", count: 1 });
    if (spread < 12) links.push({ source_id: a, target_id: b, relation: "prerequisite", count: 1 });
    if (spread < 10) links.push({ source_id: a, target_id: b, relation: "continuation", count: 1 });
  });
  return links;
}
function paged<T>(items: T[]) {
  return { items, total: items.length, offset: 0, limit: 50, has_next: false };
}
type Controls = { failRelations?: boolean; failSample?: boolean; dense?: boolean; calls: URL[] };
async function data(page: Page, controls: Controls = { calls: [] }) {
  const links = controls.dense ? denseDocLinks() : docLinks;
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (r) => r.fulfill({ json: ok(paged(profiles)) }));
  await page.route("**/api/chat/conversations**", (r) => r.fulfill({ json: ok(paged([])) }));
  await page.route("**/api/chat/models", (r) => r.fulfill({ json: ok([]) }));
  await page.route("**/api/documents**", (r) => r.fulfill({ json: ok(paged([])) }));
  await page.route("**/api/knowledge-bases**", (r) => r.fulfill({ json: ok(paged([kbDetail])) }));
  await page.route("**/api/knowledge-bases/kb-1", (r) => r.fulfill({ json: ok(kbDetail) }));
  await page.route("**/api/knowledge-bases/kb-1/extraction-fields", (r) =>
    r.fulfill({ json: ok({ inherits_default: true, fields: [] }) }),
  );
  await page.route(/\/api\/knowledge-bases\/kb-1\/wiki(?:\?.*)?$/, (r) =>
    r.fulfill({ json: ok({ enabled: false, counts: { READY: files.length }, blocked_reason: null, documents: paged([]) }) }),
  );
  await page.route(/\/api\/(?:wiki-search\/profiles\/[^/]+|knowledge-bases\/[^/]+\/wiki\/explore)\//, (r) => {
    const url = new URL(r.request().url());
    controls.calls.push(url);
    const parts = url.pathname.split("/");
    const end = parts.at(-1);
    const q = url.searchParams;
    const offset = Number(q.get("offset") ?? 0);
    const limit = Number(q.get("limit") ?? 25);
    const slice = <T>(items: T[]) => ({ items: items.slice(offset, offset + limit), total: items.length, offset });
    const finish = (v: unknown) => r.fulfill({ json: ok(v) });
    if (end === "scope") return finish({ knowledge_bases: kbs });
    if (end === "documents") return finish({ documents: files.slice(offset, offset + limit), status: "ok", has_more: offset + limit < files.length });
    if (end === "document-count") return finish({ total: files.length });
    if (end === "document-graph") {
      if (controls.failSample) return r.fulfill({ status: 503, json: { detail: "一時的な失敗" } });
      const degree = (id: string) => links.reduce((sum, l) => sum + (l.source_id === id || l.target_id === id ? l.count : 0), 0);
      const doc = q.get("document");
      const pool = doc
        ? files.filter((f) => links.some((l) => (l.source_id === doc && l.target_id === f.id) || (l.target_id === doc && l.source_id === f.id)))
        : [...files].sort((a, b) => degree(b.id) - degree(a.id) || a.name.localeCompare(b.name));
      const shown = pool.slice(offset, offset + limit);
      const ids = new Set([...shown.map((f) => f.id), ...(doc ? [doc] : [])]);
      return finish({
        documents: shown.map((f) => ({ ...f, reference_count: degree(f.id) })),
        links: links.filter((l) => ids.has(l.source_id) && ids.has(l.target_id)),
        total: pool.length, offset, has_more: offset + limit < pool.length,
      });
    }
    if (end === "search") return finish(slice(Array.from({ length: hitCount }, (_, i) => unit(i)).filter((u) => !q.has("kb") || u.knowledge_base_ids.includes(q.get("kb")!))));
    if (end === "connections") {
      const ids = q.get("ids")!.split(",");
      return finish({ connections: ids.includes(hash(1)) && ids.includes(hash(2)) ? [{ source_id: hash(1), target_id: hash(2), relation: "exception", scope: "same_document" }] : [] });
    }
    if (end === "locate") {
      const node = q.get("node")!;
      const index = parseInt(node, 16) - 1;
      return finish({ path: [{ id: "sec-" + Math.floor(index / UNITS_PER_DOC), index: 0 }, { id: node, index: index % UNITS_PER_DOC }] });
    }
    if (end === "tree") {
      const n = Number(parts.at(-2)!.split("-")[1]);
      const file = files[n];
      const children: unknown[] = q.has("parent")
        ? Array.from({ length: UNITS_PER_DOC }, (_, i) => unit(n * UNITS_PER_DOC + i))
        : [{ id: "sec-" + n, label: "第 1 章", kind: "section", document_id: file.id, document_name: file.name, path: ["第 1 章"], child_count: UNITS_PER_DOC, unit_count: UNITS_PER_DOC }];
      return finish(slice(children));
    }
    if (end === "relations") {
      if (controls.failRelations) return r.fulfill({ status: 503, json: { detail: "一時的な失敗" } });
      const all = parts.at(-2) === hash(1) ? related : [];
      return finish({ ...slice(all), counts: {}, preview: [], has_more: all.length > offset + limit });
    }
    const index = parseInt(end!, 16) - 1;
    const hit = unit(index);
    return finish({
      ...hit,
      parent_id: "sec-" + Math.floor(index / UNITS_PER_DOC),
      source: { unit_id: hit.id, source_hash: hash(index + 10000) },
      chunks: [{ document_id: hit.document_id, chunk_id: "c-" + index, file_name: hit.document_name, text: "照合済みの合成原文 " + (index + 1) + "。", score: 1, metadata: { page_start: (index % UNITS_PER_DOC) + 1 } }],
      context: "公開済みの合成の案内。",
      possible_questions: [],
      unresolved: [],
    });
  });
  return controls;
}

const canvasOf = (page: Page) => page.getByTestId("wiki-graph-canvas");
const graphCount = (canvas: Locator, what: "nodes" | "edges") => canvas.evaluate((el, key) => Number((el as HTMLElement).dataset[key] ?? 0), what === "nodes" ? "graphNodes" : "graphEdges");
const graphZoom = (canvas: Locator) => canvas.evaluate((el) => Number((el as HTMLElement).dataset.graphZoom ?? 0));
/** 図の全体（名前を含む）が画布に収まっているか。 */
const graphContained = (canvas: Locator) => canvas.evaluate((el) => (el as HTMLElement).dataset.graphContained);
/** 辺の文字の出し方（all = すべて / focus = 選んだ・ホバーした節点の辺だけ）。 */
const edgeLabels = (canvas: Locator) => canvas.evaluate((el) => (el as HTMLElement).dataset.graphEdgeLabels);
/** 同じノードの組に複数あって、文字を曲線に沿って回している辺の数（#5959）。 */
const parallelLabels = (canvas: Locator) => canvas.evaluate((el) => Number((el as HTMLElement).dataset.graphParallelLabels ?? -1));
const nodeList = (page: Page) => page.getByRole("region", { name: "図にあるノードの一覧" });
function detail(page: Page, phone: boolean) {
  return phone ? page.getByRole("dialog", { name: "詳細" }) : page.getByRole("complementary", { name: "詳細" });
}
async function shot(page: Page, info: TestInfo, name: string) {
  if (process.env.WIKI_GRAPH_SHOTS) await page.screenshot({ path: process.env.WIKI_GRAPH_SHOTS + "/" + info.project.name + "-" + name + ".png" });
}
async function begin(page: Page) {
  await page.goto("/wiki-search");
  await selectSearchAnswerProfile(page, profiles[0].name);
  await page.locator("#wiki-question").fill("資料の確認方法");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect(page.getByRole("heading", { name: "検索結果と関係" })).toBeVisible();
}
async function openKnowledgeBaseGraph(page: Page) {
  await page.goto("/knowledge-bases/kb-1");
  const opener = page.getByTestId("wiki-graph-disclosure").locator("summary");
  await opener.focus();
  await opener.press("Enter");
  return page.getByTestId("wiki-graph-disclosure");
}

test("Wiki 検索は結果で図を置き換え、結果の間の関係をつなぎ、一覧は図のノード、検索結果は 25 件ずつ読む", { tag: "@responsive" }, async ({ page }, info) => {
  const phone = info.project.name === "mobile";
  const control = await data(page);
  await begin(page);
  const canvas = canvasOf(page);
  await expect.poll(() => graphCount(canvas, "nodes")).toBe(25);
  // 結果の原文 1 と 2 の間の関係（connections）を線にする。
  await expect.poll(() => graphCount(canvas, "edges")).toBe(1);
  expect(control.calls.some((u) => u.pathname.endsWith("/connections"))).toBe(true);
  await expect(page.getByTestId("wiki-graph-node-kinds")).toContainText("原文 25");
  // 開いた直後と全体表示で、図の全体が画布に収まる（#5953。手で縮める下限まで縮めてよい）。
  await expect.poll(() => graphContained(canvas)).toBe("true");
  await page.getByRole("button", { name: "拡大", exact: true }).click();
  await page.getByRole("button", { name: "拡大", exact: true }).click();
  await page.getByRole("button", { name: "全体表示", exact: true }).click();
  await expect.poll(() => graphContained(canvas)).toBe("true");
  expect(await graphZoom(canvas)).toBeGreaterThanOrEqual(0.1);
  await shot(page, info, "wiki-search-graph");
  // 関係図・一覧・検索結果は Tabs（押下のボタンではない）。「一覧」はいま図にあるノード（#5953）。
  // 狭い画面では種類と所属の文書を名前の下に出すので、名前は先頭で照合する。
  await page.getByRole("tab", { name: "一覧" }).click();
  await expect(page.getByText("全 25 件")).toBeVisible();
  await expect(nodeList(page).getByRole("button", { name: /^合成の原文 1(?!\d)/ })).toBeVisible();
  await shot(page, info, "wiki-search-list");
  // 「検索結果」はサーバー側のページングの結果（全件数・順位・ナレッジベースの名前）。
  await page.getByRole("tab", { name: "検索結果" }).click();
  await expect(page.getByText("全 60 件")).toBeVisible();
  const list = page.getByRole("region", { name: "検索結果の一覧" });
  await expect(list.getByRole("button")).toHaveCount(25);
  const pager = page.getByRole("navigation", { name: "検索結果の一覧" });
  await pager.getByRole("button", { name: "次へ", exact: true }).click();
  await expect(list.getByRole("button", { name: /合成の原文 26/ })).toBeVisible();
  // 新しいページの結果で図を置き換え、「一覧」も同じノードになる。
  await page.getByRole("tab", { name: "関係図" }).click();
  await expect.poll(() => graphCount(canvas, "nodes")).toBe(25);
  await page.getByRole("tab", { name: "一覧" }).click();
  await expect(page.getByText("全 25 件")).toBeVisible();
  await expect(nodeList(page).getByRole("button", { name: /^合成の原文 1(?!\d)/ })).toHaveCount(0);
  // 行を選ぶと、図と同じノードが選ばれて詳細が出る。
  await nodeList(page).getByRole("button", { name: /^合成の原文 27/ }).click();
  const panel = detail(page, phone);
  await expect(panel.getByText("照合済みの合成原文 27。")).toBeVisible();
  await panel.getByRole("button", { name: "質問に追加" }).click();
  if (phone) await panel.getByRole("button", { name: "選択を解除" }).click();
  await page.getByRole("button", { name: "チャットで質問" }).click();
  await expect(page.locator("#chat-composer")).toHaveValue("資料の確認方法");
  await expect(page.getByRole("list", { name: "Wiki で選んだ原文" }).getByRole("listitem")).toHaveCount(1);
  // 戻るボタンは実際の戻り先（Wiki 検索）を示す。
  await page.getByRole("button", { name: "Wiki 検索に戻る" }).click();
  await expect(page.locator("#wiki-question")).toHaveValue("資料の確認方法");
  await page.getByRole("button", { name: "新しい検索を開始" }).click();
  await expect(page.locator("#wiki-question")).toHaveValue("");
  await expectNoPageOverflow(page);
});

test("ナレッジベース詳細は参照の多い文書の見本で開き、キーボードで展開し、図内検索の当たりを足してクリアできる", { tag: "@responsive" }, async ({ page }, info) => {
  const phone = info.project.name === "mobile";
  const control = await data(page, { calls: [], failSample: true });
  const area = await openKnowledgeBaseGraph(page);
  await area.getByRole("button", { name: /再試行|再読み込み/ }).first().click().catch(() => undefined);
  control.failSample = false;
  await area.getByRole("button", { name: /再試行|再読み込み/ }).first().click();
  const canvas = canvasOf(page);
  // 30 文書のうち参照の多い 25 文書と、その間の参照 6 本。
  await expect.poll(() => graphCount(canvas, "nodes")).toBe(25);
  await expect.poll(() => graphCount(canvas, "edges")).toBe(docLinks.length);
  await expect(area.getByTestId("wiki-graph-node-kinds")).toContainText("文書 25");
  await expect(area.getByText(/参照の多い 25 件の文書/)).toBeVisible();
  // 開いた直後に図の全体が画布に収まり（desktop・375px。#5953）、辺の少ない図はすべての辺に種類の文字を出す。
  await expect.poll(() => graphContained(canvas)).toBe("true");
  expect(await edgeLabels(canvas)).toBe("all");
  // 同じ組に 1 本ずつの辺は、文字を回さない（水平のまま）。
  expect(await parallelLabels(canvas)).toBe(0);
  await shot(page, info, "kb-sample");
  // Tab で画布に入り、矢印キーで選び、Enter で隣接（構成と参照の相手）を展開する。
  await canvas.focus();
  await page.keyboard.press("ArrowRight");
  const before = await graphCount(canvas, "nodes");
  await page.keyboard.press("Enter");
  await expect.poll(() => graphCount(canvas, "nodes")).toBeGreaterThan(before);
  // 右クリック（Shift+F10）のメニュー。
  await page.keyboard.press("Shift+F10");
  const menu = page.getByRole("menu", { name: "ノードの操作" });
  await expect(menu).toBeVisible();
  for (const name of ["関係の種類を選んで展開", "このノードを外す", "選択したノードだけ残す"]) {
    await expect(menu.getByRole("menuitem", { name: new RegExp(name) })).toBeVisible();
  }
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  // 凡例を押すとその種類のノードをすべて選ぶ。
  await area.getByTestId("wiki-graph-kind-document").click();
  await expect(area.getByTestId("wiki-graph-selected-count")).toContainText("件を選択");
  // 図内検索はナレッジベース全体をサーバーで検索し、当たった原文を今の図に足して強調する。
  const nodesBefore = await graphCount(canvas, "nodes");
  // 図を拡大して全体が収まっていない状態から検索する。
  for (let i = 0; i < 3; i++) await area.getByRole("button", { name: "拡大", exact: true }).click();
  await expect.poll(() => graphContained(canvas)).toBe("false");
  await page.locator("#wiki-graph-find").fill("合成");
  await expect.poll(() => control.calls.some((u) => u.pathname.endsWith("/search") && u.searchParams.get("method") === "keyword")).toBe(true);
  await expect.poll(() => graphCount(canvas, "nodes")).toBeGreaterThanOrEqual(nodesBefore + 25);
  await expect(page.getByTestId("wiki-graph-find-nav")).toContainText("1 / 25");
  // 当たりと図にある原文の間の関係は、1 回の照会で引く（#5953。Neo4j Browser の「Connect result nodes」）。
  const connectionCalls = control.calls.filter((u) => u.pathname.endsWith("/connections"));
  expect(connectionCalls).toHaveLength(1);
  expect(connectionCalls[0].searchParams.get("ids")!.split(",")).toEqual(expect.arrayContaining([hash(1), hash(2), hash(25)]));
  // 当たった原文までの段（文書 → 章節）を位置合わせで開く。
  await expect.poll(() => control.calls.some((u) => u.pathname.endsWith("/locate"))).toBe(true);
  // 足した当たり（と開いた段）は画面の外に出ない（図の全体を収め直す。その前に図を動かしていても）。
  await expect.poll(() => graphContained(canvas)).toBe("true");
  const panel = detail(page, phone);
  if (phone) await page.getByRole("button", { name: "詳細を開く" }).click();
  await expect(panel.getByText("照合済みの合成原文 1。")).toBeVisible();
  if (phone) await panel.getByRole("button", { name: "選択を解除" }).click();
  await shot(page, info, "kb-find");
  // 「一覧」はいま図にあるノード（文書の見本・展開で足したノード・当たりの原文）で、種類・所属の文書・関係の数を出す。
  await page.getByRole("tab", { name: "一覧" }).click();
  const firstHit = nodeList(page).getByRole("row", { name: /合成の原文 1(?!\d)/ });
  await expect(firstHit).toBeVisible();
  // 原文 1 は、章節からの構成と、原文 2 との関係（connections）の 2 本。
  await expect(firstHit.getByRole("cell").last()).toHaveText("2");
  await expect(firstHit).toContainText(files[0].name);
  await shot(page, info, "kb-list");
  await expectNoPageOverflow(page);
  await page.getByRole("tab", { name: "関係図" }).click();
  await area.getByRole("button", { name: "画面をクリア" }).click();
  await expect.poll(() => graphCount(canvas, "nodes")).toBe(0);
  await expect(area.getByText(/図は空です/)).toBeVisible();
  // 図を空にすると「一覧」も空になる（同じ集合）。
  await page.getByRole("tab", { name: "一覧" }).click();
  await expect(nodeList(page)).toHaveCount(0);
  await expect(area.getByText(/図は空です/)).toBeVisible();
  await expectNoPageOverflow(page);
});

test("関係の多い図は全体が画布に収まり、辺の文字は選んだノードの辺だけに出し、種類を絞るとその種類の辺だけになる", { tag: "@responsive" }, async ({ page }, info) => {
  await data(page, { calls: [], dense: true });
  const area = await openKnowledgeBaseGraph(page);
  const canvas = canvasOf(page);
  // 25 文書と、その間の関係 448 本（配備した環境で測った密度の合成。#5953）。
  await expect.poll(() => graphCount(canvas, "nodes")).toBe(25);
  await expect.poll(() => graphCount(canvas, "edges")).toBe(448);
  // 開いた直後に図の全体が画布に収まる（desktop・375px）。
  await expect.poll(() => graphContained(canvas)).toBe("true");
  // 文字つきの辺が多いので、何も選んでいない間は辺の文字を出さない（選んだ・ホバーしたノードの辺だけ）。
  expect(await edgeLabels(canvas)).toBe("focus");
  // 448 本は 283 組なので、同じ組に複数ある辺（文字を曲線に沿って回す辺）がある。
  expect(await parallelLabels(canvas)).toBeGreaterThan(0);
  await shot(page, info, "kb-dense");
  // ノードを 1 つ選ぶ（隣接だけがはっきりし、その辺の文字が出る。見た目は screenshot で確かめる）。
  await canvas.focus();
  await page.keyboard.press("ArrowRight");
  await expect(area.getByTestId("wiki-graph-announcement")).toContainText("を選択しました");
  await shot(page, info, "kb-dense-selected");
  await page.keyboard.press("Escape");
  // 関係の種類を「例外」だけにすると、その 17 本だけになり、すべての辺に種類の文字を出す。
  for (const kind of ["reference", "prerequisite", "continuation"]) await area.getByTestId("wiki-graph-edge-kinds-" + kind).click();
  await expect.poll(() => graphCount(canvas, "edges")).toBe(17);
  await expect.poll(() => edgeLabels(canvas)).toBe("all");
  await shot(page, info, "kb-dense-one-kind");
  // 「一覧」の関係の数は、図に出ている関係（絞り込みの前の全部）の数。
  await page.getByRole("tab", { name: "一覧" }).click();
  await expect(area.getByText("全 25 件")).toBeVisible();
  await expectNoPageOverflow(page);
});

test("原文の関係は 1 回で取り切り、25 件ずつ展開して「さらに展開」で続きを出す", { tag: "@responsive" }, async ({ page }, info) => {
  const phone = info.project.name === "mobile";
  const control = await data(page);
  await begin(page);
  await page.getByRole("tab", { name: "検索結果" }).click();
  await page.getByRole("region", { name: "検索結果の一覧" }).getByRole("button", { name: /合成の原文 1 / }).click();
  const panel = detail(page, phone);
  await panel.getByRole("tab", { name: "関係", exact: true }).click();
  // 関係の一覧は詳細の中でも共通のページング（25 件）。
  await expect(panel.getByRole("region", { name: "関係の一覧" }).getByRole("listitem")).toHaveCount(25);
  await panel.getByRole("button", { name: "関連を展開" }).click();
  const canvas = canvasOf(page);
  await expect(page.getByRole("tab", { name: "関係図" })).toHaveAttribute("aria-selected", "true");
  // 25 件の結果に、図に無い隣接を 25 件まで足し、残りは「さらに展開」の点。
  await expect.poll(() => graphCount(canvas, "nodes")).toBeGreaterThan(25);
  const relationCalls = control.calls.filter((u) => u.pathname.endsWith("/relations"));
  // 関係は 1 回の要求で取り切り、ページを繰り返して読まない（#5916）。
  expect(relationCalls.length).toBeGreaterThan(0);
  expect(relationCalls.every((u) => u.searchParams.get("limit") === "500" && u.searchParams.get("offset") === "0")).toBe(true);
  await expectNoPageOverflow(page);
});

test("関係の読み込みの失敗は詳細の同じ場所で再試行でき、Wiki 権限の無い利用者は開けない", async ({ page }, info) => {
  const control = await data(page, { calls: [], failRelations: true });
  await begin(page);
  await page.getByRole("tab", { name: "検索結果" }).click();
  await page.getByRole("region", { name: "検索結果の一覧" }).getByRole("button", { name: /合成の原文 1 / }).click();
  const panel = detail(page, info.project.name === "mobile");
  await panel.getByRole("tab", { name: "関係", exact: true }).click();
  const retry = panel.getByRole("button", { name: /再試行|再読み込み/ });
  await expect(retry).toBeVisible();
  control.failRelations = false;
  await retry.click();
  await expect(panel.getByRole("region", { name: "関係の一覧" })).toBeVisible();
  await page.route("**/api/auth/me", (r) => r.fulfill({ json: ok(dbUser({ permissions: ["menu.search"] })) }));
  await page.reload();
  await expect(page.locator("#wiki-question")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Wiki 検索" })).toHaveCount(0);
});

test("検索の条件の行の部品は同じ高さで、検索中の回転は 1 つだけ", { tag: "@responsive" }, async ({ page }) => {
  await data(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route(/\/wiki-search\/profiles\/[^/]+\/search\?/, async (r) => { await pending; return r.fallback(); });
  await page.goto("/wiki-search");
  await selectSearchAnswerProfile(page, profiles[0].name);
  await page.locator("#wiki-question").fill("資料の確認方法");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  const progress = page.getByTestId("wiki-wait-results");
  await expect(progress).toBeVisible();
  await expect(progress).toHaveAttribute("data-processing-activity-icon", "none");
  release();
  await expect(page.getByRole("button", { name: "新しい検索を開始" })).toBeVisible();
  const heights = await Promise.all(["新しい検索を開始", "検索"].map((name) => page.getByRole("button", { name, exact: true }).evaluate((el) => el.getBoundingClientRect().height)));
  expect(Math.abs(heights[0] - heights[1])).toBeLessThanOrEqual(1);
});
