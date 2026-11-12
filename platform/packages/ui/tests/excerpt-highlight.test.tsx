// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ExcerptContent } from "../src/components/data/excerpt-content";
(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement, root: Root;
beforeEach(() => {host = document.createElement("div"); document.body.append(host); root = createRoot(host);});
afterEach(() => {act(() => root.unmount()); host.remove();});
const table = '<table><tr><th>項目</th><th>値</th></tr><tr><td>合成A</td><td>同じ値😀 &amp; 前<br>後</td></tr><tr><td>合成B</td><td>同じ値😀 &amp; 前<br>後</td></tr></table>';
const start = Array.from(table.slice(0,table.lastIndexOf('同じ値'))).length;
describe("表の保存範囲の強調", () => {
  it("同じ値の2つ目のセルだけを強調し、改行と実体参照を保つ", () => {
    act(() => root.render(<ExcerptContent text={table} highlights={[[start,start+3]]}/>));
    expect([...host.querySelectorAll("mark")].map(el => el.textContent)).toEqual(["同じ値😀 & 前\n後"]);
    expect(host.querySelectorAll("table")).toHaveLength(1);
    expect(host.querySelectorAll("td")[1].querySelector("mark")).toBeNull();
  });
  it("保存範囲がなければ既存どおり強調しない", () => {
    act(() => root.render(<ExcerptContent text={table} highlights={[]}/>));
    expect(host.querySelector("mark")).toBeNull();
  });
  it("原文から外れた座標は強調しない", () => {
    act(() => root.render(<ExcerptContent text={table} highlights={[[-1,3],[0,100000]]}/>));
    expect(host.querySelector("mark")).toBeNull();
  });
  it("タグ属性の範囲だけでは表示セルを強調しない", () => {
    const text='<table><tr><th>項目</th></tr><tr><td title="target">値</td></tr></table>';
    const begin=Array.from(text.slice(0,text.indexOf("target"))).length;
    act(() => root.render(<ExcerptContent text={text} highlights={[[begin,begin+6]]}/>));
    expect(host.querySelector("mark")).toBeNull();
  });
  it("エスケープしたタグはセルの文字として強調し、実行しない", () => {
    const text='<table><tr><th>項目</th></tr><tr><td>&lt;script&gt;合成&lt;/script&gt;</td></tr></table>';
    const begin=Array.from(text.slice(0,text.indexOf("&lt;script"))).length;
    act(() => root.render(<ExcerptContent text={text} highlights={[[begin,begin+4]]}/>));
    expect(host.querySelector("script")).toBeNull();
    expect(host.querySelector("mark")?.textContent).toBe("<script>合成</script>");
  });
});

describe("解析済みの縦線表と引用座標", () => {
  const pipe = '| 項目 | 値 |\n| 合成A | 同じ値😀 & 前 後 |\n| 合成B | 同じ値😀 & 前 後 |';
  it("実解析と同じ表で2つ目の重複値だけを強調する", () => {
    const begin=Array.from(pipe.slice(0,pipe.lastIndexOf('同じ値'))).length;
    act(() => root.render(<ExcerptContent text={pipe} contentKind="table" highlights={[[begin,begin+3]]}/>));
    expect(host.querySelectorAll('table')).toHaveLength(1);
    expect([...host.querySelectorAll('mark')].map(el=>el.textContent)).toEqual(['同じ値😀 & 前 後']);
    expect(host.querySelectorAll('tr')[1].querySelector('mark')).toBeNull();
  });
  it("行の途中から始まる断片は原文の文字範囲を保って強調する", () => {
    const text='途中😀 | 値 |\n| 合成B | 続き |';
    act(()=>root.render(<ExcerptContent text={text} contentKind="table" highlights={[[2,3]]}/>));
    expect(host.querySelector('table')).toBeNull();
    expect(host.querySelector('mark')?.textContent).toBe('😀');
    expect(host.textContent).toContain(text);
  });
  it("表と宣言していない本文の縦線は表にしない", () => {
    act(()=>root.render(<ExcerptContent text={pipe} highlights={[[0,5]]}/>));
    expect(host.querySelector('table')).toBeNull();
    expect(host.querySelector('mark')).toBeNull();
  });
  it("区切り行、CRLF、空セル、escapeした縦線を元の範囲で読む", () => {
    const text='| 項目 | 値 |\r\n| :--- | ---: |\r\n| A |  |\r\n| B | 😀\\|値 |';
    const begin=Array.from(text.slice(0,text.indexOf('😀'))).length;
    act(()=>root.render(<ExcerptContent text={text} contentKind="table" highlights={[[begin,begin+1]]}/>));
    expect(host.querySelectorAll('table')).toHaveLength(1);
    expect(host.querySelectorAll('tr')).toHaveLength(3);
    expect(host.querySelector('mark')?.textContent).toBe('😀|値');
  });
  it("列数が不揃いの行はセルを捨てず原文へ戻す", () => {
    const text='| A | B |\n| C | D | E |';
    act(()=>root.render(<ExcerptContent text={text} contentKind="table" highlights={[[0,3]]}/>));
    expect(host.querySelector('table')).toBeNull();
    expect(host.textContent).toContain(text);
    expect(host.querySelector('mark')?.textContent).toBe('| A');
  });
});

it("表として保存された1列も本文と区別して描画する",()=>{
 const text="| 見出し |\n| 合成😀 |";
 const begin=Array.from(text.slice(0,text.indexOf("合成"))).length;
 act(()=>root.render(<ExcerptContent text={text} contentKind="table" highlights={[[begin,begin+2]]}/>));
 expect(host.querySelectorAll("table")).toHaveLength(1);
 expect(host.querySelector("mark")?.textContent).toBe("合成😀");
});
