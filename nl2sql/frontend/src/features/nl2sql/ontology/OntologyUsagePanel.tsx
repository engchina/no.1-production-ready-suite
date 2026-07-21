import { Check, ClipboardCopy, Search, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { Banner, Button, StatusBadge, toast } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import {
  confirmOntologyProfileRecommendation,
  recommendOntologyProfiles,
  searchOntologyContext,
} from "./api";
import type {
  OntologyContextSearchResult,
  OntologyProfileRecommendation,
  ProfileOntologyView,
} from "./types";

type PreviewTab = "llm" | "owl" | "shacl" | "mermaid";

const inputClass =
  "min-h-11 w-full rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";
const textareaClass = `${inputClass} min-h-28 resize-y leading-6`;

export function OntologyUsagePanel({
  profileId,
  view,
  onSaveScenarios,
}: {
  profileId: string;
  view: ProfileOntologyView | null;
  onSaveScenarios: (scenarios: string[], keywords: string[]) => Promise<void>;
}) {
  const [scenariosText, setScenariosText] = useState(
    () => view?.activation_scenarios_ja?.join("\n") ?? ""
  );
  const [keywordsText, setKeywordsText] = useState(
    () => view?.activation_keywords?.join("、") ?? ""
  );
  const [question, setQuestion] = useState("");
  const [recommendation, setRecommendation] = useState<OntologyProfileRecommendation | null>(null);
  const [context, setContext] = useState<OntologyContextSearchResult | null>(null);
  const [previewTab, setPreviewTab] = useState<PreviewTab>("llm");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const previewText = useMemo(() => {
    if (!context) return "";
    if (previewTab === "owl") return context.owl_turtle;
    if (previewTab === "shacl") return context.shacl_turtle;
    if (previewTab === "mermaid") return context.mermaid;
    return context.llm_markdown;
  }, [context, previewTab]);

  const saveScenarios = async () => {
    setBusy("scenarios");
    setError("");
    try {
      await onSaveScenarios(
        scenariosText.split("\n").map((item) => item.trim()).filter(Boolean),
        keywordsText.split(/[、,\n]/).map((item) => item.trim()).filter(Boolean)
      );
      toast.success(t("ontologyUsage.scenarios.saved"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("ontologyUsage.error.save"));
    } finally {
      setBusy("");
    }
  };

  const recommend = async () => {
    if (!question.trim()) return;
    setBusy("recommend");
    setError("");
    setContext(null);
    try {
      setRecommendation(await recommendOntologyProfiles(question.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("ontologyUsage.error.recommend"));
    } finally {
      setBusy("");
    }
  };

  const confirm = async (candidateProfileId: string, revisionId: string) => {
    if (!recommendation) return;
    setBusy(`confirm:${candidateProfileId}`);
    try {
      await confirmOntologyProfileRecommendation(
        recommendation.id,
        candidateProfileId,
        revisionId
      );
      toast.success(t("ontologyUsage.recommend.confirmed"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("ontologyUsage.error.confirm"));
    } finally {
      setBusy("");
    }
  };

  const searchContext = async () => {
    if (!question.trim() || !view?.ontology_revision_id) return;
    setBusy("context");
    setError("");
    try {
      setContext(
        await searchOntologyContext(profileId, {
          question: question.trim(),
          ontologyRevisionId: view.ontology_revision_id,
        })
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : t("ontologyUsage.error.context"));
    } finally {
      setBusy("");
    }
  };

  return (
    <section className="grid gap-4" aria-label={t("ontologyUsage.title")}>
      {error ? <Banner severity="danger">{error}</Banner> : null}

      <section className="grid gap-3 rounded-md border border-border bg-card p-4">
        <div>
          <h2 className="text-base font-semibold text-foreground">
            {t("ontologyUsage.scenarios.title")}
          </h2>
          <p className="mt-1 text-sm leading-6 text-muted">
            {t("ontologyUsage.scenarios.hint")}
          </p>
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          <label className="grid gap-1 text-sm font-medium text-foreground">
            <span>{t("ontologyUsage.scenarios.label")}</span>
            <textarea
              className={textareaClass}
              value={scenariosText}
              onChange={(event) => setScenariosText(event.currentTarget.value)}
            />
          </label>
          <label className="grid gap-1 text-sm font-medium text-foreground">
            <span>{t("ontologyUsage.keywords.label")}</span>
            <textarea
              className={textareaClass}
              value={keywordsText}
              onChange={(event) => setKeywordsText(event.currentTarget.value)}
            />
          </label>
        </div>
        <div>
          <Button
            type="button"
            variant="primary"
            size="sm"
            loading={busy === "scenarios"}
            onClick={() => void saveScenarios()}
          >
            <ShieldCheck size={16} aria-hidden="true" />
            {t("ontologyUsage.scenarios.save")}
          </Button>
        </div>
      </section>

      <section className="grid gap-3 rounded-md border border-border bg-card p-4">
        <div>
          <h2 className="text-base font-semibold text-foreground">
            {t("ontologyUsage.recommend.title")}
          </h2>
          <p className="mt-1 text-sm leading-6 text-muted">
            {t("ontologyUsage.recommend.hint")}
          </p>
        </div>
        <label className="grid gap-1 text-sm font-medium text-foreground">
          <span>{t("ontologyUsage.question.label")}</span>
          <textarea
            className={textareaClass}
            value={question}
            onChange={(event) => setQuestion(event.currentTarget.value)}
          />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="primary"
            size="sm"
            loading={busy === "recommend"}
            disabled={!question.trim()}
            onClick={() => void recommend()}
          >
            <Search size={16} aria-hidden="true" />
            {t("ontologyUsage.recommend.run")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={busy === "context"}
            disabled={!question.trim() || !view?.ontology_revision_id}
            onClick={() => void searchContext()}
          >
            {t("ontologyUsage.context.run")}
          </Button>
        </div>

        {recommendation ? (
          recommendation.candidates.length > 0 ? (
            <ol className="grid gap-2" aria-label={t("ontologyUsage.recommend.results")}>
              {recommendation.candidates.map((candidate, index) => (
                <li
                  key={candidate.profile_id}
                  className="grid gap-2 rounded-md border border-border bg-background p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge variant="neutral" label={`#${index + 1}`} />
                      <span className="font-semibold text-foreground">
                        {candidate.profile_name}
                      </span>
                      <StatusBadge
                        variant="info"
                        label={`${Math.round(candidate.score * 100)}%`}
                      />
                    </div>
                    <p className="mt-1 text-sm leading-6 text-muted">
                      {candidate.reasons_ja.join(" ")}
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant={candidate.profile_id === profileId ? "primary" : "secondary"}
                    size="sm"
                    loading={busy === `confirm:${candidate.profile_id}`}
                    onClick={() =>
                      void confirm(candidate.profile_id, candidate.ontology_revision_id)
                    }
                  >
                    <Check size={16} aria-hidden="true" />
                    {t("ontologyUsage.recommend.confirm")}
                  </Button>
                </li>
              ))}
            </ol>
          ) : (
            <div className="grid gap-2 rounded-md border border-border bg-background p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
              <Banner severity="info">{t("ontologyUsage.recommend.empty")}</Banner>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={busy === `confirm:${profileId}`}
                onClick={() => void confirm(profileId, recommendation.ontology_revision_id)}
              >
                <Check size={16} aria-hidden="true" />
                {t("ontologyUsage.recommend.confirmCurrent")}
              </Button>
            </div>
          )
        ) : null}
      </section>

      {context ? (
        <section className="grid gap-3 rounded-md border border-border bg-card p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold text-foreground">
                {t("ontologyUsage.context.title")}
              </h2>
              <p className="mt-1 break-all font-mono text-xs text-muted">
                SHA-256: {context.context_hash}
              </p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                void navigator.clipboard
                  .writeText(previewText)
                  .then(() => toast.success(t("ontologyUsage.context.copied")))
                  .catch(() => toast.error(t("common.action.copyFailed")));
              }}
            >
              <ClipboardCopy size={16} aria-hidden="true" />
              {t("ontologyUsage.context.copy")}
            </Button>
          </div>
          <div className="flex flex-wrap gap-2" role="tablist" aria-label={t("ontologyUsage.context.tabs")}>
            {(["llm", "owl", "shacl", "mermaid"] as const).map((tab) => (
              <Button
                key={tab}
                type="button"
                variant={previewTab === tab ? "primary" : "secondary"}
                size="sm"
                role="tab"
                aria-selected={previewTab === tab}
                onClick={() => setPreviewTab(tab)}
              >
                {t(`ontologyUsage.context.tab.${tab}`)}
              </Button>
            ))}
          </div>
          <pre className="max-h-[32rem] overflow-auto rounded-md border border-border bg-code p-3 text-sm leading-6 text-code-fg">
            <code>{previewText}</code>
          </pre>
        </section>
      ) : null}
    </section>
  );
}
