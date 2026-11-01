import { ArrowLeft, ArrowRight, Download, LocateFixed } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ExcerptContent } from "../data/excerpt-content";
import { ApiErrorState } from "../feedback/api-error-banner";
import { TimedLoadingState } from "../feedback/processing-state";
import { Button, buttonVariants } from "../ui/button";
import { Disclosure } from "../ui/disclosure";
import { Skeleton } from "../ui/skeleton";

export interface EvidenceReadingInput {
  cursor?: string;
  direction?: "before" | "after";
  scope?: "paragraph" | "section" | "parent";
  section_id?: string;
}
export interface EvidenceReadingWindow {
  revision: string;
  anchor_status: "exact" | "element" | "unresolved";
  section_path: string[];
  blocks: { id: string; text: string; offset: number; highlights: [number, number][]; cited: boolean; kind?: string; heading?: string[] }[];
  previous_cursor: string | null;
  next_cursor: string | null;
  outline: { id: string; title: string; parent_id: string | null; has_children: boolean }[];
  scope: string;
}

/** 製品で複製しない共通の日本語リソース。 */
const labels = {
  loading: "引用の前後を読み込んでいます", before: "前文を読む", after: "後文を読む",
  anchor: "引用位置に戻る", section: "本節を読む", parent: "親章節を読む", outline: "目次",
  cited: "引用の元となる段落", context: "追加の参考文脈（この引用には含まれません）",
  unresolved: "引用位置を確定できないため、保存された引用のみ表示します。",
  approximate: "原要素の位置を表示しています。文字単位の引用位置は未確定です。",
  table: "引用元の表", retry: "再試行", download: "原本をダウンロード", empty: "表示できる本文がありません。",
  body: "引用元の本文",
};
const EMPTY_INPUT: EvidenceReadingInput = {};

export function EvidenceReader({ readingKey, loadWindow, enabled = true, downloadUrl, highlight = true }: {
  readingKey: string;
  loadWindow: (input: EvidenceReadingInput, signal: AbortSignal) => Promise<EvidenceReadingWindow>;
  enabled?: boolean;
  downloadUrl?: string;
  highlight?: boolean;
}) {
  const [request, setRequest] = useState<{ key: string; input: EvidenceReadingInput; serial: number }>({ key: readingKey, input: {}, serial: 0 });
  const input = request.key === readingKey ? request.input : EMPTY_INPUT;
  const [outline, setOutline] = useState({ key: readingKey, open: false });
  const focusBody = useRef<string | null>(null);
  const [state, setState] = useState<{ key: string; windows: EvidenceReadingWindow[]; input?: EvidenceReadingInput; pending: boolean; error?: unknown }>({ key: readingKey, windows: [], pending: true });
  const viewport = useRef<HTMLDivElement>(null);
  const scroll = useRef<{ key: string; anchors: { top: number; anchor: string }[] } | null>(null);
  const active = state.key === readingKey ? state.windows : [];
  const first = active[0]; const last = active.at(-1);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setState(old => ({ key: readingKey, windows: old.key === readingKey ? old.windows : [], input: old.key === readingKey ? old.input : undefined, pending: true }));
    void loadWindow(input, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setState(old => {
        const previous = old.key === readingKey && old.windows[0]?.revision === result.revision ? old.windows : [];
        const windows = input.cursor ? input.direction === "before" ? [result, ...previous].slice(0, 3) : [...previous, result].slice(-3) : [result];
        return { key: readingKey, windows, input, pending: false };
      });
    }).catch(error => { if (!controller.signal.aborted) setState(old => ({ ...old, pending: false, error })); });
    return () => controller.abort();
  }, [readingKey, loadWindow, enabled, request, input]);
  useLayoutEffect(() => {
    const node = viewport.current;
    const input = state.input ?? EMPTY_INPUT;
    if (!node || state.key !== readingKey || !state.windows.length) return;
    if (scroll.current?.key === readingKey && input.cursor) {
      const elements = Array.from(node.querySelectorAll<HTMLElement>("[data-reader-block]"));
      const anchor = scroll.current.anchors.find(candidate => elements.some(element => element.dataset.readerBlock === candidate.anchor));
      const block = anchor && elements.find(element => element.dataset.readerBlock === anchor.anchor);
      if (block && anchor) node.scrollTop += block.getBoundingClientRect().top - anchor.top;
      scroll.current = null;
    } else if (!input.cursor) {
      node.scrollTop = 0;
      const target = input.section_id || input.scope ? null : node.querySelector<HTMLElement>("mark") ?? node.querySelector<HTMLElement>("[data-reader-cited='true']");
      if (target) node.scrollTop += target.getBoundingClientRect().top - node.getBoundingClientRect().top - node.clientHeight / 4;
    }
    if (focusBody.current === readingKey) { node.focus({ preventScroll: true }); focusBody.current = null; }
    // pending と error だけの変更では、表示済みの読み取り位置を動かさない。
  }, [state.windows, state.key, state.input, readingKey]);
  useLayoutEffect(() => {
    const node = viewport.current;
    if (node && outline.key === readingKey && outline.open) {
      const bounds = node.getBoundingClientRect();
      if (bounds.bottom > window.innerHeight || bounds.top < 0) node.scrollIntoView({ block: "nearest" });
    }
  }, [outline, readingKey]);
  const read = (input: EvidenceReadingInput = {}) => {
    const node = viewport.current;
    const top = node?.getBoundingClientRect().top ?? 0;
    const anchors = node ? Array.from(node.querySelectorAll<HTMLElement>("[data-reader-block]")).map(block => ({
      top: block.getBoundingClientRect().top, bottom: block.getBoundingClientRect().bottom, anchor: block.dataset.readerBlock!,
    })) : [];
    // 窓の破棄後にも残る表示位置を優先し、前後いずれの追加でも読みかけを保つ。
    anchors.sort((a, b) => Number(a.bottom <= top) - Number(b.bottom <= top) || Math.abs(a.top - top) - Math.abs(b.top - top));
    scroll.current = input.cursor ? { key: readingKey, anchors } : null;
    if (input.section_id || input.scope) { setOutline({ key: readingKey, open: false }); focusBody.current = readingKey; }
    setRequest(old => ({ key: readingKey, input, serial: old.serial + 1 }));
  };
  const blocks = active.flatMap(window => window.blocks).filter((block, index, all) => all.findIndex(other => other.id === block.id && other.offset === block.offset) === index);
  return <div className="flex min-h-0 flex-1 flex-col gap-3" data-testid="evidence-reader">
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="secondary" onClick={() => read()} disabled={state.pending}><LocateFixed size={14} aria-hidden />{labels.anchor}</Button>
      <Button size="sm" variant="secondary" onClick={() => read({ scope: "section" })} disabled={state.pending || !first?.section_path.length}>{labels.section}</Button>
      <Button size="sm" variant="secondary" onClick={() => read({ scope: "parent" })} disabled={state.pending || !first?.section_path.length}>{labels.parent}</Button>
      {downloadUrl ? <a className={buttonVariants({ variant: "ghost", size: "sm" })} href={downloadUrl} download referrerPolicy="no-referrer"><Download size={14} aria-hidden />{labels.download}</a> : null}
    </div>
    {first?.section_path.length ? <p className="text-xs text-fg-muted">{first.section_path.join(" › ")}</p> : null}
    {first && (first.anchor_status !== "exact" || !highlight) ? <p className="text-sm text-fg-muted" role="status">{first.anchor_status === "unresolved" || !highlight ? labels.unresolved : labels.approximate}</p> : null}
    {first?.outline.length ? <Disclosure summary={labels.outline} className="shrink-0" open={outline.key === readingKey && outline.open} onOpenChange={open => setOutline({ key: readingKey, open })} summaryProps={{ "data-testid": "evidence-reader-outline-toggle" }} contentClassName="max-h-[min(12rem,20dvh)] overflow-y-auto overscroll-contain" data-testid="evidence-reader-outline">
      <nav aria-label={labels.outline} className="flex flex-col items-start gap-1">
        {first.outline.map(item => <Button key={item.id} variant="ghost" size="sm" className="max-w-full whitespace-normal text-left" disabled={state.pending} onClick={() => read({ scope: "section", section_id: item.id })}>{item.title}</Button>)}
      </nav>
    </Disclosure> : null}
    {state.error ? <ApiErrorState error={state.error} fallback="引用の前後を読み込めませんでした。" onRetry={() => setRequest(old => ({ ...old, serial: old.serial + 1 }))} retryLabel={labels.retry} /> : null}
    {state.pending ? <TimedLoadingState label={labels.loading} operationKey={`${readingKey}:${request.serial}`} testId="evidence-reader-loading" framed={false}><Skeleton className="h-16 w-full" /></TimedLoadingState> : null}
    <Button variant="secondary" size="sm" disabled={!first?.previous_cursor || state.pending} onClick={() => read({ cursor: first!.previous_cursor!, direction: "before" })}><ArrowLeft size={14} aria-hidden />{labels.before}</Button>
    <div ref={viewport} role="region" aria-label={labels.body} tabIndex={0} className="min-h-[min(16rem,30dvh)] flex-1 space-y-4 overflow-y-auto overscroll-contain [overflow-anchor:none]" data-testid="evidence-reader-content">
      {blocks.map(block => <section key={`${block.id}:${block.offset}`} data-reader-block={`${block.id}:${block.offset}`} data-reader-cited={block.cited} className="space-y-1">
        <p className="text-xs text-fg-muted">{block.cited ? labels.cited : labels.context}</p>
        {block.heading?.length ? <p className="text-sm font-semibold">{block.heading.join(" › ")}</p> : null}
        {block.kind === "table" ? <ExcerptContent text={block.text} tableLabel={labels.table} className="text-sm leading-relaxed" /> : <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{highlight && block.highlights.length ? highlighted(block.text, block.highlights) : block.text}</p>}
      </section>)}
      {!blocks.length && !state.pending && !state.error ? <p className="text-sm text-fg-muted">{labels.empty}</p> : null}
    </div>
    <Button variant="secondary" size="sm" disabled={!last?.next_cursor || state.pending} onClick={() => read({ cursor: last!.next_cursor!, direction: "after" })}>{labels.after}<ArrowRight size={14} aria-hidden /></Button>
  </div>;
}
function highlighted(text: string, ranges: [number, number][]) {
  const parts = []; let offset = 0;
  for (const [start, end] of ranges) { parts.push(text.slice(offset, start), <mark key={start} data-surface-tint="accent" className="rounded-sm bg-accent-muted text-fg underline decoration-accent-emphasis decoration-2 underline-offset-2 box-decoration-clone">{text.slice(start, end)}</mark>); offset = end; }
  parts.push(text.slice(offset)); return parts;
}
