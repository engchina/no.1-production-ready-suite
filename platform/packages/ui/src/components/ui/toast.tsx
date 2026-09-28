import { X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FocusEvent } from "react";
import { createPortal } from "react-dom";

import { useNavDrawerMode } from "../app-shell/nav-drawer";
import {
  PAGE_HEADER_SELECTOR,
  readToastPlacementInput,
  resolveToastPlacement,
  type ToastPlacement,
} from "../../lib/toast-placement";
import { cn } from "../../lib/utils";
import { useToastStore, type ToastItem } from "../../store/toast-store";

import { Button } from "./button";
import { toneIcon, toneRole, toneText } from "./feedback-tone";
import { MessageText } from "./message-text";

export interface ToasterProps {
  dismissLabel?: string;
  regionLabel?: string;
}

/**
 * Toast 表示領域。アプリ最上位で一度だけ描画する。フォーカスを奪わず aria-live で読み上げる（toast-accessibility）。
 *
 * 置き場所（#411。`lib/toast-placement.ts`）は主操作を覆わない位置にし、製品では変えない。
 * 画面の上端の見出しの面に重ね、その面の操作は覆わない。
 * - md 以上: PageHeader に重ね、ページの操作は覆わない（通常はページの操作のすぐ左）。PageHeader が見えなければ画面の右上。
 *   ページの末尾（画面の下端）の操作と、内容の面の右上の操作（ObjectActionBar / ContentActionBar）から離れる。
 * - md 未満: 上端の全幅。上端のバーに重ね、メニューのボタンは覆わない。
 * 通知は上から順に積む（新しい通知は下に足す。読み上げ・Tab の順と見た目の順をそろえる）。
 *
 * 重なり順はモーダルの下（`--z-toast` < `--z-scrim` < `--z-dialog`）。モーダル中はモーダル外を操作できないため、
 * 通知を上に重ねても押せず、確認ダイアログのボタンを覆うだけになる。
 *
 * 閉じるボタンの aria ラベルは `dismissLabel` で注入（既定「閉じる」）。
 *
 * いずれかの通知にポインタが乗っている間、または通知の中にフォーカスがある間は、すべての通知の自動消滅を止め、
 * 離れたら残り時間から再開する（WCAG 2.2.1 の考え方。読んでいる途中・操作しようとしている途中で消さない）。
 * 通知ごとではなく全体を止めるのは、下の通知が消えて読んでいる通知の位置がずれないようにするため。
 */
export function Toaster({ dismissLabel = "閉じる", regionLabel = "通知" }: ToasterProps = {}) {
  const toasts = useToastStore((state) => state.toasts);
  const placement = useToastPlacement(toasts.length > 0);
  const pause = useToastStore((state) => state.pause);
  const resume = useToastStore((state) => state.resume);
  const [mounted, setMounted] = useState(false);
  // ホバー・フォーカス中の通知（`hover:<id>` / `focus:<id>`）。1 つでもあれば止める。
  const engaged = useRef(new Set<string>());
  const setEngaged = useCallback(
    (key: string, active: boolean) => {
      if (active) engaged.current.add(key);
      else engaged.current.delete(key);
      if (engaged.current.size > 0) pause();
      else resume();
    },
    [pause, resume]
  );

  useEffect(() => setMounted(true), []);
  if (!mounted) return null;

  return createPortal(
    <div
      role="region"
      aria-label={regionLabel}
      aria-live="polite"
      aria-relevant="additions"
      data-toast-placement={placement.mode}
      className="pointer-events-none fixed z-[var(--z-toast)] flex flex-col gap-2 overflow-y-auto"
      style={placement.style}
    >
      {toasts.map((item) => (
        <ToastCard key={item.id} item={item} dismissLabel={dismissLabel} onEngage={setEngaged} />
      ))}
    </div>,
    document.body
  );
}

/**
 * 置き場所を画面から求める。通知が出ている間だけ、スクロール（祖先のどれでも）・リサイズ・PageHeader の大きさの変化に追従する
 * （lg 未満の PageHeader は本文と一緒にスクロールする。ページの操作の幅は loading などで変わる）。
 */
function useToastPlacement(active: boolean): ToastPlacement {
  const narrow = useNavDrawerMode();
  const [placement, setPlacement] = useState<ToastPlacement>(() =>
    resolveToastPlacement({ narrow, topBar: false, viewportWidth: 0, remPx: 16, header: null, headerActions: null })
  );

  useLayoutEffect(() => {
    const measure = () => {
      const next = resolveToastPlacement(readToastPlacementInput(document, narrow));
      setPlacement((current) => (JSON.stringify(current) === JSON.stringify(next) ? current : next));
    };
    measure();
    if (!active) return undefined;

    let frame = 0;
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        measure();
      });
    };
    // 画面を移ると PageHeader が差し替わる。外れた要素の大きさは 0 になって通知が来るので、そのたびに今の PageHeader を見直す。
    let observed: Element | null = null;
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      const header = document.querySelector(PAGE_HEADER_SELECTOR);
      if (header !== observed) {
        if (observed) resize?.unobserve(observed);
        observed = header;
        if (header) resize?.observe(header);
      }
      schedule();
    });
    observed = document.querySelector(PAGE_HEADER_SELECTOR);
    if (observed) resize?.observe(observed);
    document.addEventListener("scroll", schedule, { capture: true, passive: true });
    window.addEventListener("resize", schedule, { passive: true });
    return () => {
      if (frame) cancelAnimationFrame(frame);
      resize?.disconnect();
      document.removeEventListener("scroll", schedule, { capture: true });
      window.removeEventListener("resize", schedule);
    };
  }, [active, narrow]);

  return placement;
}

function ToastCard({
  item,
  dismissLabel,
  onEngage,
}: {
  item: ToastItem;
  dismissLabel: string;
  onEngage: (key: string, active: boolean) => void;
}) {
  const dismiss = useToastStore((state) => state.dismiss);
  const Icon = toneIcon[item.tone];
  const hoverKey = `hover:${item.id}`;
  const focusKey = `focus:${item.id}`;

  // ホバー・フォーカス中に閉じられた（DOM から外れた）通知は、leave / blur が届かないため、外れたときに解除する。
  useEffect(
    () => () => {
      onEngage(hoverKey, false);
      onEngage(focusKey, false);
    },
    [onEngage, hoverKey, focusKey]
  );

  const handleBlur = (event: FocusEvent<HTMLDivElement>) => {
    // 通知の中でフォーカスが移るだけなら止めたままにする。
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    onEngage(focusKey, false);
  };

  return (
    <div
      role={toneRole(item.tone)}
      onPointerEnter={() => onEngage(hoverKey, true)}
      onPointerLeave={() => onEngage(hoverKey, false)}
      onFocus={() => onEngage(focusKey, true)}
      onBlur={handleBlur}
      className="animate-toast-in pointer-events-auto flex items-start gap-2.5 rounded-lg border border-border bg-surface-raised px-3.5 py-3 shadow-[var(--shadow-toast)]"
    >
      <Icon size={16} className={cn("mt-0.5 shrink-0", toneText[item.tone])} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium leading-relaxed text-fg">
          <MessageText text={item.message} />
        </p>
        {item.description ? (
          <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">
            <MessageText text={item.description} />
          </p>
        ) : null}
        {item.action ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              item.action?.onClick();
              dismiss(item.id);
            }}
            className="mt-1.5"
          >
            {item.action.label}
          </Button>
        ) : null}
      </div>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        iconOnly
        touchTarget
        icon={X}
        onClick={() => dismiss(item.id)}
        aria-label={dismissLabel}
        className="-mr-2 -mt-2 text-fg-muted hover:enabled:text-fg"
      />
    </div>
  );
}
