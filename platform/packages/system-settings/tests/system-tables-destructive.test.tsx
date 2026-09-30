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
