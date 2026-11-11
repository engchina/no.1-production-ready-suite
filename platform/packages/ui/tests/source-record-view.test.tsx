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
 it("原行列を表示し、選択した記録のセルを高亮する",()=>{act(()=>root.render(<SourceRecordView record={record} cited highlight fallback=""/>));expect(host.textContent).toContain("シート: シートA");expect(host.textContent).toContain("セル: A4:B4");expect(Array.from(host.querySelectorAll("mark")).map(node=>node.textContent)).toEqual(["同じ値😀","前\n後<script>"]);expect(host.querySelector("script")).toBeNull();expect(host.querySelector('th[scope="row"]')?.textContent).toBe("4");});
 it("引用が未確定ならセルの強調をしない",()=>{act(()=>root.render(<SourceRecordView record={record} cited highlight={false} fallback=""/>));expect(host.querySelectorAll("mark")).toHaveLength(0);});
 it("Wordの変換後の段落位置を原本の頁番号と区別する",()=>{act(()=>root.render(<SourceRecordView record={{source_type:"doc",location:{word_location_basis:"derived",word_paragraph_start:3},precision:"record",parts:[{kind:"text",text:"合成段落"}]}} cited highlight fallback=""/>));expect(host.textContent).toContain("変換後の文書の位置");expect(host.textContent).toContain("開始段落: 3");expect(host.querySelector("mark")?.textContent).toBe("合成段落");});
});
