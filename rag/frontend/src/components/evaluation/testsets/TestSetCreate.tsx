import {
  Banner, Button, ContentActionBar, FormActionBar, FormGrid, SaveErrorBanner, apiErrorMessage,
  ListToolbar, PagedDataTable,
  Section, SelectField, Skeleton, TextField, TimedLoadingState,
} from "@production-ready/ui";
import { Save, Search } from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { listCountProps, useClientListPaging } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import { testsetApi, type TestSetDraft } from "@/lib/testset-api";

function Loading() { return <TimedLoadingState label={t("evaluation.testsets.loading")}><Skeleton className="h-40 w-full" /></TimedLoadingState>; }

export function CreateDraft({ onCreated, onCancel }: { onCreated: (draft: TestSetDraft) => void; onCancel: () => void }) {
  const sourcePaging = useClientListPaging("evaluation.testsets.sourcesPaging", "sources");
  const candidatePaging = useClientListPaging("evaluation.testsets.candidatesPaging", "candidates");
  const [method, setMethod] = useState("evaluation");
  const imported = useMutation({ mutationFn: testsetApi.import });
  const previewPaging = useClientListPaging("evaluation.testsets.importPaging", "preview");
  const sources = useQuery({ queryKey: ["testset-sources"], queryFn: testsetApi.sources });
  const [name, setName] = useState("");
  const [jobs, setJobs] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const candidates = useMutation({ mutationFn: testsetApi.candidates, onSuccess: () => setSelected([]) });
  const create = useMutation({ mutationFn: () => method === "evaluation" ? testsetApi.create(name, jobs, selected) : testsetApi.createInput(name, method as "manual" | "excel", method === "excel" ? imported.data ?? [] : []), onSuccess: onCreated });
  const guard = useLeaveGuard(Boolean(name || jobs.length), create.isPending);
  const toggle = (values: string[], id: string) => values.includes(id) ? values.filter(value => value !== id) : [...values, id];
  return <Section title={t("evaluation.testsets.new")} description={t("evaluation.testsets.selectHelp")}>
    <div className="grid min-w-0 gap-4">
      <FormGrid columns={1}><TextField width="md" id="testset-name" label={t("evaluation.testsets.name")} required value={name} onChange={event => setName(event.target.value)} maxLength={200} disabled={create.isPending} /></FormGrid>
      <SelectField width="md" id="testset-create-method" label={t("evaluation.testsets.method")} value={method} disabled={create.isPending} onValueChange={setMethod} options={["evaluation", "excel", "manual"].map(value => ({ value, label: t(`evaluation.testsets.method.${value}` as "evaluation.testsets.method.manual") }))} />
      {method === "excel" ? <>
        <TextField width="full" id="testset-import-file" type="file" accept=".xlsx" label={t("evaluation.testsets.import")} disabled={create.isPending || imported.isPending} onChange={event => { const file = event.target.files?.[0]; if (file) imported.mutate(file); }} />
        {imported.isPending ? <Loading /> : null}
        {imported.isError ? <ApiErrorState fallback={t("evaluation.testsets.importError")} error={imported.error} /> : null}
        {imported.data ? <><Banner severity="info">{t("evaluation.testsets.importPending", { count: imported.data.length })}</Banner><ListToolbar count={listCountProps({ matchedTotal: imported.data.length, filtered: false })} /><PagedDataTable {...previewPaging} paginationLabels={paginationLabels()} paginationAriaLabel={t("evaluation.testsets.preview")} ariaLabel={t("evaluation.testsets.preview")} rows={imported.data} getRowKey={row => row.item_id} columns={[{ key: "question", header: t("evaluation.testsets.question"), render: row => row.question }, { key: "answer", header: t("evaluation.testsets.standard"), render: row => row.standard_answer }]} /></> : null}
      </> : null}
      {method === "manual" ? <Banner severity="info">{t("evaluation.testsets.manualHelp")}</Banner> : null}
      {method === "evaluation" ? <>
      {sources.isPending ? <Loading /> : sources.isError ? <ApiErrorState fallback={t("common.apiError")} error={sources.error} onRetry={() => void sources.refetch()} /> : <>
        <ListToolbar count={listCountProps({ matchedTotal: sources.data.length, filtered: false })} />
        <PagedDataTable {...sourcePaging} paginationLabels={paginationLabels()} paginationAriaLabel={t("evaluation.testsets.sources")}
        testId="testset-sources" ariaLabel={t("evaluation.testsets.sources")} rows={sources.data} getRowKey={row => row.job_id}
        dense
        empty={<EmptyState title={t("evaluation.testsets.noSources")} />}
        columns={[
          { key: "select", header: t("evaluation.testsets.select"), render: row => <input type="checkbox" disabled={create.isPending || candidates.isPending} aria-label={t("evaluation.testsets.selectSource", { id: row.job_id })} checked={jobs.includes(row.job_id)} onChange={() => { setJobs(toggle(jobs, row.job_id)); candidates.reset(); setSelected([]); }} /> },
          { key: "finished", header: t("evaluation.testsets.finished"), render: row => formatDateTime(row.finished_at ?? row.created_at) },
          { key: "count", header: t("evaluation.testsets.items"), render: row => row.total_cases },
        ]} /></>}
      <ContentActionBar ariaLabel={t("evaluation.testsets.showItems")} actionsClassName="justify-start"><Button variant="secondary" icon={Search} loading={candidates.isPending} disabled={!jobs.length || create.isPending} onClick={() => candidates.mutate(jobs)}>{t("evaluation.testsets.showItems")}</Button></ContentActionBar>
      {candidates.isError ? <ApiErrorState fallback={t("common.apiError")} error={candidates.error} /> : null}
      {candidates.data ? <>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled={create.isPending} onClick={() => setSelected(candidates.data.map(row => row.item_id))}>{t("evaluation.testsets.selectAll")}</Button>
          <Button variant="ghost" disabled={create.isPending} onClick={() => setSelected([])}>{t("evaluation.testsets.clear")}</Button>
          <span className="text-sm text-fg-muted">{t("evaluation.testsets.selected", { count: selected.length })}</span>
        </div>
        <ListToolbar count={listCountProps({ matchedTotal: candidates.data.length, filtered: false })} />
        <PagedDataTable {...candidatePaging} paginationLabels={paginationLabels()} paginationAriaLabel={t("evaluation.testsets.candidates")} ariaLabel={t("evaluation.testsets.candidates")} testId="testset-candidates" rows={candidates.data} getRowKey={row => row.item_id}
          dense
          columns={[
            { key: "select", header: t("evaluation.testsets.select"), render: row => <input type="checkbox" disabled={create.isPending} aria-label={t("evaluation.testsets.selectItem", { question: row.question || row.source_case_id })} checked={selected.includes(row.item_id)} onChange={() => setSelected(toggle(selected, row.item_id))} /> },
            { key: "question", header: t("evaluation.testsets.question"), render: row => row.question || t("evaluation.testsets.missingQuestion") },
            { key: "source", header: t("evaluation.testsets.source"), render: row => <span className="break-all font-mono text-xs">{row.source_job_id} / {row.source_case_id}{row.source_pass ? t("evaluation.testsets.comparison") : ""}</span> },
          ]} />
      </> : null}
      </> : null}
      <SaveErrorBanner message={create.isError ? apiErrorMessage(create.error, t("evaluation.testsets.saveError")) : null} attemptKey={create.submittedAt} testId="testset-create-error" />
      <FormActionBar ariaLabel={t("evaluation.testsets.save")} primaryActions={[{ id: "save", label: t("evaluation.testsets.create"), icon: Save, loading: create.isPending, disabled: !name.trim() || (method === "evaluation" && (!selected.length || selected.length > 200)) || (method === "excel" && !imported.data?.length), onClick: () => create.mutate() }]}
        secondaryActions={[{ id: "cancel", label: t("common.cancel"), disabled: create.isPending, onClick: async () => { if (await guard()) onCancel(); } }]} />
    </div>
  </Section>;
}

