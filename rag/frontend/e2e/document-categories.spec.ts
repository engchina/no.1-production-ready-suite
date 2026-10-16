import { expect, test, type Page, type Route } from "./fixtures/test";

import { apiEnvelope, expectNoPageOverflow, mockLocalAuth } from "./_helpers";

// #2131: 文書の分類（大分類 → 中分類 → 小分類）の一覧の管理。作成・名前の変更・並べ替え・削除はその場で保存する。
// 名前は合成の値（業務の名前を使わない）。

type Node = {
  id: string;
  name: string;
  level: number;
  parent_id: string | null;
  document_count: number;
  children: Node[];
};

function category(id: string, name: string, level: number, parent: string | null, children: Node[] = []): Node {
  return { id, name, level, parent_id: parent, document_count: 0, children };
}

/** 分類の一覧の API を、状態を持つ mock にする（作成・名前の変更・並べ替え・削除を反映する）。 */
async function mockCategories(page: Page) {
  const state = {
    tree: [
      category("l1", "10_業務A", 1, null, [category("m1", "種別X", 2, "l1")]),
      category("l2", "20_業務B", 1, null),
    ],
    requests: [] as { method: string; path: string; body: unknown }[],
  };
  state.tree[0].document_count = 2;
  state.tree[0].children[0].document_count = 2;
  const find = (nodes: Node[], id: string): Node[] | undefined => {
    for (const node of nodes) {
      if (node.id === id) return nodes;
      const found = find(node.children, id);
      if (found) return found;
    }
    return undefined;
  };
  let next = 1;
  const handler = async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postData() ? request.postDataJSON() : null;
    state.requests.push({ method: request.method(), path, body });
    const id = path.split("/")[3];
    if (request.method() === "POST" && path === "/api/document-categories") {
      const { parent_id: parentId, name } = body as { parent_id: string | null; name: string };
      const siblings = parentId ? find(state.tree, parentId)!.find((node) => node.id === parentId)!.children : state.tree;
      if (siblings.some((node) => node.name === name)) {
        return route.fulfill({
          status: 409,
          json: {
            data: null,
            error_messages: ["同じ分類の下に同じ名前の分類があります（先頭の番号「10_」などの違いは同じ名前として扱います）。"],
            warning_messages: [],
          },
        });
      }
      const level = parentId ? find(state.tree, parentId)!.find((node) => node.id === parentId)!.level + 1 : 1;
      siblings.push(category(`new-${next++}`, name, level, parentId));
      return route.fulfill({ status: 201, json: apiEnvelope(state.tree) });
    }
    if (request.method() === "PATCH") {
      const node = find(state.tree, id)!.find((item) => item.id === id)!;
      node.name = (body as { name: string }).name;
      return route.fulfill({ json: apiEnvelope({ updated_document_count: node.document_count }) });
    }
    if (request.method() === "POST" && path.endsWith("/move")) {
      const siblings = find(state.tree, id)!;
      const index = siblings.findIndex((node) => node.id === id);
      const target = index + ((body as { direction: string }).direction === "up" ? -1 : 1);
      [siblings[index], siblings[target]] = [siblings[target], siblings[index]];
      return route.fulfill({ json: apiEnvelope(state.tree) });
    }
    if (request.method() === "DELETE") {
      const siblings = find(state.tree, id)!;
      const node = siblings.find((item) => item.id === id)!;
      if (node.children.length || node.document_count) {
        return route.fulfill({
          status: 409,
          json: {
            data: null,
            error_messages: ["この分類は削除できません（2 件の文書が使っています）。先に下の分類を削除するか、文書の分類を変えてください。"],
            warning_messages: [],
          },
        });
      }
      siblings.splice(siblings.indexOf(node), 1);
      return route.fulfill({ json: apiEnvelope(state.tree) });
    }
    return route.fulfill({ json: apiEnvelope(state.tree) });
  };
  await page.route("**/api/document-categories", handler);
  await page.route("**/api/document-categories/**", handler);
  return state;
}

async function openActions(page: Page, id: string) {
  await page.getByTestId(`document-category-actions-${id}`).getByRole("button").first().click();
}

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

test("分類の一覧を階層で出し、作成・名前の変更・並べ替え・削除ができる", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const state = await mockCategories(page);
  await page.goto("/document-categories");

  await expect(page.getByRole("heading", { name: "文書の分類", level: 1 })).toBeVisible();
  const list = page.getByRole("list", { name: "文書の分類の一覧" });
  await expect(list).toBeVisible();
  // 大分類の下に中分類を字下げで出し、段の名前と文書の数を文字で示す。
  await expect(page.getByTestId("document-category-l1")).toContainText("10_業務A");
  await expect(page.getByTestId("document-category-l1")).toContainText("大分類 · 文書 2 件");
  await expect(page.getByTestId("document-category-m1")).toHaveAttribute("data-level", "2");
  await expect(page.getByTestId("document-category-m1")).toContainText("中分類 · 文書 2 件");

  // 大分類を追加する（同じ名前は理由を欄の下に出す）。
  await page.getByRole("button", { name: "大分類を追加" }).click();
  const largeName = page.getByRole("textbox", { name: "新しい大分類の名前" });
  await expect(largeName).toBeFocused();
  await largeName.fill("20_業務B");
  await page.getByRole("button", { name: "追加", exact: true }).click();
  await expect(page.getByText("同じ分類の下に同じ名前の分類があります")).toBeVisible();
  await largeName.fill("30_業務C");
  await page.getByRole("button", { name: "追加", exact: true }).click();
  await expect(page.getByTestId("document-category-new-1")).toContainText("30_業務C");
  expect(state.requests.filter((item) => item.method === "POST").at(-1)?.body).toEqual({
    parent_id: null,
    name: "30_業務C",
  });

  // 中分類の下に小分類を追加する。
  await openActions(page, "m1");
  await page.getByRole("menuitem", { name: "小分類を追加" }).click();
  await page.getByRole("textbox", { name: "新しい小分類の名前" }).fill("項目1");
  await page.getByRole("button", { name: "追加", exact: true }).click();
  await expect(page.getByTestId("document-category-new-2")).toContainText("小分類 · 文書 0 件");
  // 小分類の下には追加できない（操作に出さない）。
  await openActions(page, "new-2");
  await expect(page.getByRole("menuitem", { name: /を追加$/ })).toHaveCount(0);
  await page.keyboard.press("Escape");

  // 名前を変える（使っている文書の分類も変わることを知らせる）。
  await openActions(page, "m1");
  await page.getByRole("menuitem", { name: "名前を変更" }).click();
  const rename = page.getByRole("textbox", { name: "中分類の名前" });
  await expect(rename).toHaveValue("種別X");
  await rename.fill("種別Y");
  await page.getByRole("button", { name: "名前を保存" }).click();
  await expect(page.getByText("名前を変更しました（2 件の文書の分類も変えました）。")).toBeVisible();
  await expect(page.getByTestId("document-category-m1")).toContainText("種別Y");

  // 並べ替え: 2 番目の大分類を上へ。先頭の「上へ移動」は押せない。
  await openActions(page, "l2");
  await page.getByRole("menuitem", { name: "上へ移動" }).click();
  await expect(list.locator(":scope > li").first()).toHaveAttribute("data-testid", "document-category-l2");
  await openActions(page, "l2");
  await expect(page.getByRole("menuitem", { name: "上へ移動" })).toBeDisabled();
  await page.keyboard.press("Escape");

  // 使っている分類の削除は理由を出して止める。使っていない分類は確認して削除する。
  await openActions(page, "l1");
  await page.getByRole("menuitem", { name: "削除" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "削除" }).click();
  await expect(page.getByText("この分類は削除できません（2 件の文書が使っています）。")).toBeVisible();
  await openActions(page, "new-1");
  await page.getByRole("menuitem", { name: "削除" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "削除" }).click();
  await expect(page.getByTestId("document-category-new-1")).toHaveCount(0);

  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("document-categories.png"), fullPage: true });
});

test("分類がまだ無いときは作り方を案内する", async ({ page }) => {
  await page.route("**/api/document-categories", (route) => route.fulfill({ json: apiEnvelope([]) }));
  await page.goto("/document-categories");

  await expect(page.getByText("分類がまだありません")).toBeVisible();
  await expect(page.getByText("「大分類を追加」から最初の分類を作ります。")).toBeVisible();
});
