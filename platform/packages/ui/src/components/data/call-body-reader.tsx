import { useEffect, useRef, useState } from "react";
import { BookOpen, Info, RefreshCw } from "lucide-react";
import { parseCallBodyPage, type CallBodyLoader, type CallBodySlot, type CallContent } from "../../lib/call-body";
import { Button } from "../ui/button";
import { Banner } from "../ui/banner";
import { Skeleton } from "../ui/skeleton";
import { TimedLoadingState } from "../feedback/processing-state";

export const DEFAULT_CALL_BODY_LABELS = {
  preview: "長い記録はプレビューで表示しています。",
  availableOnly: "取得できる記録だけを表示しています。",
  read: "保存済みの全文を読む", inspect: "保存状態を確認する", more: "続きを読む", retry: "再試行",
  loading: "記録した全文を読み込んでいます", pending: "全文の保存はまだ完了していません。",
  incomplete: "この記録は不完全です。", unavailable: "全文を取得できませんでした。時間をおいて再度お試しください。",
  noReader: "この表示では全文を取得できません。", denied: "現在の権限では、この内容を表示できません。",
  truncated: "保存時に内容が切り詰められています。この記録から全文は復元できません。",
  reasons: {
    storage_unavailable: "保存先を利用できませんでした。", queue_full: "記録の待ち行列が上限に達しました。",
    write_failed: "本文の保存に失敗しました。", capture_failed: "内容の記録に失敗しました。",
    writer_stopped: "記録処理が停止しました。", invalid_encoding: "文字の符号化を確認できませんでした。",
    body_unavailable: "保存済みの本文を確認できません。",
  } as Record<string, string>,
  loaded: (read: number, total: number) => `${read.toLocaleString()} / ${total.toLocaleString()} 文字を取得しました。`,
};
export type CallBodyLabels = typeof DEFAULT_CALL_BODY_LABELS;
interface BodyState {
  key: string; text: string; next: number | null; end: number;
  status: "pending" | "complete" | "incomplete";
  reason?: string; error?: boolean; denied?: boolean;
}

/** 明示操作で順に読み、選択変更・解除後の応答を別のspanへ適用しない。 */
export function useCallBodyContent(content: CallContent, spanId: string | undefined, slot: CallBodySlot, load: CallBodyLoader | undefined, inputLabels?: Partial<CallBodyLabels>) {
  const labels = {...DEFAULT_CALL_BODY_LABELS, ...inputLabels};
  const reference = content.body_reference;
  const key = `${spanId ?? ""}:${slot}:${reference?.body_id ?? ""}:${reference?.sha256 ?? ""}:${content.status}`;
  const [stored, setStored] = useState<BodyState | null>(null);
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const statusRef = useRef<HTMLParagraphElement | null>(null);
  const completedRequest = useRef<string | null>(null);
  const latestKey = useRef(key);
  latestKey.current = key;
  useEffect(() => () => { controller.current?.abort(); controller.current = null; }, [key]);
  const state = stored?.key === key ? stored : null;
  const loading = loadingKey === key && controller.current !== null;
  const loaded = state?.status === "complete" && state.next === null && !state.error;
  useEffect(() => {
    if (loaded && completedRequest.current === key) {
      completedRequest.current = null;
      statusRef.current?.focus({preventScroll: true});
    }
  }, [loaded, key]);
  const read = async () => {
    if (!load || !reference || !spanId || controller.current) return;
    const request = new AbortController();
    controller.current = request;
    setLoadingKey(key);
    const firstPart = state?.status === "complete" && !state.denied ? state.next ?? 0 : 0;
    try {
      const raw = await load({spanId, slot, firstPart, signal: request.signal});
      if (request.signal.aborted || latestKey.current !== key) return;
      const page = parseCallBodyPage(raw, reference, firstPart);
      if (!page || (firstPart > 0 && page.parts[0]?.start !== state?.end)) throw new Error("Invalid trace body page");
      const text = page.status === "complete" ? (firstPart > 0 ? state?.text ?? "" : "") + page.parts.map(part => part.text).join("") : "";
      if (page.status === "complete" && page.next_part === null) completedRequest.current = key;
      setStored({key, text, next: page.next_part, end: page.parts.at(-1)?.end ?? 0, status: page.status, reason: page.failure_code ?? undefined});
    } catch (error) {
      if (request.signal.aborted || latestKey.current !== key) return;
      const status = error && typeof error === "object" && "status" in error ? error.status : null;
      const denied = status === 401 || status === 403 || status === 404;
      setStored({...state, key, text: denied ? "" : state?.text ?? "", next: state?.next ?? 0, end: state?.end ?? 0, status: state?.status ?? "pending", error: true, denied});
    } finally {
      if (controller.current === request) { controller.current = null; setLoadingKey(null); }
    }
  };
  const effective: CallContent = state?.denied ? {status: "withheld", text: ""} : state?.status === "complete" && state.text ? {...content, status: "recorded", text: state.text, preview_only: !loaded} : content;
  const reason = state?.reason ?? content.failure_code;
  const incomplete = state ? state.status === "incomplete" : content.status === "incomplete";
  const visible = content.status !== "withheld" && content.status !== "not_recorded" && Boolean(reference || incomplete || content.status === "truncated");
  const controls = !visible ? null : <div className="grid min-w-0 gap-2" data-call-full-body="">
    {state?.denied ? null : incomplete ? <Banner severity="warning" title={labels.incomplete}>{reason ? labels.reasons[reason] ?? labels.reasons.body_unavailable : labels.reasons.body_unavailable}</Banner> : null}
    {content.status === "truncated" ? <p className="text-xs text-fg-muted">{labels.truncated}</p> : null}
    {reference && !state?.denied ? <>
      <p ref={statusRef} tabIndex={-1} className="flex min-w-0 items-start gap-1.5 text-xs text-fg-muted" role="status"><span className="pr-icon-line"><Info size={14} aria-hidden="true" /></span><span className="min-w-0">{incomplete ? labels.availableOnly : state?.status === "complete" ? labels.loaded(state.end, reference.character_count) : state?.status === "pending" && !state.error ? labels.pending : labels.preview}</span></p>
      {state?.error ? <Banner severity="danger">{labels.unavailable}</Banner> : null}
      {!loaded ? load && spanId ? <div><Button size="sm" variant="secondary" icon={state?.error || state?.status === "pending" ? RefreshCw : BookOpen} loading={loading} onClick={() => void read()}>{state?.error || state?.status === "pending" ? labels.retry : incomplete ? labels.inspect : state?.next ? labels.more : labels.read}</Button></div> : <p className="text-xs text-fg-muted">{labels.noReader}</p> : null}
      {loading ? <TimedLoadingState label={labels.loading} activityIcon="none" framed={false} operationKey={key}><Skeleton className="h-6 w-full" /></TimedLoadingState> : null}
    </> : null}
  </div>;
  return {content: effective, controls};
}
