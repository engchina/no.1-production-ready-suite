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
    "合成資料 " + String(i + 1).padStart(2, "0") + "." + ["pdf", "md", "txt", "docx", "pptx", "json"][i % 6],
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
const hitIndexes = [0, 1, 80, 160, ...Array.from({length:4400}, (_, i) => i).filter(i => ![0,1,80,160].includes(i))];
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
    await panel.getByRole("button", { name: "第 1 章", exact: true }).click();
    await panel.getByRole("button", { name: "合成の原文 1", exact: true }).click();
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
    await expect(page.locator(".react-flow__node").first()).toBeVisible();
    expect(await page.locator(".react-flow__node").count()).toBeLessThanOrEqual(10);
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
