import { expect, type Page } from "@playwright/test";

/**
 * 共通認証の利用者（`GET /api/auth/me` の CurrentUser。#214）。
 * 権限コードは backend `app/security/permissions.py` の権限カタログと同じ（unit テストで一致を確かめる）。
 */
export const MENU_PERMISSION_CODES = [
  "menu.search",
  "menu.chat",
  "menu.business_views",
  "menu.evaluation",
  "menu.feedback",
  "menu.upload",
  "menu.file_list",
  "menu.knowledge_bases",
  "menu.settings_pipeline",
  "menu.settings_preprocess",
  "menu.settings_parser_adapters",
  "menu.settings_chunking",
  "menu.settings_vector_index",
  "menu.settings_retrieval",
  "menu.settings_prompts",
  "menu.settings_guardrail",
  "menu.settings_evaluation",
  "menu.settings_graph",
  "menu.settings_huggingface",
  "menu.settings_services",
  "menu.security_users",
  "menu.security_roles",
  "menu.security_permissions",
  "menu.settings_oci",
  "menu.settings_upload_storage",
  "menu.settings_model",
  "menu.settings_database",
  "menu.settings_appearance",
] as const;

export const CAPABILITY_PERMISSION_CODES = [
  "rag.business_views.manage",
  "rag.knowledge_bases.manage",
  "rag.feedback.manage",
  "rag.system_tables.manage",
] as const;

export const ALL_PERMISSION_CODES: string[] = [
  ...MENU_PERMISSION_CODES,
  ...CAPABILITY_PERMISSION_CODES,
];

export interface CurrentUserPayload {
  user_uuid: string;
  login_user_id: string;
  display_name: string;
  status: string;
  force_password_change: boolean;
  role_codes: string[];
  is_system_admin: boolean;
  permissions: string[];
  allowed_business_view_ids: string[] | null;
  allowed_knowledge_base_ids: string[] | null;
  debug_mode: boolean;
  password_change_allowed: boolean;
}

/** ローカル DEBUG（`auth_mode=local`）の利用者。ログインなしで全画面を使える（backend の local_debug_principal と同じ）。 */
export const LOCAL_CURRENT_USER: CurrentUserPayload = {
  user_uuid: "00000000-0000-0000-0000-000000000000",
  login_user_id: "local",
  display_name: "ローカル利用者",
  status: "ACTIVE",
  force_password_change: false,
  role_codes: ["SYSTEM_ADMIN"],
  is_system_admin: true,
  permissions: ALL_PERMISSION_CODES,
  allowed_business_view_ids: null,
  allowed_knowledge_base_ids: null,
  debug_mode: true,
  password_change_allowed: false,
};

export function apiEnvelope<T>(data: T) {
  return { data, error_messages: [] as string[], warning_messages: [] as string[] };
}

/** `GET /api/auth/me` のローカル DEBUG の応答。 */
export const LOCAL_AUTH_ME = apiEnvelope(LOCAL_CURRENT_USER);

/**
 * `/api/auth/me` をローカル DEBUG の利用者（全権限・範囲の制限なし）にする。
 * DB ゲートを通す画面（RAG 固有の設定を含む。#325）を開けるよう、`/api/ready/database` も
 * 既定で ok にする。DB の状態を変える spec は、この後に `page.route` で上書きする（後の route が優先）。
 */
export async function mockLocalAuth(page: Page): Promise<void> {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: LOCAL_AUTH_ME }));
}

/** DB ユーザー（ログイン済み）の CurrentUser。既定は権限なし・範囲なし。 */
export function dbUser(overrides: Partial<CurrentUserPayload> = {}): CurrentUserPayload {
  return {
    user_uuid: "11111111-1111-1111-1111-111111111111",
    login_user_id: "user01",
    display_name: "利用者 一郎",
    status: "ACTIVE",
    force_password_change: false,
    role_codes: ["RAG_USER"],
    is_system_admin: false,
    permissions: [],
    allowed_business_view_ids: [],
    allowed_knowledge_base_ids: [],
    debug_mode: false,
    password_change_allowed: true,
    ...overrides,
  };
}

/**
 * `/api/auth/me` を、任意の権限・対象範囲を持つログイン済みの DB ユーザーにする。
 * 返した関数で利用者を差し替えられる（null で 401 = 未ログイン）。
 */
export async function mockAuthUser(
  page: Page,
  overrides: Partial<CurrentUserPayload> = {}
): Promise<(next: CurrentUserPayload | null) => void> {
  let current: CurrentUserPayload | null = dbUser(overrides);
  await page.route("**/api/auth/me", (route) =>
    current
      ? route.fulfill({ json: apiEnvelope(current) })
      : route.fulfill({
          status: 401,
          json: { data: null, error_messages: ["ログインが必要です。"], warning_messages: [] },
        })
  );
  return (next) => {
    current = next;
  };
}

/**
 * DB ゲート用の共通モック。
 * 設定ページ以外を開く前に呼ばれる `/api/ready/database` を「利用可能」にして、
 * ゲートに塞がれず本来のページを描画させる。
 */
export const DB_STATUS_OK = {
  data: { status: "ok", check: "ok", detail: null },
  error_messages: [],
  warning_messages: [],
};

/** `/api/ready/database` を ok 応答にして DB ゲートを通過させる。 */
export async function mockDatabaseReady(page: Page): Promise<void> {
  await page.route("**/api/ready/database", (route) => route.fulfill({ json: DB_STATUS_OK }));
}

/**
 * `/settings/database` の SystemTablesCard 用 status stub。
 *
 * `/api/settings/database/system-tables` は `**\/api/settings/database**` に一致するため、
 * 設定ページの mock が catch-all で別 payload を返すとカードがエラー表示になる。
 * 同じページの他カード(ADB 管理・Wallet)を検証する spec は、この stub を明示 route する。
 */
export const SYSTEM_TABLES_STATUS_OK = {
  data: {
    status: "ready",
    schema_version: "0003",
    schema_head: "0003",
    applied_versions: ["0001", "0002", "0003"],
    pending_versions: [],
    expected_object_count: 96,
    existing_object_count: 96,
    expected_table_count: 24,
    existing_table_count: 24,
    missing_objects: [],
    retired_objects: [],
    tables: [],
    operation_state: {
      status: "idle",
      operation_kind: null,
      lease_expires_at: null,
      last_error_code: null,
      schema_epoch: 3,
      updated_at: "2026-07-23T00:00:00+09:00",
    },
  },
  error_messages: [],
  warning_messages: [],
};

/**
 * ページ全体が横スクロールせず、document に第2の縦スクロールがないことを検証する。
 *
 * `documentElement` だけでなく **`main`(`overflow-y-auto` で overflow-x も auto になる
 * スクロール領域)の内部はみ出し**も検査する。広いテーブルの `min-w-[…]` がグリッド子の
 * `min-w-0` 欠落でカラム幅を押し広げると、`main` が横スクロールを内部吸収してしまい
 * `documentElement` 基準のチェックだけでは見逃すため(ナレッジベース管理ページの崩れの実例)。
 * テーブル等の意図的な横スクロールは各自の `overflow-x-auto` の箱に閉じ込める前提。
 *
 * `expect.poll` で短時間リトライし、サイドバー折りたたみ等の **UI 遷移中の一過性のはみ出し**は
 * 吸収する(例: viewport を desktop→375 にリサイズした直後の width transition 200ms)。
 * 静的な実バグ(グリッド崩れ・scroll container の伝播)は沈静後も残るため確実に検出する。
 */
export async function expectNoPageOverflow(page: Page): Promise<void> {
  const measure = () =>
    page.evaluate(() => {
      const root = document.documentElement;
      const main = document.querySelector("main");
      return {
        horizontal: Math.max(
          root.scrollWidth - root.clientWidth,
          main ? main.scrollWidth - main.clientWidth : 0
        ),
        documentVertical: root.scrollHeight - root.clientHeight,
      };
    });
  // 1px はスクロールバー/小数丸めの許容。遷移沈静まで最大 2s リトライ。
  await expect
    .poll(async () => (await measure()).horizontal, {
      message: "ページ全体(documentElement / main)の横はみ出し",
      timeout: 2000,
    })
    .toBeLessThanOrEqual(1);
  await expect
    .poll(async () => (await measure()).documentVertical, {
      message: "documentElement に第2の縦スクロールがないこと",
      timeout: 2000,
    })
    .toBeLessThanOrEqual(1);
}

/** main を末尾までスクロールしたとき、実コンテンツの後ろに空白が残らないことを検証する。 */
export async function expectMainScrollEndsAtContent(page: Page): Promise<void> {
  const main = page.getByRole("main");
  await expect
    .poll(
      () =>
        main.evaluate((element) => {
          const content = element.firstElementChild;
          if (!content) return Number.POSITIVE_INFINITY;
          element.scrollTo({ top: element.scrollHeight, left: 0, behavior: "auto" });
          return Math.max(
            0,
            element.getBoundingClientRect().bottom - content.getBoundingClientRect().bottom
          );
        }),
      { message: "main の末尾に実コンテンツを超える空白がないこと", timeout: 2000 }
    )
    .toBeLessThanOrEqual(1);
}

/**
 * 一覧の各行で、セルの内容（要素と文字）が列の境界を超えないことを実測する。
 *
 * - 次のセルが同じ行に並ぶ場合は「内容の右端 <= 次のセルの左端」、最後のセルと縦に積まれる場合は「<= 自セルの右端」。
 * - 内容の左端も自セルの左端を下回らないこと（右寄せの nowrap が左の列へはみ出す場合）。
 * - `overflow: hidden` の祖先（truncate / line-clamp）で切り取られる部分は見えないので数えない。
 * 違反を「列見出し: 内容 はみ出し量」の配列で返す（空配列が合格）。
 */
export async function measureTableCellOverflow(page: Page, tableSelector: string): Promise<string[]> {
  return page.locator(tableSelector).evaluateAll((tables) => {
    const violations: string[] = [];
    for (const table of tables) {
      const headers = Array.from(table.querySelectorAll("thead th, [role='columnheader']")).map(
        (th) => th.textContent?.trim() ?? ""
      );
      for (const row of Array.from(table.querySelectorAll("tr, [role='row']"))) {
        const cells = Array.from(row.querySelectorAll("td, th, [role='cell'], [role='columnheader']")).filter(
          (cell) => cell.parentElement?.closest("tr, [role='row']") === row && cell.getBoundingClientRect().width > 0
        );
        cells.forEach((cell, index) => {
          const cellRect = cell.getBoundingClientRect();
          const nextRect = cells[index + 1]?.getBoundingClientRect();
          const limitRight =
            nextRect && nextRect.top < cellRect.bottom - 1 && nextRect.left >= cellRect.left ? nextRect.left : cellRect.right;
          const walker = document.createTreeWalker(cell, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
          for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const element = node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as Element);
            if (!element) continue;
            if (node.nodeType === Node.TEXT_NODE && !node.textContent?.trim()) continue;
            let rect: DOMRect;
            if (node.nodeType === Node.TEXT_NODE) {
              const range = document.createRange();
              range.selectNodeContents(node);
              rect = range.getBoundingClientRect();
            } else {
              rect = element.getBoundingClientRect();
            }
            if (rect.width <= 1 || rect.height <= 1) continue;
            let right = rect.right;
            let left = rect.left;
            for (let ancestor = element; ancestor && ancestor !== row; ancestor = ancestor.parentElement!) {
              if (ancestor === element && node.nodeType !== Node.TEXT_NODE) continue;
              if (getComputedStyle(ancestor).overflowX !== "visible") {
                const clip = ancestor.getBoundingClientRect();
                right = Math.min(right, clip.right);
                left = Math.max(left, clip.left);
              }
            }
            const label = `${headers[index] ?? index}: ${(node.textContent ?? "").trim().slice(0, 24)}`;
            if (right > limitRight + 0.5) violations.push(`${label} right +${(right - limitRight).toFixed(1)}px`);
            if (left < cellRect.left - 0.5) violations.push(`${label} left -${(cellRect.left - left).toFixed(1)}px`);
          }
        });
      }
    }
    return Array.from(new Set(violations));
  });
}

/**
 * サイドナビ（`<aside>`）を返す。md 未満（767px 以下）ではナビがドロワーのため（#367）、
 * 閉じていれば上端のバーの「メニュー」を押して開いてから返す。md 以上はそのまま返す。
 * ナビのリンクを押すとドロワーは閉じるので、続けて操作するときは再び呼ぶ。
 */
export async function openSidebarNav(page: Page) {
  const trigger = page.getByTestId("nav-drawer-trigger");
  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  // 画面の描画を待ってから、どちらの形かを見る（シェルが出る前に判定しない）。
  await expect(trigger.or(sidebar).first()).toBeVisible();
  if ((await trigger.isVisible()) && (await trigger.getAttribute("aria-expanded")) !== "true") {
    await trigger.click();
    await expect(page.getByTestId("nav-drawer")).toHaveAttribute("data-state", "open");
  }
  await expect(sidebar).toBeVisible();
  return sidebar;
}

/** 操作部品の高さの段（README §4「操作部品の高さと幅」、#613）。 */
export const CONTROL_HEIGHT = { sm: 32, md: 36, lg: 40 } as const;

/**
 * 入力欄・選択欄・ボタンの期待する高さ（px）。タッチ端末（pointer: coarse）では段によらず 44px。
 * 画面幅ではなく入力方式で決まる（desktop の project で 375px に縮めた画面は 32 / 36 / 40px のまま）。
 */
export async function expectedControlHeight(page: Page, size: keyof typeof CONTROL_HEIGHT = "md") {
  const coarse = await page.evaluate(() => window.matchMedia("(pointer: coarse)").matches);
  return coarse ? 44 : CONTROL_HEIGHT[size];
}

/**
 * RAG 検索・チャットの「対象の業務ビュー」を 1 つ選ぶ（#635。共有の SearchableSelectField）。
 * ボタンを押して候補の一覧を開き、候補を選ぶ（選ぶと一覧は閉じてボタンへ戻る）。
 */
export async function selectBusinessView(page: Page, name: RegExp | string) {
  await page.getByRole("button", { name: /対象の業務ビュー/ }).click();
  await page.getByRole("listbox", { name: /対象の業務ビュー/ }).getByRole("option", { name }).click();
  await expect(page.getByRole("listbox", { name: /対象の業務ビュー/ })).toHaveCount(0);
}

/** RAG 検索の「LLM で回答を生成する」をオンにする（既定はオフで、検索結果までを出す。#649）。 */
export async function enableSearchAnswer(page: Page) {
  const toggle = page.getByRole("switch", { name: "LLM で回答を生成する" });
  if ((await toggle.getAttribute("aria-checked")) !== "true") await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
}

/**
 * チャットの会話の履歴を開き、その領域を返す（#664）。履歴は既定で閉じている。
 * lg 以上はチャットの左のパネル（`complementary`）、lg 未満はモーダルの side sheet（`dialog`）。
 */
export async function openChatHistory(page: Page) {
  const toggle = page.getByTestId("chat-history-toggle");
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const history = page.getByTestId("chat-history");
  await expect(history).toBeVisible();
  return history;
}
