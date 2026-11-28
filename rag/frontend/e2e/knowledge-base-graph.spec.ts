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
type Controls = { failRelations?: boolean; failSample?: boolean; dense?: boolean; limit?: number; calls: URL[] };
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
    // 一度に選べる原文の上限（backend の設定。既定 10。#5981）。
    if (end === "scope") return finish({ knowledge_bases: kbs, selection_limit: controls.limit ?? 10 });
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
/**
 * 開いた直後は、節点の名前を読める縮尺（0.65 以上）で表示する。その縮尺で全体が収まらない図（狭い画面など）は
 * 中央を表示し、「全体表示」で全体を画布に収める（#5973。全体が収まる図は開いた直後から収まる。#5953）。
 */
async function expectOpensReadable(page: Page, canvas: Locator) {
  await expect.poll(() => graphZoom(canvas)).toBeGreaterThanOrEqual(0.65);
  if ((await graphContained(canvas)) === "true") return;
  await page.getByRole("button", { name: "全体表示", exact: true }).click();
  await expect.poll(() => graphContained(canvas)).toBe("true");
  expect(await graphZoom(canvas)).toBeLessThan(0.65);
}
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
const results = (page: Page) => page.getByRole("region", { name: "検索結果の一覧" });
const selectedList = (page: Page) => page.getByRole("list", { name: "Wiki で選んだ原文" });
/** 検索結果の行のチェックボックス（名前は行の名前を含む）。 */
const rowCheckbox = (page: Page, n: number) => results(page).getByRole("checkbox", { name: "合成の原文 " + n + "を選ぶ", exact: true });
const relationCalls = (control: Controls) => control.calls.filter((u) => u.pathname.endsWith("/relations"));
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

test("Wiki 検索は検索結果の一覧から始まり、選んだ原文とその次の段だけを図にする", { tag: "@responsive" }, async ({ page }, info) => {
  const phone = info.project.name === "mobile";
  const control = await data(page);
  await begin(page);
  // 検索の後は「検索結果」のタブ（先頭）を開く。サーバー側のページングの結果（全件数・順位・ナレッジベースの名前）。
  const tabs = page.getByRole("tablist", { name: "検索結果と関係" }).getByRole("tab");
  await expect(tabs).toHaveText(["検索結果", "関係図", "一覧"]);
  await expect(page.getByRole("tab", { name: "検索結果" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("全 60 件")).toBeVisible();
  const list = results(page);
  await expect(list.getByRole("button")).toHaveCount(25);
  await expect(list.getByRole("checkbox")).toHaveCount(25);
  await expect(list.getByRole("checkbox", { checked: true })).toHaveCount(0);
  await shot(page, info, "wiki-list");
  // 1 件も選んでいないときは、図の場所に案内を出す（「一覧」も同じ）。
  await page.getByRole("tab", { name: "関係図" }).click();
  await expect(page.getByText("検索結果から原文を選ぶと、選んだ原文とその関係を図に表示します。")).toBeVisible();
  await expect(canvasOf(page)).toHaveCount(0);
  await shot(page, info, "wiki-graph-empty");
  await page.getByRole("tab", { name: "一覧" }).click();
  await expect(page.getByText("検索結果から原文を選ぶと、選んだ原文とその関係を図に表示します。")).toBeVisible();
  await page.getByRole("button", { name: "検索結果を表示" }).click();
  await expect(page.getByRole("tab", { name: "検索結果" })).toHaveAttribute("aria-selected", "true");
  // 行のチェックボックスで選ぶ。ページを移っても選択は残る。
  await rowCheckbox(page, 1).check();
  await rowCheckbox(page, 2).check();
  await expect(page.getByTestId("selected-sources-count")).toHaveText("2 / 10 件");
  const pager = page.getByRole("navigation", { name: "検索結果の一覧" });
  await pager.getByRole("button", { name: "次へ", exact: true }).click();
  await expect(list.getByRole("button", { name: /合成の原文 26/ })).toBeVisible();
  await rowCheckbox(page, 26).check();
  await expect(page.getByTestId("selected-sources-count")).toHaveText("3 / 10 件");
  await expect(selectedList(page).getByRole("listitem")).toHaveCount(3);
  await pager.getByRole("button", { name: "前へ", exact: true }).click();
  await expect(rowCheckbox(page, 1)).toBeChecked();
  await expect(list.getByRole("checkbox", { checked: true })).toHaveCount(2);
  await page.getByTestId("wiki-selected").scrollIntoViewIfNeeded();
  await shot(page, info, "wiki-selected-3");
  // 検索結果のタブにいる間は、関係（relations）も結果の間の関係（connections）も照会しない（#5966）。
  expect(relationCalls(control)).toHaveLength(0);
  expect(control.calls.some((u) => u.pathname.endsWith("/connections"))).toBe(false);
  // 「関係図」は、選んだ原文 3 件（1・2・26）と、その次の段（原文 1 の関係 30 件のうち、図に無い最初の 10 件）と、
  // 残りの「さらに展開」の点 1 つ。結果のページの 25 件は図にしない。
  await page.getByRole("tab", { name: "関係図" }).click();
  const canvas = canvasOf(page);
  await expect.poll(() => graphCount(canvas, "nodes")).toBe(14);
  await expect(page.getByTestId("wiki-graph-node-kinds")).toContainText("選んだ原文 3");
  await expect(page.getByTestId("wiki-graph-node-kinds")).toContainText("原文 10");
  // 関係は、選んだ原文ごとに 1 回だけ読む（取り切る。#5916）。
  expect(relationCalls(control).map((u) => u.pathname.split("/").at(-2)).sort()).toEqual([hash(1), hash(2), hash(26)]);
  expect(relationCalls(control).every((u) => u.searchParams.get("limit") === "500" && u.searchParams.get("offset") === "0")).toBe(true);
  // 開いた直後は名前を読める縮尺で、全体表示で図の全体が画布に収まる（#5953・#5973）。
  await expect.poll(() => graphZoom(canvas)).toBeGreaterThanOrEqual(0.65);
  await canvas.scrollIntoViewIfNeeded();
  await shot(page, info, "wiki-graph");
  await expectOpensReadable(page, canvas);
  await page.getByRole("button", { name: "全体表示", exact: true }).click();
  await expect.poll(() => graphContained(canvas)).toBe("true");
  // 「一覧」は図と同じノード（「さらに展開」の点は含まない）。選んだ原文は種類で分かる。
  await page.getByRole("tab", { name: "一覧" }).click();
  await expect(page.getByText("全 13 件")).toBeVisible();
  await expect(nodeList(page).getByRole("row", { name: /合成の原文 1(?!\d)/ })).toContainText("選んだ原文");
  await shot(page, info, "wiki-node-list");
  // 次の段の節点を詳細から選択に足す（上限は同じ）。
  await nodeList(page).getByRole("button", { name: /^合成の原文 3(?!\d)/ }).click();
  const panel = detail(page, phone);
  await expect(panel.getByText("照合済みの合成原文 3。")).toBeVisible();
  await panel.getByRole("button", { name: "質問に追加" }).click();
  await expect(panel.getByRole("button", { name: "質問から外す" })).toBeVisible();
  if (phone) await panel.getByRole("button", { name: "選択を解除" }).click();
  await expect(page.getByTestId("selected-sources-count")).toHaveText("4 / 10 件");
  await expect(nodeList(page).getByRole("row", { name: /合成の原文 3(?!\d)/ })).toContainText("選んだ原文");
  // タグの × で 1 件外す。原文 1 を外すと、その次の段も図から外れる（選んだ原文でもある原文 2・3・26 は残る）。
  await selectedList(page).getByRole("button", { name: /を外す$/ }).first().click();
  await expect(page.getByTestId("selected-sources-count")).toHaveText("3 / 10 件");
  await expect(page.getByText("全 3 件")).toBeVisible();
  await page.getByRole("tab", { name: "関係図" }).click();
  await expect.poll(() => graphCount(canvas, "nodes")).toBe(3);
  // チャットへ渡す。戻るボタンは実際の戻り先（Wiki 検索）を示し、選択は残る。
  await page.getByRole("button", { name: "チャットで質問" }).click();
  await expect(page.locator("#chat-composer")).toHaveValue("資料の確認方法");
  await expect(selectedList(page).getByRole("listitem")).toHaveCount(3);
  await page.getByRole("button", { name: "Wiki 検索に戻る" }).click();
  await expect(page.locator("#wiki-question")).toHaveValue("資料の確認方法");
  await expect(page.getByTestId("selected-sources-count")).toHaveText("3 / 10 件");
  // 「すべて外す」で選択を空にすると、選んだ原文の欄が消え、図は案内に戻る。
  await page.getByRole("button", { name: "すべて外す" }).click();
  await expect(page.getByTestId("wiki-selected")).toHaveCount(0);
  await expect(results(page).getByRole("checkbox", { checked: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "関係図" }).click();
  await expect(page.getByText("検索結果から原文を選ぶと、選んだ原文とその関係を図に表示します。")).toBeVisible();
  // 質問・結果・選んだ原文をまとめて消すボタンは持たない（#5967）。
  await expect(page.getByRole("button", { name: "新しい検索を開始" })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("上限に達すると、選んでいない行のチェックボックスと詳細の「質問に追加」を押せなくして理由を出す", { tag: "@responsive" }, async ({ page }, info) => {
  const phone = info.project.name === "mobile";
  // 上限は backend が返す値（合成では 3 件）。
  const control = await data(page, { calls: [], limit: 3 });
  await begin(page);
  const list = results(page);
  for (const n of [1, 2, 3]) await rowCheckbox(page, n).check();
  await expect(page.getByTestId("selected-sources-count")).toHaveText("3 / 3 件");
  // 選んでいない行は押せない（自動で入れ替えない）。理由は一覧の上に常に出し、チェックボックスの説明にもなる。
  await expect(list.getByRole("checkbox", { disabled: true })).toHaveCount(22);
  const reason = page.getByTestId("wiki-selection-limit");
  await expect(reason).toContainText("一度に選べる原文は 3 件までです。");
  await expect(rowCheckbox(page, 4)).toBeDisabled();
  await expect(rowCheckbox(page, 4)).toHaveAccessibleDescription(/一度に選べる原文は 3 件までです。/);
  await expect(rowCheckbox(page, 1)).toBeEnabled();
  await shot(page, info, "wiki-limit");
  // 詳細の「質問に追加」も押せず、同じ理由を出す。
  await list.getByRole("button", { name: /合成の原文 4 / }).click();
  const panel = detail(page, phone);
  await expect(panel.getByRole("button", { name: "質問に追加" })).toBeDisabled();
  await expect(panel.getByText("一度に選べる原文は 3 件までです。")).toBeVisible();
  await shot(page, info, "wiki-limit-detail");
  if (phone) await panel.getByRole("button", { name: "選択を解除" }).click();
  // 1 件外すと、また選べる。
  await rowCheckbox(page, 2).uncheck();
  await expect(page.getByTestId("selected-sources-count")).toHaveText("2 / 3 件");
  await expect(reason).toHaveCount(0);
  await expect(list.getByRole("checkbox", { disabled: true })).toHaveCount(0);
  await rowCheckbox(page, 4).check();
  await expect(page.getByTestId("selected-sources-count")).toHaveText("3 / 3 件");
  // 保存済みの選択が上限を超えていたら、先頭から上限までにする。
  control.limit = 2;
  await page.reload();
  await expect(page.getByTestId("selected-sources-count")).toHaveText("2 / 2 件");
  await expect(selectedList(page).getByRole("listitem")).toHaveCount(2);
  await expectNoPageOverflow(page);
});

for (const mode of ["only", "mix"] as const) {
  test(`チャットへ渡した原文は件数と使い方の選択を出し、要求に wiki_source_mode=${mode} を入れる`, { tag: "@responsive" }, async ({ page }, info) => {
    await data(page);
    const conversation = { id: "conv-1", search_answer_profile_id: profiles[0].id, title: null, status: "ACTIVE", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", messages: [] };
    await page.route("**/api/chat/conversations**", (r) => {
      const path = new URL(r.request().url()).pathname;
      if (path.endsWith("/messages/stream")) return r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: "" });
      if (path === "/api/chat/conversations" && r.request().method() !== "POST") return r.fulfill({ json: ok(paged([])) });
      return r.fulfill({ json: ok(conversation) });
    });
    await begin(page);
    for (const n of [1, 2, 3]) await rowCheckbox(page, n).check();
    await page.getByRole("button", { name: "チャットで質問" }).click();
    // 件数は見出しの後ろに出す（タグは 2 行までで、多いと総数が分からない。#5978）。
    await expect(page.getByTestId("selected-sources-count")).toHaveText("3 / 10 件");
    await expect(selectedList(page).getByRole("listitem")).toHaveCount(3);
    // 既定は「選んだ原文だけで回答」。
    const choice = page.getByRole("radiogroup", { name: "選んだ原文の使い方" });
    await expect(choice.getByRole("radio", { name: "選んだ原文だけで回答" })).toBeChecked();
    if (mode === "mix") {
      await choice.getByRole("radio", { name: "選んだ原文に追加して探す" }).check();
      await expect(choice.getByRole("radio", { name: "選んだ原文に追加して探す" })).toBeChecked();
    }
    if (mode === "only") {
      await page.getByTestId("chat-wiki-sources").scrollIntoViewIfNeeded();
      await shot(page, info, "chat-selected");
    }
    const sent = page.waitForRequest((request) => request.url().endsWith("/messages/stream") && request.method() === "POST");
    await page.getByTestId("chat-run-stop").click();
    const body = (await sent).postDataJSON() as { wiki_sources: { unit_id: string }[]; wiki_source_mode: string };
    expect(body.wiki_source_mode).toBe(mode);
    expect(body.wiki_sources.map((source) => source.unit_id)).toEqual([hash(1), hash(2), hash(3)]);
  });
}

test("ナレッジベース詳細の図から選ぶ原文にも、上限と件数の表示が効く", { tag: "@responsive" }, async ({ page }, info) => {
  const phone = info.project.name === "mobile";
  const control = await data(page, { calls: [], limit: 1 });
  await page.route("**/api/wiki-search/profiles-for-kb/**", (r) => r.fulfill({ json: ok([{ id: profiles[0].id, name: profiles[0].name }]) }));
  const area = await openKnowledgeBaseGraph(page);
  const canvas = canvasOf(page);
  await expect.poll(() => graphCount(canvas, "nodes")).toBe(25);
  // 選ぶ操作を出す前に、範囲（上限）を読む。
  expect(control.calls.some((u) => u.pathname.endsWith("/wiki/explore/scope"))).toBe(true);
  await page.locator("#wiki-graph-find").fill("合成");
  await expect(page.getByTestId("wiki-graph-find-nav")).toContainText("1 / 25");
  const panel = detail(page, phone);
  if (phone) await page.getByRole("button", { name: "詳細を開く" }).click();
  await expect(panel.getByText("照合済みの合成原文 1。")).toBeVisible();
  await panel.getByRole("button", { name: "質問に追加" }).click();
  if (phone) await panel.getByRole("button", { name: "選択を解除" }).click();
  await expect(area.getByTestId("selected-sources-count")).toHaveText("1 / 1 件");
  // 上限に達したら、ほかの原文の「質問に追加」は押せず、理由を出す。
  await area.getByRole("button", { name: "次の一致へ" }).click();
  if (phone) await page.getByRole("button", { name: "詳細を開く" }).click();
  await expect(panel.getByText("照合済みの合成原文 2。")).toBeVisible();
  await expect(panel.getByRole("button", { name: "質問に追加" })).toBeDisabled();
  await expect(panel.getByText("一度に選べる原文は 1 件までです。")).toBeVisible();
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
  await expectOpensReadable(page, canvas);
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
  await expectOpensReadable(page, canvas);
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

test("原文の関係は 1 回で取り切り、選んだ原文の次の段は 10 件、「さらに展開」で続きを 25 件ずつ出す", { tag: "@responsive" }, async ({ page }, info) => {
  const phone = info.project.name === "mobile";
  const control = await data(page);
  await begin(page);
  await results(page).getByRole("button", { name: /合成の原文 1 / }).click();
  const panel = detail(page, phone);
  // 関係は「関係」のタブを開くまで読まない（選ぶたびに読むと、原文・線の照会と重なる。#5966）。
  await expect(panel.getByRole("button", { name: "質問に追加" })).toBeVisible();
  expect(relationCalls(control)).toHaveLength(0);
  // 図に無い原文からは展開しない（図は選んだ原文とその次の段だけ。選ぶと図に入る）。
  await expect(panel.getByRole("button", { name: "関連を展開" })).toHaveCount(0);
  await panel.getByRole("tab", { name: "関係", exact: true }).click();
  // 関係の一覧は詳細の中でも共通のページング（25 件）。
  await expect(panel.getByRole("region", { name: "関係の一覧" }).getByRole("listitem")).toHaveCount(25);
  await panel.getByRole("button", { name: "質問に追加" }).click();
  if (phone) await panel.getByRole("button", { name: "選択を解除" }).click();
  await page.getByRole("tab", { name: "関係図" }).click();
  const canvas = canvasOf(page);
  // 選んだ原文 1 件と、次の段の最初の 10 件と、残り（20 件）の「さらに展開」の点。
  await expect.poll(() => graphCount(canvas, "nodes")).toBe(12);
  // 関係は 1 回の要求で取り切り、ページを繰り返して読まない（#5916）。
  expect(relationCalls(control).length).toBeGreaterThan(0);
  expect(relationCalls(control).every((u) => u.searchParams.get("limit") === "500" && u.searchParams.get("offset") === "0")).toBe(true);
  // 選んだ原文から「さらに展開」で残りを出す（既存の展開。1 回 25 件まで）。
  await page.getByRole("tab", { name: "一覧" }).click();
  await nodeList(page).getByRole("button", { name: /^合成の原文 1(?!\d)/ }).click();
  await panel.getByRole("button", { name: "さらに展開" }).click();
  await expect(page.getByRole("tab", { name: "関係図" })).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => graphCount(canvas, "nodes")).toBe(31);
  await expectNoPageOverflow(page);
});

test("関係の読み込みの失敗は詳細の同じ場所で再試行でき、Wiki 権限の無い利用者は開けない", async ({ page }, info) => {
  const control = await data(page, { calls: [], failRelations: true });
  await begin(page);
  await results(page).getByRole("button", { name: /合成の原文 1 / }).click();
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

test("検索中の回転は 1 つだけで、検索の条件の行のボタンは「検索」だけ", { tag: "@responsive" }, async ({ page }) => {
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
  await expect(page.getByRole("heading", { name: "検索結果と関係" })).toBeVisible();
  await expect(page.getByRole("button", { name: "新しい検索を開始" })).toHaveCount(0);
});
