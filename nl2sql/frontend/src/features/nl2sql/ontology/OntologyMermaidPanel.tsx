import { Button } from "@/components/ui/button";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { ClipboardCopy, FileCode2, Loader2, Maximize2, Minus, Network, Plus } from "lucide-react";

import { Banner, StatusBadge, toast } from "@engchina/production-ready-ui";

import { ContentActionBar } from "@/components/ContentActionBar";
import { ManagementTabs } from "../components/DbAdminShared";
import { DbObjectPanelHeader } from "../components/DbObjectManagementShared";
import { fetchProfileOntologyMermaid } from "./api";
import { t } from "@/lib/i18n";

type MermaidPanelTab = "code" | "graph";

export interface OntologyMermaidPanelProps {
  profileId: string;
  graphRevisionId?: string;
  refreshToken?: number;
}

interface MermaidGraphTransform {
  scale: number;
  x: number;
  y: number;
}

interface MermaidGraphBounds {
  x: number;
  y: number;
  width: number;
  height: number;
  source: MermaidGraphBoundsSource;
}

type MermaidGraphBoundsSource = "content" | "svg-bbox" | "viewbox" | "size" | "client-rect";

const MERMAID_GRAPH_MIN_SCALE = 0.2;
const MERMAID_GRAPH_MAX_SCALE = 40;
const MERMAID_GRAPH_FIT_MAX_SCALE = 40;
const MERMAID_GRAPH_ZOOM_STEP = 1.2;
const MERMAID_GRAPH_FIT_PADDING = 8;
const MERMAID_GRAPH_KEYBOARD_PAN = 36;
const MERMAID_GRAPH_CONTENT_SELECTOR =
  "g,path,line,polyline,polygon,rect,circle,ellipse,text,tspan";
const DEFAULT_MERMAID_GRAPH_TRANSFORM: MermaidGraphTransform = { scale: 1, x: 0, y: 0 };

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function parseSvgLength(value: string | null): number | null {
  if (!value) return null;
  const match = value.trim().match(/^([0-9]+(?:\.[0-9]+)?)(?:px)?$/u);
  if (!match) return null;
  const parsed = Number.parseFloat(match[1]);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function parseSvgViewBoxAttribute(value: string | null): MermaidGraphBounds | null {
  const parts = value
    ?.trim()
    .split(/[\s,]+/u)
    .map((part) => Number.parseFloat(part));
  if (!parts || parts.length !== 4) return null;
  return validSvgBounds(
    { x: parts[0], y: parts[1], width: parts[2], height: parts[3] },
    "viewbox"
  );
}

function mergeSvgSizeStyle(style: string | null, width: number, height: number): string {
  const retainedRules = (style ?? "")
    .split(";")
    .map((rule) => rule.trim())
    .filter((rule) => rule && !/^(?:max-width|width|height)\s*:/iu.test(rule));
  return [
    ...retainedRules,
    `width: ${width}px`,
    `height: ${height}px`,
    "max-width: none",
  ].join("; ");
}

function normalizeMermaidSvgMarkup(svgMarkup: string): string {
  const document = new DOMParser().parseFromString(svgMarkup, "image/svg+xml");
  const svg = document.querySelector("svg");
  if (!svg) return svgMarkup;

  const viewBox = parseSvgViewBoxAttribute(svg.getAttribute("viewBox"));
  if (!viewBox) return svgMarkup;
  svg.setAttribute("width", `${viewBox.width}`);
  svg.setAttribute("height", `${viewBox.height}`);
  svg.setAttribute("style", mergeSvgSizeStyle(svg.getAttribute("style"), viewBox.width, viewBox.height));
  return new XMLSerializer().serializeToString(svg);
}

function validSvgBounds(
  bounds: Omit<MermaidGraphBounds, "source">,
  source: MermaidGraphBoundsSource
): MermaidGraphBounds | null {
  if (
    Number.isFinite(bounds.x) &&
    Number.isFinite(bounds.y) &&
    Number.isFinite(bounds.width) &&
    Number.isFinite(bounds.height) &&
    bounds.width > 0 &&
    bounds.height > 0
  ) {
    return { ...bounds, source };
  }
  return null;
}

function getSvgClassName(element: Element): string {
  return element.getAttribute("class")?.toLowerCase() ?? "";
}

function getSvgRenderedWidth(svg: SVGSVGElement): number | null {
  const viewBox = svg.viewBox.baseVal;
  return parseSvgLength(svg.getAttribute("width")) ?? (viewBox.width > 0 ? viewBox.width : null);
}

function getSvgRenderedHeight(svg: SVGSVGElement): number | null {
  const viewBox = svg.viewBox.baseVal;
  return parseSvgLength(svg.getAttribute("height")) ?? (viewBox.height > 0 ? viewBox.height : null);
}

function normalizeMermaidSvgSize(element: HTMLDivElement | null): SVGSVGElement | null {
  const svg = element?.querySelector("svg");
  if (!(svg instanceof SVGSVGElement)) return null;

  const viewBox = svg.viewBox.baseVal;
  if (viewBox.width > 0 && viewBox.height > 0) {
    svg.setAttribute("width", `${viewBox.width}`);
    svg.setAttribute("height", `${viewBox.height}`);
    svg.style.width = `${viewBox.width}px`;
    svg.style.height = `${viewBox.height}px`;
    svg.style.maxWidth = "none";
  }
  return svg;
}

function svgUserBoundsToViewportBounds(
  svg: SVGSVGElement,
  bounds: Omit<MermaidGraphBounds, "source">
): Omit<MermaidGraphBounds, "source"> {
  const viewBox = svg.viewBox.baseVal;
  const width = getSvgRenderedWidth(svg);
  const height = getSvgRenderedHeight(svg);
  if (viewBox.width <= 0 || viewBox.height <= 0 || !width || !height) return bounds;

  const scaleX = width / viewBox.width;
  const scaleY = height / viewBox.height;
  return {
    x: (bounds.x - viewBox.x) * scaleX,
    y: (bounds.y - viewBox.y) * scaleY,
    width: bounds.width * scaleX,
    height: bounds.height * scaleY,
  };
}

function transformSvgBounds(
  bounds: Omit<MermaidGraphBounds, "source">,
  matrix: DOMMatrix
): Omit<MermaidGraphBounds, "source"> {
  const points = [
    new DOMPoint(bounds.x, bounds.y).matrixTransform(matrix),
    new DOMPoint(bounds.x + bounds.width, bounds.y).matrixTransform(matrix),
    new DOMPoint(bounds.x, bounds.y + bounds.height).matrixTransform(matrix),
    new DOMPoint(bounds.x + bounds.width, bounds.y + bounds.height).matrixTransform(matrix),
  ];
  const left = Math.min(...points.map((point) => point.x));
  const right = Math.max(...points.map((point) => point.x));
  const top = Math.min(...points.map((point) => point.y));
  const bottom = Math.max(...points.map((point) => point.y));
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function unionSvgBounds(
  current: Omit<MermaidGraphBounds, "source"> | null,
  next: Omit<MermaidGraphBounds, "source">
): Omit<MermaidGraphBounds, "source"> {
  if (!current) return next;
  const left = Math.min(current.x, next.x);
  const top = Math.min(current.y, next.y);
  const right = Math.max(current.x + current.width, next.x + next.width);
  const bottom = Math.max(current.y + current.height, next.y + next.height);
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function hasMermaidContentClass(element: SVGGraphicsElement): boolean {
  return /attribute|edge|entity|label|node|relationship|title/u.test(getSvgClassName(element));
}

function isViewportSizedRect(
  svg: SVGSVGElement,
  element: SVGGraphicsElement,
  localBounds: Omit<MermaidGraphBounds, "source">
): boolean {
  if (element.tagName.toLowerCase() !== "rect" || hasMermaidContentClass(element)) return false;
  const width = getSvgRenderedWidth(svg);
  const height = getSvgRenderedHeight(svg);
  if (!width || !height) return false;
  const bounds = svgUserBoundsToViewportBounds(svg, localBounds);
  return (
    Math.abs(bounds.x) <= 1 &&
    Math.abs(bounds.y) <= 1 &&
    bounds.width >= width - 1 &&
    bounds.height >= height - 1
  );
}

function isMermaidContentCandidate(
  svg: SVGSVGElement,
  element: SVGGraphicsElement,
  localBounds: Omit<MermaidGraphBounds, "source">
): boolean {
  if (element === svg) return false;
  if (
    element.closest(
      "defs,style,marker,clipPath,mask,pattern,filter,linearGradient,radialGradient,symbol"
    )
  ) {
    return false;
  }
  const computed = window.getComputedStyle(element);
  if (computed.display === "none" || computed.visibility === "hidden" || computed.opacity === "0") {
    return false;
  }
  return !isViewportSizedRect(svg, element, localBounds);
}

function readSvgGraphicsBounds(
  element: SVGGraphicsElement
): Omit<MermaidGraphBounds, "source"> | null {
  try {
    const box = element.getBBox();
    return box.width > 0 && box.height > 0
      ? { x: box.x, y: box.y, width: box.width, height: box.height }
      : null;
  } catch {
    return null;
  }
}

function measureMermaidSvgContent(svg: SVGSVGElement): MermaidGraphBounds | null {
  let graphBounds: Omit<MermaidGraphBounds, "source"> | null = null;
  const candidates = Array.from(svg.querySelectorAll(MERMAID_GRAPH_CONTENT_SELECTOR));
  for (const candidate of candidates) {
    if (!(candidate instanceof SVGGraphicsElement)) continue;
    const localBounds = readSvgGraphicsBounds(candidate);
    if (!localBounds || !isMermaidContentCandidate(svg, candidate, localBounds)) continue;
    const matrix = candidate.getCTM();
    const bounds = matrix
      ? transformSvgBounds(localBounds, matrix)
      : svgUserBoundsToViewportBounds(svg, localBounds);
    const validBounds = validSvgBounds(bounds, "content");
    if (!validBounds) continue;
    graphBounds = unionSvgBounds(graphBounds, validBounds);
  }
  return graphBounds ? validSvgBounds(graphBounds, "content") : null;
}

function measureMermaidSvg(element: HTMLDivElement | null): MermaidGraphBounds | null {
  const svg = normalizeMermaidSvgSize(element);
  if (!svg) return null;

  const contentBounds = measureMermaidSvgContent(svg);
  if (contentBounds) return contentBounds;

  try {
    const box = svg.getBBox();
    const bounds = validSvgBounds(svgUserBoundsToViewportBounds(svg, box), "svg-bbox");
    if (bounds) return bounds;
  } catch {
    // Some hidden or partially initialized SVGs cannot report a bbox yet.
  }

  const viewBox = svg.viewBox.baseVal;
  if (viewBox.width > 0 && viewBox.height > 0) {
    return { x: 0, y: 0, width: viewBox.width, height: viewBox.height, source: "viewbox" };
  }

  const width = parseSvgLength(svg.getAttribute("width"));
  const height = parseSvgLength(svg.getAttribute("height"));
  if (width && height) return { x: 0, y: 0, width, height, source: "size" };

  const rect = svg.getBoundingClientRect();
  if (rect.width > 0 && rect.height > 0) {
    return { x: 0, y: 0, width: rect.width, height: rect.height, source: "client-rect" };
  }
  return null;
}

function MermaidGraphPreview({
  mermaid,
  revisionId,
}: {
  mermaid: string;
  revisionId: string;
}) {
  const [svg, setSvg] = useState("");
  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState("");
  const [transform, setTransform] = useState<MermaidGraphTransform>(
    DEFAULT_MERMAID_GRAPH_TRANSFORM
  );
  const [transformReady, setTransformReady] = useState(false);
  const [contentBounds, setContentBounds] = useState<MermaidGraphBounds | null>(null);
  const [dragging, setDragging] = useState(false);
  const renderRequestRef = useRef(0);
  const viewportRef = useRef<HTMLDivElement>(null);
  const controlsRef = useRef<HTMLDivElement>(null);
  const graphContentRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const reactId = useId();
  const renderIdBase = `ontology-mermaid-${reactId.replace(/[^a-zA-Z0-9_-]/gu, "")}`;

  const fitGraph = useCallback(() => {
    const viewport = viewportRef.current;
    const graphBounds = measureMermaidSvg(graphContentRef.current);
    if (!viewport || !graphBounds) return;

    const viewportWidth = viewport.clientWidth;
    const viewportHeight = viewport.clientHeight;
    const availableWidth = Math.max(1, viewportWidth - MERMAID_GRAPH_FIT_PADDING * 2);
    const availableHeight = Math.max(1, viewportHeight - MERMAID_GRAPH_FIT_PADDING * 2);
    const nextScale = clamp(
      Math.min(availableWidth / graphBounds.width, availableHeight / graphBounds.height),
      MERMAID_GRAPH_MIN_SCALE,
      MERMAID_GRAPH_FIT_MAX_SCALE
    );
    let nextX = (viewportWidth - graphBounds.width * nextScale) / 2 - graphBounds.x * nextScale;
    let nextY = (viewportHeight - graphBounds.height * nextScale) / 2 - graphBounds.y * nextScale;

    const controls = controlsRef.current;
    if (controls) {
      const controlsLeft = controls.offsetLeft - MERMAID_GRAPH_FIT_PADDING;
      const controlsTop = controls.offsetTop - MERMAID_GRAPH_FIT_PADDING;
      const controlsBottom =
        controls.offsetTop + controls.offsetHeight + MERMAID_GRAPH_FIT_PADDING;
      const contentTop = nextY + graphBounds.y * nextScale;
      const contentBottom = nextY + (graphBounds.y + graphBounds.height) * nextScale;
      const contentRight = nextX + (graphBounds.x + graphBounds.width) * nextScale;
      if (
        contentRight > controlsLeft &&
        contentTop < controlsBottom &&
        contentBottom > controlsTop
      ) {
        const safeY = controlsBottom - graphBounds.y * nextScale;
        const maxY =
          viewportHeight -
          MERMAID_GRAPH_FIT_PADDING -
          (graphBounds.y + graphBounds.height) * nextScale;
        if (safeY <= maxY) {
          nextY = Math.max(nextY, safeY);
        } else {
          const safeX = controlsLeft - (graphBounds.x + graphBounds.width) * nextScale;
          const minX = MERMAID_GRAPH_FIT_PADDING - graphBounds.x * nextScale;
          if (safeX >= minX) nextX = Math.min(nextX, safeX);
        }
      }
    }

    setTransform({
      scale: nextScale,
      x: nextX,
      y: nextY,
    });
    setContentBounds(graphBounds);
    setTransformReady(true);
  }, []);

  const zoomGraph = useCallback((direction: "in" | "out") => {
    const viewport = viewportRef.current;
    const centerX = viewport ? viewport.clientWidth / 2 : 0;
    const centerY = viewport ? viewport.clientHeight / 2 : 0;
    const multiplier = direction === "in" ? MERMAID_GRAPH_ZOOM_STEP : 1 / MERMAID_GRAPH_ZOOM_STEP;

    setTransform((current) => {
      const nextScale = clamp(
        current.scale * multiplier,
        MERMAID_GRAPH_MIN_SCALE,
        MERMAID_GRAPH_MAX_SCALE
      );
      if (nextScale === current.scale) return current;
      return {
        scale: nextScale,
        x: centerX - ((centerX - current.x) / current.scale) * nextScale,
        y: centerY - ((centerY - current.y) / current.scale) * nextScale,
      };
    });
  }, []);

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !svg || rendering) return;
    if (
      event.target instanceof Element &&
      event.target.closest("[data-mermaid-graph-controls='true']")
    ) {
      return;
    }
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: transform.x,
      originY: transform.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  };

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setTransform((current) => ({
      ...current,
      x: drag.originX + event.clientX - drag.startX,
      y: drag.originY + event.clientY - drag.startY,
    }));
  };

  const finishPointerDrag = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
    setDragging(false);
  };

  const handleGraphKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!svg || rendering) return;
    const panDistance = event.shiftKey
      ? MERMAID_GRAPH_KEYBOARD_PAN * 2
      : MERMAID_GRAPH_KEYBOARD_PAN;
    const deltaByKey: Partial<Record<string, { x: number; y: number }>> = {
      ArrowDown: { x: 0, y: panDistance },
      ArrowLeft: { x: -panDistance, y: 0 },
      ArrowRight: { x: panDistance, y: 0 },
      ArrowUp: { x: 0, y: -panDistance },
    };
    const delta = deltaByKey[event.key];
    if (!delta) return;
    event.preventDefault();
    setTransform((current) => ({
      ...current,
      x: current.x + delta.x,
      y: current.y + delta.y,
    }));
  };

  useEffect(() => {
    const source = mermaid.trim();
    renderRequestRef.current += 1;
    const requestId = renderRequestRef.current;
    if (!source) {
      setSvg("");
      setRenderError("");
      setRendering(false);
      setTransform(DEFAULT_MERMAID_GRAPH_TRANSFORM);
      setTransformReady(false);
      setContentBounds(null);
      dragRef.current = null;
      setDragging(false);
      return undefined;
    }

    let cancelled = false;
    setSvg("");
    setRenderError("");
    setRendering(true);
    setTransform(DEFAULT_MERMAID_GRAPH_TRANSFORM);
    setTransformReady(false);
    setContentBounds(null);
    dragRef.current = null;
    setDragging(false);

    void (async () => {
      try {
        const { default: mermaidApi } = await import("mermaid");
        mermaidApi.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
        });
        const result = await mermaidApi.render(`${renderIdBase}-${requestId}`, source);
        if (cancelled || renderRequestRef.current !== requestId) return;
        setSvg(normalizeMermaidSvgMarkup(result.svg));
      } catch (err) {
        if (cancelled || renderRequestRef.current !== requestId) return;
        setRenderError(
          err instanceof Error ? err.message : t("profiles.ontologyBuild.error.mermaidRender")
        );
      } finally {
        if (!cancelled && renderRequestRef.current === requestId) setRendering(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [mermaid, renderIdBase, revisionId]);

  useEffect(() => {
    if (!svg || rendering) return undefined;
    let followupFrameId = 0;
    const frameId = window.requestAnimationFrame(() => {
      fitGraph();
      followupFrameId = window.requestAnimationFrame(() => fitGraph());
    });
    return () => {
      window.cancelAnimationFrame(frameId);
      if (followupFrameId) window.cancelAnimationFrame(followupFrameId);
    };
  }, [fitGraph, rendering, svg]);

  if (!mermaid.trim()) {
    return (
      <div
        className="grid min-h-80 place-items-center rounded-md border border-border bg-muted/20 p-4 text-center"
        data-testid="ontology-mermaid-graph-empty"
      >
        <div className="grid gap-1">
          <p className="text-sm font-semibold text-foreground">
            {t("profiles.ontologyBuild.mermaidEmptyCode")}
          </p>
          <p className="text-xs leading-5 text-muted">
            {t("profiles.ontologyBuild.mermaidEmptyCodeHint")}
          </p>
        </div>
      </div>
    );
  }

  if (renderError) {
    return (
      <div data-testid="ontology-mermaid-render-error">
        <Banner severity="danger" title={t("profiles.ontologyBuild.error.mermaidRender")}>
          {renderError}
        </Banner>
      </div>
    );
  }

  return (
    <div
      ref={viewportRef}
      className="relative h-[32rem] min-h-80 overflow-hidden rounded-md border border-border bg-background md:h-[40rem]"
      aria-busy={rendering}
      aria-label={t("profiles.ontologyBuild.mermaidGraphLabel")}
      data-testid="ontology-mermaid-rendered-graph"
      onKeyDown={handleGraphKeyDown}
      onPointerCancel={finishPointerDrag}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishPointerDrag}
      role="group"
      tabIndex={0}
      style={{ touchAction: "none" }}
    >
      {rendering ? (
        <div
          className="grid h-full place-items-center p-4 text-sm text-muted"
          role="status"
          aria-live="polite"
          data-testid="ontology-mermaid-rendering"
        >
          <span className="inline-flex items-center gap-2">
            <Loader2
              size={16}
              className="animate-spin text-primary motion-reduce:animate-none"
              aria-hidden="true"
            />
            {t("profiles.ontologyBuild.mermaidRendering")}
          </span>
        </div>
      ) : null}
      {!rendering && svg ? (
        <>
          <div
            ref={controlsRef}
            className="absolute right-3 top-3 z-10 flex gap-1 rounded-md border border-border bg-card p-1 shadow-sm"
            role="group"
            aria-label={t("profiles.ontologyBuild.mermaidGraphLabel")}
            data-testid="ontology-mermaid-graph-controls"
            data-mermaid-graph-controls="true"
          >
            <Button
              type="button"
              size="md"
              variant="ghost"
              className="w-9 px-0"
              aria-label={t("nl2sql.ontology.graphZoomIn")}
              title={t("nl2sql.ontology.graphZoomIn")}
              disabled={transform.scale >= MERMAID_GRAPH_MAX_SCALE - 0.001}
              onClick={() => zoomGraph("in")}
              data-testid="ontology-mermaid-graph-zoom-in"
            >
              <Plus size={15} aria-hidden="true" />
            </Button>
            <Button
              type="button"
              size="md"
              variant="ghost"
              className="w-9 px-0"
              aria-label={t("nl2sql.ontology.graphZoomOut")}
              title={t("nl2sql.ontology.graphZoomOut")}
              disabled={transform.scale <= MERMAID_GRAPH_MIN_SCALE + 0.001}
              onClick={() => zoomGraph("out")}
              data-testid="ontology-mermaid-graph-zoom-out"
            >
              <Minus size={15} aria-hidden="true" />
            </Button>
            <Button
              type="button"
              size="md"
              variant="ghost"
              className="w-9 px-0"
              aria-label={t("nl2sql.ontology.graphFit")}
              title={t("nl2sql.ontology.graphFit")}
              onClick={fitGraph}
              data-testid="ontology-mermaid-graph-fit"
            >
              <Maximize2 size={15} aria-hidden="true" />
            </Button>
          </div>
          <div
            ref={graphContentRef}
            className={
              "absolute left-0 top-0 select-none [&_svg]:block [&_svg]:h-auto [&_svg]:max-w-none [&_svg]:font-sans " +
              (dragging ? "cursor-grabbing" : "cursor-grab")
            }
            role="img"
            aria-label={t("profiles.ontologyBuild.mermaidGraphLabel")}
            data-testid="ontology-mermaid-graph-content"
            data-transform-ready={transformReady ? "true" : "false"}
            data-transform-scale={transform.scale.toFixed(4)}
            data-transform-x={transform.x.toFixed(2)}
            data-transform-y={transform.y.toFixed(2)}
            data-content-bounds-x={contentBounds?.x.toFixed(2)}
            data-content-bounds-y={contentBounds?.y.toFixed(2)}
            data-content-bounds-width={contentBounds?.width.toFixed(2)}
            data-content-bounds-height={contentBounds?.height.toFixed(2)}
            data-content-bounds-source={contentBounds?.source}
            style={{
              transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
              transformOrigin: "0 0",
            }}
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        </>
      ) : null}
    </div>
  );
}

export function OntologyMermaidPanel({
  profileId,
  graphRevisionId = "",
  refreshToken = 0,
}: OntologyMermaidPanelProps) {
  const [mermaid, setMermaid] = useState("");
  const [mermaidRevisionId, setMermaidRevisionId] = useState("");
  const [activeTab, setActiveTab] = useState<MermaidPanelTab>("code");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestIdRef = useRef(0);
  const handledRefreshTokenRef = useRef(refreshToken);

  useEffect(() => {
    requestIdRef.current += 1;
    setMermaid("");
    setMermaidRevisionId("");
    setError("");
    setLoading(false);
  }, [profileId]);

  const loadMermaid = useCallback(async (targetProfileId = profileId) => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    setError("");
    try {
      const data = await fetchProfileOntologyMermaid(targetProfileId);
      if (requestIdRef.current !== requestId) return;
      setMermaid(data.mermaid);
      setMermaidRevisionId(data.ontology_revision_id);
    } catch (err) {
      if (requestIdRef.current !== requestId) return;
      setError(
        err instanceof Error ? err.message : t("profiles.ontologyBuild.error.mermaid")
      );
    } finally {
      if (requestIdRef.current === requestId) setLoading(false);
    }
  }, [profileId]);

  useEffect(() => {
    if (refreshToken === handledRefreshTokenRef.current) return;
    handledRefreshTokenRef.current = refreshToken;
    requestIdRef.current += 1;
    setMermaid("");
    setMermaidRevisionId("");
    setError("");
    setLoading(false);
    if (!profileId) return;
    void loadMermaid(profileId);
  }, [loadMermaid, profileId, refreshToken]);

  const copyMermaid = async () => {
    try {
      await navigator.clipboard.writeText(mermaid);
      toast.success(t("common.action.copied"));
    } catch {
      toast.error(t("common.action.copyFailed"));
    }
  };

  const revisionMismatch = Boolean(
    graphRevisionId && mermaidRevisionId && graphRevisionId !== mermaidRevisionId
  );
  const tabs = [
    {
      id: "code" as const,
      label: t("profiles.ontologyBuild.mermaidTab.code"),
      icon: FileCode2,
    },
    {
      id: "graph" as const,
      label: t("profiles.ontologyBuild.mermaidTab.graph"),
      icon: Network,
    },
  ];

  return (
    <section
      className="grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm"
      aria-labelledby="ontology-mermaid-heading"
      data-testid="ontology-mermaid-panel"
    >
      <DbObjectPanelHeader
        headingId="ontology-mermaid-heading"
        icon={FileCode2}
        title={t("profiles.ontologyBuild.mermaidSectionTitle")}
        description={t("profiles.ontologyBuild.mermaidHint")}
        action={<StatusBadge variant="neutral" label={t("profiles.ontologyBuild.readOnly")} />}
      />
      <div className="grid gap-3 rounded-md border border-border bg-background p-3">
        {error ? <Banner severity="danger">{error}</Banner> : null}
        {revisionMismatch ? (
          <div data-testid="ontology-mermaid-revision-mismatch">
            <Banner
              severity="warning"
              title={t("profiles.ontologyBuild.mermaidRevisionMismatchTitle")}
            >
              {t("profiles.ontologyBuild.mermaidRevisionMismatch")}
            </Banner>
          </div>
        ) : null}
        <ContentActionBar
          ariaLabel={t("profiles.ontologyBuild.mermaidTitle")}
          testId="ontology-mermaid-actions"
          title={t("profiles.ontologyBuild.mermaidTitle")}
          meta={
            <span
              className="grid gap-1"
              aria-live="polite"
              data-testid="ontology-mermaid-revision-summary"
            >
              {graphRevisionId ? (
                <span data-testid="ontology-graph-revision-id">
                  {t("profiles.ontologyBuild.graphRevisionLabel")}{" "}
                  <code className="break-all font-mono text-[11px] text-foreground">
                    {graphRevisionId}
                  </code>
                </span>
              ) : null}
              <span data-testid="ontology-mermaid-revision-id">
                {t("profiles.ontologyBuild.mermaidRevisionLabel")}{" "}
                {mermaidRevisionId ? (
                  <code className="break-all font-mono text-[11px] text-foreground">
                    {mermaidRevisionId}
                  </code>
                ) : (
                  t("profiles.ontologyBuild.mermaidRevisionPending")
                )}
              </span>
            </span>
          }
        >
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={loading}
            onClick={() => void loadMermaid()}
          >
            <FileCode2 size={15} aria-hidden="true" />
            <span>{t("profiles.ontologyBuild.mermaidLoad")}</span>
          </Button>
          {mermaid ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              aria-label={t("profiles.ontologyBuild.mermaidCopy")}
              onClick={() => void copyMermaid()}
            >
              <ClipboardCopy size={15} aria-hidden="true" />
              <span>{t("profiles.ontologyBuild.mermaidCopy")}</span>
            </Button>
          ) : null}
        </ContentActionBar>
        <ManagementTabs
          activeView={activeTab}
          tabs={tabs}
          idPrefix="ontology-mermaid"
          ariaLabel={t("profiles.ontologyBuild.mermaidTabsLabel")}
          onViewChange={setActiveTab}
        />
        {activeTab === "code" ? (
          <div
            id="ontology-mermaid-panel-code"
            role="tabpanel"
            aria-labelledby="ontology-mermaid-tab-code"
            data-testid="ontology-mermaid-code-tab-panel"
          >
            {mermaid ? (
              <pre
                className="max-h-80 max-w-full overflow-auto rounded-md border border-border bg-code p-3 font-mono text-sm leading-6 text-code-fg"
                data-testid="ontology-build-mermaid"
              >
                <code>{mermaid}</code>
              </pre>
            ) : (
              <div
                className="grid min-h-40 place-items-center rounded-md border border-border bg-muted/20 p-4 text-center"
                data-testid="ontology-mermaid-code-empty"
              >
                <div className="grid gap-1">
                  <p className="text-sm font-semibold text-foreground">
                    {t("profiles.ontologyBuild.mermaidEmptyCode")}
                  </p>
                  <p className="text-xs leading-5 text-muted">
                    {t("profiles.ontologyBuild.mermaidEmptyCodeHint")}
                  </p>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div
            id="ontology-mermaid-panel-graph"
            role="tabpanel"
            aria-labelledby="ontology-mermaid-tab-graph"
            data-testid="ontology-mermaid-graph-tab-panel"
          >
            <MermaidGraphPreview mermaid={mermaid} revisionId={mermaidRevisionId} />
          </div>
        )}
      </div>
    </section>
  );
}
