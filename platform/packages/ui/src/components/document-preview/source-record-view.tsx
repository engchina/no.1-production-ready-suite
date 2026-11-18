import { INFORMATION_TABLE_FIXED_VISIBLE_ROWS } from "../../lib/list-density";
import { DataTable, type DataTableColumn } from "../data/data-table";
import { RunFactList } from "../data/run-detail";
import { excerptPlainText } from "../../lib/excerpt-text";
import { resolveContentLang } from "../../lib/content-lang";
import { CodepointHighlight } from "./codepoint-highlight";

export interface SourceRecordViewData {
  source_type: string;
  location: Record<string, string | number | boolean | null>;
  precision: "record";
  parts: ({kind:"text";text:string} | {kind:"table";columns:string[];rows:{label:string;cells:string[]}[]})[];
}

const labels: Record<string,string> = {
  sheet_name:"シート",row_start:"開始行",row_end:"終了行",cell_range:"セル",
  source_line_start:"原本の開始行",source_line_end:"原本の終了行",table_row_start:"記録の開始行",table_row_end:"記録の終了行",
  word_paragraph_start:"開始段落",word_paragraph_end:"終了段落",word_body_start:"開始ブロック",word_body_end:"終了ブロック",word_table_indices:"表",
};

/** 変換器の原生位置を表示する。原Officeのページ版式や推測のbboxは作らない。 */
export function SourceRecordView({record,cited,highlight,fallback}:{record:SourceRecordViewData;cited:boolean;highlight:boolean;fallback:string}) {
  const mark=(text:string) => highlight && cited && text ? <CodepointHighlight text={text} ranges={[[0,Array.from(text).length]]}/> : text;
  return <div className="min-w-0 space-y-2" data-source-record="">
    <p className="text-xs text-fg-muted">{Object.entries(record.location).filter(([key,value])=>labels[key] && !(key.endsWith("_end") && record.location[key.replace(/_end$/, "_start")] === value) && !(key.startsWith("word_body_") && record.location.word_paragraph_start != null) && !(["row_start","row_end"].includes(key) && record.location.source_line_start != null)).map(([key,value])=>`${labels[key]}: ${value}`).join(" · ")}</p>
    {record.location.word_location_basis === "derived" ? <p className="text-xs text-fg-muted">変換後の文書の位置です。</p> : null}
    {highlight && cited ? <p className="text-xs text-fg-muted">対応する原文の記録を強調しています。</p> : null}
    {record.parts.length ? record.parts.map((part,index)=>{
      if(part.kind === "text")return <p key={index} lang={resolveContentLang(undefined,part.text)} className="whitespace-pre-wrap break-words text-sm leading-relaxed">{mark(part.text)}</p>;
      type Row=typeof part.rows[number];
      // 長い値（改行・40 文字超）の列は狭く折り返さない（#5415）。
      const longColumns=new Set(part.columns.map((_,column)=>column).filter(column=>part.rows.some(row=>{const value=row.cells[column] ?? "";return value.includes("\n") || Array.from(value).length>40;})));
      if(cited) {
        // 引用した記録は「項目 → 値」で縦に並べ、記録の枠ごと強調する（横のスクロールを出さない。#5415）。
        return <div key={index} className="space-y-3">{part.rows.map(row=><section key={row.label} aria-label={`行 ${row.label}`} data-source-record-cited="" data-surface-tint={highlight ? "accent" : undefined} className={highlight ? "space-y-2 rounded-md border-l-4 border-accent-emphasis bg-accent-subtle p-3" : "space-y-2"}>
          <p className="text-xs font-medium text-fg-muted">行 {row.label}</p>
          <RunFactList facts={part.columns.map((label,column)=>{const value=row.cells[column] ?? "";return {id:`column-${column}`,label,wide:longColumns.has(column),value:value ? <span lang={resolveContentLang(undefined,value)} className="whitespace-pre-wrap">{value}</span> : <span className="text-fg-muted">—</span>};})}/>
        </section>)}</div>;
      }
      const columns:DataTableColumn<Row>[]=[{key:"row",header:"行",rowHeader:true,className:"align-top",render:row=>row.label},...part.columns.map((label,column)=>({key:`column-${column}`,header:label,className:longColumns.has(column) ? "align-top min-w-[20rem]" : "align-top",render:(row:Row)=><span lang={resolveContentLang(undefined,row.cells[column] ?? "")} className="whitespace-pre-wrap break-words">{mark(row.cells[column] ?? "")}</span>}))];
      return <DataTable key={index} columns={columns} rows={part.rows} getRowKey={(_,row)=>row} dense stickyHeader visibleRows={INFORMATION_TABLE_FIXED_VISIBLE_ROWS} scrollAriaLabel="原文の表" ariaLabel="原文の表"/>;
    }) : <p lang={resolveContentLang(undefined,fallback)} className="whitespace-pre-wrap break-words text-sm leading-relaxed">{mark(excerptPlainText(fallback))}</p>}
  </div>;
}
