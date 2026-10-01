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
  ProcessingIndicator,
  Skeleton,
  Tabs,
  TextareaField,
  toast,
  TimedLoadingState,
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
import { RuntimeKnowledgeManager, runtimeKindLabel } from "./RuntimeKnowledgeManager";

type KnowledgeTab = "approvedFaq" | "terms" | "domainKeywords" | "rules";

/** 業務ビュー単位の知識(ドメインキーワード等)。編集中の業務ビューにだけ表示する。 */
export function BusinessViewKnowledgePanel({
  businessViewId,
}: {
  businessViewId: string;
}) {
  // よく使う Approved FAQ を先頭・既定のタブにする(#636)。
  const [tab, setTab] = useState<KnowledgeTab>("approvedFaq");
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
          // 回答フローで使う順に並べる（#682）: 類似問の提示 → 用語・同義語で質問を広げる →
          // ドメインキーワードでキーワード検索の語を切り出す → 回答ルールを回答の生成に渡す。
          items={[
            { id: "approvedFaq", label: t("businessViews.faq.title") },
            { id: "terms", label: runtimeKindLabel("terms") },
            {
              id: "domainKeywords",
              label: t("businessViews.domainKeywords.title"),
            },
            { id: "rules", label: runtimeKindLabel("rules") },
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
            // 種類ごとに編集中の入力を持つので、タブを切り替えたら作り直す。
            <RuntimeKnowledgeManager key={tab} businessViewId={businessViewId} kind={tab} />
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

  if (query.isPending) {
    return (
      <TimedLoadingState
        label={t("businessViews.knowledge.loading")}
        operationKey="business-view-knowledge-load"
        testId="business-view-knowledge-loading"
      >
        <Skeleton className="h-40 w-full" />
      </TimedLoadingState>
    );
  }

  return (
    <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="min-w-0 space-y-2">
        <TextareaField
          id="domain-keywords-editor"
          label={t("businessViews.domainKeywords.editorLabel")}
          helper={t("businessViews.domainKeywords.help")}
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={12}
          placeholder={t("businessViews.domainKeywords.placeholder")}
          disabled={save.isPending}
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
        {suggest.isPending ? (
          // 参照ナレッジベースの配信中チャンクを走査するため、件数が多いと数秒以上かかる。
          <ProcessingIndicator
            active
            label={t("businessViews.domainKeywords.suggesting")}
            operationKey="domain-keywords-suggest"
            placement="action"
            activityIcon="none"
            testId="domain-keywords-suggest-processing"
          />
        ) : null}
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
