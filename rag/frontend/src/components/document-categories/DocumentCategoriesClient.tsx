import {
  Button,
  Card,
  CardContent,
  FieldActionRow,
  FieldError,
  PageBody,
  PageHeader,
  RowActionMenu,
  Skeleton,
  TextField,
  TimedLoadingState,
  toast,
  type EntityAction,
} from "@production-ready/ui";
import { ArrowDown, ArrowUp, Pencil, Plus, Save, Trash2, X } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/components/security/AuthProvider";
import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { api, ApiError, type DocumentCategory } from "@/lib/api";
import { t } from "@/lib/i18n";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { useDocumentCategories, useDocumentCategoryMutation } from "@/lib/queries";

/** 段（1 = 大分類、2 = 中分類、3 = 小分類）の名前の i18n キー。 */
const LEVEL_KEYS = {
  1: "documentCategories.level.large",
  2: "documentCategories.level.middle",
  3: "documentCategories.level.small",
} as const;
const MAX_LEVEL = 3;
const NAME_MAX_LENGTH = 200;

/** 開いている入力の行（新しい分類の追加・名前の変更）。同時に 1 つだけ開く。 */
type Editing = { mode: "add"; parentId: string | null; level: number } | { mode: "rename"; node: DocumentCategory };

function levelLabel(level: number): string {
  return t(LEVEL_KEYS[level as keyof typeof LEVEL_KEYS] ?? LEVEL_KEYS[1]);
}

function failureMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/**
 * 文書の分類の一覧（大分類 → 中分類 → 小分類。#2131）。どの大分類の下にどの中分類、どの中分類の下にどの小分類が
 * あるかを作成・名前の変更・並べ替え・削除する。ここで作った分類を、文書の詳細と RAG 検索で選ぶ（どちらも必須
 * ではない）。操作はその場で保存する（1 件ずつの操作で、まとめて保存するフォームではない）。名前を変えると、その
 * 分類を使っている文書の分類の値も変わる。下に分類がある・文書が使っている分類は削除できない（backend が理由を返す）。
 */
export function DocumentCategoriesClient() {
  const query = useDocumentCategories();
  const confirm = useConfirm();
  const canManage = useAuth().hasPermission(MENU_PERMISSIONS.documentCategories);
  const [editing, setEditing] = useState<Editing | null>(null);
  const create = useDocumentCategoryMutation(api.createDocumentCategory);
  const rename = useDocumentCategoryMutation(({ id, name }: { id: string; name: string }) =>
    api.renameDocumentCategory(id, name),
  );
  const move = useDocumentCategoryMutation(({ id, direction }: { id: string; direction: "up" | "down" }) =>
    api.moveDocumentCategory(id, direction),
  );
  const remove = useDocumentCategoryMutation(api.deleteDocumentCategory);
  const busy = create.isPending || rename.isPending || move.isPending || remove.isPending;

  const onDelete = async (node: DocumentCategory) => {
    const confirmed = await confirm({
      title: t("documentCategories.delete.title", { name: node.name }),
      description: t("documentCategories.delete.description"),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!confirmed) return;
    remove.mutate(node.id, {
      onSuccess: () => toast.success(t("documentCategories.delete.done", { name: node.name })),
      onError: (error) => toast.error(failureMessage(error, t("documentCategories.error.delete"))),
    });
  };

  const onMove = (node: DocumentCategory, direction: "up" | "down") =>
    move.mutate(
      { id: node.id, direction },
      { onError: (error) => toast.error(failureMessage(error, t("documentCategories.error.move"))) },
    );

  const rowActions = (node: DocumentCategory, index: number, siblingCount: number): EntityAction[] => [
    ...(node.level < MAX_LEVEL
      ? [
          {
            id: "add-child",
            label: t("documentCategories.action.addChild", { level: levelLabel(node.level + 1) }),
            icon: Plus,
            disabled: busy,
            onSelect: () => setEditing({ mode: "add", parentId: node.id, level: node.level + 1 }),
          },
        ]
      : []),
    {
      id: "rename",
      label: t("documentCategories.action.rename"),
      icon: Pencil,
      disabled: busy,
      onSelect: () => setEditing({ mode: "rename", node }),
    },
    {
      id: "up",
      label: t("documentCategories.action.moveUp"),
      icon: ArrowUp,
      disabled: busy || index === 0,
      onSelect: () => onMove(node, "up"),
    },
    {
      id: "down",
      label: t("documentCategories.action.moveDown"),
      icon: ArrowDown,
      disabled: busy || index === siblingCount - 1,
      onSelect: () => onMove(node, "down"),
    },
    {
      id: "delete",
      label: t("documentCategories.action.delete"),
      icon: Trash2,
      tone: "danger",
      disabled: busy,
      onSelect: () => void onDelete(node),
    },
  ];

  const nameForm = (current: Editing) => (
    <CategoryNameForm
      key={current.mode === "rename" ? `rename-${current.node.id}` : `add-${current.parentId ?? "root"}`}
      idSuffix={current.mode === "rename" ? current.node.id : (current.parentId ?? "root")}
      label={
        current.mode === "rename"
          ? t("documentCategories.field.rename", { level: levelLabel(current.node.level) })
          : t("documentCategories.field.add", { level: levelLabel(current.level) })
      }
      initialName={current.mode === "rename" ? current.node.name : ""}
      submitLabel={current.mode === "rename" ? t("documentCategories.action.saveName") : t("documentCategories.action.add")}
      pending={create.isPending || rename.isPending}
      onCancel={() => setEditing(null)}
      onSubmit={(name, setError) => {
        if (current.mode === "rename") {
          rename.mutate(
            { id: current.node.id, name },
            {
              onSuccess: (result) => {
                setEditing(null);
                toast.success(
                  result.updated_document_count > 0
                    ? t("documentCategories.rename.doneWithDocuments", { count: result.updated_document_count })
                    : t("documentCategories.rename.done"),
                );
              },
              onError: (error) => setError(failureMessage(error, t("documentCategories.error.save"))),
            },
          );
          return;
        }
        create.mutate(
          { parent_id: current.parentId, name },
          {
            onSuccess: () => {
              setEditing(null);
              toast.success(t("documentCategories.add.done", { name }));
            },
            onError: (error) => setError(failureMessage(error, t("documentCategories.error.save"))),
          },
        );
      }}
    />
  );

  const renderNodes = (nodes: readonly DocumentCategory[], parentId: string | null, level: number) => (
    <ul
      className={level > 1 ? "mt-1 space-y-1 border-l border-border pl-4" : "space-y-1"}
      aria-label={level === 1 ? t("documentCategories.list.label") : undefined}
    >
      {nodes.map((node, index) => (
        <li key={node.id} data-testid={`document-category-${node.id}`} data-level={node.level}>
          {editing?.mode === "rename" && editing.node.id === node.id ? (
            nameForm(editing)
          ) : (
            <div className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-sunken">
              <span className="min-w-0 flex-1 break-words text-sm text-fg">
                <span className="font-medium">{node.name}</span>
                <span className="ml-2 text-xs text-fg-muted">
                  {levelLabel(node.level)}
                  {" · "}
                  {t("documentCategories.documentCount", { count: node.document_count })}
                </span>
              </span>
              {canManage ? (
                <RowActionMenu
                  actions={rowActions(node, index, nodes.length)}
                  ariaLabel={t("documentCategories.actions.aria", { name: node.name })}
                  testId={`document-category-actions-${node.id}`}
                />
              ) : null}
            </div>
          )}
          {node.children.length > 0 || (editing?.mode === "add" && editing.parentId === node.id)
            ? renderNodes(node.children, node.id, level + 1)
            : null}
        </li>
      ))}
      {editing?.mode === "add" && editing.parentId === parentId ? <li>{nameForm(editing)}</li> : null}
    </ul>
  );

  return (
    <div>
      <PageHeader
        wide
        title={t("nav.documentCategories")}
        subtitle={t("documentCategories.subtitle")}
        actions={
          canManage
            ? [
                {
                  id: "add-large",
                  kind: "primary",
                  label: t("documentCategories.action.addLarge"),
                  icon: Plus,
                  disabled: busy || query.isPending,
                  onClick: () => setEditing({ mode: "add", parentId: null, level: 1 }),
                },
              ]
            : []
        }
      />
      <PageBody wide>
        <Card>
          <CardContent className="space-y-3 pt-5">
            <p className="text-xs leading-relaxed text-fg-muted">{t("documentCategories.description")}</p>
            {query.isPending ? (
              <TimedLoadingState label={t("documentCategories.loading")} operationKey="document-categories">
                <div className="space-y-2" aria-hidden="true">
                  {Array.from({ length: 4 }, (_, index) => (
                    <Skeleton key={index} className="h-8 w-full" />
                  ))}
                </div>
              </TimedLoadingState>
            ) : query.isError ? (
              <ApiErrorState
                error={query.error}
                fallback={t("documentCategories.error.load")}
                onRetry={() => void query.refetch()}
              />
            ) : query.data.length === 0 && editing?.mode !== "add" ? (
              <EmptyState title={t("documentCategories.empty.title")} hint={t("documentCategories.empty.hint")} />
            ) : (
              renderNodes(query.data, null, 1)
            )}
          </CardContent>
        </Card>
      </PageBody>
    </div>
  );
}

/** 分類の名前の入力の行（追加・名前の変更）。保存の失敗は欄の下に出す。 */
function CategoryNameForm({
  idSuffix,
  label,
  initialName,
  submitLabel,
  pending,
  onCancel,
  onSubmit,
}: {
  idSuffix: string;
  label: string;
  initialName: string;
  submitLabel: string;
  pending: boolean;
  onCancel: () => void;
  onSubmit: (name: string, setError: (message: string) => void) => void;
}) {
  const [name, setName] = useState(initialName);
  const [error, setError] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError(t("documentCategories.error.nameRequired"));
      return;
    }
    setError("");
    onSubmit(trimmed, setError);
  };
  return (
    <form onSubmit={submit} className="rounded-md bg-surface-sunken p-2" data-testid="document-category-form">
      <FieldActionRow
        fit
        actions={
          <>
            <Button type="submit" icon={Save} loading={pending}>
              {submitLabel}
            </Button>
            <Button type="button" variant="secondary" icon={X} disabled={pending} onClick={onCancel}>
              {t("common.cancel")}
            </Button>
          </>
        }
        footer={<FieldError id={`document-category-name-${idSuffix}-error`} message={error} />}
      >
        <TextField
          id={`document-category-name-${idSuffix}`}
          label={label}
          width="md"
          value={name}
          maxLength={NAME_MAX_LENGTH}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `document-category-name-${idSuffix}-error` : undefined}
          autoFocus
          disabled={pending}
          onValueChange={(value) => {
            setName(value);
            if (error) setError("");
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") onCancel();
          }}
        />
      </FieldActionRow>
    </form>
  );
}
