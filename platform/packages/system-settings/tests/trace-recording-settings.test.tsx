// @vitest-environment happy-dom
import { ConfirmProvider } from "@production-ready/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { TraceRecordingSettingsPage } from "../src";

(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true;
it("既定は無効で、本文とメタデータの範囲を説明し、保存した値を反映する", async()=>{
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const update=vi.fn(async ({enabled}:{enabled:boolean})=>({enabled}));
 try {
  await act(async()=>root.render(<MemoryRouter><QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><ConfirmProvider><TraceRecordingSettingsPage api={{get:async()=>({enabled:false}),update}} /></ConfirmProvider></QueryClientProvider></MemoryRouter>));
  for(let i=0;i<10;i++) await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});
  const toggle=host.querySelector<HTMLButtonElement>('[role="switch"]')!;
  expect(toggle.getAttribute("aria-checked")).toBe("false");expect(host.textContent).toContain("Agent・RAG・NL2SQL");expect(host.textContent).toContain("embedding");expect(host.textContent).toContain("削除しません");
  await act(async()=>toggle.click());
  await act(async()=>host.querySelector("form")!.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
  expect(update).toHaveBeenCalledWith({enabled:true});
 }finally{act(()=>root.unmount());host.remove();}
});
