// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const toastSuccess = vi.hoisted(() => vi.fn());
vi.mock("@production-ready/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@production-ready/ui")>();
  return { ...actual, toast: { ...actual.toast, success: toastSuccess } };
});

import {
  SystemTablesCard,
  SYSTEM_TABLES_QUERY_KEY,
  type SystemTablesApi,
  type SystemTablesCardProps,
  type SystemTablesInitializeRequest,
  type SystemTablesStatusData,
} from "../src";

// #5278: 作成・更新は後台の操作。誰が始めた操作でも、操作中は「作成・更新」を回し（押せない・フォーカスを
// 外さない）、終われば元に戻して結果を知らせる。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  toastSuccess.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function statusData(overrides: Partial<SystemTablesStatusData> = {}): SystemTablesStatusData {
  return {
    status: "outdated",
    schema_head: 3,
    applied_versions: [1, 2],
    pending_versions: [3],
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

function running(kind: "initialize" | "recreate" = "initialize", epoch = 7): SystemTablesStatusData {
  return statusData({
    operation_state: {
      status: "running",
      operation_kind: kind,
      lease_expires_at: new Date(Date.now() + 900_000).toISOString(),
      started_at: new Date(Date.now() - 5_000).toISOString(),
      last_error_code: null,
      schema_epoch: epoch,
      updated_at: null,
    },
  });
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderCard(api: SystemTablesApi, props: Partial<SystemTablesCardProps> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SystemTablesCard
            api={api}
            canManage
            recreateConfirmation="RECREATE_DEMO_SYSTEM_TABLES"
            databaseRoutes={{ databaseSettings: "/settings/database", systemTables: "/settings/system-tables" }}
            {...props}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  for (let attempt = 0; attempt < 50 && !host.querySelector("#system-tables button"); attempt += 1) await flush();
  return client;
}

function initializeButton(): HTMLButtonElement {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.trim() === "作成・更新");
  if (!button) throw new Error("作成・更新のボタンがありません");
  return button;
}

describe("後台の作成・更新（#5278）", () => {
  it("押した「作成・更新」は開始の要求の間も操作中も回り続け、フォーカスを保ち、終われば戻って結果を知らせる", async () => {
    let resolveStart: (value: SystemTablesStatusData) => void = () => undefined;
    const initialize = vi.fn(
      (_body: SystemTablesInitializeRequest) =>
        new Promise<SystemTablesStatusData>((resolve) => {
          resolveStart = resolve;
        }),
    );
    const product = ["product", "cache"] as const;
    const client = await renderCard(
      { getSystemTablesStatus: () => Promise.resolve(statusData()), initializeSystemTables: initialize },
      { invalidateQueryKeys: [product] },
    );
    const invalidate = vi.spyOn(client, "invalidateQueries");

    initializeButton().focus();
    await act(async () => initializeButton().click());
    await flush();
    expect(initialize).toHaveBeenCalledTimes(1);
    // 開始の要求の間: loading（aria-busy・aria-disabled）で、ネイティブの disabled は付けない。
    expect(initializeButton().getAttribute("aria-busy")).toBe("true");
    expect(initializeButton().disabled).toBe(false);
    expect(document.activeElement).toBe(initializeButton());

    // backend は lease を取ったら 202 と running の状態を返す。
    await act(async () => resolveStart(running()));
    await flush();
    expect(initializeButton().getAttribute("aria-busy")).toBe("true");
    expect(initializeButton().disabled).toBe(false);
    expect(document.activeElement).toBe(initializeButton());
    expect(host.querySelector('[data-testid="system-tables-operation-progress"]')?.textContent).toMatch(
      /^作成・更新を実行中（開始 \d{2}:\d{2}・経過 1 分未満）$/,
    );
    expect(toastSuccess).not.toHaveBeenCalled();

    // 状態の polling が終わり（idle・epoch が増えた）を見る。
    await act(async () => {
      client.setQueryData(SYSTEM_TABLES_QUERY_KEY, statusData({ status: "ready", pending_versions: [] , operation_state: { ...statusData().operation_state, schema_epoch: 8 } }));
    });
    await flush();
    expect(initializeButton().getAttribute("aria-busy")).toBeNull();
    expect(host.querySelector('[data-testid="system-tables-operation-progress"]')).toBeNull();
    expect(toastSuccess).toHaveBeenCalledWith("システムテーブルを更新しました。");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["ready", "database"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: product });
  });

  it("外で始まった操作も回して見せ、終われば同じプロセスの要約で再起動の案内を出す（Agent。#1844）", async () => {
    let current = running("initialize", 7);
    const client = await renderCard({
      getSystemTablesStatus: () => Promise.resolve(current),
      initializeSystemTables: () => new Promise<never>(() => undefined),
    });
    expect(initializeButton().getAttribute("aria-busy")).toBe("true");

    current = statusData({
      status: "ready",
      operation_state: { ...statusData().operation_state, schema_epoch: 8 },
      last_operation: {
        operation: "migrated",
        schema_epoch: 8,
        restart_required: { message: "再起動してください。", processes: ["dispatcher"] },
      },
    });
    await act(async () => {
      await client.refetchQueries({ queryKey: SYSTEM_TABLES_QUERY_KEY });
    });
    await flush();
    expect(initializeButton().getAttribute("aria-busy")).toBeNull();
    const notice = host.querySelector('[data-testid="system-tables-restart-required"]');
    expect(notice?.textContent).toContain("dispatcher");
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("変更なしで終わった操作は「変更はありません」、失敗は案内の枠に任せて Toast を出さない", async () => {
    const client = await renderCard({
      getSystemTablesStatus: () => Promise.resolve(running("initialize", 7)),
      initializeSystemTables: () => new Promise<never>(() => undefined),
    });
    await act(async () => {
      client.setQueryData(SYSTEM_TABLES_QUERY_KEY, statusData({ status: "ready" }));
    });
    await flush();
    expect(toastSuccess).toHaveBeenCalledWith("システムテーブルは最新です。変更はありません。");

    await act(async () => {
      client.setQueryData(SYSTEM_TABLES_QUERY_KEY, running("initialize", 7));
    });
    await flush();
    await act(async () => {
      client.setQueryData(
        SYSTEM_TABLES_QUERY_KEY,
        statusData({ operation_state: { ...statusData().operation_state, status: "failed", last_error_code: "ORA-00600" } }),
      );
    });
    await flush();
    expect(toastSuccess).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[data-testid="system-tables-previous-failure"]')).not.toBeNull();
  });
});
