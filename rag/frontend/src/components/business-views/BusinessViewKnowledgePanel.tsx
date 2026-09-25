import { Plus, Save, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormStatus,
  Skeleton,
  Tabs,
  toast,
} from "@engchina/production-ready-ui";

import { ApiError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useValuesChanged } from "@/lib/render-sync";
import {
  useDomainKeywords,
  useSaveDomainKeywords,
  useSuggestDomainKeywords,
} from "@/lib/queries";

import { ApprovedFaqManager } from "./ApprovedFaqManager";
import { RuntimeKnowledgeManager } from "./RuntimeKnowledgeManager";

type KnowledgeTab = "domainKeywords" | "approvedFaq" | "runtimeKnowledge";

/** 業務ビュー単位の知識(ドメインキーワード等)。編集中の業務ビューにだけ表示する。 */
export function BusinessViewKnowledgePanel({
  businessViewId,
}: {
  businessViewId: string;
}) {
  const [tab, setTab] = useState<KnowledgeTab>("domainKeywords");
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("businessViews.knowledge.title")}</CardTitle>
        <CardDescription>
          {t("businessViews.knowledge.description")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Tabs
          idPrefix="business-view-knowledge"
          ariaLabel={t("businessViews.knowledge.title")}
          value={tab}
          onChange={(value) => setTab(value as KnowledgeTab)}
          items={[
            {
              id: "domainKeywords",
              label: t("businessViews.domainKeywords.title"),
            },
            { id: "approvedFaq", label: t("businessViews.faq.title") },
            { id: "runtimeKnowledge", label: t("businessViews.runtime.title") },
          ]}
        />
        <div
          role="tabpanel"
          id={`business-view-knowledge-panel-${tab}`}
          aria-labelledby={`business-view-knowledge-tab-${tab}`}
        >
          {tab === "domainKeywords" ? (
            <DomainKeywordsEditor businessViewId={businessViewId} />
          ) : tab === "approvedFaq" ? (
            <ApprovedFaqManager businessViewId={businessViewId} />
          ) : (
            <RuntimeKnowledgeManager businessViewId={businessViewId} />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function parseKeywords(text: string): string[] {
  return [
    ...new Set(
      text
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  ];
}

function DomainKeywordsEditor({ businessViewId }: { businessViewId: string }) {
  const query = useDomainKeywords(businessViewId);
  const save = useSaveDomainKeywords(businessViewId);
  const suggest = useSuggestDomainKeywords(businessViewId);
  const [text, setText] = useState("");
  const saved = useMemo(
    () => query.data?.keywords ?? [],
    [query.data?.keywords],
  );

  // 保存済みの語句が変わったレンダーで、編集欄を保存値に戻す。
  const savedChanged = useValuesChanged([saved]);
  if (savedChanged) {
    setText(saved.join("\n"));
  }

  const current = parseKeywords(text);
  const dirty = current.join("\n") !== saved.join("\n");
  useLeaveGuard(dirty);
  const candidates = (suggest.data?.candidates ?? []).filter(
    (candidate) => !current.includes(candidate.keyword),
  );

  if (query.isPending) return <Skeleton className="h-40 w-full" />;

  return (
    <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="min-w-0 space-y-2">
        <label
          htmlFor="domain-keywords-editor"
          className="text-sm font-medium text-fg"
        >
          {t("businessViews.domainKeywords.editorLabel")}
        </label>
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("businessViews.domainKeywords.help")}
        </p>
        <textarea
          id="domain-keywords-editor"
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={12}
          placeholder={t("businessViews.domainKeywords.placeholder")}
          disabled={save.isPending}
          className="w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 text-sm outline-none focus-visible:border-focus-ring disabled:cursor-not-allowed disabled:opacity-50"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            icon={Save}
            loading={save.isPending}
            disabled={!dirty}
            onClick={() =>
              save.mutate(current, {
                onSuccess: (data) =>
                  toast.success(
                    t("businessViews.domainKeywords.saved", {
                      count: data.keywords.length,
                    }),
                  ),
                onError: (error) =>
                  toast.error(
                    error instanceof ApiError
                      ? error.message
                      : t("businessViews.domainKeywords.saveError"),
                  ),
              })
            }
          >
            {t("businessViews.domainKeywords.save")}
          </Button>
          <span className="tnum text-xs text-fg-muted">
            {t("businessViews.domainKeywords.count", { count: current.length })}
          </span>
        </div>
      </div>
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm font-medium text-fg">
            {t("businessViews.domainKeywords.candidates")}
          </span>
          <Button
            size="sm"
            variant="secondary"
            icon={Sparkles}
            loading={suggest.isPending}
            onClick={() => suggest.mutate()}
          >
            {t("businessViews.domainKeywords.suggest")}
          </Button>
        </div>
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("businessViews.domainKeywords.suggestHelp")}
        </p>
        {suggest.isError ? (
          <FormStatus
            tone="danger"
            message={
              suggest.error instanceof ApiError
                ? suggest.error.message
                : t("businessViews.domainKeywords.suggestError")
            }
          />
        ) : null}
        {suggest.data && candidates.length === 0 ? (
          <p className="text-xs text-fg-muted">
            {t("businessViews.domainKeywords.noCandidates")}
          </p>
        ) : null}
        {candidates.length > 0 ? (
          <ul
            className="flex flex-wrap gap-2"
            aria-label={t("businessViews.domainKeywords.candidates")}
          >
            {candidates.map((candidate) => (
              <li key={candidate.keyword}>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={Plus}
                  title={t("businessViews.domainKeywords.candidateStats", {
                    frequency: candidate.frequency,
                    documents: candidate.document_count,
                  })}
                  onClick={() =>
                    setText((value) =>
                      [...parseKeywords(value), candidate.keyword].join("\n"),
                    )
                  }
                >
                  {candidate.keyword}
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
