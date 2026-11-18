// @vitest-environment happy-dom
import { act } from "react";
import {createRoot, type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it} from "vitest";
import {SourceRecordView,type SourceRecordViewData} from "../src/components/document-preview/source-record-view";
(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true;
let host:HTMLDivElement,root:Root;
beforeEach(()=>{host=document.createElement("div");document.body.append(host);root=createRoot(host);});
afterEach(()=>{act(()=>root.unmount());host.remove();});
const record:SourceRecordViewData={source_type:"xlsx",location:{sheet_name:"シートA",row_start:4,cell_range:"A4:B4"},precision:"record",parts:[{kind:"table",columns:["項目","備考"],rows:[{label:"4",cells:["同じ値😀","前\n後<script>"]}]}]};
describe("SourceRecordView",()=>{
 it("引用した記録は項目と値で縦に並べ、記録の枠ごと強調する（#5415）",()=>{act(()=>root.render(<SourceRecordView record={record} cited highlight fallback=""/>));expect(host.textContent).toContain("シート: シートA");expect(host.textContent).toContain("セル: A4:B4");const cited=host.querySelector<HTMLElement>("[data-source-record-cited]")!;expect(cited.className).toContain("border-l-4");expect(cited.textContent).toContain("行 4");expect(Array.from(cited.querySelectorAll("dt")).map(node=>node.textContent)).toEqual(["項目","備考"]);expect(Array.from(cited.querySelectorAll("dd")).map(node=>node.textContent)).toEqual(["同じ値😀","前\n後<script>"]);expect(host.querySelector("table")).toBeNull();expect(host.querySelectorAll("mark")).toHaveLength(0);expect(host.querySelector("script")).toBeNull();});
 it("引用が未確定なら記録の枠を強調しない。空の値は「—」",()=>{act(()=>root.render(<SourceRecordView record={{...record,parts:[{kind:"table",columns:["項目","備考"],rows:[{label:"4",cells:["同じ値😀",""]}]}]}} cited highlight={false} fallback=""/>));expect(host.querySelector<HTMLElement>("[data-source-record-cited]")!.className).not.toContain("border-l-4");expect(Array.from(host.querySelectorAll("dd")).map(node=>node.textContent)).toEqual(["同じ値😀","—"]);});
 it("前後の記録は表のまま、セルを上にそろえ、長い値の列を狭く折り返さない（#5415）",()=>{const long="合成の長い備考です。".repeat(5);act(()=>root.render(<SourceRecordView record={{...record,parts:[{kind:"table",columns:["項目","備考"],rows:[{label:"5",cells:["短い値",long]}]}]}} cited={false} highlight fallback=""/>));expect(host.querySelector('th[scope="row"]')?.textContent).toBe("5");const cells=Array.from(host.querySelectorAll("tbody td"));expect(cells.every(cell=>cell.className.includes("align-top"))).toBe(true);expect(cells.map(cell=>cell.className.includes("min-w-[20rem]"))).toEqual([false,true]);expect(host.querySelectorAll("mark")).toHaveLength(0);});
 it("Wordの変換後の段落位置を原本の頁番号と区別する",()=>{act(()=>root.render(<SourceRecordView record={{source_type:"doc",location:{word_location_basis:"derived",word_paragraph_start:3},precision:"record",parts:[{kind:"text",text:"合成段落"}]}} cited highlight fallback=""/>));expect(host.textContent).toContain("変換後の文書の位置");expect(host.textContent).toContain("開始段落: 3");expect(host.querySelector("mark")?.textContent).toBe("合成段落");});
});
