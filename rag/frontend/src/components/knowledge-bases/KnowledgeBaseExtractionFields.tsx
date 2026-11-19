"use client";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  FormSkeleton,
  TimedLoadingState,
} from "@production-ready/ui";
import { CopyPlus, ListChecks } from "lucide-react";

import { ExtractionFieldsForm } from "@/components/settings/ExtractionFieldsEditor";
import { ApiErrorState } from "@/components/StateViews";
import { ApiError, type ExtractionFieldDefinition } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  useKnowledgeBaseExtractionFields,
  useResetKnowledgeBaseExtractionFields,
  useUpdateKnowledgeBaseExtractionFields,
} from "@/lib/queries";
import { toast } from "@/lib/toast";

/**
 * ナレッジベースの「抽出する項目」（#548）。KB に定義が無ければ全体の既定を表示し、既定を写して
 * KB の定義を作れる。KB の定義は編集でき、「全体の既定に戻す」で全体の既定の項目を編集欄に入れ、「保存」で
 * KB の定義を消す（#5543）。アーカイブ済みは表示だけ。
 */
export function KnowledgeBaseExtractionFields({
  knowledgeBaseId,
  editable,
}: {
  knowledgeBaseId: string;
  editable: boolean;
}) {
  const query = useKnowledgeBaseExtractionFields(knowledgeBaseId);
  const save = useUpdateKnowledgeBaseExtractionFields(knowledgeBaseId);
  const reset = useResetKnowledgeBaseExtractionFields(knowledgeBaseId);
  const defineOwn = (fields: ExtractionFieldDefinition[]) => {
    save.mutate(fields, {
      onError: (error) =>
        toast.error(
          error instanceof ApiError ? error.message : t("settings.extractionFields.saveError")
        ),
    });
  };

  let body;
  if (query.isPending) {
    body = (
      <TimedLoadingState
        label={t("knowledgeBases.extractionFields.loading")}
        operationKey={`knowledge-base-extraction-fields-${knowledgeBaseId}`}
        testId="knowledge-base-extraction-fields-loading"
      >
        <FormSkeleton fields={2} />
      </TimedLoadingState>
    );
  } else if (query.isError || !query.data) {
    body = (
      <ApiErrorState
        error={query.error}
        fallback={t("settings.extractionFields.loadError")}
        onRetry={() => void query.refetch()}
      />
    );
  } else if (query.data.inherits_default || !editable) {
    const { fields, inherits_default: inherits } = query.data;
    body = (
      <div className="space-y-3">
        <p className="text-sm text-fg-muted">
          {inherits
            ? t("knowledgeBases.extractionFields.inherited", { count: fields.length })
            : t("knowledgeBases.extractionFields.own", { count: fields.length })}
        </p>
        {fields.length > 0 ? (
          <ul
            aria-label={t("knowledgeBases.extractionFields.title")}
            className="flex flex-wrap gap-1.5"
          >
            {fields.map((field) => (
              <li
                key={field.name}
                className="max-w-full break-all rounded-full border border-border bg-surface-sunken px-2 py-0.5 text-xs text-fg"
              >
                {field.name}
                <span className="text-fg-muted">
                  {" "}
                  ({t(`settings.extractionFields.valueType.${field.value_type}`)})
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {editable ? (
          <Button
            type="button"
            variant="secondary"
            icon={CopyPlus}
            loading={save.isPending}
            onClick={() => defineOwn(fields)}
            className="w-full sm:w-auto"
          >
            {t("knowledgeBases.extractionFields.defineOwn")}
          </Button>
        ) : null}
      </div>
    );
  } else {
    body = (
      <ExtractionFieldsForm
        saved={query.data.fields}
        save={save}
        description={t("knowledgeBases.extractionFields.ownDescription")}
        testId="knowledge-base-extraction-fields-editor"
        restoreDefaults={{
          label: t("knowledgeBases.extractionFields.reset"),
          fields: query.data.default_fields,
          available: true,
          pendingMessage: t("knowledgeBases.extractionFields.reset.pending"),
          save: reset,
          onSaved: () => toast.success(t("knowledgeBases.extractionFields.reset.done")),
        }}
      />
    );
  }

  return (
    <Card data-testid="knowledge-base-extraction-fields">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ListChecks className="size-4 text-fg-muted" aria-hidden />
          {t("knowledgeBases.extractionFields.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("knowledgeBases.extractionFields.description")}
        </p>
        {body}
      </CardContent>
    </Card>
  );
}
