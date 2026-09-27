import { History, Trash2 } from "lucide-react";
import { useState } from "react";

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  type EntityAction,
  FormStatus,
  ObjectActionBar,
  Skeleton,
  StatusBadge,
  useConfirm,
} from "@engchina/production-ready-ui";

import { ApiError } from "@/lib/api";
import { confidenceVariant } from "@/lib/docrag-answer";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useAnswerRecordSettings,
  useDeleteDocragAnswer,
  useDocragAnswer,
  useDocragAnswers,
} from "@/lib/queries";

import { CitationCard } from "./CitationCard";
import { DocragAnswerPanel } from "./DocragAnswerPanel";

/** 業務ビューの保存済み DocRAG 回答(rag_poc の回答 JSON 相当)を開き直す。 */
export function DocragAnswerHistory({
  businessViewId,
}: {
  businessViewId: string;
}) {
  const list = useDocragAnswers(businessViewId);
  const retention = useAnswerRecordSettings().data?.retention_days;
  const [selected, setSelected] = useState<string | null>(null);
  const answers = list.data ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History size={16} className="text-accent-fg" aria-hidden />
          {t("search.history.title")}
        </CardTitle>
        <CardDescription>
          {t("search.history.description")}
          {retention === undefined
            ? null
            : ` ${
                retention > 0
                  ? t("search.history.retention", { days: retention })
                  : t("search.history.retentionUnlimited")
              }`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {list.isPending ? <Skeleton className="h-16 w-full" /> : null}
        {list.isError ? (
          <FormStatus
            tone="danger"
            message={
              list.error instanceof ApiError
                ? list.error.message
                : t("search.history.loadError")
            }
          />
        ) : null}
        {list.isSuccess && answers.length === 0 ? (
          <p className="text-xs text-fg-muted">{t("search.history.empty")}</p>
        ) : null}
        {answers.length > 0 ? (
          <ul className="space-y-1" aria-label={t("search.history.title")}>
            {answers.map((item) => (
              <li key={item.trace_id}>
                <Button
                  size="sm"
                  variant={selected === item.trace_id ? "secondary" : "ghost"}
                  className="h-auto w-full flex-wrap justify-start py-1.5 text-left [&>span]:whitespace-normal!"
                  aria-pressed={selected === item.trace_id}
                  onClick={() =>
                    setSelected(
                      selected === item.trace_id ? null : item.trace_id,
                    )
                  }
                >
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="break-words text-sm text-fg">
                      {item.rewritten_question || item.question}
                    </span>
                    <span className="tnum text-xs text-fg-muted">
                      {formatDateTime(item.created_at)} ·{" "}
                      {t(`search.history.surface.${item.surface}`)}
                    </span>
                  </span>
                  {item.confidence ? (
                    <StatusBadge
                      variant={confidenceVariant(item.confidence)}
                      label={t("search.docrag.confidence", {
                        value: item.confidence,
                      })}
                    />
                  ) : null}
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
        {selected ? (
          <SavedDocragAnswer
            traceId={selected}
            businessViewId={businessViewId}
            onDeleted={() => setSelected(null)}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}

/** 保存済み DocRAG 回答 1 件(質問・回答・根拠パネル・引用)。 */
export function SavedDocragAnswer({
  traceId,
  businessViewId,
  showAnswer = true,
  onDeleted,
}: {
  traceId: string;
  businessViewId: string;
  showAnswer?: boolean;
  onDeleted?: () => void;
}) {
  const detail = useDocragAnswer(traceId);
  const remove = useDeleteDocragAnswer();
  const confirm = useConfirm();

  async function handleDelete() {
    const confirmed = await confirm({
      title: t("search.history.deleteTitle"),
      description: t("search.history.deleteDescription"),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!confirmed) return;
    remove.mutate(traceId, { onSuccess: () => onDeleted?.() });
  }

  if (detail.isPending) return <Skeleton className="h-24 w-full" />;
  if (detail.isError || !detail.data) {
    return (
      <FormStatus
        tone="danger"
        message={
          detail.error instanceof ApiError
            ? detail.error.message
            : t("search.history.loadError")
        }
      />
    );
  }
  const record = detail.data;
  // 保存された回答 1 件の操作。危険な操作だけなので ObjectActionBar の「その他の操作」に入り、
  // 確定は確認ダイアログで行う（buttons.md §5.1）。
  const actions: EntityAction[] = [
    {
      id: "delete",
      label: t("search.history.delete"),
      icon: Trash2,
      tone: "danger",
      disabled: remove.isPending,
      loading: remove.isPending,
      testId: `docrag-answer-delete-${record.trace_id}`,
      onSelect: () => void handleDelete(),
    },
  ];
  return (
    <div className="space-y-3 rounded-md border border-border bg-surface-sunken p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 flex-1 break-words text-xs text-fg-muted">
          {showAnswer
            ? t("search.history.question", { question: record.question })
            : t("search.history.savedAt", { value: formatDateTime(record.created_at) })}
        </p>
        <ObjectActionBar
          actions={actions}
          ariaLabel={t("common.objectActions.aria", { name: t("search.history.objectName") })}
          moreLabel={t("common.objectActions.more")}
          testId="docrag-answer-actions"
        />
      </div>
      {showAnswer ? (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">
          {record.answer}
        </p>
      ) : null}
      <DocragAnswerPanel
        docrag={record.docrag}
        traceId={record.evaluation_available ? record.trace_id : null}
        evaluation={record.evaluation}
      />
      {record.citations.length > 0 ? (
        <ul className="space-y-2">
          {record.citations.map((chunk, index) => (
            <CitationCard
              key={chunk.chunk_id}
              chunk={chunk}
              index={index}
              traceId={record.trace_id}
              businessViewId={businessViewId}
              sourceSurface={record.surface}
            />
          ))}
        </ul>
      ) : null}
      {remove.isError ? (
        <FormStatus
          tone="danger"
          message={
            remove.error instanceof ApiError
              ? remove.error.message
              : t("search.history.deleteError")
          }
        />
      ) : null}
    </div>
  );
}
