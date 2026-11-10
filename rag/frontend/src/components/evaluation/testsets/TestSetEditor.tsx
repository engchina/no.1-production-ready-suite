import { AnswerPlainText, Banner, Button, Disclosure, FormActionBar, FormGrid, FormStatus, PageBody, PageHeader, SaveErrorBanner, Section, SearchableMultiSelect, SelectField, StatusBadge, Tabs, TextareaField, TextField, apiErrorMessage, useConfirm } from "@production-ready/ui";
import { Check, ChevronLeft, ChevronRight, Download, FolderOpen, Play, Plus, Save, Trash2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { saveDownloadedFile } from "@/lib/download";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { blankTestSetItem, testsetApi, type TestSetDraft, type TestSetItem } from "@/lib/testset-api";
import { toast } from "@/lib/toast";

const valid = (item: TestSetItem) => Boolean(item.question.trim() && (item.response_decision !== "answer" || item.standard_answer.trim()));

/** 入力は全項目を保持し、参考と編集を切り替えても破棄しない。 */
export function DraftEditor({ initial, onClose, onEvaluate, loadNotice }: { initial: TestSetDraft; onClose: () => void; onEvaluate: () => void; loadNotice?: ReactNode }) {
  const [draft, setDraft] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const [selected, setSelected] = useState(initial.items.find(item => !item.confirmed)?.item_id ?? initial.items[0]?.item_id ?? "");
  const [checked, setChecked] = useState<string[]>([]);
  const [tab, setTab] = useState("edit");
  const [downloading, setDownloading] = useState(false);
  const editorRef = useRef<HTMLDivElement>(null);
  const referenceRef = useRef<HTMLDivElement>(null);
  const positions = useRef(new Map<string, [number, number]>());
  const confirm = useConfirm();
  const qc = useQueryClient();
  const save = useMutation({ mutationFn: ({ value, ids = [] }: { value: TestSetDraft; ids?: string[] }) => testsetApi.save(value, ids), onSuccess: value => { setDraft(value); setBaseline(value); void qc.invalidateQueries({ queryKey: ["testsets"] }); void qc.invalidateQueries({ queryKey: ["testset", initial.draft_id] }); toast.success(t("evaluation.testsets.saved")); } });
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const guard = useLeaveGuard(dirty, save.isPending);
  const item = draft.items.find(value => value.item_id === selected);
  const index = draft.items.findIndex(value => value.item_id === selected);
  const count = draft.items.filter(value => value.confirmed).length;
  const remember = () => { const value = positions.current.get(selected) ?? [0, 0]; if (window.matchMedia("(max-width: 64rem)").matches) value[tab === "edit" ? 0 : 1] = document.getElementById("pr-main")?.scrollTop ?? 0; else { value[0] = editorRef.current?.scrollTop ?? 0; value[1] = referenceRef.current?.scrollTop ?? 0; } positions.current.set(selected, value); };
  const choose = (id: string) => { remember(); setSelected(id); };
  useEffect(() => { const frame = requestAnimationFrame(() => { const [edit, reference] = positions.current.get(selected) ?? [0, 0]; if (window.matchMedia("(max-width: 64rem)").matches) document.getElementById("pr-main")?.scrollTo({ top: tab === "edit" ? edit : reference, behavior: "instant" }); else { if (editorRef.current) editorRef.current.scrollTop = edit; if (referenceRef.current) referenceRef.current.scrollTop = reference; } }); return () => cancelAnimationFrame(frame); }, [selected, tab]);
  const change = (patch: Partial<TestSetItem>) => setDraft(previous => ({ ...previous, items: previous.items.map(value => value.item_id === selected ? { ...value, ...patch, confirmed: false } : value) }));
  const next = () => choose(draft.items[(index + 1) % draft.items.length]?.item_id ?? "");
  const saveConfirm = async () => { if (!item || !valid(item)) return; try { const result = await save.mutateAsync({ value: draft, ids: [selected] }); const remaining = result.items.slice(index + 1).concat(result.items.slice(0, index)).find(value => !value.confirmed); if (remaining) choose(remaining.item_id); } catch { /* 保存の案内に残す。 */ } };
  const batch = async () => { const items = draft.items.filter(value => checked.includes(value.item_id)); if (!items.length) return; if (items.some(value => !valid(value))) { toast.error(t("evaluation.testsets.invalid")); return; } if (await confirm({ title: t("evaluation.testsets.batchTitle", { count: items.length }), description: t("evaluation.testsets.batchHelp"), confirmLabel: t("common.confirm"), tone: "info" })) { try { await save.mutateAsync({ value: draft, ids: items.map(value => value.item_id) }); setChecked([]); } catch { /* 保存の案内に残す。 */ } } };
  const remove = async () => { if (!item || !(await confirm({ title: t("evaluation.testsets.deleteTitle"), description: t("evaluation.testsets.deleteHelp"), confirmLabel: t("common.delete") }))) return; const items = draft.items.filter(value => value.item_id !== selected); setDraft({ ...draft, items }); setChecked(values => values.filter(value => value !== selected)); choose(items[Math.min(index, items.length - 1)]?.item_id ?? ""); };
  const download = async () => { setDownloading(true); try { saveDownloadedFile(await testsetApi.download(baseline)); } catch (error) { toast.error(apiErrorMessage(error, t("evaluation.testsets.downloadError"))); } finally { setDownloading(false); } };
  return <>
    <PageHeader wide title={draft.name} subtitle={t("evaluation.testsets.progress", { confirmed: count, total: draft.items.length, pending: draft.items.length - count })} back={{ label: t("common.backToList"), onClick: async () => { if (await guard()) onClose(); } }} actions={[
      { id: "export", kind: "utility", label: t("evaluation.testsets.download"), icon: Download, loading: downloading, disabled: dirty || !count || save.isPending, onClick: () => void download() },
      { id: "run", kind: "utility", label: t("evaluation.testsets.run", { count }), icon: Play, disabled: dirty || !count || save.isPending, onClick: onEvaluate },
    ]} />
    <PageBody wide>
      {loadNotice}
      <Section><FormGrid columns={1}><TextField width="md" id="testset-draft-name" required label={t("evaluation.testsets.name")} value={draft.name} maxLength={200} disabled={save.isPending} onChange={event => setDraft({ ...draft, name: event.target.value })} /></FormGrid><p className="mt-3 text-sm text-fg-muted">{t("evaluation.testsets.pendingHelp")}</p></Section>
      <div className="testset-mobile-tools"><SelectField width="full" id="testset-item-picker" label={t("evaluation.testsets.itemSelector")} value={selected} onValueChange={choose} options={draft.items.map((value, i) => ({ value: value.item_id, label: `${i + 1}. ${value.question || t("evaluation.testsets.missingQuestion")} · ${t(value.confirmed ? "evaluation.testsets.isConfirmed" : "evaluation.testsets.unconfirmed")}` }))} /><Tabs idPrefix="testset-mobile" ariaLabel={t("evaluation.testsets.itemSelector")} value={tab} onChange={value => { remember(); setTab(value); }} items={[{ id: "edit", label: t("evaluation.testsets.editTab") }, { id: "reference", label: t("evaluation.testsets.referenceTab") }]} /></div>
      <div className="testset-review-workspace" data-testid="testset-editor">
        <aside className="testset-item-nav"><Section title={t("evaluation.testsets.items")}><div className="grid gap-3">
          {draft.items.map((value, i) => <div key={value.item_id} className="grid gap-1"><div className="flex items-start gap-2"><input type="checkbox" aria-label={t("evaluation.testsets.selectItem", { question: value.question || String(i + 1) })} checked={checked.includes(value.item_id)} disabled={save.isPending} onChange={() => setChecked(values => values.includes(value.item_id) ? values.filter(id => id !== value.item_id) : [...values, value.item_id])} /><Button variant={selected === value.item_id ? "secondary" : "ghost"} className="flex-1 justify-start whitespace-normal text-left" onClick={() => choose(value.item_id)}>{i + 1}. {value.question || t("evaluation.testsets.missingQuestion")}</Button></div><StatusBadge variant={value.confirmed ? "success" : "neutral"} label={t(value.confirmed ? "evaluation.testsets.isConfirmed" : "evaluation.testsets.unconfirmed")} /></div>)}
        </div></Section></aside>
        <div ref={editorRef} className={`testset-editor-pane ${tab === "edit" ? "testset-mobile-active" : ""}`}>
          <Section title={item ? t("evaluation.testsets.itemNo", { count: index + 1 }) : t("evaluation.testsets.noItems")} actions={<Button icon={Plus} variant="secondary" disabled={save.isPending || draft.items.length >= 200} onClick={() => { const value = blankTestSetItem(); setDraft({ ...draft, items: [...draft.items, value] }); choose(value.item_id); }}>{t("evaluation.testsets.add")}</Button>}>
            {item ? <FormGrid columns={1}>
              {item.source_notice ? <Banner severity="warning">{item.source_notice}</Banner> : null}
              <TextareaField width="full" id="testset-question" required label={t("evaluation.testsets.question")} rows={3} maxLength={2000} disabled={save.isPending} value={item.question} onChange={event => change({ question: event.target.value })} />
              <TextareaField width="full" id="testset-standard" required={item.response_decision === "answer"} label={t("evaluation.testsets.standard")} helper={t("evaluation.testsets.standardHelp")} rows={7} maxLength={8000} disabled={save.isPending} value={item.standard_answer} onChange={event => change({ standard_answer: event.target.value })} />
              <TextareaField width="full" id="testset-evidence" label={t("evaluation.testsets.evidence")} helper={t("evaluation.testsets.evidenceHelp")} rows={3} disabled={save.isPending} value={item.evidence.join("\n")} onChange={event => change({ evidence: event.target.value.split("\n") })} />
              <Disclosure summary={t("evaluation.testsets.advanced")}><FormGrid columns={1}>
                <SelectField width="full" id="testset-decision" label={t("evaluation.testsets.decision")} disabled={save.isPending} value={item.response_decision} options={(["answer", "clarify", "hold"] as const).map(value => ({ value, label: t(`evaluation.testsets.decision.${value}`) }))} onValueChange={value => change({ response_decision: value as TestSetItem["response_decision"] })} />
                <TextareaField width="full" id="testset-points" label={t("evaluation.testsets.points")} rows={3} disabled={save.isPending} value={item.required_answer_points.join("\n")} onChange={event => change({ required_answer_points: event.target.value.split("\n") })} />
                <TextareaField width="full" id="testset-process" label={t("evaluation.testsets.process")} rows={4} maxLength={8000} disabled={save.isPending} value={item.answer_process} onChange={event => change({ answer_process: event.target.value })} />
              </FormGrid></Disclosure>
            </FormGrid> : null}
            <SaveErrorBanner testId="testset-save-error" message={save.isError ? apiErrorMessage(save.error, t("evaluation.testsets.saveError")) : null} attemptKey={save.submittedAt} />
            <FormActionBar ariaLabel={t("evaluation.testsets.save")} status={<FormStatus tone={save.isPending ? "info" : save.isError ? "danger" : dirty ? "warning" : "success"} message={t(save.isPending ? "evaluation.testsets.saveState.saving" : save.isError ? "evaluation.testsets.saveState.failed" : dirty ? "evaluation.testsets.saveState.unsaved" : "evaluation.testsets.saveState.saved")} />} primaryActions={[{ id: "confirm", label: t("evaluation.testsets.confirmItem"), icon: Check, loading: save.isPending, disabled: !item || !valid(item) || !draft.name.trim(), onClick: () => void saveConfirm() }]} secondaryActions={[{ id: "save", label: t("evaluation.testsets.save"), icon: Save, disabled: save.isPending || !dirty || !draft.name.trim(), onClick: () => save.mutate({ value: draft }) }]} />
            {item ? <div className="mt-4 flex flex-wrap gap-2"><Button variant="ghost" icon={ChevronLeft} disabled={save.isPending || index <= 0} onClick={() => choose(draft.items[index - 1].item_id)}>{t("evaluation.testsets.previous")}</Button><Button variant="ghost" icon={ChevronRight} disabled={save.isPending || draft.items.length < 2} onClick={next}>{t("evaluation.testsets.skip")}</Button><Button variant="ghost" icon={Trash2} disabled={save.isPending} onClick={() => void remove()}>{t("evaluation.testsets.deleteItem")}</Button></div> : null}
          </Section>
        </div>
        <aside ref={referenceRef} className={`testset-reference-pane ${tab === "reference" ? "testset-mobile-active" : ""}`}><Section title={t("evaluation.testsets.reference")} description={t("evaluation.testsets.referenceHelp")}>
          <div className="grid gap-4"><AnswerPlainText text={item?.reference_answer || t("evaluation.testsets.noReference")} />
          <Button variant="secondary" icon={FolderOpen} disabled={!item?.reference_answer || save.isPending} onClick={() => { if (item) change({ standard_answer: item.reference_answer }); setTab("edit"); }}>{t("evaluation.testsets.adopt")}</Button>
          {item?.reference_evidence.map((entry, i) => <AnswerPlainText key={i} text={[entry.file_name, entry.text ?? entry.content].filter(Boolean).map(String).join("\n")} />)}
          {item ? <Disclosure summary={t("evaluation.testsets.original")}><AnswerPlainText text={[item.original.question, item.original.standard_answer, ...item.original.evidence].join("\n")} /></Disclosure> : null}</div>
        </Section></aside>
      </div>
      <Section><div className="flex flex-wrap items-center gap-3"><SearchableMultiSelect width="md" id="testset-batch-picker" label={t("evaluation.testsets.select")} value={checked} disabled={save.isPending} options={draft.items.map((value, i) => ({ value: value.item_id, label: `${i + 1}. ${value.question || t("evaluation.testsets.missingQuestion")}` }))} onValueChange={setChecked} /><Button variant="secondary" icon={Check} disabled={!checked.length || save.isPending} onClick={() => void batch()}>{t("evaluation.testsets.batch")}</Button><span className="text-sm text-fg-muted">{t("evaluation.testsets.selected", { count: checked.length })}</span></div></Section>
    </PageBody>
  </>;
}
