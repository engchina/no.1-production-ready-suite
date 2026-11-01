import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { resolveContentLang } from "../../lib/content-lang";
import { ApiErrorState } from "../feedback/api-error-banner";
import { TimedLoadingState } from "../feedback/processing-state";
import { Button } from "../ui/button";
import { Disclosure } from "../ui/disclosure";
import { Skeleton } from "../ui/skeleton";
import { CodepointHighlight } from "./codepoint-highlight";
import { originalSourceLabels } from "./labels";

export interface OriginalSourceInput {
  element_id: string;
  expected_revision: string;
  source_index?: number;
  offset?: number;
}
export interface OriginalSourceWindow {
  status: "exact" | "unrecorded" | "unresolved" | "version_mismatch";
  revision: string;
  chunk_revision?: string;
  anchor_status?: "exact" | "element";
  element_id: string;
  variant?: "original" | "prepared";
  pointer?: string;
  source_index?: number;
  source_count?: number;
  text: string;
  highlights: [number, number][];
  offset?: number;
  previous_offset?: number | null;
  next_offset?: number | null;
  coordinate_unit: "unicode_codepoint";
}
export type OriginalSourceLoader = (input: OriginalSourceInput, signal: AbortSignal) => Promise<OriginalSourceWindow>;
const labels = originalSourceLabels();

/** 展開時だけ有界な原ノードを取得する。完全ファイルをブラウザで検索・整形しない。 */
export function OriginalSourcePanel({readingKey, elementId, revision, loadWindow, enabled = true}: {
  readingKey: string; elementId: string; revision: string; loadWindow: OriginalSourceLoader; enabled?: boolean;
}) {
  const key = `${readingKey}:${revision}:${elementId}`;
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState<{key:string; source_index?:number; offset?:number; serial:number}>({key, serial:0});
  const [state, setState] = useState<{key:string; data?:OriginalSourceWindow; pending:boolean; error?:unknown}>({key,pending:false});
  const move = useRef(false); const body = useRef<HTMLDivElement>(null);
  const data = state.key === key ? state.data : undefined;
  useEffect(() => {
    if (!open || !enabled) return;
    const controller = new AbortController();
    setState(old => ({key, data:old.key === key ? old.data : undefined, pending:true}));
    const input = {element_id:elementId, expected_revision:revision, ...(request.key === key ? {source_index:request.source_index, offset:request.offset} : {})};
    void loadWindow(input, controller.signal).then(data => {
      if (!controller.signal.aborted) setState({key,data,pending:false});
    }).catch(error => {if (!controller.signal.aborted) setState(old => ({...old,pending:false,error}));});
    return () => controller.abort();
  }, [open, enabled, key, elementId, revision, request, loadWindow]);
  useLayoutEffect(() => {
    if (data && !state.pending && !state.error && move.current && body.current) {
      const text = body.current.querySelector("pre");
      if (text) text.scrollTop = 0;
      body.current.scrollIntoView({block:"nearest"}); body.current.focus({preventScroll:true}); move.current = false;
    }
  }, [state, data, key]);
  const read = (source_index = data?.source_index ?? 0, offset?: number) => {
    move.current = true; setRequest(old => ({key,source_index,offset,serial:old.serial+1}));
  };
  const pending = state.key === key && state.pending;
  return <Disclosure summary={labels.title} open={open} onOpenChange={value => {move.current = value; setOpen(value);}} data-testid="original-source-panel">
    {pending ? <TimedLoadingState label={labels.loading} operationKey={`${key}:${request.serial}`} framed={false}><Skeleton className="h-16 w-full" /></TimedLoadingState> : null}
    {state.key === key && state.error ? <ApiErrorState error={state.error} fallback={labels.failed} onRetry={() => {move.current = true; setRequest(old => ({...(old.key === key ? old : {key}), serial:old.serial+1}));}} retryLabel={labels.retry} /> : null}
    {data ? <div ref={body} tabIndex={0} role="region" aria-label={labels.body} className="space-y-2" data-testid="original-source-content">
      {data.status !== "exact" ? <p className="text-sm text-fg-muted" role="status">{data.status === "unrecorded" ? labels.unrecorded : data.status === "version_mismatch" ? labels.versionMismatch : labels.unresolved}</p> : <>
        <p className="text-xs text-fg-muted">{data.variant === "prepared" ? labels.prepared : labels.original} · {(data.source_index ?? 0) + 1} / {data.source_count}</p>
        <p className="break-all font-mono text-xs">{data.pointer}</p>
        <p className="text-xs text-fg-muted">{labels.note}</p>
        {data.chunk_revision && data.anchor_status === "element" ? <p className="text-sm text-fg-muted" role="status">{labels.citationElement}</p> : null}
        <pre lang={resolveContentLang(undefined, data.text)} className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-border bg-surface-sunken p-3 font-mono text-sm leading-relaxed"><CodepointHighlight text={data.text} ranges={data.highlights} /></pre>
        <div className="flex flex-wrap gap-2">
          {(data.source_count ?? 0) > 1 ? <><Button variant="secondary" size="sm" disabled={pending || !data.source_index} onClick={() => read((data.source_index ?? 0)-1)}>{labels.previousNode}</Button>
          <Button variant="secondary" size="sm" disabled={pending || (data.source_index ?? 0)+1 >= (data.source_count ?? 0)} onClick={() => read((data.source_index ?? 0)+1)}>{labels.nextNode}</Button></> : null}
          {data.previous_offset != null || data.next_offset != null ? <><Button variant="ghost" size="sm" disabled={pending || data.previous_offset == null} onClick={() => read(data.source_index,data.previous_offset ?? undefined)}>{labels.previous}</Button>
          <Button variant="ghost" size="sm" disabled={pending || data.next_offset == null} onClick={() => read(data.source_index,data.next_offset ?? undefined)}>{labels.next}</Button></> : null}
        </div>
      </>}
    </div> : null}
  </Disclosure>;
}
