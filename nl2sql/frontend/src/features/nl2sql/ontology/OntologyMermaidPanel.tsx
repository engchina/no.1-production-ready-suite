import { Button } from "@/components/ui/button";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ClipboardCopy, FileCode2, Loader2, Network } from "lucide-react";

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
  const renderRequestRef = useRef(0);
  const reactId = useId();
  const renderIdBase = `ontology-mermaid-${reactId.replace(/[^a-zA-Z0-9_-]/gu, "")}`;

  useEffect(() => {
    const source = mermaid.trim();
    renderRequestRef.current += 1;
    const requestId = renderRequestRef.current;
    if (!source) {
      setSvg("");
      setRenderError("");
      setRendering(false);
      return undefined;
    }

    let cancelled = false;
    setSvg("");
    setRenderError("");
    setRendering(true);

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
        setSvg(result.svg);
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
      className="min-h-80 max-h-[36rem] overflow-auto rounded-md border border-border bg-background p-3"
      aria-busy={rendering}
      aria-label={t("profiles.ontologyBuild.mermaidGraphLabel")}
      data-testid="ontology-mermaid-rendered-graph"
    >
      {rendering ? (
        <div
          className="grid min-h-72 place-items-center text-sm text-muted"
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
        <div
          className="min-w-max [&_svg]:h-auto [&_svg]:max-w-none [&_svg]:font-sans"
          role="img"
          aria-label={t("profiles.ontologyBuild.mermaidGraphLabel")}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
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
