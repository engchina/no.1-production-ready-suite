// @vitest-environment happy-dom
import { ConfirmProvider } from "@production-ready/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { GuardrailSettingsPage, type GuardrailSettingsData } from "../src";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  for (let i = 0; i < 10; i++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
};

async function render(get: () => Promise<GuardrailSettingsData>, update = vi.fn()) {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ConfirmProvider><GuardrailSettingsPage api={{ get, update }} /></ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  ));
  await flush();
  return { host, update, cleanup: () => { act(() => root.unmount()); host.remove(); } };
}

const off: GuardrailSettingsData = { input_enabled: false, output_enabled: false, oci_configured: true, oci_warning_code: null };

it("既定は両方とも未選択で、入力だけを選んで保存できる", async () => {
  const update = vi.fn(async (payload: { input_enabled: boolean; output_enabled: boolean }) => ({ ...off, ...payload }));
  const { host, cleanup } = await render(async () => off, update);
  try {
    const input = host.querySelector<HTMLInputElement>('input[aria-label="入力を検査する"]')!;
    expect(input.checked).toBe(false);
    expect(host.querySelector<HTMLInputElement>('input[aria-label="出力を検査する"]')!.checked).toBe(false);
    await act(async () => input.click());
    expect(host.textContent).toContain("未保存の変更があります。");
    const save = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("保存"))!;
    await act(async () => save.click());
    await flush();
    expect(update).toHaveBeenCalledWith({ input_enabled: true, output_enabled: false });
  } finally { cleanup(); }
});

it("有効にしようとして OCI の compartment が無ければ注意と OCI 認証設定へのリンクを出す", async () => {
  const { host, cleanup } = await render(async () => ({ ...off, oci_configured: false, oci_warning_code: "oci_guardrails_compartment_missing" }));
  try {
    expect(host.textContent).not.toContain("compartment が未設定");
    await act(async () => host.querySelector<HTMLInputElement>('input[aria-label="出力を検査する"]')!.click());
    expect(host.textContent).toContain("compartment が未設定");
    expect(host.querySelector('a[href="/settings/oci"]')).not.toBeNull();
  } finally { cleanup(); }
});

it("取得失敗から再試行できる", async () => {
  const get = vi.fn().mockRejectedValueOnce(new Error("synthetic")).mockResolvedValue(off);
  const { host, cleanup } = await render(get);
  try {
    expect(host.textContent).toContain("安全チェックの設定を取得できませんでした。");
    const retry = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("再試行"))!;
    await act(async () => retry.click());
    await flush();
    expect(host.querySelector('input[aria-label="入力を検査する"]')).not.toBeNull();
  } finally { cleanup(); }
});
