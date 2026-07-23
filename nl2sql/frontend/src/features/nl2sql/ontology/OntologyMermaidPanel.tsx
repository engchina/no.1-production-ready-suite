import { useEffect, useState } from "react";
import { ClipboardCopy, FileCode2 } from "lucide-react";

import { Banner, Button, StatusBadge, toast } from "@engchina/production-ready-ui";

import { DbObjectPanelHeader } from "../components/DbObjectManagementShared";
import { fetchProfileOntologyMermaid } from "./api";
import { t } from "@/lib/i18n";

export function OntologyMermaidPanel({ profileId }: { profileId: string }) {
  const [mermaid, setMermaid] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setMermaid("");
    setError("");
  }, [profileId]);

  const loadMermaid = async () => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchProfileOntologyMermaid(profileId);
      setMermaid(data.mermaid);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("profiles.ontologyBuild.error.mermaid")
      );
    } finally {
      setLoading(false);
    }
  };

  const copyMermaid = async () => {
    try {
      await navigator.clipboard.writeText(mermaid);
      toast.success(t("common.action.copied"));
    } catch {
      toast.error(t("common.action.copyFailed"));
    }
  };

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
      <details className="rounded-md border border-border bg-background p-3">
        <summary className="flex min-h-11 cursor-pointer items-center gap-2 text-sm font-semibold text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
          <FileCode2 size={16} className="text-primary" aria-hidden="true" />
          {t("profiles.ontologyBuild.mermaidTitle")}
        </summary>
        <div className="mt-3 grid gap-3">
          {error ? <Banner severity="danger">{error}</Banner> : null}
          <div className="flex flex-wrap gap-2">
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
                variant="ghost"
                size="sm"
                aria-label={t("profiles.ontologyBuild.mermaidCopy")}
                onClick={() => void copyMermaid()}
              >
                <ClipboardCopy size={15} aria-hidden="true" />
                <span>{t("profiles.ontologyBuild.mermaidCopy")}</span>
              </Button>
            ) : null}
          </div>
          {mermaid ? (
            <pre
              className="max-h-80 max-w-full overflow-auto rounded-md border border-border bg-code p-3 font-mono text-sm leading-6 text-code-fg"
              data-testid="ontology-build-mermaid"
            >
              <code>{mermaid}</code>
            </pre>
          ) : null}
        </div>
      </details>
    </section>
  );
}
