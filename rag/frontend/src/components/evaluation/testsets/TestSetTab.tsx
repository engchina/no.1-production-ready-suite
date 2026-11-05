import {
  AnswerPlainText, Banner, Button, ContentActionBar, Disclosure, FormActionBar, FormGrid, FormStatus, SaveErrorBanner, apiErrorMessage,
  ListToolbar, PagedDataTable, RowTitleButton,
  Section, SelectField, Skeleton, StatusBadge, TextareaField, TextField, TimedLoadingState,
} from "@production-ready/ui";
import { Check, Download, FolderOpen, Plus, Save, Search } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { saveDownloadedFile } from "@/lib/download";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { listCountProps, useClientListPaging } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import { toast } from "@/lib/toast";
import { testsetApi, type TestSetDraft, type TestSetItem } from "@/lib/testset-api";

import { PreviousCalibration } from "./PreviousCalibration";

/** 本人の評価結果を手で整理する。参考回答の自動採用や自動確認はしない。 */
export function TestSetTab() {
  const query = useQuery({ queryKey: ["testsets"], queryFn: testsetApi.list });
  const [draft, setDraft] = useState<TestSetDraft | null>(null);
  const [creating, setCreating] = useState(false);
  const [legacy, setLegacy] = useState(false);
  const open = useMutation({ mutationFn: testsetApi.get, onSuccess: (value) => { setDraft(value); setCreating(false); } });
  const paging = useClientListPaging("evaluation.testsets.paging", "drafts");
  return <div className="grid min-w-0 gap-6" data-testid="testset-creation">
    {!draft && !creating ? <Section className="[&>div:first-child]:flex-wrap" title={t("evaluation.testsets.title")} description={t("evaluation.testsets.description")}
      actions={<Button icon={Plus} onClick={() => setCreating(true)}>{t("evaluation.testsets.new")}</Button>}>
      {query.isPending ? <Loading /> : query.isError ? <ApiErrorState fallback={t("common.apiError")} error={query.error} onRetry={() => void query.refetch()} /> : <>
        <ListToolbar count={listCountProps({ matchedTotal: query.data.length, filtered: false })} />
        <PagedDataTable {...paging} paginationLabels={paginationLabels()} paginationAriaLabel={t("evaluation.testsets.title")}
          testId="testset-drafts" ariaLabel={t("evaluation.testsets.title")} rows={query.data} getRowKey={row => row.draft_id}
          onRowClick={row => open.mutate(row.draft_id)}
          empty={<EmptyState title={t("evaluation.testsets.empty")} />}
          columns={[
            { key: "name", header: t("evaluation.testsets.name"), rowHeader: true, render: row => <RowTitleButton title={row.name} onClick={() => open.mutate(row.draft_id)} /> },
            { key: "count", header: t("evaluation.testsets.confirmed"), render: row => `${row.confirmed_count} / ${row.item_count}` },
            { key: "date", header: t("evaluation.testsets.updated"), render: row => formatDateTime(row.updated_at) },
          ]} />
      </>}
      {open.isError ? <ApiErrorState fallback={t("common.apiError")} error={open.error} /> : null}
    </Section> : null}
    {creating ? <CreateDraft onCreated={value => { setDraft(value); setCreating(false); }} onCancel={() => setCreating(false)} /> : null}
    {draft ? <DraftEditor key={draft.draft_id} initial={draft} onClose={() => { setDraft(null); void query.refetch(); }} /> : null}
    {!draft && !creating ? <Disclosure summary={t("evaluation.testsets.previous")} open={legacy} onOpenChange={setLegacy}>
      {legacy ? <PreviousCalibration /> : null}
    </Disclosure> : null}
  </div>;
}

function Loading() {
  return <TimedLoadingState label={t("evaluation.testsets.loading")} operationKey="testset-load"><Skeleton className="h-40 w-full" /></TimedLoadingState>;
}

function CreateDraft({ onCreated, onCancel }: { onCreated: (draft: TestSetDraft) => void; onCancel: () => void }) {
  const sourcePaging = useClientListPaging("evaluation.testsets.sourcesPaging", "sources");
  const candidatePaging = useClientListPaging("evaluation.testsets.candidatesPaging", "candidates");
  const sources = useQuery({ queryKey: ["testset-sources"], queryFn: testsetApi.sources });
  const [name, setName] = useState("");
  const [jobs, setJobs] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const candidates = useMutation({ mutationFn: testsetApi.candidates, onSuccess: () => setSelected([]) });
  const create = useMutation({ mutationFn: () => testsetApi.create(name, jobs, selected), onSuccess: onCreated });
  const guard = useLeaveGuard(Boolean(name || jobs.length), create.isPending);
  const toggle = (values: string[], id: string) => values.includes(id) ? values.filter(value => value !== id) : [...values, id];
  return <Section title={t("evaluation.testsets.new")} description={t("evaluation.testsets.selectHelp")}>
    <div className="grid min-w-0 gap-4">
      <FormGrid columns={1}><TextField width="md" id="testset-name" label={t("evaluation.testsets.name")} required value={name} onChange={event => setName(event.target.value)} maxLength={200} disabled={create.isPending} /></FormGrid>
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
      <SaveErrorBanner message={create.isError ? apiErrorMessage(create.error, t("evaluation.testsets.saveError")) : null} attemptKey={create.submittedAt} testId="testset-create-error" />
      <FormActionBar ariaLabel={t("evaluation.testsets.save")} primaryActions={[{ id: "save", label: t("evaluation.testsets.create"), icon: Save, loading: create.isPending, disabled: !name.trim() || !selected.length || selected.length > 200, onClick: () => create.mutate() }]}
        secondaryActions={[{ id: "cancel", label: t("common.cancel"), disabled: create.isPending, onClick: async () => { if (await guard()) onCancel(); } }]} />
    </div>
  </Section>;
}

function DraftEditor({ initial, onClose }: { initial: TestSetDraft; onClose: () => void }) {
  const [draft, setDraft] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const [selected, setSelected] = useState(initial.items[0]?.item_id ?? "");
  const [downloading, setDownloading] = useState(false);
  const qc = useQueryClient();
  const itemPaging = useClientListPaging("evaluation.testsets.itemsPaging", initial.draft_id);
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const save = useMutation({ mutationFn: testsetApi.save, onSuccess: value => { setDraft(value); setBaseline(value); void qc.invalidateQueries({ queryKey: ["testsets"] }); toast.success(t("evaluation.testsets.saved")); } });
  const guard = useLeaveGuard(dirty, save.isPending);
  const item = draft.items.find(value => value.item_id === selected);
  const change = (patch: Partial<TestSetItem>) => setDraft(previous => ({ ...previous, items: previous.items.map(value => value.item_id === selected ? { ...value, ...patch, confirmed: false } : value) }));
  const confirm = async () => {
    try {
      // 編集を先に保存し、同じ内容への明示確認を別の保存で確定する。
      const current = dirty ? await save.mutateAsync(draft) : draft;
      await save.mutateAsync({ ...current, items: current.items.map(value => value.item_id === selected ? { ...value, confirmed: true } : value) });
    } catch { /* mutation が共通のエラー表示を持つ。 */ }
  };
  const download = async () => {
    setDownloading(true);
    try { saveDownloadedFile(await testsetApi.download(baseline)); }
    catch (error) { toast.error(apiErrorMessage(error, t("evaluation.testsets.downloadError"))); }
    finally { setDownloading(false); }
  };
  return <Section title={t("evaluation.testsets.edit")} description={t("evaluation.testsets.editHelp")}>
    <div className="grid min-w-0 gap-4" data-testid="testset-editor">
      <FormGrid columns={1}><TextField width="md" id="testset-draft-name" label={t("evaluation.testsets.name")} required value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} maxLength={200} disabled={save.isPending} /></FormGrid>
      <ListToolbar count={listCountProps({ matchedTotal: draft.items.length, filtered: false })} />
      <PagedDataTable {...itemPaging} paginationLabels={paginationLabels()} paginationAriaLabel={t("evaluation.testsets.items")} ariaLabel={t("evaluation.testsets.items")} rows={draft.items} getRowKey={row => row.item_id} selectedRowKey={selected}
        onRowClick={row => setSelected(row.item_id)} dense
        columns={[
          { key: "question", header: t("evaluation.testsets.question"), rowHeader: true, render: row => <RowTitleButton title={row.question || t("evaluation.testsets.missingQuestion")} onClick={() => setSelected(row.item_id)} /> },
          { key: "confirmed", header: t("evaluation.testsets.confirmed"), render: row => <StatusBadge variant={row.confirmed ? "success" : "neutral"} label={t(row.confirmed ? "evaluation.testsets.isConfirmed" : "evaluation.testsets.unconfirmed")} /> },
        ]} />
      {item ? <FormGrid columns={1}>
        {item.source_notice ? <Banner severity="warning">{item.source_notice}</Banner> : null}
        <TextareaField width="full" id="testset-question" label={t("evaluation.testsets.question")} helper={t("evaluation.testsets.questionHelp")} required rows={3} value={item.question} onChange={event => change({ question: event.target.value })} maxLength={2000} disabled={save.isPending} />
        <TextareaField width="full" id="testset-standard" label={t("evaluation.testsets.standard")} helper={t("evaluation.testsets.standardHelp")} required={item.response_decision === "answer"} rows={5} value={item.standard_answer} onChange={event => change({ standard_answer: event.target.value })} maxLength={8000} disabled={save.isPending} />
        <Disclosure summary={t("evaluation.testsets.original")}>
          <div className="grid min-w-0 gap-3">
            <p className="break-all font-mono text-xs">{item.source_job_id} / {item.source_case_id}</p>
            <AnswerPlainText text={item.original.question} />
            <AnswerPlainText text={item.original.standard_answer || t("evaluation.testsets.noOriginalStandard")} />
            <AnswerPlainText text={item.original.evidence.join("\n")} />
          </div>
        </Disclosure>
        <Disclosure summary={t("evaluation.testsets.reference")}>
          <div className="grid min-w-0 gap-3">
            <AnswerPlainText text={item.reference_answer || t("evaluation.testsets.noReference")} />
            <ContentActionBar ariaLabel={t("evaluation.testsets.adopt")} actionsClassName="justify-start"><Button variant="secondary" icon={FolderOpen} disabled={!item.reference_answer || save.isPending} onClick={() => change({ standard_answer: item.reference_answer })}>{t("evaluation.testsets.adopt")}</Button></ContentActionBar>
            {item.reference_evidence.map((entry, index) => <AnswerPlainText key={index} text={String(entry.text ?? entry.content ?? "")} />)}
          </div>
        </Disclosure>
        <TextareaField width="full" id="testset-evidence" label={t("evaluation.testsets.evidence")} helper={t("evaluation.testsets.evidenceHelp")} rows={4} disabled={save.isPending} value={item.evidence.join("\n")} onChange={event => change({ evidence: event.target.value.split("\n") })} />
        <Disclosure summary={t("evaluation.testsets.advanced")}>
          <FormGrid columns={1}>
            <SelectField width="md" id="testset-decision" label={t("evaluation.testsets.decision")} helper={t("evaluation.testsets.decisionHelp")} disabled={save.isPending} value={item.response_decision} options={(["answer", "clarify", "hold"] as const).map(value => ({ value, label: t(`evaluation.testsets.decision.${value}`) }))} onValueChange={value => change({ response_decision: value as TestSetItem["response_decision"] })} />
            <TextareaField width="full" id="testset-points" label={t("evaluation.testsets.points")} helper={t("evaluation.testsets.pointsHelp")} rows={3} disabled={save.isPending} value={item.required_answer_points.join("\n")} onChange={event => change({ required_answer_points: event.target.value.split("\n") })} />
            <TextareaField width="full" id="testset-process" label={t("evaluation.testsets.process")} helper={t("evaluation.testsets.processHelp")} rows={4} maxLength={8000} disabled={save.isPending} value={item.answer_process} onChange={event => change({ answer_process: event.target.value })} />
          </FormGrid>
        </Disclosure>
        <ContentActionBar ariaLabel={t("evaluation.testsets.confirmItem")} actionsClassName="justify-start"><Button variant="secondary" icon={Check} loading={save.isPending} disabled={item.confirmed || !item.question.trim() || (item.response_decision === "answer" && !item.standard_answer.trim())} onClick={() => void confirm()}>{t("evaluation.testsets.confirmItem")}</Button></ContentActionBar>
      </FormGrid> : null}
      <SaveErrorBanner message={save.isError ? apiErrorMessage(save.error, t("evaluation.testsets.saveError")) : null} attemptKey={save.submittedAt} testId="testset-save-error" />
      <FormActionBar ariaLabel={t("evaluation.testsets.save")} status={save.isPending
        ? <FormStatus tone="info" message={t("evaluation.testsets.saveState.saving")} />
        : save.isError ? <FormStatus tone="danger" message={t("evaluation.testsets.saveState.failed")} />
        : dirty ? <FormStatus tone="warning" message={t("evaluation.testsets.saveState.unsaved")} />
        : <FormStatus tone="success" message={t("evaluation.testsets.saveState.saved")} />}
        primaryActions={[{ id: "save", label: t("evaluation.testsets.save"), icon: Save, loading: save.isPending, disabled: !dirty || !draft.name.trim(), onClick: () => save.mutate(draft) }]}
        secondaryActions={[{ id: "cancel", label: t("evaluation.testsets.back"), onClick: async () => { if (await guard()) onClose(); } }]} />
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <Button variant="secondary" icon={Download} loading={downloading} disabled={dirty || save.isPending || !baseline.items.some(value => value.confirmed)} onClick={() => void download()}>{t("evaluation.testsets.download")}</Button>
        <p className="text-sm text-fg-muted">{t("evaluation.testsets.downloadHelp")}</p>
      </div>
    </div>
  </Section>;
}
