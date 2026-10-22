import {
  Banner, Button, FormGrid, FormSkeleton, SearchableSelectField, SelectField,
  TimedLoadingState,
} from "@production-ready/ui";
import { Eye } from "lucide-react";
import { useState } from "react";

import { ApiErrorState } from "@/components/StateViews";
import { type SectionRule, type SectionRulesMode } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useDocumentRecipes, useDocuments, usePreviewSectionRules } from "@/lib/queries";

/** 保存しない比較用の候補。人が編集した章節の最終表示とは区別する。 */
export function SectionRulesPreview({ mode, rules, disabled }: {
  mode: SectionRulesMode; rules: SectionRule[]; disabled: boolean;
}) {
  const [documentId, setDocumentId] = useState("");
  const [recipeId, setRecipeId] = useState("");
  const [q, setQ] = useState("");
  const documents = useDocuments({ q: q || undefined, limit: 25 });
  const recipes = useDocumentRecipes(documentId || null);
  const preview = usePreviewSectionRules();
  const selectedRecipe = recipeId || recipes.data?.[0]?.recipe_id || "";
  const variables = { documentId, recipe_id: selectedRecipe, mode, rules };
  const current = JSON.stringify(preview.variables) === JSON.stringify(variables);
  const data = current ? preview.data : undefined;
  const run = () => preview.mutate(variables);
  return (
    <div className="space-y-3" data-testid="section-rules-preview">
      <p className="text-xs text-fg-muted">{t("sectionRules.preview.hint")}</p>
      {documents.isError ? <ApiErrorState error={documents.error} fallback={t("sectionRules.preview.error")} onRetry={() => void documents.refetch()} /> : null}
      <FormGrid columns={2}>
        <SearchableSelectField
          id="section-rules-preview-document"
          label={t("sectionRules.preview.document")}
          placeholder={t("sectionRules.preview.choose")}
          value={documentId}
          options={(documents.data?.items ?? []).map((doc) => ({ value: doc.id, label: doc.file_name }))}
          onQueryChange={setQ}
          onValueChange={(id) => { setDocumentId(id); setRecipeId(""); preview.reset(); }}
          remote={{ total: documents.data?.total ?? 0, hasMore: false, loadingMore: false, searching: documents.isFetching, onLoadMore: () => undefined }}
          disabled={preview.isPending}
          width="lg"
        />
        <SelectField
          id="section-rules-preview-recipe"
          label={t("sectionRules.preview.recipe")}
          value={selectedRecipe}
          options={(recipes.data ?? []).map((recipe) => ({ value: recipe.recipe_id, label: t("sectionRules.preview.recipeName", { number: recipe.slot_no }) }))}
          onValueChange={(id) => { setRecipeId(id); preview.reset(); }}
          disabled={!documentId || recipes.isPending || preview.isPending}
          width="md"
        />
      </FormGrid>
      {recipes.isError ? <ApiErrorState error={recipes.error} fallback={t("sectionRules.preview.error")} onRetry={() => void recipes.refetch()} /> : null}
      <Button type="button" icon={Eye} variant="secondary" loading={preview.isPending} disabled={!documentId || !selectedRecipe || disabled || recipes.isError} onClick={run}>
        {t("sectionRules.preview.run")}
      </Button>
      {preview.isPending ? <TimedLoadingState label={t("sectionRules.preview.loading")} operationKey="section-rules-preview"><FormSkeleton fields={2} /></TimedLoadingState> : null}
      {preview.isError && current ? <ApiErrorState error={preview.error} fallback={t("sectionRules.preview.error")} onRetry={run} /> : null}
      {preview.data && !current ? <p role="status" className="text-sm text-fg-muted">{t("sectionRules.preview.stale")}</p> : null}
      {data ? <div className="space-y-3" aria-live="polite">
        {data.recipe_overrides_mode ? <Banner severity="info">{t("sectionRules.preview.override", { mode: t(`sectionRules.mode.${data.effective_mode}`) })}</Banner> : null}
        {data.manual_edits_present ? <Banner severity="info">{t("sectionRules.preview.manual")}</Banner> : null}
        {!data.extraction_available ? <p className="text-sm text-fg-muted">{t("sectionRules.preview.noExtraction")}</p> : <>
          <p className="text-sm font-medium text-fg">{t("sectionRules.preview.count", { count: data.sections.length })}</p>
          {data.sections.length === 0 ? <p className="text-sm text-fg-muted">{t("sectionRules.preview.empty")}</p> : <ol className="divide-y divide-border rounded-md border border-border" aria-label={t("sectionRules.preview.count", { count: data.sections.length })}>
            {data.sections.map((section) => <li key={section.id} className="space-y-1 px-3 py-2" style={{ paddingInlineStart: `calc(var(--space-3) + var(--space-4) * ${section.level - 1})` }}>
              <p className="break-words text-sm font-medium text-fg">{section.title}</p>
              <p className="text-xs text-fg-muted">{t("sectionRules.level", { level: section.level })} · {section.page_start == null ? t("sectionRules.preview.noPage") : t("sectionRules.preview.page", { start: section.page_start, end: section.page_end ?? section.page_start })}</p>
            </li>)}
          </ol>}
        </>}
      </div> : null}
    </div>
  );
}
