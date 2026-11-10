// @vitest-environment happy-dom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DATA_LOAD_MAX_RETRIES,
  DATABASE_READ_FAILED_CODE,
  DATABASE_READ_TIMEOUT_CODE,
  DATABASE_UNAVAILABLE_CODE,
  DataLoadFailureState,
  StaleDataNotice,
  dataLoadFailureKind,
  dataLoadRetryDelay,
  dataLoadView,
  retryDataLoad,
  type DataLoadFailureLabels,
} from "../src";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mounted: { host: HTMLElement; root: Root }[] = [];

function mount(node: ReactNode): HTMLElement {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  mounted.push({ host, root });
  act(() => root.render(node));
  return host;
}

afterEach(() => {
  for (const { host, root } of mounted.splice(0)) {
    act(() => root.unmount());
    host.remove();
  }
  (document.activeElement as HTMLElement | null)?.blur?.();
});

function retryButton(host: HTMLElement): HTMLButtonElement {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.includes("再試行"));
  if (!button) throw new Error("再試行のボタンがありません");
  return button;
}

const databaseAction = <a href="/settings/database">データベース設定を開く</a>;

const LABELS: DataLoadFailureLabels = {
  timeout: "タイムアウトの文",
  unavailable: "接続不可の文",
  unavailableRestricted: "接続不可の文（管理者へ連絡）",
  retry: "再試行",
  staleTitle: (time) => `${time} 時点の内容`,
  staleOther: "読み取れませんでした",
};

function apiError(code: string | undefined, status = 503) {
  return Object.assign(new Error("backend の文"), { errorCode: code, status });
}

describe("dataLoadFailureKind", () => {
  it("原因は error_code で分け、文言を見ない", () => {
    expect(dataLoadFailureKind(apiError(DATABASE_READ_TIMEOUT_CODE))).toBe("timeout");
    expect(dataLoadFailureKind(apiError(DATABASE_UNAVAILABLE_CODE))).toBe("unavailable");
    expect(dataLoadFailureKind(apiError("oracle_connection_unavailable"))).toBe("unavailable");
    expect(dataLoadFailureKind(apiError("agent_storage_unavailable"))).toBe("unavailable");
    expect(dataLoadFailureKind(apiError(DATABASE_READ_FAILED_CODE))).toBe("other");
    expect(dataLoadFailureKind(new Error("データベースに接続できません"))).toBe("other");
    expect(dataLoadFailureKind(null)).toBe("other");
  });
});

describe("retryDataLoad / dataLoadRetryDelay", () => {
  it("回数に上限があり、その後は手動に任せる", () => {
    const error = apiError(DATABASE_READ_TIMEOUT_CODE);
    expect(retryDataLoad(0, error)).toBe(true);
    expect(retryDataLoad(DATA_LOAD_MAX_RETRIES - 1, error)).toBe(true);
    expect(retryDataLoad(DATA_LOAD_MAX_RETRIES, error)).toBe(false);
  });

  it("同じ結果になる 4xx は試さない（408 と 429 は試す）", () => {
    expect(retryDataLoad(0, apiError(undefined, 403))).toBe(false);
    expect(retryDataLoad(0, apiError(undefined, 404))).toBe(false);
    expect(retryDataLoad(0, apiError(undefined, 408))).toBe(true);
    expect(retryDataLoad(0, apiError(undefined, 429))).toBe(true);
    expect(retryDataLoad(0, new Error("応答なし"))).toBe(true);
  });

  it("DB の読み取りの失敗の後は間隔を広げて空ける", () => {
    const busy = apiError(DATABASE_READ_TIMEOUT_CODE);
    expect(dataLoadRetryDelay(0, busy)).toBe(5_000);
    expect(dataLoadRetryDelay(1, busy)).toBe(15_000);
    expect(dataLoadRetryDelay(5, busy)).toBe(15_000);
    expect(dataLoadRetryDelay(0, apiError(DATABASE_UNAVAILABLE_CODE))).toBe(5_000);
    expect(dataLoadRetryDelay(0, new Error("x"))).toBe(1_000);
  });
});

describe("dataLoadView", () => {
  it("読み取れていないときは 0 件と判断できない段階にし、作成を止める", () => {
    expect(dataLoadView({ data: undefined, isError: true })).toEqual({ phase: "failed", blockCreate: true });
    expect(dataLoadView({ data: undefined, isError: false })).toEqual({ phase: "loading", blockCreate: false });
  });

  it("自動の再試行を待つ間は読み込み中のまま作成を止める", () => {
    expect(dataLoadView({ data: undefined, isError: false, failureCount: 1 })).toEqual({
      phase: "loading",
      blockCreate: true,
    });
  });

  it("前回の内容があれば残す。前回が 0 件なら残すものが無いので失敗にする", () => {
    expect(dataLoadView({ data: { total: 3 }, isError: true })).toEqual({ phase: "stale", blockCreate: true });
    expect(dataLoadView({ data: { total: 0 }, isError: true }, { isEmpty: true })).toEqual({
      phase: "failed",
      blockCreate: true,
    });
  });

  it("読み取れたときだけ ready にする", () => {
    expect(dataLoadView({ data: { total: 0 }, isError: false }, { isEmpty: true })).toEqual({
      phase: "ready",
      blockCreate: false,
    });
  });
});

describe("DataLoadFailureState", () => {
  it("timeout は role=alert で事実を出し、再試行へフォーカスを移し、DB 設定の導線を出さない", () => {
    const onRetry = vi.fn();
    const host = mount(
      <DataLoadFailureState
        error={apiError(DATABASE_READ_TIMEOUT_CODE)}
        fallback="読み込めませんでした"
        labels={LABELS}
        onRetry={onRetry}
        databaseAction={databaseAction}
      />,
    );
    expect(host.querySelector("[role='alert']")?.textContent).toContain("タイムアウトの文");
    expect(host.querySelector("a")).toBeNull();
    const retry = retryButton(host);
    expect(document.activeElement).toBe(retry);
    act(() => retry.click());
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("接続の失敗は導線を再試行の前に出す", () => {
    const host = mount(
      <DataLoadFailureState
        error={apiError(DATABASE_UNAVAILABLE_CODE)}
        fallback="読み込めませんでした"
        labels={LABELS}
        onRetry={() => {}}
        databaseAction={databaseAction}
      />,
    );
    expect(host.querySelector("[role='alert']")?.textContent).toContain("接続不可の文");
    const link = host.querySelector("a");
    expect(link).not.toBeNull();
    expect(link!.compareDocumentPosition(retryButton(host)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("接続の失敗で導線が無い利用者には管理者への連絡を案内する", () => {
    const host = mount(
      <DataLoadFailureState
        error={apiError(DATABASE_UNAVAILABLE_CODE)}
        fallback="読み込めませんでした"
        labels={LABELS}
        onRetry={() => {}}
      />,
    );
    expect(host.querySelector("[role='alert']")?.textContent).toContain("管理者へ連絡");
    expect(host.querySelector("a")).toBeNull();
  });

  it("その他の失敗は接続できないと言わず、導線も出さない", () => {
    const host = mount(
      <DataLoadFailureState
        error={apiError(DATABASE_READ_FAILED_CODE, 500)}
        fallback="読み込めませんでした"
        labels={LABELS}
        onRetry={() => {}}
        databaseAction={databaseAction}
      />,
    );
    expect(host.querySelector("[role='alert']")?.textContent).not.toContain("接続不可");
    expect(host.querySelector("a")).toBeNull();
  });

  it("入力中の欄からフォーカスを奪わない", () => {
    const inputHost = mount(<input aria-label="検索" />);
    const input = inputHost.querySelector("input")!;
    input.focus();
    mount(
      <DataLoadFailureState
        error={apiError(DATABASE_READ_TIMEOUT_CODE)}
        fallback="読み込めませんでした"
        labels={LABELS}
        onRetry={() => {}}
      />,
    );
    expect(document.activeElement).toBe(input);
  });
});

describe("DataLoadFailureState layout=field（選択欄の候補。#4222）", () => {
  it("欄の位置の danger の Banner に同じ文・導線・再試行を出す", () => {
    const onRetry = vi.fn();
    const host = mount(
      <DataLoadFailureState
        error={apiError(DATABASE_UNAVAILABLE_CODE)}
        fallback="候補を読み込めませんでした"
        labels={LABELS}
        onRetry={onRetry}
        databaseAction={databaseAction}
        layout="field"
        testId="field-failure"
      />,
    );
    const root = host.querySelector("[data-testid='field-failure']")!;
    expect(root.getAttribute("data-failure-layout")).toBe("field");
    expect(root.getAttribute("data-failure-kind")).toBe("unavailable");
    expect(host.querySelector("[role='alert']")?.textContent).toContain("接続不可の文");
    const link = host.querySelector("a");
    expect(link).not.toBeNull();
    const retry = retryButton(host);
    expect(link!.compareDocumentPosition(retry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    act(() => retry.click());
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("その他の失敗は backend の文を出し、接続できないと言わず導線も出さない", () => {
    const host = mount(
      <DataLoadFailureState
        error={apiError(DATABASE_READ_FAILED_CODE, 500)}
        fallback="候補を読み込めませんでした。"
        labels={LABELS}
        onRetry={() => {}}
        databaseAction={databaseAction}
        layout="field"
      />,
    );
    expect(host.querySelector("[role='alert']")?.textContent).toContain("backend の文");
    expect(host.querySelector("[role='alert']")?.textContent).not.toContain("接続不可");
    expect(host.querySelector("a")).toBeNull();
  });

  it("ほかにフォーカスがあれば動かさない（欄の操作から奪わない）", () => {
    const buttonHost = mount(<button type="button">選択欄</button>);
    const button = buttonHost.querySelector("button")!;
    button.focus();
    mount(
      <DataLoadFailureState
        error={apiError(DATABASE_READ_TIMEOUT_CODE)}
        fallback="候補を読み込めませんでした"
        labels={LABELS}
        onRetry={() => {}}
        layout="field"
      />,
    );
    expect(document.activeElement).toBe(button);
  });
});

describe("StaleDataNotice", () => {
  it("いつの内容かと原因を出す。フォーカスのある要素からは動かさない", () => {
    const rowHost = mount(<button type="button">行の操作</button>);
    const row = rowHost.querySelector("button")!;
    row.focus();
    const host = mount(
      <StaleDataNotice
        error={apiError(DATABASE_READ_TIMEOUT_CODE)}
        updatedAt="2026/11/03 10:00"
        labels={LABELS}
        onRetry={() => {}}
      />,
    );
    const notice = host.querySelector("[role='status']");
    expect(notice?.textContent).toContain("2026/11/03 10:00 時点の内容");
    expect(notice?.textContent).toContain("タイムアウトの文");
    expect(document.activeElement).toBe(row);
  });

  it("何にもフォーカスが無ければ再試行へ移す", () => {
    const host = mount(
      <StaleDataNotice
        error={apiError(DATABASE_READ_TIMEOUT_CODE)}
        updatedAt="2026/11/03 10:00"
        labels={LABELS}
        onRetry={() => {}}
      />,
    );
    expect(document.activeElement).toBe(retryButton(host));
  });
});
