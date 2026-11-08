import { AnswerPlainText, Button, DataTable, DEFAULT_PAGE_SIZE, INFORMATION_TABLE_VISIBLE_ROWS, ListToolbar, PagedDataTable, Pagination, RowTitleButton, Section } from "@production-ready/ui";
import { Download } from "lucide-react";
import { useState } from "react";

import { ApiErrorState } from "@/components/StateViews";
import { requestFile } from "@/lib/api";
import { useAnnotationRows, useCalibrationAgreement, useLabelSet, useLabelSets } from "@/lib/calibration-queries";
import { saveDownloadedFile } from "@/lib/download";
import { type I18nKey, t } from "@/lib/i18n";
import { listCountProps, useClientListPaging } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import { toast } from "@/lib/toast";

/** 旧集合は閲覧と書き出しだけ。標準回答や新しい正解へ自動転記しない。 */
export function PreviousCalibration() {
  const sets = useLabelSets();
  const [selected, setSelected] = useState("");
  // 旧集合の一覧は全件を持つので、件数と 25 件のページングを付ける（#4636）。
  const paging = useClientListPaging("evaluation.testsets.previousCalibrationPaging", "sets");
  return <Section title={t("evaluation.testsets.previous")} description={t("evaluation.testsets.previousHelp")}>
    {sets.isError ? <ApiErrorState fallback={t("common.apiError")} error={sets.error} /> : <>
      <ListToolbar count={listCountProps({ matchedTotal: sets.data?.items.length, filtered: false })} testId="previous-calibration-toolbar" />
      <PagedDataTable {...paging} paginationLabels={paginationLabels()} paginationAriaLabel={t("evaluation.testsets.previous")}
        rows={sets.data?.items ?? []} getRowKey={row => row.label_set_id} loading={sets.isPending}
        ariaLabel={t("evaluation.testsets.previous")}
        onRowClick={row => setSelected(row.label_set_id)} selectedRowKey={selected}
        columns={[{ key: "name", header: t("evaluation.testsets.name"), render: row => <RowTitleButton title={row.name} onClick={() => setSelected(row.label_set_id)} /> }]} />
      {selected ? <PreviousDetail key={selected} id={selected} /> : null}
    </>}
  </Section>;
}

function PreviousDetail({ id }: { id: string }) {
  const detail = useLabelSet(id);
  const [offset, setOffset] = useState(0);
  const rows = useAnnotationRows(id, { offset, status: "all" });
  const agreement = useCalibrationAgreement(id, { offset: offset * DEFAULT_PAGE_SIZE, limit: DEFAULT_PAGE_SIZE, status: "all" }, Boolean(detail.data?.can_manage));
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const row = detail.data?.can_manage ? agreement.data?.rows.find(value => `${value.item_id}:${value.aspect}` === selected) ?? agreement.data?.rows[0] : rows.data?.items[0];
  const download = async () => {
    setBusy(true);
    try { saveDownloadedFile(await requestFile(`/api/evaluation/calibration/label-sets/${encodeURIComponent(id)}/archive`, "previous_calibration.json")); }
    catch (error) { toast.error(error instanceof Error ? error.message : t("evaluation.testsets.downloadError")); }
    finally { setBusy(false); }
  };
  return <div className="mt-4 grid min-w-0 gap-3" data-testid="previous-calibration-readonly">
    {detail.data?.can_manage ? <>
      {agreement.isError ? <ApiErrorState fallback={t("common.apiError")} error={agreement.error} /> : null}
      <ListToolbar count={listCountProps({ matchedTotal: agreement.data?.total, filtered: false })} testId="previous-calibration-agreement-toolbar" />
      <DataTable ariaLabel={t("evaluation.testsets.previous")} rows={agreement.data?.rows ?? []} getRowKey={value => `${value.item_id}:${value.aspect}`}
        visibleRows={INFORMATION_TABLE_VISIBLE_ROWS} stickyHeader loading={agreement.isPending}
        onRowClick={value => setSelected(`${value.item_id}:${value.aspect}`)}
        columns={[
          { key: "question", header: t("evaluation.testsets.question"), rowHeader: true, render: value => <RowTitleButton title={value.question} onClick={() => setSelected(`${value.item_id}:${value.aspect}`)} /> },
          { key: "aspect", header: t("evaluation.calibration.progress.aspect"), render: value => t(`evaluation.calibration.aspect.${value.aspect}` as I18nKey) },
          { key: "label", header: t("evaluation.testsets.previousFinal"), render: value => value.final_label ? t(`evaluation.calibration.label.${value.final_label}` as I18nKey) : t("evaluation.calibration.label.none") },
        ]} />
      <Pagination page={offset + 1} totalPages={Math.max(1, Math.ceil((agreement.data?.total ?? 0) / DEFAULT_PAGE_SIZE))} onPageChange={page => setOffset(page - 1)} labels={paginationLabels()}
        range={agreement.data ? { start: agreement.data.total ? agreement.data.offset + 1 : 0, end: agreement.data.offset + agreement.data.rows.length, total: agreement.data.total } : undefined} />
    </> : null}
    {rows.isError ? <ApiErrorState fallback={t("common.apiError")} error={rows.error} /> : null}
    {row ? <>
      <p className="text-sm text-fg-muted">{t(`evaluation.calibration.aspect.${row.aspect}` as I18nKey)}</p>
      <AnswerPlainText text={row.question} />
      <AnswerPlainText text={row.answer} />
      <AnswerPlainText text={row.standard_answer ?? ""} />
      {"label" in row && row.label ? <p>{t(`evaluation.calibration.label.${row.label}` as I18nKey)}</p> : null}
      {!detail.data?.can_manage ? <Pagination page={offset + 1} totalPages={Math.max(1, rows.data?.total ?? 1)} onPageChange={page => setOffset(page - 1)} labels={paginationLabels()} /> : null}
    </> : null}
    {detail.data?.can_manage ? <Button variant="secondary" icon={Download} loading={busy} onClick={() => void download()}>{t("evaluation.testsets.previousExport")}</Button> : null}
  </div>;
}
