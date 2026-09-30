import { expect, test, type Locator, type Page } from "@playwright/test";
import { LOCAL_AUTH_ME } from "./_helpers";

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: LOCAL_AUTH_ME });
      return;
    }
    if (url.pathname === "/api/ready/database") {
      await route.fulfill({
        json: {
          data: { status: "ok", check: "ok", detail: null },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (url.pathname === "/api/business-views") {
      // 検索ページは業務ビュー選択が前提のため、最低 1 件を返してフィルタを描画する。
      await route.fulfill({
        json: {
          data: {
            items: [
              {
                id: "bv-1",
                name: "経理ビュー",
                description: null,
                status: "ACTIVE",
                knowledge_base_count: 1,
                created_at: "2026-06-19T00:00:00Z",
                updated_at: "2026-06-19T00:00:00Z",
                archived_at: null,
              },
            ],
            total: 1,
            limit: 50,
            offset: 0,
            has_next: false,
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (url.pathname === "/api/settings/oci") {
      // リージョン SelectField の選択済み値を確認するため region を返す。
      await route.fulfill({
        json: {
          data: {
            config_file: "~/.oci/config",
            profile: "DEFAULT",
            user: "",
            fingerprint: "",
            tenancy: "",
            region: "us-chicago-1",
            key_file: "",
            key_file_exists: false,
            config_file_exists: false,
            config_source: "runtime",
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (url.pathname === "/api/settings/upload-storage") {
      // 共有 OCI 認証画面は Object Storage の値もここから読む（#100）。
      await route.fulfill({
        json: {
          data: {
            backend: "local",
            local_storage_dir: "/u01/data/production-ready-rag",
            object_storage_region: "",
            object_storage_namespace: "",
            object_storage_bucket: "",
            readiness: "ok",
            max_upload_bytes: 209715200,
            config_source: "runtime",
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    await route.fulfill({
      json: { data: null, error_messages: [], warning_messages: [] },
    });
  });
}

test.beforeEach(async ({ page }) => {
  if ((page.viewportSize()?.width ?? 0) <= 500) {
    await page.addInitScript(() => {
      window.localStorage.setItem(
        "production-ready-rag.ui",
        JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
      );
    });
  }
  await mockApi(page);
});

test("OCI リージョンの候補を指定順で表示し、選択できる", async ({ page }) => {
  await page.goto("/settings/oci");

  const region = page.getByRole("combobox", { name: "リージョン", exact: true });
  await expect(region).toContainText("us-chicago-1");

  await region.click();
  const listbox = page.getByRole("listbox", { name: "リージョン", exact: true });
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option")).toHaveText([
    "ap-tokyo-1",
    "ap-osaka-1",
    "us-chicago-1",
  ]);
  // 浮いた面は popover の影トークンを使う（ダークテーマでも段が見える）
  await expect(listbox).toHaveClass(/shadow-\[var\(--shadow-popover\)\]/);

  await listbox.getByRole("option", { name: "ap-tokyo-1" }).click();
  await expect(region).toContainText("ap-tokyo-1");
  await expect(page.getByRole("listbox", { name: "リージョン", exact: true })).toBeHidden();
});

test("検索条件の内容種別も同じドロップダウン UI で選択できる", async ({ page }) => {
  await page.goto("/search");

  // 内容種別は「詳細条件」ディスクロージャ内にあるため展開してから操作する。
  await page.getByRole("button", { name: "詳細条件" }).click();
  const contentKind = page.getByRole("combobox", { name: "内容種別" });
  await contentKind.click();

  const listbox = page.getByRole("listbox", { name: "内容種別" });
  await expect(listbox.getByRole("option")).toHaveText([
    "すべて",
    "本文",
    "箇条書き",
    "表",
    "図・画像",
    "数式",
    "コード",
    "メール",
    "スライド",
    "シート",
    "抽出項目",
    "章節要約",
  ]);

  await listbox.getByRole("option", { name: "表" }).click();
  await expect(contentKind).toContainText("表");
});

test("評価のランキング指標も同じドロップダウン UI で選択できる", async ({ page }) => {
  await page.goto("/evaluation");

  const rankingMetric = page.getByRole("combobox", { name: "ランキング指標" });
  await rankingMetric.click();

  const listbox = page.getByRole("listbox", { name: "ランキング指標" });
  await expect(listbox.getByRole("option")).toHaveText([
    "正解文書の再現率",
    "正解文書の順位(MRR)",
    "根拠への忠実さ",
    "引用の追跡可能性",
    "主張の裏付け",
    "期待する語の一致",
    "拒答の正しさ",
    "標準回答の網羅",
    "標準回答での合格",
  ]);

  await listbox.getByRole("option", { name: "根拠への忠実さ" }).click();
  await expect(rankingMetric).toContainText("根拠への忠実さ");
});

// ── #352: Portal・反転・typeahead・選択肢のスクロール ──────────────────────────

/** 要素の中心で一番上にある要素が、その要素（か子孫）であること。親の overflow で切れていれば別の要素になる。 */
async function expectCenterHitsSelf(locator: Locator) {
  await expect
    .poll(() =>
      locator.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return Boolean(hit && node.contains(hit));
      })
    )
    .toBe(true);
}

/** 強調中の選択肢（aria-activedescendant）が一覧の表示範囲に入っていること。 */
async function expectActiveOptionInView(combobox: Locator, listbox: Locator, name: string) {
  const activeId = await combobox.getAttribute("aria-activedescendant");
  expect(activeId).toBeTruthy();
  const active = listbox.locator(`[id="${activeId}"]`);
  await expect(active).toHaveText(name);
  await expect
    .poll(async () => {
      const [item, list] = await Promise.all([active.boundingBox(), listbox.boundingBox()]);
      if (!item || !list) return false;
      return item.y >= list.y - 1 && item.y + item.height <= list.y + list.height + 1;
    })
    .toBe(true);
}

test("overflow: hidden の親の中でも一覧が切れず、選択肢を押せる", async ({ page }) => {
  await page.goto("/settings/oci");
  const region = page.getByRole("combobox", { name: "リージョン", exact: true });
  await expect(region).toContainText("us-chicago-1");
  // フィールドの外枠をトリガーの高さで切る（DataTable のセル・カード・スクロール枠と同じ状況）。
  await region.evaluate((button) => {
    const clip = button.parentElement as HTMLElement;
    clip.style.overflow = "hidden";
    clip.style.height = `${button.offsetHeight}px`;
  });

  await region.click();
  const listbox = page.getByRole("listbox", { name: "リージョン", exact: true });
  await expect(listbox).toBeVisible();
  const last = listbox.getByRole("option", { name: "us-chicago-1" });
  await expectCenterHitsSelf(last);
  await expectCenterHitsSelf(listbox.getByRole("option", { name: "ap-tokyo-1" }));

  // 一覧（Portal 先）の中を押しても外側クリックとみなさない。選んだら閉じてボタンにフォーカスを戻す。
  await listbox.getByRole("option", { name: "ap-osaka-1" }).click();
  await expect(region).toContainText("ap-osaka-1");
  await expect(listbox).toBeHidden();
  await expect(region).toBeFocused();

  // 一覧の外を押すと閉じる。
  await region.click();
  await expect(listbox).toBeVisible();
  await page.getByRole("heading", { level: 1 }).click();
  await expect(listbox).toBeHidden();
  await expect(region).toContainText("ap-osaka-1");
});

test("画面の下端では一覧を上に開き、画面の外にはみ出さない", async ({ page }) => {
  await page.goto("/evaluation");
  const rankingMetric = page.getByRole("combobox", { name: "ランキング指標" });
  await expect(rankingMetric).toBeVisible();
  const trigger = await rankingMetric.boundingBox();
  expect(trigger).not.toBeNull();
  // フィールドの下に 1 行分の余白しか残らない高さにする（上の内容は画面の高さに依らない）。
  const width = page.viewportSize()?.width ?? 1440;
  await page.setViewportSize({ width, height: Math.ceil(trigger!.y + trigger!.height + 40) });

  await rankingMetric.click();
  const listbox = page.getByRole("listbox", { name: "ランキング指標" });
  await expect(listbox).toHaveAttribute("data-floating-menu-placement", "top");
  const [list, button] = await Promise.all([listbox.boundingBox(), rankingMetric.boundingBox()]);
  const viewportHeight = page.viewportSize()!.height;
  expect(list!.y).toBeGreaterThanOrEqual(0);
  expect(list!.y + list!.height).toBeLessThanOrEqual(button!.y);
  expect(list!.y + list!.height).toBeLessThanOrEqual(viewportHeight);
  // 一覧はトリガーと同じ幅・左端。
  expect(Math.abs(list!.x - button!.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(list!.width - button!.width)).toBeLessThanOrEqual(1);

  await listbox.getByRole("option", { name: "根拠への忠実さ" }).click();
  await expect(rankingMetric).toContainText("根拠への忠実さ");
});

test("7 件以上の一覧でも、キーボードで強調した選択肢を表示範囲に入れる", async ({ page }) => {
  await page.goto("/evaluation");
  const rankingMetric = page.getByRole("combobox", { name: "ランキング指標" });
  await rankingMetric.focus();
  await page.keyboard.press("ArrowDown");
  const listbox = page.getByRole("listbox", { name: "ランキング指標" });
  await expect(listbox).toBeVisible();
  await expectActiveOptionInView(rankingMetric, listbox, "正解文書の再現率");

  await page.keyboard.press("End");
  await expectActiveOptionInView(rankingMetric, listbox, "標準回答での合格");
  await page.keyboard.press("Home");
  await expectActiveOptionInView(rankingMetric, listbox, "正解文書の再現率");
  for (let index = 0; index < 4; index += 1) await page.keyboard.press("ArrowDown");
  await expectActiveOptionInView(rankingMetric, listbox, "主張の裏付け");
  // PageDown / PageUp は 10 件ずつ動き、端で止まる。
  await page.keyboard.press("PageDown");
  await expectActiveOptionInView(rankingMetric, listbox, "標準回答での合格");
  await page.keyboard.press("PageUp");
  await expectActiveOptionInView(rankingMetric, listbox, "正解文書の再現率");
  for (let index = 0; index < 3; index += 1) await page.keyboard.press("ArrowDown");
  await expectActiveOptionInView(rankingMetric, listbox, "引用の追跡可能性");

  await page.keyboard.press("Enter");
  await expect(listbox).toBeHidden();
  await expect(rankingMetric).toContainText("引用の追跡可能性");
});

test("文字の入力で選択肢に飛ぶ（typeahead）", async ({ page }) => {
  // 評価の指標の表示名は日本語になった（#591）ため、英字の選択肢を持つリージョンで確かめる。
  await page.goto("/settings/oci");
  const region = page.getByRole("combobox", { name: "リージョン", exact: true });
  await region.focus();

  // 閉じているときに打つと開き、その文字で始まる選択肢を強調する。
  await page.keyboard.press("a");
  const listbox = page.getByRole("listbox", { name: "リージョン", exact: true });
  await expect(listbox).toBeVisible();
  await expectActiveOptionInView(region, listbox, "ap-tokyo-1");
  // 同じ文字を続けて打つと、その文字で始まる次の選択肢へ巡る。
  await page.keyboard.press("a");
  await expectActiveOptionInView(region, listbox, "ap-osaka-1");

  // 入力が途切れたら（500ms）リセットし、続けて打った文字は前方一致で絞り込む。
  await page.waitForTimeout(700);
  await page.keyboard.type("us", { delay: 30 });
  await expectActiveOptionInView(region, listbox, "us-chicago-1");

  // 大文字・小文字を区別しない。
  await page.waitForTimeout(700);
  await page.keyboard.type("AP-T", { delay: 30 });
  await expectActiveOptionInView(region, listbox, "ap-tokyo-1");
  await page.keyboard.press("Enter");
  await expect(listbox).toBeHidden();
  await expect(region).toContainText("ap-tokyo-1");
});

test("モーダルの層の中でも一覧を暗幕とモーダルの上に出し、Esc は一覧だけを閉じる", async ({ page }) => {
  await page.goto("/settings/oci");
  const region = page.getByRole("combobox", { name: "リージョン", exact: true });
  await expect(region).toContainText("us-chicago-1");
  // ConfirmDialog と同じ重なり（暗幕 --z-scrim の上に --z-dialog の面）を作り、フィールドをその面に載せる。
  await region.evaluate((button) => {
    const field = button.parentElement?.parentElement as HTMLElement;
    const scrim = document.createElement("div");
    scrim.dataset.testid = "test-scrim";
    scrim.style.cssText = "position:fixed;inset:0;z-index:var(--z-scrim);background:var(--scrim)";
    document.body.append(scrim);
    // フィールドを --z-dialog の面に載せる。React の root の外へは動かせないため、その場で持ち上げる
    // （途中の重なり文脈に閉じ込められないよう、body までの祖先をすべて --z-dialog にする）。
    field.setAttribute("role", "dialog");
    field.style.background = "var(--color-surface-overlay)";
    for (let node: HTMLElement | null = field; node && node !== document.body; node = node.parentElement) {
      if (getComputedStyle(node).position === "static") node.style.position = "relative";
      node.style.zIndex = "var(--z-dialog)";
    }
    (window as unknown as { escapeCount: number }).escapeCount = 0;
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") (window as unknown as { escapeCount: number }).escapeCount += 1;
    });
  });

  await region.click();
  const listbox = page.getByRole("listbox", { name: "リージョン", exact: true });
  await expect(listbox).toBeVisible();
  await expectCenterHitsSelf(listbox.getByRole("option", { name: "ap-osaka-1" }));

  await page.keyboard.press("Escape");
  await expect(listbox).toBeHidden();
  await expect(region).toBeFocused();
  expect(await page.evaluate(() => (window as unknown as { escapeCount: number }).escapeCount)).toBe(0);

  // aria-modal のモーダルでは、一覧をモーダルの中に描く（外に出すと支援技術がモーダルの外として読まない）。
  await region.evaluate((button) => button.parentElement?.parentElement?.setAttribute("aria-modal", "true"));
  await page.keyboard.press("ArrowDown");
  await expect(listbox).toBeVisible();
  expect(
    await listbox.evaluate((node) => Boolean(node.closest('[aria-modal="true"]')?.contains(document.activeElement)))
  ).toBe(true);
  await expectCenterHitsSelf(listbox.getByRole("option", { name: "ap-tokyo-1" }));
  await listbox.getByRole("option", { name: "ap-tokyo-1" }).click();
  await expect(region).toContainText("ap-tokyo-1");
  await expect(listbox).toBeHidden();
});
