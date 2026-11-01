// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { OriginalSourcePanel, type OriginalSourceWindow } from "../src";

(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT = true;
const settle = () => new Promise(resolve => setTimeout(resolve,0));
const data: OriginalSourceWindow = {status:"exact",revision:"ex1",element_id:"e1",variant:"original",pointer:"/nodes/1",source_count:2,source_index:0,text:"😀引用本文",highlights:[[1,3]],coordinate_unit:"unicode_codepoint",next_offset:50};

it("原本は展開時だけ取得し、次のノードと部分読み取りに版を渡す", async () => {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  const load = vi.fn().mockResolvedValue(data);
  await act(async () => {root.render(<OriginalSourcePanel readingKey="d1" elementId="e1" revision="ex1" loadWindow={load} />); await settle();});
  expect(load).not.toHaveBeenCalled();
  await act(async () => {(host.querySelector("summary") as HTMLElement).click(); await settle();});
  expect(load.mock.calls[0][0]).toMatchObject({element_id:"e1",expected_revision:"ex1"});
  expect(host.querySelector("mark")?.textContent).toBe("引用");
  const button = (text:string) => Array.from(host.querySelectorAll("button")).find(button => button.textContent?.includes(text))!;
  const text = host.querySelector("pre")!; text.scrollTop = 100;
  await act(async () => {button("次の原本ノード").click(); await settle();});
  expect(text.scrollTop).toBe(0);
  expect(load.mock.calls.at(-1)?.[0]).toMatchObject({source_index:1,expected_revision:"ex1"});
  await act(async () => {button("後の部分を読む").click(); await settle();});
  expect(load.mock.calls.at(-1)?.[0]).toMatchObject({offset:50,source_index:0,expected_revision:"ex1"});
  await act(async () => root.unmount()); host.remove();
});

it("閉じると進行中の取得を中断する", async () => {
  const host = document.createElement("div"); const root = createRoot(host); let signal:AbortSignal | undefined;
  const load = vi.fn((_input:unknown,next:AbortSignal) => {signal=next; return new Promise<OriginalSourceWindow>(() => {});});
  await act(async () => root.render(<OriginalSourcePanel readingKey="d1" elementId="e1" revision="ex1" loadWindow={load} />));
  await act(async () => {(host.querySelector("summary") as HTMLElement).click(); await settle();});
  await act(async () => {(host.querySelector("summary") as HTMLElement).click(); await settle();});
  expect(signal?.aborted).toBe(true);
  await act(async () => root.unmount());
});

it("旧記録と版不一致を確定した引用に見せず、本文を空のまま説明する", async () => {
  for (const status of ["unrecorded","version_mismatch"] as const) {
    const host=document.createElement("div"); const root=createRoot(host);
    const load=vi.fn().mockResolvedValue({...data,status,text:"",highlights:[]});
    await act(async () => root.render(<OriginalSourcePanel readingKey="d1" elementId="e1" revision="ex1" loadWindow={load} />));
    await act(async () => {(host.querySelector("summary") as HTMLElement).click(); await settle();});
    expect(host.querySelector("pre")).toBeNull(); expect(host.querySelector('[role="status"]')).not.toBeNull();
    await act(async () => root.unmount());
  }
});

it("取得失敗でも本文を保持し、失敗した同じノードを再試行する", async () => {
  const host=document.createElement("div"); document.body.append(host); const root=createRoot(host);
  const load=vi.fn().mockResolvedValueOnce(data).mockRejectedValueOnce(new Error("合成の取得失敗")).mockResolvedValueOnce({...data,source_index:1,text:"次の原本",highlights:[]});
  await act(async () => root.render(<OriginalSourcePanel readingKey="d1" elementId="e1" revision="ex1" loadWindow={load} />));
  await act(async () => {(host.querySelector("summary") as HTMLElement).click(); await settle();});
  const button=(text:string)=>Array.from(host.querySelectorAll("button")).find(button=>button.textContent?.includes(text))!;
  const text=host.querySelector("pre")!; text.scrollTop=77;
  await act(async () => {button("次の原本ノード").click(); await settle();});
  expect(text.scrollTop).toBe(77);
  expect(host.textContent).toContain("😀引用本文"); expect(host.textContent).toContain("合成の取得失敗");
  await act(async () => {button("再試行").click(); await settle();});
  expect(load.mock.calls.at(-1)?.[0]).toMatchObject({source_index:1,expected_revision:"ex1"});
  expect(host.textContent).toContain("次の原本");
  await act(async () => root.unmount()); host.remove();
});
