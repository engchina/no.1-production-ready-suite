// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { DATABASE_STATUS_QUERY_KEY, DatabaseGate, type DatabaseStatusApi } from "../src";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
let client: QueryClient;
const ok = { status: "ok" as const, check: "ok", context_id: "context-a" };

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  client.clear();
  host.remove();
  vi.useRealTimers();
});
async function flush(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}
async function mount(getDatabaseStatus: DatabaseStatusApi["getDatabaseStatus"]) {
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/work"]}>
        <DatabaseGate api={{ getDatabaseStatus }} routes={{ databaseSettings: "/settings/database" }}>
          <input aria-label="編集中の質問" defaultValue="未送信の質問" />
        </DatabaseGate>
      </MemoryRouter>
    </QueryClientProvider>,
  ));
  await flush();
}

it("確認済み画面の通信断を2回まで自動再試行し、入力ノードと値を保って復帰する", async () => {
  client.setQueryData(DATABASE_STATUS_QUERY_KEY, ok);
  const get = vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValue(ok);
  await mount(get);
  const input = host.querySelector("input")!;
  input.value = "編集を続けた質問";
  input.focus();
  void client.invalidateQueries({ queryKey: DATABASE_STATUS_QUERY_KEY });
  await flush();
  expect(get).toHaveBeenCalledTimes(1);
  expect(host.querySelector("input")).toBe(input);
  await flush(1000);
  expect(get).toHaveBeenCalledTimes(2);
  expect(host.querySelector("input")).toBe(input);
  await flush(2001);
  expect(get).toHaveBeenCalledTimes(3);
  expect(client.getQueryState(DATABASE_STATUS_QUERY_KEY)?.status).toBe("success");
  expect(host.querySelector("input")).toBe(input);
  expect(input.value).toBe("編集を続けた質問");
  expect(document.activeElement).toBe(input);
  // 直後に明示的な DB 不通が確認された場合は、正常 cache より新しい応答を優先する。
  get.mockResolvedValue({ status: "unreachable", check: "ok" });
  void client.invalidateQueries({ queryKey: DATABASE_STATUS_QUERY_KEY });
  await flush();
  await flush();
  expect(host.querySelector("input")).toBeNull();
  expect(host.textContent).toContain("データベースに接続できません");
});

it("初回失敗は有限回で止まり、手動の再試行で復帰する", async () => {
  const get = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
  await mount(get);
  await flush(1000);
  await flush(2001);
  await flush();
  expect(get).toHaveBeenCalledTimes(3);
  expect(host.querySelector("input")).toBeNull();
  expect(host.textContent).toContain("データベースの状態を確認できません");
  await flush(20000);
  expect(get).toHaveBeenCalledTimes(3);
  get.mockResolvedValue(ok);
  await act(async () => host.querySelector("button")!.click());
  await flush();
  expect(host.querySelector("input")).not.toBeNull();
});

it("権限エラーを自動再試行しない", async () => {
  const get = vi.fn().mockRejectedValue(Object.assign(new Error("Forbidden"), { status: 403 }));
  await mount(get);
  await flush(10000);
  expect(get).toHaveBeenCalledTimes(1);
});
