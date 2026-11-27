import { expect, test, type Page, type TestInfo } from "./fixtures/test";
import {
  apiEnvelope as ok,
  dbUser,
  expectNoPageOverflow,
  mockLocalAuth,
  selectSearchAnswerProfile,
} from "./_helpers";

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
  document_count: 2,
  indexed_document_count: 2,
  error_document_count: 0,
  searchable_chunk_count: 10,
  created_at: "2026-06-15T00:00:00Z",
  updated_at: "2026-06-15T00:00:00Z",
  archived_at: null,
  retrieval_config: {},
  adapter_config: { version: 1, ingestion: nullIngestion, query: { guardrail_policy: null } },
};

const files = Array.from({ length: 55 }, (_, i) => ({
  id: "doc-" + i,
  document_id: "doc-" + i,
  name:
    "合成資料 " + String(i + 1).padStart(2, "0") + (i === 1 ? "_" + "Reference".repeat(16) : "") + "." +
    ["pdf", "md", "txt", "docx", "pptx", "json"][i % 6],
  unit_count: 80,
  outgoing_links: 2,
  incoming_links: 1,
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
const hash = (n: number) => n.toString(16).padStart(64, "0");
function unit(index: number) {
  const file = files[Math.floor(index / 80)];
  return {
    id: hash(index + 1),
    kind: "unit",
    label: "合成の原文 " + String(index + 1),
    document_id: file.id,
    document_name: file.name,
    path: ["第 1 章"],
    knowledge_base_ids: [kbs[Math.floor(index / 80) % 2].id],
  };
}
const hitIndexes = [
  0,
  1,
  80,
  160,
  ...Array.from({ length: 4400 }, (_, i) => i).filter((i) => ![0, 1, 80, 160].includes(i)),
];
const related = Array.from({ length: 137 }, (_, i) => ({
  unit: unit(i + 1),
  direction: i % 2 ? "outgoing" : "incoming",
  scope: i < 79 ? "same_document" : "cross_document",
  relation: i < 2 ? "exception" : "reference",
}));
function paged<T>(items: T[]) {
  return { items, total: items.length, offset: 0, limit: 50, has_next: false };
}
type Controls = { failDocs?: boolean; failUnit?: boolean; calls: URL[] };
async function data(page: Page, controls: Controls = { calls: [] }) {
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
    r.fulfill({
      json: ok({ enabled: false, counts: { READY: 55 }, blocked_reason: null, documents: paged([]) }),
    }),
  );
  await page.route(/\/api\/(?:wiki-search\/profiles\/[^/]+|knowledge-bases\/[^/]+\/wiki\/explore)\//, (r) => {
    const url = new URL(r.request().url());
    controls.calls.push(url);
    const parts = url.pathname.split("/"),
      end = parts.at(-1),
      q = url.searchParams;
    const offset = Number(q.get("offset")),
      limit = Number(q.get("limit") ?? 5);
    const slice = <T>(items: T[]) => ({
      items: items.slice(offset, offset + limit),
      total: items.length,
      offset,
    });
    const finish = (v: unknown) => r.fulfill({ json: ok(v) });
    if (end === "scope") return finish({ knowledge_bases: kbs });
    if (end === "documents")
      return controls.failDocs
        ? r.fulfill({ status: 503, json: { detail: "一時的な失敗" } })
        : finish({
            documents: files.slice(offset, offset + limit),
            status: "ok",
            has_more: offset + limit < files.length,
          });
    if (end === "document-count") return finish({ total: files.length });
    if (end === "search")
      return finish(
        slice(
          hitIndexes.map(unit).filter((u) => !q.has("kb") || u.knowledge_base_ids.includes(q.get("kb")!)),
        ),
      );
    if (end === "tree") {
      const n = Number(parts.at(-2)!.split("-")[1]),
        file = files[n];
      return finish(
        slice(
          q.has("parent")
            ? Array.from({ length: 80 }, (_, i) => unit(n * 80 + i))
            : [
                {
                  id: "sec-" + n,
                  label: "第 1 章",
                  kind: "section",
                  knowledge_base_ids: [],
                  document_id: file.id,
                  document_name: file.name,
                  path: ["第 1 章"],
                },
              ],
        ),
      );
    }
    if (end === "relations") {
      const all = parts.at(-2) === hash(1) ? related : [];
      return finish({
        ...slice(all.filter((v) => !q.has("relation") || q.get("relation") === v.relation)),
        counts: all.length ? { exception: 2, reference: 135 } : {},
        preview: all.length ? [all[0], all[2]] : [],
        has_more: all.length > offset + limit,
      });
    }
    if (controls.failUnit) return r.fulfill({ status: 503, json: { detail: "一時的な失敗" } });
    const index = parseInt(end!, 16) - 1;
    const hit = unit(index);
    return finish({
      ...hit,
      parent_id: "sec-" + Math.floor(index / 80),
      source: { unit_id: hit.id, source_hash: hash(index + 10000) },
      chunks: [
        {
          document_id: hit.document_id,
          chunk_id: "c-" + index,
          file_name: hit.document_name,
          text: "照合済みの合成原文 " + (index + 1) + "。",
          score: 1,
          metadata: { page_start: (index % 80) + 1 },
        },
      ],
      context: "公開済みの合成の案内。",
      possible_questions: [],
      unresolved: [],
    });
  });
  return controls;
}
async function begin(page: Page) {
  await page.goto("/wiki-search");
  await selectSearchAnswerProfile(page, profiles[0].name);
  await page.locator("#wiki-question").fill("資料の確認方法");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect(page.getByRole("heading", { name: /検索結果と関係/ })).toBeVisible();
}
function detail(page: Page, phone: boolean) {
  return phone
    ? page.getByRole("dialog", { name: "詳細" })
    : page.getByRole("complementary", { name: "詳細" });
}
function result(page: Page, n: number) {
  return page.getByRole("button", { name: new RegExp("合成の原文 " + n + " 合成資料") });
}
async function shot(page: Page, info: TestInfo, name: string) {
  if (process.env.WIKI_GRAPH_SHOTS)
    await page.screenshot({
      path: process.env.WIKI_GRAPH_SHOTS + "/" + info.project.name + "-" + name + ".png",
    });
}
test(
  "長いファイル名は検索結果の幅に収まり、原文欄で横スクロールせずに全文を読める",
  { tag: "@responsive" },
  async ({ page }, info) => {
    await data(page);
    await begin(page);
    const hit = result(page, 81);
    const list = hit.locator("..");
    await expect(hit).toBeVisible();
    const overflow = (el: Element) => el.scrollWidth - el.clientWidth;
    expect(await list.evaluate(overflow)).toBeLessThanOrEqual(1);
    await hit.click();
    const original = detail(page, info.project.name === "mobile").getByRole("tabpanel", { name: "原文" });
    const location = original.getByText(files[1].name + " · p.1", { exact: true });
    await expect(location).toBeVisible();
    expect(await original.locator("..").evaluate(overflow)).toBeLessThanOrEqual(1);
    expect(await location.evaluate(overflow)).toBeLessThanOrEqual(1);
    await expectNoPageOverflow(page);
  },
);
test(
  "KB の図は展開時に読み、文書・章節・原文を同じ詳細で開き、失敗から再試行できる",
  { tag: "@responsive" },
  async ({ page }, info) => {
    const phone = info.project.name === "mobile";
    const control = await data(page, { calls: [], failDocs: true });
    await page.goto("/knowledge-bases/kb-1");
    const opener = page.getByTestId("wiki-graph-disclosure").locator("summary");
    await expect(opener).toBeVisible();
    expect(control.calls).toHaveLength(0);
    await opener.focus();
    await opener.press("Enter");
    const area = page.getByTestId("wiki-graph-disclosure");
    control.failDocs = false;
    await area.getByRole("button", { name: /再試行|再読み込み/ }).click();
    await area.getByRole("button", { name: /1 合成資料 01/ }).click();
    const panel = detail(page, phone);
    await panel.getByRole("button", { name: "関連を展開" }).click();
    const graph = area.locator(".react-flow");
    const chapter = graph.locator('.react-flow__node[data-id="sec-0"]');
    await chapter.focus();
    await chapter.press("Enter");
    await panel.getByRole("button", { name: "関連を展開" }).click();
    const firstUnit = graph.locator('.react-flow__node[data-id="' + hash(1) + '"]');
    await expect(firstUnit).toBeVisible();
    await graph.getByRole("button", { name: "全体表示", exact: true }).click();
    if (process.env.WIKI_GRAPH_SHOTS) {
      await graph.screenshot({ path: process.env.WIKI_GRAPH_SHOTS + "/" + info.project.name + "-kb-tree.png" });
    }
    await firstUnit.focus();
    await firstUnit.press("Enter");
    await expect(panel.getByText("照合済みの合成原文 1。")).toBeVisible();
    await panel.getByRole("tab", { name: "Wiki の案内" }).click();
    await expect(panel.getByText("公開済みの合成の案内。")).toBeVisible();
    await expectNoPageOverflow(page);
  },
);
test(
  "プロファイル内の Wiki を検索し、関係を部分展開し、選択した原文を通常のチャットで使う",
  { tag: "@responsive" },
  async ({ page }, info) => {
    const phone = info.project.name === "mobile",
      control = await data(page);
    await begin(page);
    await expect
      .poll(() => control.calls.find((u) => u.pathname.endsWith("/search"))?.searchParams.get("method"))
      .toBe("hybrid");
    await result(page, 1).click();
    const panel = detail(page, phone);
    await expect(panel.getByText("照合済みの合成原文 1。")).toBeVisible();
    await shot(page, info, "original");
    await panel.getByRole("button", { name: "質問に追加" }).click();
    await panel.getByRole("button", { name: /参照 135/ }).click();
    await expect
      .poll(() =>
        control.calls.some(
          (u) => u.searchParams.get("relation") === "reference" && u.searchParams.get("limit") === "6",
        ),
      )
      .toBe(true);
    await panel.getByRole("button", { name: "関連を展開" }).click();
    const canvas = page.locator(".react-flow");
    const nodes = canvas.locator(".react-flow__node");
    await expect(nodes.first()).toBeVisible();
    expect(await nodes.count()).toBeLessThanOrEqual(13);
    await expect(canvas.locator('.react-flow__node[data-id="' + hash(161) + '"]')).toHaveCount(1);
    await page.getByRole("button", { name: "全体表示", exact: true }).click();
    await expect.poll(() => canvas.evaluate((el) => {
      const area = el.getBoundingClientRect();
      return [...el.querySelectorAll(".react-flow__node")].every((node) => {
        const box = node.getBoundingClientRect();
        return box.width > 0 && box.left >= area.left - 1 && box.right <= area.right + 1 &&
          box.top >= area.top - 1 && box.bottom <= area.bottom + 1;
      });
    })).toBe(true);
    await shot(page, info, "wiki");
    if (phone) await page.getByRole("button", { name: "一覧", exact: true }).click();
    await result(page, 2).click();
    await panel.getByRole("button", { name: "質問に追加" }).click();
    if (phone) await panel.getByRole("button", { name: "選択を解除" }).click();
    await page.getByRole("button", { name: "チャットで質問" }).click();
    await expect(page.locator("#chat-search-answer-profile")).toContainText(profiles[0].name);
    const composer = page.locator("#chat-composer");
    await expect(composer).toHaveValue("資料の確認方法");
    const selected = page.getByRole("list", { name: "Wiki で選んだ原文" });
    await expect(selected.getByRole("listitem")).toHaveCount(2);
    await selected.getByRole("button").first().click();
    await expect(selected).toContainText("p.2");
    await expect(selected.getByRole("listitem")).toHaveCount(1);
    await expectNoPageOverflow(page);
    await shot(page, info, "chat");
    await selectSearchAnswerProfile(page, profiles[1].name);
    await expect(selected).toHaveCount(0);
  },
);
test(
  "検索方法と KB を絞り込めて、Wiki 権限を持たない利用者は直リンクからも開けない",
  { tag: "@responsive" },
  async ({ page }) => {
    const control = await data(page);
    await begin(page);
    await page.getByText("詳細条件", { exact: true }).click();
    await page.locator("#wiki-search-method").click();
    await page
      .getByRole("option", { name: /キーワード/, exact: false })
      .last()
      .click();
    await page.locator("#wiki-search-kb").click();
    await page.getByRole("option", { name: "ナレッジベース B" }).click();
    await page.getByRole("button", { name: "検索", exact: true }).click();
    await expect
      .poll(() =>
        control.calls.some(
          (u) => u.searchParams.get("kb") === "kb-2" && u.searchParams.get("method") === "keyword",
        ),
      )
      .toBe(true);
    await page.route("**/api/auth/me", (r) =>
      r.fulfill({ json: ok(dbUser({ permissions: ["menu.search"] })) }),
    );
    await page.reload();
    await expect(page.locator("#wiki-question")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Wiki 検索" })).toHaveCount(0);
  },
);

test(
  "複数の文書から 40 原文を持ち込んでも入力・送信とプロファイルが見える",
  { tag: "@responsive" },
  async ({ page }, info) => {
    test.setTimeout(90000);
    const phone = info.project.name === "mobile";
    await data(page);
    await begin(page);
    const panel = detail(page, phone);
    for (let p = 0; p < 8; p++) {
      if (p)
        await page
          .getByRole("navigation", { name: "検索結果", exact: true })
          .getByRole("button", { name: "次へ", exact: true })
          .click();
      for (const index of hitIndexes.slice(p * 5, (p + 1) * 5)) {
        await result(page, index + 1).click();
        await panel.getByRole("button", { name: "質問に追加" }).click();
        if (phone) await panel.getByRole("button", { name: "選択を解除" }).click();
      }
    }
    await page.getByRole("button", { name: "チャットで質問" }).click();
    const picked = page.getByRole("list", { name: "Wiki で選んだ原文" });
    await expect(picked.getByRole("listitem")).toHaveCount(40);
    await expect(page.locator("#chat-search-answer-profile")).toContainText(profiles[0].name);
    await expect(page.getByRole("button", { name: "送信", exact: true })).toBeInViewport();
    const size = await picked.evaluate((el) => ({
      height: el.clientHeight,
      total: el.scrollHeight,
      row: el.firstElementChild!.getBoundingClientRect().height,
      gap: parseFloat(getComputedStyle(el).rowGap),
    }));
    expect(size.height).toBeLessThanOrEqual(size.row * 2 + size.gap + 1);
    expect(size.total).toBeGreaterThan(size.height);
    await expectNoPageOverflow(page);
    await shot(page, info, "chat-40");
  },
);

test("検索の待ち時間を示し、チャットから現場に戻り、新しい検索で条件を引き継ぐ", { tag: "@responsive" }, async ({ page }, info) => {
  const phone = info.project.name === "mobile";
  await data(page);
  let release: (() => void) | undefined;
  await page.route(/\/wiki-search\/profiles\/[^/]+\/search\?/, async r => {
    await new Promise<void>(done => { release = done; });
    return r.fallback();
  }, { times: 1 });
  await page.goto("/wiki-search");
  await selectSearchAnswerProfile(page, profiles[0].name);
  await page.getByText("詳細条件", { exact: true }).click();
  await page.locator("#wiki-search-method").click();
  await page.getByRole("option", { name: "キーワード", exact: true }).click();
  await page.locator("#wiki-search-kb").click();
  await page.getByRole("option", { name: kbs[1].name }).click();
  await page.locator("#wiki-question").fill("資料の確認方法");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  const progress = page.getByTestId("wiki-wait-results");
  await expect(progress.getByRole("timer")).toHaveText(/00:0[1-9]/);
  await shot(page, info, "wiki-loading");
  release!();
  await result(page, 81).click();
  await detail(page, phone).getByRole("button", { name: "質問に追加" }).click();
  if (phone) await detail(page, phone).getByRole("button", { name: "選択を解除" }).click();
  await page.getByRole("button", { name: "チャットで質問" }).click();
  await page.getByRole("button", { name: "Wiki 検索に戻る" }).click();
  await expect(page.locator("#wiki-question")).toHaveValue("資料の確認方法");
  await expect(page.getByRole("list", { name: "Wiki で選んだ原文" })).toHaveCount(1);
  await page.getByRole("button", { name: "新しい検索" }).click();
  await expect(page.locator("#wiki-question")).toBeFocused();
  await expect(page.locator("#wiki-question")).toHaveValue("");
  await expect(page.getByText("質問を入力して原文を検索してください。")).toBeVisible();
  await expect(page.getByRole("list", { name: "Wiki で選んだ原文" })).toHaveCount(0);
  await page.getByText("詳細条件", { exact: true }).click();
  await expect(page.locator("#wiki-search-method")).toContainText("キーワード");
  await expect(page.locator("#wiki-search-kb")).toContainText(kbs[1].name);
  await expect(page.locator("#wiki-search-profile")).toContainText(profiles[0].name);
  await shot(page, info, "wiki-new");
});

test("二つの展開元を持つ原文を保持し、向き別の関係と折りたたみを確認できる", { tag: "@responsive" }, async ({ page }, info) => {
  const phone = info.project.name === "mobile";
  await data(page);
  const rel = (i: number, relation = "reference", direction = "outgoing") => ({ unit: unit(i), relation, direction, scope: "same_document" });
  const byUnit = new Map([
    [hash(1), [rel(1), rel(2)]],
    [hash(2), [rel(3), rel(3, "exception"), rel(3, "prerequisite"), rel(3, "reference", "incoming"), rel(4)]],
    [hash(3), [rel(3)]],
  ]);
  await page.route(/\/wiki-search\/profiles\/[^/]+\/units\/[^/]+\/relations\?/, async route => {
    const url = new URL(route.request().url());
    const items = byUnit.get(url.pathname.split("/").at(-2)!) ?? [];
    return route.fulfill({ json: ok({ items, preview: items, total: items.length, has_more: false, counts: {} }) });
  });
  await begin(page);
  if (phone) await page.getByRole("button", { name: "関係図", exact: true }).click();
  const graph = page.locator(".react-flow");
  const node = (n: number) => graph.locator('.react-flow__node[data-id="' + hash(n) + '"]');
  const select = async (n: number) => { await node(n).focus(); await node(n).press("Enter"); };
  const panel = detail(page, phone);
  const camera = () => graph.locator(".react-flow__viewport").evaluate(el => (el as HTMLElement).style.transform);
  await select(2);
  await panel.getByRole("button", { name: "関連を展開" }).click();
  await select(3);
  await panel.getByRole("button", { name: "関連を展開" }).click();
  await select(2);
  await panel.getByRole("button", { name: "関連を折りたたむ" }).click();
  await expect(node(4)).toHaveCount(1);
  await expect(node(5)).toHaveCount(0);
  await page.getByRole("button", { name: "折りたたみを元に戻す" }).click();
  await expect(node(5)).toHaveCount(1);
  await graph.getByRole("button", { name: "全体表示" }).click();
  const previous = await camera();
  await graph.getByRole("button", { name: "選択を中心に表示" }).click();
  await expect.poll(camera).not.toBe(previous);
  await graph.getByRole("button", { name: "前の表示に戻る" }).click();
  await expect.poll(camera).toBe(previous);
  await graph.locator(".graph-edge-count").focus();
  await graph.locator(".graph-edge-count").press("Enter");
  await expect(panel.getByRole("heading", { name: "関係 3 件" })).toBeVisible();
  await expect(panel.getByRole("listitem")).toHaveCount(3);
  await shot(page, info, "direction");
});
