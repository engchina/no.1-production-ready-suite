import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  WORKSPACE_NAMESPACE,
  WORKSPACE_TTL_MS,
  bindWorkspaceOwner,
  clearWorkspace,
  isOneOf,
  readWorkspace,
  removeWorkspace,
  writeWorkspace,
} from "./workspace-state";

class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length() {
    return this.map.size;
  }
  clear() {
    this.map.clear();
  }
  getItem(key: string) {
    return this.map.get(key) ?? null;
  }
  key(index: number) {
    return [...this.map.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.map.delete(key);
  }
  setItem(key: string, value: string) {
    this.map.set(key, value);
  }
}

let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  vi.stubGlobal("window", { sessionStorage: storage });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("workspace-state", () => {
  it("namespace 付きで保存し、同じ値を読み戻す", () => {
    expect(writeWorkspace("search.query", "契約の解約条件")).toBe(true);
    expect(storage.getItem(`${WORKSPACE_NAMESPACE}search.query`)).toContain("契約の解約条件");
    expect(readWorkspace("search.query", "")).toBe("契約の解約条件");
  });

  it("scope ごとに別の値を持ち、削除できる", () => {
    writeWorkspace("businessViews.draft", { name: "a" }, "bv-1");
    writeWorkspace("businessViews.draft", { name: "b" }, "new");
    expect(readWorkspace("businessViews.draft", { name: "" }, undefined, "bv-1")).toEqual({ name: "a" });
    removeWorkspace("businessViews.draft", "bv-1");
    expect(readWorkspace("businessViews.draft", { name: "" }, undefined, "bv-1")).toEqual({ name: "" });
    expect(readWorkspace("businessViews.draft", { name: "" }, undefined, "new")).toEqual({ name: "b" });
  });

  it("期限切れの値は初期値に戻して消す", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-01T00:00:00Z"));
    writeWorkspace("search.query", "old");
    vi.setSystemTime(Date.now() + WORKSPACE_TTL_MS + 1);
    expect(readWorkspace("search.query", "")).toBe("");
    expect(storage.getItem(`${WORKSPACE_NAMESPACE}search.query`)).toBeNull();
  });

  it("型が合わない値・壊れた JSON は初期値に戻す", () => {
    writeWorkspace("search.topK", 20);
    expect(readWorkspace("search.topK", "20", isOneOf(["5", "10", "20"] as const))).toBe("20");
    storage.setItem(`${WORKSPACE_NAMESPACE}search.query`, "{broken");
    expect(readWorkspace("search.query", "fallback")).toBe("fallback");
    writeWorkspace("search.businessViewIds", [1, 2]);
    expect(readWorkspace<string[]>("search.businessViewIds", [])).toEqual([]);
  });

  it("上限を超える値は保存せず false を返す", () => {
    expect(writeWorkspace("evaluation.requestJson", "x".repeat(200_001))).toBe(false);
    expect(readWorkspace("evaluation.requestJson", "sample")).toBe("sample");
  });

  it("storage が使えない環境では初期値で動き、保存は false", () => {
    vi.stubGlobal("window", {
      get sessionStorage(): Storage {
        throw new Error("SecurityError");
      },
    });
    expect(writeWorkspace("search.query", "q")).toBe(false);
    expect(readWorkspace("search.query", "init")).toBe("init");
  });

  it("別ユーザーに結び付けると以前の作業状態を消し、namespace 外は残す", () => {
    storage.setItem("other-app", "keep");
    bindWorkspaceOwner("user-a");
    writeWorkspace("chat.composer", "下書き");
    bindWorkspaceOwner("user-a");
    expect(readWorkspace("chat.composer", "")).toBe("下書き");
    bindWorkspaceOwner("user-b");
    expect(readWorkspace("chat.composer", "")).toBe("");
    expect(storage.getItem("other-app")).toBe("keep");
  });

  it("clearWorkspace は namespace 配下だけを消す", () => {
    storage.setItem("other-app", "keep");
    writeWorkspace("fileList.view", { filter: "ALL" });
    clearWorkspace();
    expect(storage.getItem(`${WORKSPACE_NAMESPACE}fileList.view`)).toBeNull();
    expect(storage.getItem("other-app")).toBe("keep");
  });
});
