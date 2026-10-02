import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  ListToolbar,
  ObjectActionBar,
  TableSkeleton,
  PageHeader,
  RowActionMenu,
  Section,
  StatusBadge,
  ToggleChip,
  toast,
  useConfirm,
  type DataTableColumn,
  type EntityAction,
  PageBody,
  RowTitleButton,
} from "@engchina/production-ready-ui";
import { agentApi, type ApprovalRequest, type RunState } from "@/lib/api";
import { AgentSplitPane } from "@/components/EntityLayout";
import { PagedDataTable, QueryState } from "@/components/ListViews";
import {
  FilterChipGroup,
  ListSearchField,
  listCountLabel,
  matchesSearch,
  NoMatchState,
  useListSearch,
} from "@/components/ListFilters";
import { t } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";
import { approvalStatusView, runStatusView } from "@/lib/status-labels";
import { isNullableString, isOneOf, useWorkspaceState } from "@/lib/workspace-state";
import { JsonPanel } from "@/pages/shared/page-helpers";

type ApprovalRow = { run: RunState; approval: ApprovalRequest };

const APPROVAL_FILTERS = ["pending", "decided", "all"] as const;

type ApprovalFilter = (typeof APPROVAL_FILTERS)[number];

const isApprovalFilter = isOneOf<ApprovalFilter>(APPROVAL_FILTERS);

function approvalMatchesFilter(approval: ApprovalRequest, filter: ApprovalFilter): boolean {
  if (filter === "all") return true;
  return filter === "pending" ? approval.status === "pending" : approval.status !== "pending";
}

export function ApprovalsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const capabilities = useCapabilities();
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: agentApi.listRuns,
    refetchInterval: 5000,
  });
  const decide = useMutation({
    mutationFn: ({ approval, approved }: { approval: ApprovalRequest; approved: boolean }) =>
      // 決定者はログイン中の利用者から server が決める（#215）。
      agentApi.decideApproval(approval.id, { approved }),
    onSuccess: (_data, { approved }) => {
      toast.success(approved ? t("approval.decided") : t("approval.rejected"));
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const approvals = useMemo<ApprovalRow[]>(
    () => (runs.data?.runs ?? []).flatMap((run) => run.approvals.map((approval) => ({ run, approval }))),
    [runs.data?.runs]
  );
  // 承認キューの既定は「保留中」（判断の要る承認を先に見せる）。検索語・絞り込み・選択は作業状態に残す（#808）。
  const [approvalQuery, setApprovalQuery] = useListSearch("approvals");
  const [approvalFilter, setApprovalFilter] = useWorkspaceState(
    "listFilter",
    "approvals",
    "pending" as ApprovalFilter,
    isApprovalFilter
  );
  const visibleApprovals = approvals.filter(
    ({ run, approval }) =>
      approvalMatchesFilter(approval, approvalFilter) &&
      matchesSearch(approvalQuery, [approval.tool_call.name, run.goal, run.id])
  );
  const [selectedId, setSelectedId] = useWorkspaceState("approvals", "selectedId", null as string | null, isNullableString);
  const selected = visibleApprovals.find((row) => row.approval.id === selectedId) ?? visibleApprovals[0];

  async function decideApproval(approval: ApprovalRequest, approved: boolean) {
    const ok = await confirm({
      title: approved ? t("run.approveTitle") : t("run.rejectTitle"),
      description: approval.tool_call.name,
      confirmLabel: approved ? t("common.approve") : t("common.reject"),
      cancelLabel: t("common.cancel"),
      tone: approved ? "info" : "danger",
    });
    if (ok) {
      decide.mutate({ approval, approved });
    }
  }

  // 一覧の行と詳細で同じ定義を使う。判断は保留中の承認だけに出す。
  const approvalActions = (approval: ApprovalRequest): EntityAction[] => [
    {
      id: "approve",
      label: t("common.approve"),
      icon: Check,
      // 承認・却下は承認の判断の権限（approver）が必要（#215）。
      visible: capabilities.decideApprovals && approval.status === "pending",
      disabled: decide.isPending,
      onSelect: () => decideApproval(approval, true),
    },
    {
      id: "reject",
      label: t("common.reject"),
      icon: X,
      tone: "danger",
      visible: capabilities.decideApprovals && approval.status === "pending",
      disabled: decide.isPending,
      onSelect: () => decideApproval(approval, false),
    },
  ];

  const columns: DataTableColumn<ApprovalRow>[] = [
    {
      key: "tool",
      header: t("common.tool"),
      rowHeader: true,
      render: ({ run, approval }) => (
        <RowTitleButton
          title={approval.tool_call.name}
          subtitle={run.goal}
          current={approval.id === selected?.approval.id}
          onClick={() => setSelectedId(approval.id)}
        />
      ),
    },
    {
      key: "status",
      header: t("common.status"),
      render: ({ approval }) => (
        <StatusBadge {...approvalStatusView(approval.status)} />
      ),
    },
    {
      key: "actions",
      header: t("run.actions"),
      align: "right",
      render: ({ approval }) => (
        <RowActionMenu
          actions={approvalActions(approval)}
          ariaLabel={t("common.entityActions", { name: approval.tool_call.name })}
          testId={`approval-row-actions-${approval.id}`}
        />
      ),
    },
  ];

  return (
    <>
      <PageHeader wide title={t("nav.approvals")} subtitle={t("page.approvals.subtitle")} />
      <PageBody wide>
        <QueryState query={runs} loadingLabel={t("loading.approvals")} skeleton={<TableSkeleton columns={3} />}>
          <AgentSplitPane
            splitId="approvals-list"
            left={
              <Section title={t("approval.list")}>
                <ListToolbar
                  search={
                    <ListSearchField
                      id="approval-search"
                      label={t("approval.search")}
                      value={approvalQuery}
                      onSearch={setApprovalQuery}
                      count={visibleApprovals.length}
                    />
                  }
                  filters={
                    <FilterChipGroup label={t("approval.filter.label")}>
                      {APPROVAL_FILTERS.map((filter) => (
                        <ToggleChip
                          key={filter}
                          selected={approvalFilter === filter}
                          onClick={() => setApprovalFilter(filter)}
                        >
                          {t(`approval.filter.${filter}`)}
                        </ToggleChip>
                      ))}
                    </FilterChipGroup>
                  }
                  summary={listCountLabel(visibleApprovals.length, approvals.length)}
                  testId="approval-list-toolbar"
                />
                {/* 5 秒ごとの再取得で行が変わっても、ページは作業状態に残して戻さない（戻すのは絞り込みを変えたときだけ）。 */}
                <PagedDataTable
                  pageKey="approvals"
                  resetKey={`${approvalQuery}\u0000${approvalFilter}`}
                  rows={visibleApprovals}
                  columns={columns}
                  getRowKey={({ approval }) => approval.id}
                  selectedRowKey={selected?.approval.id ?? null}
                  onRowClick={({ approval }) => setSelectedId(approval.id)}
                  rowProps={() => ({ className: "align-top" })}
                  ariaLabel={t("approval.list")}
                  paginationTestId="approval-list-pagination"
                  empty={
                    // 検索語があるか、保留中以外の絞り込みで判断済みの承認が無いときは「一致しない」。保留中が無いのは通常の状態。
                    approvalQuery || (approvals.length > 0 && approvalFilter !== "pending") ? (
                      <NoMatchState
                        title={t("approval.noMatch")}
                        clearLabel={approvalFilter === "pending" ? t("common.clearSearch") : t("common.clearFilters")}
                        onClear={() => {
                          setApprovalQuery("");
                          setApprovalFilter("pending");
                        }}
                      />
                    ) : (
                      <EmptyState
                        title={approvalFilter === "pending" ? t("approval.empty.pendingTitle") : t("approval.empty.title")}
                        hint={t("approval.empty.hint")}
                      />
                    )
                  }
                />
              </Section>
            }
            right={
              selected ? (
                <Section
                  title={t("approval.detail")}
                  aria-label={t("approval.detail")}
                  actions={
                    <ObjectActionBar
                      actions={approvalActions(selected.approval)}
                      ariaLabel={t("common.entityActions", { name: selected.approval.tool_call.name })}
                      moreLabel={t("common.moreActions")}
                      testId="approval-object-actions"
                    />
                  }
                >
                  <Card className="min-w-0">
                    <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
                      <div className="min-w-0">
                        <CardTitle>{selected.approval.tool_call.name}</CardTitle>
                        <CardDescription className="break-words [overflow-wrap:anywhere]">
                          {selected.run.goal}
                        </CardDescription>
                      </div>
                      <StatusBadge {...approvalStatusView(selected.approval.status)} />
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="grid gap-2 text-xs text-fg-muted sm:grid-cols-2">
                        <span className="break-all">{`${t("audit.runId")}: ${selected.run.id}`}</span>
                        <span>{`${t("audit.runStatus")}: ${runStatusView(selected.run.status).label}`}</span>
                      </div>
                      <JsonPanel title={t("approval.arguments")} value={selected.approval.tool_call.arguments} />
                    </CardContent>
                  </Card>
                </Section>
              ) : (
                <EmptyState title={t("common.empty.title")} hint={t("approval.selectHint")} />
              )
            }
          />
        </QueryState>
      </PageBody>
    </>
  );
}
