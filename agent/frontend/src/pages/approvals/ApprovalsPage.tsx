import { useEffect, useMemo } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, History, RefreshCw, X } from "lucide-react";
import {
  Banner,
  ButtonLink,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  FormSkeleton,
  ListToolbar,
  ObjectActionBar,
  TableSkeleton,
  PageHeader,
  RowActionMenu,
  StatusBadge,
  ToggleChip,
  toast,
  useActionPending,
  useConfirm,
  type DataTableColumn,
  type EntityAction,
  PageBody,
  RowTitleButton,
} from "@production-ready/ui";
import { agentApi, type ApprovalRequest, type RunState } from "@/lib/api";
import { MissingEditorTarget } from "@/components/EntityLayout";
import { PagedDataTable, QueryState } from "@/components/ListViews";
import {
  FilterChipGroup,
  ListSearchField,
  listCountLabel,
  matchesSearch,
  NoMatchState,
  useListSearch,
} from "@/components/ListFilters";
import { useEditorRoute } from "@/lib/editor-route";
import { t } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";
import { approvalStatusView, runStatusView } from "@/lib/status-labels";
import { isNullableString, isOneOf, useWorkspaceState } from "@/lib/workspace-state";
import { JsonPreview } from "@/pages/shared/page-helpers";
import { isOwnDecision } from "@/pages/shared/approval-decision";
import { useAuth } from "@/components/security/AuthProvider";
import { useViewSwitchFocus } from "@/pages/shared/view-switch-focus";

type ApprovalRow = { run: RunState; approval: ApprovalRequest };
const APPROVAL_FILTERS = ["pending", "decided", "all"] as const;
type ApprovalFilter = (typeof APPROVAL_FILTERS)[number];
const isApprovalFilter = isOneOf<ApprovalFilter>(APPROVAL_FILTERS);
const approvalDateFormat = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

/** 判断の日時はブラウザの timezone に依存せず JST。古い記録の欠落は補完しない。 */
function approvalDate(value: string | null | undefined) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? approvalDateFormat.format(date) : t("approval.notRecorded");
}

function approvalMatchesFilter(approval: ApprovalRequest, filter: ApprovalFilter): boolean {
  if (filter === "all") return true;
  return filter === "pending" ? approval.status === "pending" : approval.status !== "pending";
}

export function ApprovalsPage() {
  const editor = useEditorRoute();
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const capabilities = useCapabilities();
  const { user } = useAuth();
  const manualRefresh = useActionPending();
  const runs = useQuery({ queryKey: ["runs"], queryFn: agentApi.listRuns, refetchInterval: 5000 });
  const decide = useMutation({
    mutationFn: ({ approval, approved }: { approval: ApprovalRequest; approved: boolean }) =>
      // 決定者はログイン中の利用者から server が決める（#215）。
      agentApi.decideApproval(approval.id, { approved }),
    onSuccess: (updatedRun, { approval, approved }) => {
      // 押した判断が自分の判断として残ったときだけ成功と案内する（ほかの操作者が先に判断した承認は、backend が
      // 状態を変えずに 200 で返す。#1119）。
      if (isOwnDecision(updatedRun, approval.id, approved, user?.login_user_id)) {
        toast.success(approved ? t("approval.decided") : t("approval.rejected"));
      } else {
        toast.info(t("approval.changedDuringReview"));
      }
      // 判断の返却値を先に反映し、再取得を待つ間の二重判断を防ぐ。対象の URL は変えない（#877）。
      queryClient.setQueryData<{ runs: RunState[] }>(["runs"], (current) => ({
        runs: (current?.runs ?? []).map((run) => (run.id === updatedRun.id ? updatedRun : run)),
      }));
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (error) => {
      toast.error(error.message);
      // 他の操作者が先に判断した場合も、最新の状態を取り直す。
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const approvals = useMemo<ApprovalRow[]>(
    () => (runs.data?.runs ?? []).flatMap((run) => run.approvals.map((approval) => ({ run, approval }))),
    [runs.data?.runs]
  );
  const [approvalQuery, setApprovalQuery] = useListSearch("approvals");
  const [approvalFilter, setApprovalFilter] = useWorkspaceState(
    "listFilter",
    "approvals",
    "pending" as ApprovalFilter,
    isApprovalFilter
  );
  const [selectedId, setSelectedId] = useWorkspaceState(
    "approvals",
    "selectedId",
    null as string | null,
    isNullableString
  );
  const visibleApprovals = approvals.filter(
    ({ run, approval }) =>
      approvalMatchesFilter(approval, approvalFilter) &&
      matchesSearch(approvalQuery, [approval.id, approval.tool_call.name, run.goal, run.id])
  );
  const isList = editor.target.kind === "list";
  const targetId = editor.target.kind === "edit" ? editor.target.id : null;
  // 保留中の一覧から消えても、判断した承認の詳細を維持する。次の対象へ暗黙に移動しない。
  const selected = approvals.find((row) => row.approval.id === targetId);

  useEffect(() => {
    if (targetId) setSelectedId(targetId);
  }, [targetId, setSelectedId]);
  // 詳細を開くと見出しへ、一覧へ戻ると選んだ行へフォーカスを移す（開いた直後は動かさない。#1122）。
  useViewSwitchFocus(
    `${isList ? "list" : "detail"}:${targetId ?? ""}`,
    isList && selectedId ? `a[data-approval-id="${CSS.escape(selectedId)}"]` : null
  );

  function openApproval(id: string) {
    setSelectedId(id);
    editor.openItem(id);
  }

  async function decideApproval(approval: ApprovalRequest, approved: boolean) {
    if (decide.isPending || !capabilities.decideApprovals || approval.status !== "pending") return;
    const row = approvals.find((item) => item.approval.id === approval.id);
    const ok = await confirm({
      title: approved ? t("run.approveTitle") : t("run.rejectTitle"),
      description: t("approval.confirmContext", {
        tool: approval.tool_call.name,
        goal: row?.run.goal ?? "",
        id: approval.id,
      }),
      confirmLabel: approved ? t("common.approve") : t("common.reject"),
      cancelLabel: t("common.cancel"),
      tone: approved ? "info" : "danger",
    });
    if (ok) {
      // 確認中にポーリングで更新された場合は、現在の cache を読み、古い状態で送らない。
      const latest = queryClient
        .getQueryData<{ runs: RunState[] }>(["runs"])
        ?.runs.flatMap((run) => run.approvals)
        .find((item) => item.id === approval.id);
      if (!latest || latest.status !== "pending") {
        toast.info(t("approval.changedDuringReview"));
        return;
      }
      decide.mutate({ approval: latest, approved });
    }
  }

  // 一覧の行と詳細で同じ定義を使う。処理中は押した対象の操作だけを回す。
  const approvalActions = (approval: ApprovalRequest): EntityAction[] => [
    {
      id: "approve",
      label: t("common.approve"),
      icon: Check,
      visible: capabilities.decideApprovals && approval.status === "pending",
      disabled: decide.isPending,
      loading: decide.isPending && decide.variables?.approval.id === approval.id && decide.variables.approved,
      onSelect: () => decideApproval(approval, true),
    },
    {
      id: "reject",
      label: t("common.reject"),
      icon: X,
      tone: "danger",
      visible: capabilities.decideApprovals && approval.status === "pending",
      disabled: decide.isPending,
      loading: decide.isPending && decide.variables?.approval.id === approval.id && !decide.variables.approved,
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
          subtitle={
            <>
              <span className="block line-clamp-2">{run.goal}</span>
              <span className="block">{approvalDate(approval.created_at)}</span>
            </>
          }
          href={editor.itemHref(approval.id)}
          data-approval-id={approval.id}
          current={approval.id === selectedId}
          onClick={() => openApproval(approval.id)}
        />
      ),
    },
    {
      key: "status",
      header: t("common.status"),
      render: ({ approval }) => <StatusBadge {...approvalStatusView(approval.status)} />,
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
      <PageHeader
        wide
        title={isList ? t("nav.approvals") : t("approval.detail")}
        subtitle={isList ? t("page.approvals.subtitle") : t("approval.detailDescription")}
        back={!isList ? { label: t("common.backToList"), onClick: () => editor.backToList() } : undefined}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            loading: manualRefresh.pending,
            onClick: () => void manualRefresh.track(() => runs.refetch()),
          },
        ]}
      />
      <PageBody wide>
        <QueryState
          query={runs}
          loadingLabel={t("loading.approvals")}
          skeleton={isList ? <TableSkeleton columns={3} /> : <FormSkeleton fields={4} />}
        >
          {isList ? (
            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>{t("approval.list")}</CardTitle>
                <CardDescription>{t("approval.listDescription")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
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
                <PagedDataTable
                  pageKey="approvals"
                  resetKey={`${approvalQuery}\u0000${approvalFilter}`}
                  rows={visibleApprovals}
                  columns={columns}
                  getRowKey={({ approval }) => approval.id}
                  selectedRowKey={selectedId}
                  onRowClick={({ approval }) => openApproval(approval.id)}
                  rowProps={({ approval }) => ({
                    className: "align-top",
                    "data-testid": `approval-row-${approval.id}`,
                  })}
                  ariaLabel={t("approval.list")}
                  paginationTestId="approval-list-pagination"
                  empty={
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
                        title={
                          approvalFilter === "pending" ? t("approval.empty.pendingTitle") : t("approval.empty.title")
                        }
                        hint={t("approval.empty.hint")}
                      />
                    )
                  }
                />
              </CardContent>
            </Card>
          ) : selected ? (
            <section className="space-y-5" aria-label={t("approval.detail")}>
              <Card className="min-w-0">
                <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0 space-y-2">
                    <CardTitle className="break-words [overflow-wrap:anywhere]">
                      {selected.approval.tool_call.name}
                    </CardTitle>
                    <StatusBadge {...approvalStatusView(selected.approval.status)} />
                  </div>
                  <ObjectActionBar
                    actions={approvalActions(selected.approval)}
                    ariaLabel={t("common.entityActions", { name: selected.approval.tool_call.name })}
                    moreLabel={t("common.moreActions")}
                    testId="approval-object-actions"
                  />
                </CardHeader>
                <CardContent className="space-y-4">
                  <dl className="grid gap-4 sm:grid-cols-2">
                    <ApprovalFact label={t("run.form.goal")} value={selected.run.goal} className="sm:col-span-2" />
                    <ApprovalFact
                      label={t("approval.reason")}
                      value={selected.approval.reason || t("approval.notRecorded")}
                      className="sm:col-span-2"
                    />
                    <ApprovalFact label={t("approval.createdAt")} value={approvalDate(selected.approval.created_at)} />
                    <ApprovalFact label={t("approval.id")} value={selected.approval.id} />
                  </dl>
                  {selected.approval.status === "pending" && !capabilities.decideApprovals ? (
                    <Banner severity="info">{t("approval.viewOnly")}</Banner>
                  ) : null}
                </CardContent>
              </Card>
              <Card className="min-w-0">
                <CardHeader>
                  <CardTitle>{t("approval.arguments")}</CardTitle>
                  <CardDescription>{t("approval.argumentsDescription")}</CardDescription>
                </CardHeader>
                <CardContent>
                  <JsonPreview value={selected.approval.tool_call.arguments} />
                </CardContent>
              </Card>
              {selected.approval.status !== "pending" ? (
                <Card data-testid="approval-decision-record">
                  <CardHeader>
                    <CardTitle>{t("approval.decisionRecord")}</CardTitle>
                    <CardDescription>{t("approval.finished")}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <dl className="grid gap-4 sm:grid-cols-2">
                      <ApprovalFact
                        label={t("approval.decidedBy")}
                        value={selected.approval.decided_by || t("approval.notRecorded")}
                      />
                      <ApprovalFact
                        label={t("approval.decidedAt")}
                        value={approvalDate(selected.approval.decided_at)}
                      />
                    </dl>
                  </CardContent>
                </Card>
              ) : null}
              <Card className="min-w-0">
                <CardHeader>
                  <CardTitle>{t("approval.relatedRun")}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <dl className="grid gap-4 sm:grid-cols-2">
                    <ApprovalFact label={t("audit.runId")} value={selected.run.id} />
                    <ApprovalFact label={t("audit.runStatus")} value={runStatusView(selected.run.status).label} />
                  </dl>
                  {capabilities.viewRuns ? (
                    <ButtonLink
                      to={`/runs?id=${encodeURIComponent(selected.run.id)}`}
                      linkComponent={Link}
                      variant="secondary"
                      icon={History}
                    >
                      {t("approval.openRun")}
                    </ButtonLink>
                  ) : null}
                </CardContent>
              </Card>
            </section>
          ) : (
            <MissingEditorTarget
              id={editor.target.kind === "edit" ? editor.target.id : "new"}
              onBack={() => editor.backToList()}
            />
          )}
        </QueryState>
      </PageBody>
    </>
  );
}

function ApprovalFact({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className={className}>
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="break-words text-sm text-fg [overflow-wrap:anywhere]">{value}</dd>
    </div>
  );
}
