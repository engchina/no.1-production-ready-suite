// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  SystemTablesCard,
  type SystemTablesApi,
  type SystemTablesCardProps,
  type SystemTablesConfirmRequest,
  type SystemTablesInitializeRequest,
  type SystemTablesStatusData,
  SYSTEM_TABLES_QUERY_KEY,
} from "../src";

// #619: データを消す未適用の migration は、「作成・更新」の前に確認ダイアログで承認させ、
// 承認したときだけ allow_destructive を送る。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const DESTRUCTIVE = {
  name: "20260930_005_retire_standard_engine_objects",
  description: "旧いテーブル rag_agent_memories を削除します。",
};

function statusData(overrides: Partial<SystemTablesStatusData> = {}): SystemTablesStatusData {
  return {
    status: "outdated",
    schema_head: DESTRUCTIVE.name,
    applied_versions: [],
    pending_versions: [DESTRUCTIVE.name],
    pending_destructive_migrations: [DESTRUCTIVE],
    expected_object_count: 3,
    existing_object_count: 3,
    expected_table_count: 2,
    existing_table_count: 2,
    missing_objects: [],
    tables: [],
    operation_state: {
      status: "idle",
      operation_kind: null,
      lease_expires_at: null,
      last_error_code: null,
      schema_epoch: 7,
      updated_at: null,
    },
    ...overrides,
  };
}

function apiWith(data: SystemTablesStatusData) {
  const initialize = vi.fn((_body: SystemTablesInitializeRequest) => new Promise<never>(() => undefined));
  const api: SystemTablesApi = {
    getSystemTablesStatus: () => Promise.resolve(data),
    initializeSystemTables: initialize,
  };
  return { api, initialize };
}

async function renderCard(props: Pick<SystemTablesCardProps, "api"> & Partial<SystemTablesCardProps>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SystemTablesCard
            canManage
            recreateConfirmation="RECREATE_DEMO_SYSTEM_TABLES"
            databaseRoutes={{ databaseSettings: "/settings/database", systemTables: "/settings/system-tables" }}
            {...props}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  // 状態の取得（Promise の解決）が描画に反映されるまで待つ。
  for (let attempt = 0; attempt < 50 && !host.querySelector("#system-tables button"); attempt += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return client;
}

function initializeButton(): HTMLButtonElement {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.trim() === "作成・更新");
  if (!button) throw new Error("作成・更新のボタンがありません");
  return button;
}

describe("データを消す未適用の migration（#619）", () => {
  it("警告を出し、確認ダイアログで承認したときだけ allow_destructive を送る", async () => {
    const { api, initialize } = apiWith(statusData());
    const requests: SystemTablesConfirmRequest[] = [];
    let answer = false;
    const confirmDestructiveMigrations = vi.fn(async (request: SystemTablesConfirmRequest) => {
      requests.push(request);
      return answer;
    });
    await renderCard({ api, confirmDestructiveMigrations });

    const banner = host.querySelector('[data-testid="system-tables-destructive-migrations"]');
    expect(banner?.textContent).toContain(DESTRUCTIVE.name);
    expect(banner?.textContent).toContain("rag_agent_memories を削除します");
    expect(banner?.textContent).toContain("削除したデータは元に戻せません");
    // 「無損失で更新できます」の案内は出さない。
    expect(host.textContent).not.toContain("無損失で更新できます");
    // 削除の警告は削除の Banner 1 か所だけ。状態の Banner を重ねて出さない（#2133）。
    expect(host.textContent).not.toContain("古くなっています");
    expect(host.textContent?.split("データを削除").length).toBe(2);

    await act(async () => initializeButton().click());
    expect(confirmDestructiveMigrations).toHaveBeenCalledTimes(1);
    expect(requests[0].title).toBe("データを削除する更新を実行しますか？");
    expect(requests[0].description).toContain(DESTRUCTIVE.name);
    expect(requests[0].confirmLabel).toBe("削除して更新");
    // 取り消したら送らない。
    expect(initialize).not.toHaveBeenCalled();

    answer = true;
    await act(async () => initializeButton().click());
    expect(initialize).toHaveBeenCalledTimes(1);
    expect(initialize.mock.calls[0][0]).toEqual({
      recreate: false,
      confirmation: undefined,
      allow_destructive: true,
    });
  });

  it("データを消す migration が無ければ確認せず、承認も送らない", async () => {
    const { api, initialize } = apiWith(
      statusData({ pending_destructive_migrations: [], pending_versions: ["20260930_004_other"] }),
    );
    const confirmDestructiveMigrations = vi.fn(async () => true);
    await renderCard({ api, confirmDestructiveMigrations });

    expect(host.querySelector('[data-testid="system-tables-destructive-migrations"]')).toBeNull();
    await act(async () => initializeButton().click());
    expect(confirmDestructiveMigrations).not.toHaveBeenCalled();
    expect(initialize.mock.calls[0][0]).toEqual({ recreate: false, confirmation: undefined });
  });

  it("確認ダイアログを渡さない製品は承認を送らない（backend が止める）", async () => {
    const { api, initialize } = apiWith(statusData());
    await renderCard({ api });

    expect(host.querySelector('[data-testid="system-tables-destructive-migrations"]')).not.toBeNull();
    await act(async () => initializeButton().click());
    expect(initialize.mock.calls[0][0]).toEqual({ recreate: false, confirmation: undefined });
  });
});

describe("「最新情報を取得」のスピナー（#819）", () => {
  function refreshButton(): HTMLButtonElement {
    const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.trim() === "最新情報を取得");
    if (!button) throw new Error("最新情報を取得のボタンがありません");
    return button;
  }

  it("親の画面の invalidate による取り直しでは回さず、押したときだけ回す", async () => {
    let calls = 0;
    const api: SystemTablesApi = {
      // 2 回目以降の取得は待ったままにする（取り直し中の表示を確かめる）。
      getSystemTablesStatus: () => {
        calls += 1;
        return calls === 1 ? Promise.resolve(statusData({ pending_destructive_migrations: [] })) : new Promise<never>(() => undefined);
      },
      initializeSystemTables: () => new Promise<never>(() => undefined),
    };
    const client = await renderCard({ api });
    expect(refreshButton().getAttribute("aria-busy")).toBeNull();

    // ADB の保存などは ["settings", "database"] の下をまとめて取り直す。押していないので回さない。
    await act(async () => {
      void client.invalidateQueries({ queryKey: SYSTEM_TABLES_QUERY_KEY });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(calls).toBe(2);
    expect(refreshButton().getAttribute("aria-busy")).toBeNull();
    // 押していない取り直し（定期・invalidate）は操作を止めない（#5278）。
    expect(initializeButton().disabled).toBe(false);

    await act(async () => {
      refreshButton().click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(refreshButton().getAttribute("aria-busy")).toBe("true");
    expect(initializeButton().getAttribute("aria-busy")).toBeNull();
  });
});

// #5183: カードの案内は、どの組み合わせでも 1 つの枠（CombinedBanner）にまとめる。
describe("カードの案内の枠は 1 つ（#5183）", () => {
  const FOREIGN_KEY = {
    name: "DEMO_ITEMS_PARENT_FK",
    table_name: "DEMO_ITEMS",
    columns: ["PARENT_ID"],
    referenced_table_name: "DEMO_PARENTS",
    referenced_columns: ["PARENT_ID"],
    delete_rule: "CASCADE",
    orphan_rows: 3,
  };
  const failedState = (code: string | null) => ({
    ...statusData().operation_state,
    status: "failed" as const,
    last_error_code: code,
  });

  function notices(): Element[] {
    return [...host.querySelectorAll('#system-tables [role="status"], #system-tables [role="alert"]')];
  }

  function onlyNotice(): Element {
    const found = notices();
    expect(found).toHaveLength(1);
    return found[0];
  }

  function itemOrder(notice: Element): string[] {
    return [...notice.querySelectorAll("[data-testid]")]
      .map((element) => element.getAttribute("data-testid") ?? "")
      .filter((id) => id.startsWith("system-tables-") && !id.includes("foreign-keys-") && !id.includes("columns"));
  }

  it("partial と削除の migration は、削除を見出しに 1 つの枠で出し、補うことと削除の明細と確認の案内を順に並べる", async () => {
    const { api } = apiWith(
      statusData({
        status: "partial",
        existing_object_count: 2,
        missing_objects: [
          { name: "DEMO_IDX", object_type: "INDEX" },
          { name: "DEMO_SEQ", object_type: "SEQUENCE" },
        ],
      }),
    );
    await renderCard({ api, confirmDestructiveMigrations: async () => false });
    const notice = onlyNotice();
    expect(notice.getAttribute("role")).toBe("status");
    const text = notice.textContent ?? "";
    // 見出しは最も重い事柄。StatusBadge の文字（一部不足）を繰り返さない。
    expect(text.startsWith("データを削除する更新があります")).toBe(true);
    expect(text).not.toContain("一部不足");
    const order = [
      "不足している必須オブジェクト 2 件を補い",
      DESTRUCTIVE.name,
      "削除したデータは元に戻せません",
      "「作成・更新」を押すと、削除の確認を求めます。",
    ].map((part) => text.indexOf(part));
    expect(order.every((index) => index >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    // 状態だけの案内（補完の文）を別に出さない。
    expect(text).not.toContain("既存データを保持したまま不足分を補います");
  });

  it("outdated と削除の migration は、1 つの枠で定義の更新と削除を伝える", async () => {
    const { api } = apiWith(statusData({ missing_columns: [{ table_name: "DEMO_ITEMS", column_name: "PAYLOAD" }] }));
    await renderCard({ api });
    const text = onlyNotice().textContent ?? "";
    expect(text.startsWith("データを削除する更新があります")).toBe(true);
    expect(text).toContain("「作成・更新」で定義を更新し、次の migration でテーブルや行を削除します。");
    expect(text).toContain("DEMO_ITEMS.PAYLOAD");
    expect(text).not.toContain("更新必要");
    // 確認ダイアログを渡さない製品には、確認の案内を出さない。
    expect(text).not.toContain("削除の確認を求めます");
  });

  it("missing は info の 1 つの枠にし、閲覧専用の案内も同じ枠に並べる", async () => {
    const { api } = apiWith(
      statusData({
        status: "missing",
        pending_destructive_migrations: [],
        existing_object_count: 0,
        existing_table_count: 0,
      }),
    );
    await renderCard({ api, canManage: false });
    const notice = onlyNotice();
    expect(notice.getAttribute("role")).toBe("status");
    expect(notice.getAttribute("data-surface-tint")).toBe("info");
    expect(notice.textContent?.startsWith("主要テーブルがまだありません")).toBe(true);
    expect(itemOrder(notice)).toEqual(["system-tables-schema-notice", "system-tables-read-only"]);
  });

  it("前回の失敗は danger にし、削除・参照先のない行・閲覧専用・製品の案内を同じ枠に重い順で並べる", async () => {
    const { api } = apiWith(
      statusData({
        status: "partial",
        missing_objects: [{ name: "DEMO_IDX", object_type: "INDEX" }],
        orphaned_foreign_keys: [FOREIGN_KEY],
        operation_state: failedState("ORA-00054"),
      }),
    );
    await renderCard({
      api,
      canManage: false,
      notices: [{ id: "product", severity: "warning", title: "製品の案内", testId: "system-tables-product-notice" }],
    });
    const notice = onlyNotice();
    expect(notice.getAttribute("role")).toBe("alert");
    expect(notice.getAttribute("data-surface-tint")).toBe("danger");
    expect(notice.textContent?.startsWith("前回の操作が完了していません")).toBe(true);
    expect(itemOrder(notice)).toEqual([
      "system-tables-previous-failure",
      "system-tables-destructive-migrations",
      "system-tables-orphaned-foreign-keys",
      "system-tables-product-notice",
      "system-tables-read-only",
    ]);
    expect(notice.textContent).toContain("エラーコード: ORA-00054");
  });

  it("ready で案内が無ければ枠を出さない", async () => {
    const { api } = apiWith(statusData({ status: "ready", pending_destructive_migrations: [], pending_versions: [] }));
    await renderCard({ api });
    expect(notices()).toHaveLength(0);
    expect(host.querySelector('[data-testid="system-tables-notice"]')).toBeNull();
  });

  it("状態を取得できないときは、DB の案内と製品の案内を 1 つの枠に並べる", async () => {
    const api: SystemTablesApi = {
      getSystemTablesStatus: () => Promise.reject(new Error("503")),
      initializeSystemTables: () => new Promise<never>(() => undefined),
    };
    await renderCard({
      api,
      notices: [{ id: "product", severity: "warning", title: "製品の案内", testId: "system-tables-product-notice" }],
    });
    for (let attempt = 0; attempt < 50 && !host.querySelector('[data-testid="database-gate-retry"]'); attempt += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
    const notice = onlyNotice();
    expect(notice.textContent).toContain("データベースに接続できません");
    expect(notice.textContent).toContain("製品の案内");
    expect(notice.querySelector('[data-testid="database-gate-retry"]')).not.toBeNull();
  });
});
