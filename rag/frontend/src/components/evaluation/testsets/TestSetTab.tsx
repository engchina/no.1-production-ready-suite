import { Button, ListToolbar, PageBody, PageHeader, PagedDataTable, RowTitleButton, Section, Skeleton, Tabs, TimedLoadingState } from "@production-ready/ui";
import { Play, Plus } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { DataLoadFailure, DataLoadStaleNotice } from "@/components/DataLoadStates";
import { EmptyState } from "@/components/StateViews";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { listCountProps, useClientListPaging } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import { testsetApi } from "@/lib/testset-api";
import { CreateDraft } from "./TestSetCreate";
import { DraftEditor } from "./TestSetEditor";

/** テストセットの一覧と URL で指定する作成・編集。 */
export function TestSetTab() {
  const [params, setParams] = useSearchParams();
  const id = params.get("set");
  const creating = id === "new";
  const query = useQuery({ queryKey: ["testsets"], queryFn: testsetApi.list });
  const detail = useQuery({ queryKey: ["testset", id], queryFn: () => testsetApi.get(id!), enabled: Boolean(id && !creating) });
  const paging = useClientListPaging("evaluation.testsets.paging", "drafts");
  const open = (value: string | null) => { const next = new URLSearchParams({ tab: "testsets" }); if (value) next.set("set", value); setParams(next); };
  const run = (value: string) => setParams({ testset: value });
  if (id && !creating && detail.data) return <DraftEditor key={id} initial={detail.data} onClose={() => open(null)} onEvaluate={() => run(id)} loadNotice={detail.isError ? <DataLoadStaleNotice error={detail.error} dataUpdatedAt={detail.dataUpdatedAt} retrying={detail.isFetching} onRetry={() => void detail.refetch()} /> : null} />;
  const header = <PageHeader wide title={creating ? t("evaluation.testsets.new") : t("nav.evaluation")} subtitle={t("evaluation.testsets.description")}
    back={id ? { label: t("common.backToList"), onClick: () => open(null) } : undefined}
    tabs={!id ? <Tabs idPrefix="evaluation-page-tabs" ariaLabel={t("evaluation.tabs.label")} value="testsets" onChange={value => value === "run" && setParams({})} items={[{ id: "run", label: t("evaluation.tabs.run") }, { id: "testsets", label: t("evaluation.tabs.testsets") }]} /> : undefined}
    actions={!id ? [{ id: "new", kind: "primary", label: t("evaluation.testsets.new"), icon: Plus, disabled: query.isError || query.isPending, onClick: () => open("new") }] : []} />;
  if (creating) return <>{header}<PageBody wide><CreateDraft onCreated={value => { void query.refetch(); open(value.draft_id); }} onCancel={() => open(null)} /></PageBody></>;
  const pending = id ? detail.isPending : query.isPending;
  const error = id ? detail.error : query.error;
  return <>{header}<PageBody wide><Section title={t("evaluation.testsets.title")}><div data-testid="testset-creation">
    {pending ? <TimedLoadingState label={t("evaluation.testsets.loading")}><Skeleton className="h-40 w-full" /></TimedLoadingState> : error && !(id ? detail.data : query.data) ? <DataLoadFailure error={error} fallback={t("evaluation.testsets.loadError")} onRetry={() => void (id ? detail.refetch() : query.refetch())} /> : <>
      {query.isError ? <DataLoadStaleNotice error={query.error} dataUpdatedAt={query.dataUpdatedAt} retrying={query.isFetching} onRetry={() => void query.refetch()} /> : null}
      <ListToolbar count={listCountProps({ matchedTotal: query.data?.length ?? 0, filtered: false })} />
      <PagedDataTable {...paging} paginationLabels={paginationLabels()} paginationAriaLabel={t("evaluation.testsets.title")} testId="testset-drafts" ariaLabel={t("evaluation.testsets.title")} rows={query.data ?? []} getRowKey={row => row.draft_id} empty={<EmptyState title={t("evaluation.testsets.empty")} />} columns={[
        { key: "name", header: t("evaluation.testsets.name"), rowHeader: true, render: row => <RowTitleButton title={row.name} onClick={() => open(row.draft_id)} /> },
        { key: "count", header: t("evaluation.testsets.confirmed"), render: row => t("evaluation.testsets.progress", { confirmed: row.confirmed_count, total: row.item_count, pending: row.item_count - row.confirmed_count }) },
        { key: "date", header: t("evaluation.testsets.updated"), render: row => formatDateTime(row.updated_at) },
        { key: "actions", header: t("common.objectActions.aria", { name: t("evaluation.testsets.title") }), render: row => <div className="flex flex-wrap gap-2"><Button variant="secondary" onClick={() => open(row.draft_id)}>{t("evaluation.testsets.continue")}</Button><Button variant="ghost" icon={Play} disabled={!row.confirmed_count} onClick={() => run(row.draft_id)}>{t("evaluation.testsets.run", { count: row.confirmed_count })}</Button></div> },
      ]} />
    </>}
  </div></Section></PageBody></>;
}
