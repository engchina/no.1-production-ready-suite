import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";

import {
  FIXED_SPLIT_DIVIDER_SIZE_PX,
  FIXED_SPLIT_DEFAULT_MIN_PANE_WIDTH_PX,
  FIXED_SPLIT_KEYBOARD_FAST_STEP_PX,
  FIXED_SPLIT_KEYBOARD_STEP_PX,
  adjustFixedSplitFraction,
  clampFixedSplitFractionToPaneWidths,
  fixedSplitFractionBounds,
  fixedSplitGridTemplateColumns,
  fixedSplitStateForFraction,
  fixedSplitStateForPreferredWidePane,
  fixedSplitStateForRatio,
  fixedSplitStorageKey,
  fixedSplitValueText,
  nextFixedSplitStateFromFraction,
  parseFixedSplitStorageValue,
  serializeFixedSplitState,
  type FixedSplitPaneState,
  type FixedSplitRatio,
  type FixedSplitWidePane,
} from "@/lib/fixed-split-pane";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

interface FixedSplitPaneProps {
  splitId: string;
  preferredWidePane: FixedSplitWidePane;
  left: ReactNode;
  right: ReactNode;
  className?: string;
  leftClassName?: string;
  rightClassName?: string;
  minLeftPaneWidthPx?: number;
  minRightPaneWidthPx?: number;
}

function readStoredState(splitId: string, preferredWidePane: FixedSplitWidePane): FixedSplitPaneState {
  const fallbackState = fixedSplitStateForPreferredWidePane(preferredWidePane);
  if (typeof window === "undefined") return fallbackState;
  try {
    const value = window.localStorage.getItem(fixedSplitStorageKey(splitId));
    return parseFixedSplitStorageValue(value, fallbackState);
  } catch {
    return fallbackState;
  }
}

function valueText(ratio: FixedSplitRatio) {
  if (ratio === "leftWide") return t("fixedSplitPane.value.leftWide");
  if (ratio === "rightWide") return t("fixedSplitPane.value.rightWide");
  return t("fixedSplitPane.value.equal");
}

export function FixedSplitPane({
  splitId,
  preferredWidePane,
  left,
  right,
  className,
  leftClassName,
  rightClassName,
  minLeftPaneWidthPx = FIXED_SPLIT_DEFAULT_MIN_PANE_WIDTH_PX,
  minRightPaneWidthPx = FIXED_SPLIT_DEFAULT_MIN_PANE_WIDTH_PX,
}: FixedSplitPaneProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const hintId = useId();
  const dragStartXRef = useRef(0);
  const dragStartFractionRef = useRef(0.5);
  const dragAvailableWidthRef = useRef(1);
  const cleanupDragRef = useRef<(() => void) | null>(null);
  const previousBodyCursorRef = useRef("");
  const previousBodyUserSelectRef = useRef("");
  const [splitState, setSplitState] = useState<FixedSplitPaneState>(() =>
    readStoredState(splitId, preferredWidePane)
  );
  const [isDragging, setIsDragging] = useState(false);
  const [rootWidth, setRootWidth] = useState(0);
  const [isDesktopViewport, setIsDesktopViewport] = useState(() =>
    typeof window === "undefined" ? false : window.matchMedia("(min-width: 1280px)").matches
  );

  useEffect(() => {
    setSplitState(readStoredState(splitId, preferredWidePane));
  }, [preferredWidePane, splitId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(fixedSplitStorageKey(splitId), serializeFixedSplitState(splitState));
    } catch {
      // Storage が無効な環境では現在セッション内の state だけで動かす。
    }
  }, [splitId, splitState]);

  useEffect(() => {
    return () => {
      cleanupDragRef.current?.();
      cleanupDragRef.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const updateWidth = () => setRootWidth(root.clientWidth);
    updateWidth();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", updateWidth);
      return () => window.removeEventListener("resize", updateWidth);
    }

    const observer = new ResizeObserver(updateWidth);
    observer.observe(root);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(min-width: 1280px)");
    const updateViewport = () => setIsDesktopViewport(mediaQuery.matches);
    updateViewport();
    mediaQuery.addEventListener("change", updateViewport);
    return () => mediaQuery.removeEventListener("change", updateViewport);
  }, []);

  const availableWidth = useCallback(() => {
    const rootWidth = rootRef.current?.clientWidth ?? 0;
    return Math.max(rootWidth - FIXED_SPLIT_DIVIDER_SIZE_PX, 1);
  }, []);

  const measuredAvailableWidth = Math.max(rootWidth - FIXED_SPLIT_DIVIDER_SIZE_PX, 1);
  const splitLayout =
    isDesktopViewport &&
    rootWidth >= minLeftPaneWidthPx + FIXED_SPLIT_DIVIDER_SIZE_PX + minRightPaneWidthPx;
  const constrainedLeftFraction =
    rootWidth > 0
      ? clampFixedSplitFractionToPaneWidths(
          splitState.leftFraction,
          measuredAvailableWidth,
          minLeftPaneWidthPx,
          minRightPaneWidthPx
        )
      : splitState.leftFraction;
  const fractionBounds = fixedSplitFractionBounds(
    measuredAvailableWidth,
    minLeftPaneWidthPx,
    minRightPaneWidthPx
  );

  const style = useMemo(
    () =>
      ({
        "--fixed-split-columns": fixedSplitGridTemplateColumns(constrainedLeftFraction),
      }) as CSSProperties,
    [constrainedLeftFraction]
  );

  const applyDragDocumentState = useCallback(() => {
    if (typeof document === "undefined") return;
    previousBodyCursorRef.current = document.body.style.cursor;
    previousBodyUserSelectRef.current = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  const resetDragDocumentState = useCallback(() => {
    if (typeof document === "undefined") return;
    document.body.style.cursor = previousBodyCursorRef.current;
    document.body.style.userSelect = previousBodyUserSelectRef.current;
  }, []);

  const setRatio = useCallback((ratio: FixedSplitRatio) => {
    setSplitState(fixedSplitStateForRatio(ratio));
  }, []);

  const cycleRatio = useCallback(() => {
    setSplitState((current) => nextFixedSplitStateFromFraction(current.leftFraction));
  }, []);

  const adjustFraction = useCallback(
    (deltaPx: number) => {
      setSplitState((current) =>
        fixedSplitStateForFraction(
          adjustFixedSplitFraction(
            current.leftFraction,
            deltaPx,
            availableWidth(),
            minLeftPaneWidthPx,
            minRightPaneWidthPx
          )
        )
      );
    },
    [availableWidth, minLeftPaneWidthPx, minRightPaneWidthPx]
  );

  function handlePointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.focus();
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture 非対応の環境では window listener だけで継続する。
    }

    cleanupDragRef.current?.();
    dragStartXRef.current = event.clientX;
    dragStartFractionRef.current = constrainedLeftFraction;
    dragAvailableWidthRef.current = availableWidth();
    applyDragDocumentState();
    setIsDragging(true);

    const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
      const deltaPx = moveEvent.clientX - dragStartXRef.current;
      const nextFraction = adjustFixedSplitFraction(
        dragStartFractionRef.current,
        deltaPx,
        dragAvailableWidthRef.current,
        minLeftPaneWidthPx,
        minRightPaneWidthPx
      );
      setSplitState(fixedSplitStateForFraction(nextFraction));
    };

    const finishDrag = (shouldUpdateState = true) => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerEnd);
      window.removeEventListener("pointercancel", handlePointerEnd);
      resetDragDocumentState();
      if (shouldUpdateState) setIsDragging(false);
      cleanupDragRef.current = null;
    };

    const handlePointerEnd = () => {
      finishDrag();
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerEnd);
    window.addEventListener("pointercancel", handlePointerEnd);
    cleanupDragRef.current = () => finishDrag(false);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      cycleRatio();
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      adjustFraction(-(event.shiftKey ? FIXED_SPLIT_KEYBOARD_FAST_STEP_PX : FIXED_SPLIT_KEYBOARD_STEP_PX));
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      adjustFraction(event.shiftKey ? FIXED_SPLIT_KEYBOARD_FAST_STEP_PX : FIXED_SPLIT_KEYBOARD_STEP_PX);
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      setRatio("equal");
    }
  }

  return (
    <div
      className={cn("fixed-split-pane", className)}
      ref={rootRef}
      style={style}
      data-testid={`fixed-split-pane-${splitId}`}
      data-split-ratio={fixedSplitValueText(splitState.ratio)}
      data-split-left-fraction={constrainedLeftFraction.toFixed(4)}
      data-split-layout={splitLayout ? "split" : "stacked"}
    >
      <div
        className={cn("fixed-split-pane__panel fixed-split-pane__panel--left min-w-0", leftClassName)}
        data-split-pane-side="left"
        data-testid={`fixed-split-pane-${splitId}-left`}
      >
        {left}
      </div>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={t("fixedSplitPane.separatorLabel")}
        aria-describedby={hintId}
        aria-valuemin={Math.ceil(fractionBounds.minFraction * 100)}
        aria-valuemax={Math.floor(fractionBounds.maxFraction * 100)}
        aria-valuenow={Math.round(constrainedLeftFraction * 100)}
        aria-valuetext={`${valueText(splitState.ratio)} ${Math.round(constrainedLeftFraction * 100)}%`}
        tabIndex={0}
        className="fixed-split-pane__divider"
        data-dragging={isDragging ? "true" : "false"}
        data-testid={`fixed-split-pane-${splitId}-divider`}
        onDoubleClick={cycleRatio}
        onKeyDown={handleKeyDown}
        onPointerDown={handlePointerDown}
      >
        <span className="fixed-split-pane__line" aria-hidden="true" />
        <span className="fixed-split-pane__grip" aria-hidden="true">
          <span className="fixed-split-pane__dot" />
          <span className="fixed-split-pane__dot" />
          <span className="fixed-split-pane__dot" />
        </span>
        <span id={hintId} className="sr-only">
          {t("fixedSplitPane.hint")}
        </span>
      </div>
      <div
        className={cn("fixed-split-pane__panel fixed-split-pane__panel--right min-w-0", rightClassName)}
        data-split-pane-side="right"
        data-testid={`fixed-split-pane-${splitId}-right`}
      >
        {right}
      </div>
    </div>
  );
}
