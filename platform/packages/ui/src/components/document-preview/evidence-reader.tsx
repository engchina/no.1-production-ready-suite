import { ArrowLeft, ArrowRight, Download, LocateFixed } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ExcerptContent } from "../data/excerpt-content";
import { ApiErrorState } from "../feedback/api-error-banner";
import { TimedLoadingState } from "../feedback/processing-state";
import { Button, buttonVariants } from "../ui/button";
import { Disclosure } from "../ui/disclosure";
import { Skeleton } from "../ui/skeleton";
import { CodepointHighlight } from "./codepoint-highlight";
import { SourceRecordView, type SourceRecordViewData } from "./source-record-view";
import { OriginalSourcePanel, type OriginalSourceLoader } from "./original-source-panel";
import { SourceVersionStaleNotice } from "./source-version-stale-notice";
import { isSourceVersionStale } from "../../lib/api-error";
import { resolveContentLang } from "../../lib/content-lang";

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
  blocks: { id: string; text: string; offset: number; highlights: [number, number][]; cited: boolean; kind?: string; heading?: string[]; record?: SourceRecordViewData }[];
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
  body: "引用元の本文", current: "現在の読取位置",
  beforeShown: "前文へ移動しました。", afterShown: "後文へ移動しました。",
  sectionShown: "章節の冒頭へ移動しました。", parentShown: "親章節の冒頭へ移動しました。",
  anchorShown: "引用位置へ戻りました。",
};
const EMPTY_INPUT: EvidenceReadingInput = {};
type ReaderBlock = EvidenceReadingWindow["blocks"][number];

/**
 * 続くブロックの役割（引用の元 / 追加の参考文脈）と章節が同じなら 1 つの区画にまとめる（#5428）。
 * 1 行が 1 要素の原本・リストの項目を、役割と章節名の繰り返しで細切れにしない。CSV・Excel の記録は
 * 記録ごとの位置を出すのでまとめない。
 */
export function readerGroups(blocks: readonly ReaderBlock[]): { cited: boolean; heading?: string[]; items: ReaderBlock[] }[] {
  const groups: { key: string; cited: boolean; heading?: string[]; items: ReaderBlock[] }[] = [];
  for (const block of blocks) {
    const key = `${block.cited}:${(block.heading ?? []).join("\u0000")}`;
    const last = groups.at(-1);
    if (last && last.key === key && !block.record && !last.items.at(-1)?.record) last.items.push(block);
    else groups.push({ key, cited: block.cited, heading: block.heading, items: [block] });
  }
  return groups;
}

export function EvidenceReader({ readingKey, loadWindow, enabled = true, downloadUrl, highlight = true, loadOriginal, purpose = "citation" }: {
  readingKey: string;
  loadWindow: (input: EvidenceReadingInput, signal: AbortSignal) => Promise<EvidenceReadingWindow>;
  enabled?: boolean;
  downloadUrl?: string;
  highlight?: boolean;
  loadOriginal?: OriginalSourceLoader;
  purpose?: "citation" | "selection";
}) {
  const [request, setRequest] = useState<{ key: string; input: EvidenceReadingInput; serial: number }>({ key: readingKey, input: {}, serial: 0 });
  const input = request.key === readingKey ? request.input : EMPTY_INPUT;
  const [outline, setOutline] = useState({ key: readingKey, open: false });
  const focusBody = useRef<string | null>(null);
  const positionedKey = useRef<string | null>(null);
  const [state, setState] = useState<{ key: string; windows: EvidenceReadingWindow[]; input?: EvidenceReadingInput; current?: EvidenceReadingWindow; target?: string; notice?: string; pending: boolean; error?: unknown }>({ key: readingKey, windows: [], pending: true });
  const viewport = useRef<HTMLDivElement>(null);
  const active = state.key === readingKey ? state.windows : [];
  const first = active[0]; const last = active.at(-1);
  const current = state.key === readingKey ? state.current ?? first : undefined;
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setState(old => ({ key: readingKey, windows: old.key === readingKey ? old.windows : [], input: old.key === readingKey ? old.input : undefined, current: old.key === readingKey ? old.current : undefined, target: old.key === readingKey ? old.target : undefined, notice: old.key === readingKey ? old.notice : undefined, pending: true }));
    void loadWindow(input, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      const explicit = focusBody.current === readingKey;
      setState(old => {
        const previous = old.key === readingKey && old.windows[0]?.revision === result.revision ? old.windows : [];
        const windows = input.cursor ? input.direction === "before" ? [result, ...previous].slice(0, 3) : [...previous, result].slice(-3) : [result];
        const notice = !explicit ? undefined : !result.blocks.length ? labels.empty
          : input.direction === "before" ? labels.beforeShown : input.direction === "after" ? labels.afterShown
          : input.scope === "parent" ? labels.parentShown : input.scope || input.section_id ? labels.sectionShown : labels.anchorShown;
        const anchor = !input.cursor && !input.scope && !input.section_id;
        const block = (anchor ? result.blocks.find(block => block.highlights.length || block.cited) : undefined) ?? result.blocks[0];
        return { key: readingKey, windows, current: result, input, target: block ? `${block.id}:${block.offset}` : undefined, notice, pending: false };
      });
    }).catch(error => { if (!controller.signal.aborted) setState(old => ({ ...old, pending: false, error })); });
    return () => controller.abort();
  }, [readingKey, loadWindow, enabled, request, input]);
  useLayoutEffect(() => {
    const node = viewport.current;
    const input = state.input ?? EMPTY_INPUT;
    if (!node || state.key !== readingKey || !state.windows.length) return;
    const explicit = focusBody.current === readingKey;
    if (!explicit && positionedKey.current === readingKey) return;
    positionedKey.current = readingKey;
    const anchor = !input.cursor && !input.section_id && !input.scope;
    const target = anchor
      ? node.querySelector<HTMLElement>("mark") ?? node.querySelector<HTMLElement>("[data-reader-cited='true']")
      : Array.from(node.querySelectorAll<HTMLElement>("[data-reader-block]")).find(block => block.dataset.readerBlock === state.target);
    node.scrollTop = 0;
    if (target) node.scrollTop += target.getBoundingClientRect().top - node.getBoundingClientRect().top - (anchor ? node.clientHeight / 4 : 0);
    // 明示的な移動では、新規本文を内側・外側の両viewportに表示する。
    if (explicit) {
      node.scrollIntoView({ block: "nearest", inline: "nearest" });
      node.focus({ preventScroll: true });
      focusBody.current = null;
    }
    // pending と error だけの変更では、表示済みの読み取り位置を動かさない。
  }, [state.windows, state.key, state.input, state.target, readingKey]);
  useLayoutEffect(() => {
    const node = viewport.current;
    if (node && outline.key === readingKey && outline.open) {
      const bounds = node.getBoundingClientRect();
      if (bounds.bottom > window.innerHeight || bounds.top < 0) node.scrollIntoView({ block: "nearest" });
    }
  }, [outline, readingKey]);
  const read = (input: EvidenceReadingInput = {}) => {
    focusBody.current = readingKey;
    if (input.section_id || input.scope) setOutline({ key: readingKey, open: false });
    setRequest(old => ({ key: readingKey, input, serial: old.serial + 1 }));
  };
  // 版の失効（#4092）: どの操作も同じ結果になるので、案内だけを出す（保存した抜粋はパネルの要約に残る）。
  if (state.key === readingKey && isSourceVersionStale(state.error)) return <div className="flex min-h-0 flex-1 flex-col gap-3" data-testid="evidence-reader"><SourceVersionStaleNotice /></div>;
  const blocks = active.flatMap(window => window.blocks).filter((block, index, all) => all.findIndex(other => other.id === block.id && other.offset === block.offset) === index);
  return <div className="flex min-h-0 flex-1 flex-col gap-3" data-testid="evidence-reader">
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="secondary" onClick={() => read()} disabled={state.pending}><LocateFixed size={14} aria-hidden />{purpose === "selection" ? "選択位置に戻る" : labels.anchor}</Button>
      <Button size="sm" variant="secondary" onClick={() => read({ scope: "section" })} disabled={state.pending || !current?.section_path.length}>{labels.section}</Button>
      <Button size="sm" variant="secondary" onClick={() => read({ scope: "parent" })} disabled={state.pending || !current?.section_path.length}>{labels.parent}</Button>
      {downloadUrl ? <a className={buttonVariants({ variant: "ghost", size: "sm" })} href={downloadUrl} download referrerPolicy="no-referrer"><Download size={14} aria-hidden />{labels.download}</a> : null}
    </div>
    {blocks.some(block => block.record) ? <p className="text-xs text-fg-muted">原文の構造化表示</p> : null}
    {current?.section_path.length ? <p className="text-xs text-fg-muted">{current.section_path.join(" › ")}</p> : null}
    {current && (current.anchor_status !== "exact" || !highlight) ? <p className="text-sm text-fg-muted" role="status">{current.anchor_status === "unresolved" || !highlight ? labels.unresolved : purpose === "selection" ? "保存された要素の範囲を表示しています。" : labels.approximate}</p> : null}
    {current?.outline.length ? <Disclosure summary={labels.outline} className="shrink-0" open={outline.key === readingKey && outline.open} onOpenChange={open => setOutline({ key: readingKey, open })} summaryProps={{ "data-testid": "evidence-reader-outline-toggle" }} contentClassName="max-h-[min(12rem,20dvh)] overflow-y-auto overscroll-contain" data-testid="evidence-reader-outline">
      <nav aria-label={labels.outline} className="flex flex-col items-start gap-1">
        {current.outline.map(item => <Button key={item.id} variant="ghost" size="sm" className="max-w-full whitespace-normal text-left" disabled={state.pending} onClick={() => read({ scope: "section", section_id: item.id })}>{item.title}</Button>)}
      </nav>
    </Disclosure> : null}
    {state.key === readingKey && state.notice ? <p role="status" className="text-sm text-fg-muted" data-testid="evidence-reader-location">{state.notice}</p> : null}
    {state.error ? <ApiErrorState error={state.error} fallback="引用の前後を読み込めませんでした。" onRetry={() => setRequest(old => ({ ...old, serial: old.serial + 1 }))} retryLabel={labels.retry} /> : null}
    {/* 本文を出したままの読み直しは本文の上に 1 行（#5415）。最初の読み込みは本文の領域の中に置く（#5435）。 */}
    {state.pending && blocks.length ? <TimedLoadingState label={labels.loading} operationKey={`${readingKey}:${request.serial}`} showSlowMessage={false} testId="evidence-reader-loading" /> : null}
    <Button variant="secondary" size="sm" disabled={!first?.previous_cursor || state.pending} onClick={() => read({ cursor: first!.previous_cursor!, direction: "before" })}><ArrowLeft size={14} aria-hidden />{labels.before}</Button>
    <div ref={viewport} role="region" aria-label={labels.body} tabIndex={0} aria-busy={state.pending} data-surface-tint={state.pending ? "neutral" : undefined} className={`${outline.key === readingKey && outline.open && !state.pending ? "min-h-[min(16rem,30dvh)]" : "min-h-0"} flex-1 space-y-4 overflow-y-auto overscroll-contain [overflow-anchor:none] ${state.pending ? "bg-surface-sunken" : ""}`} data-testid="evidence-reader-content">
      {state.pending && !blocks.length ? (
        // 本文が出る場所に、処理名と経過時間の 1 行と本文の行の形の Skeleton を置く（その場の占位。#5435）。
        // 本文の領域は残りの高さを占めるので、遅延の案内は予約せず遅延したときだけ足す。
        <TimedLoadingState label={labels.loading} operationKey={`${readingKey}:${request.serial}`} reserveSlowLine={false} testId="evidence-reader-loading">
          <div className="space-y-2" aria-hidden="true">{["w-full", "w-11/12", "w-full", "w-3/4"].map((width, index) => <Skeleton key={index} className={`h-4 ${width}`} />)}</div>
        </TimedLoadingState>
      ) : null}
      {readerGroups(blocks).map(group => {
        const blockKey = (block: ReaderBlock) => `${block.id}:${block.offset}`;
        const currentBlock = state.notice ? group.items.find(block => state.target === blockKey(block)) : undefined;
        // 区画の先頭が章節の見出しそのものなら、太字の章節名を重ねずにその本文を太字にする（#5428）。
        // 章節名は空白を詰めて保存するので、空白（全角を含む）を詰めて比べる。
        const squash = (value: string | undefined) => value?.replace(/\s+/g, " ").trim();
        const leadIsHeading = group.items[0].kind === "title" && squash(group.items[0].text) === squash(group.heading?.at(-1));
        return <section key={`group:${blockKey(group.items[0])}`} data-reader-group="" className="space-y-1">
          <p className="text-xs text-fg-muted">{currentBlock ? `${labels.current} · ` : ""}{group.cited ? purpose === "selection" ? "選択した原文の記録" : labels.cited : labels.context}</p>
          {group.heading?.length && !leadIsHeading ? <p className="text-sm font-semibold">{group.heading.join(" › ")}</p> : null}
          {group.items.map((block, index) => <div key={blockKey(block)} data-reader-block={blockKey(block)} data-reader-cited={block.cited} data-reader-current={currentBlock === block} className="space-y-1">
            {block.record ? <SourceRecordView record={block.record} cited={block.cited} highlight={highlight} fallback={block.text}/> : block.kind === "table" ? <div className="space-y-1"><ExcerptContent contentKind="table" text={block.text} highlights={highlight && block.cited ? block.highlights : undefined} tableLabel={labels.table} className="text-sm leading-relaxed" /></div> : <p lang={resolveContentLang(undefined, block.text)} className={`whitespace-pre-wrap break-words text-sm leading-relaxed ${leadIsHeading && index === 0 ? "font-semibold" : ""}`}>{highlight && block.highlights.length ? <CodepointHighlight text={block.text} ranges={block.highlights} /> : block.text}</p>}
            {loadOriginal && current ? <OriginalSourcePanel key={`${block.id}:${current.revision}`} readingKey={readingKey} elementId={block.id} revision={current.revision} loadWindow={loadOriginal} enabled={enabled} /> : null}
          </div>)}
        </section>;
      })}
      {!blocks.length && !state.pending && !state.error ? <p className="text-sm text-fg-muted">{labels.empty}</p> : null}
    </div>
    <Button variant="secondary" size="sm" disabled={!last?.next_cursor || state.pending} onClick={() => read({ cursor: last!.next_cursor!, direction: "after" })}>{labels.after}<ArrowRight size={14} aria-hidden /></Button>
  </div>;
}
