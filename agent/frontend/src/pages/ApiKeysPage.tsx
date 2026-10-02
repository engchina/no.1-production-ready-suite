import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeySquare, Trash2 } from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  EmptyState,
  Fieldset,
  FormActionBar,
  FormStatus,
  PageBody,
  PageHeader,
  RowActionMenu,
  SearchableMultiSelect,
  SelectField,
  StatusBadge,
  TableSkeleton,
  TextField,
  TimedLoadingState,
  toast,
  useConfirm,
  type DataTableColumn,
} from "@engchina/production-ready-ui";

import { listScrollLabel } from "@/components/ListViews";
import { agentApi, type ApiKey, type ApiKeyCreated, type ApiKeyExpiryDays } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";

// API キー（#778）。業務システムや MCP クライアントが、業務 Agent を MCP（`POST /api/mcp`）で呼ぶための
// キー。キーは作った利用者として動き、選んだ業務 Agent だけを呼べる。秘密は作成の直後に 1 回だけ出す。

const EXPIRY_OPTIONS = ["30", "90", "365", "none"] as const;
type ExpiryOption = (typeof EXPIRY_OPTIONS)[number];
type Scope = "all" | "selected";

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(t("apiKeys.copied"));
  } catch {
    toast.error(text);
  }
}

export function ApiKeysPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const capabilities = useCapabilities();
  const canManage = capabilities.admin;
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: agentApi.listApiKeys });
  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents, enabled: canManage });
  const agentNames = useMemo(
    () => new Map((agents.data?.agents ?? []).map((agent) => [agent.id, agent.name])),
    [agents.data]
  );

  const [name, setName] = useState("");
  const [scope, setScope] = useState<Scope>("selected");
  const [agentIds, setAgentIds] = useState<string[]>([]);
  const [expiry, setExpiry] = useState<ExpiryOption>("90");
  const [submitted, setSubmitted] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);

  const nameError = submitted && !name.trim() ? t("apiKeys.create.nameRequired") : undefined;
  const agentsError =
    submitted && scope === "selected" && agentIds.length === 0 ? t("apiKeys.create.agentsRequired") : undefined;

  const create = useMutation({
    mutationFn: () =>
      agentApi.createApiKey({
        name: name.trim(),
        agent_ids: scope === "all" ? null : agentIds,
        expires_in_days: expiry === "none" ? null : (Number(expiry) as ApiKeyExpiryDays),
      }),
    onSuccess: (result) => {
      setCreated(result);
      setName("");
      setAgentIds([]);
      setSubmitted(false);
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });
  const remove = useMutation({
    mutationFn: (key: ApiKey) => agentApi.deleteApiKey(key.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
      toast.success(t("apiKeys.deleted"));
    },
    onError: (error) => toast.error(error.message),
  });

  function submit() {
    setSubmitted(true);
    if (!name.trim() || (scope === "selected" && agentIds.length === 0)) return;
    create.mutate();
  }

  async function confirmDelete(key: ApiKey) {
    const ok = await confirm({
      title: t("apiKeys.deleteTitle", { name: key.name }),
      description: t("apiKeys.deleteDescription"),
      confirmLabel: t("apiKeys.delete"),
      tone: "danger",
    });
    if (ok) remove.mutate(key);
  }

  const endpoint = `${window.location.origin}/api/mcp`;
  return (
    <>
      <PageHeader wide title={t("nav.settingsApiKeys")} subtitle={t("page.apiKeys.subtitle")} />
      <PageBody wide>
        {keys.data && !keys.data.persistent ? (
          <Banner severity="warning">{t("apiKeys.notPersistent")}</Banner>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>{t("apiKeys.endpoint.title")}</CardTitle>
            <CardDescription>{t("apiKeys.endpoint.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <code
                className="min-w-0 break-all rounded-md bg-surface-sunken px-3 py-2 font-mono text-sm text-fg"
                data-testid="api-keys-endpoint"
              >
                {endpoint}
              </code>
              <Button variant="secondary" icon={Copy} onClick={() => void copyText(endpoint)}>
                {t("apiKeys.endpoint.copy")}
              </Button>
            </div>
            <p className="text-xs leading-5 text-fg-muted">{t("apiKeys.endpoint.tools")}</p>
          </CardContent>
        </Card>

        {canManage ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("apiKeys.create.title")}</CardTitle>
              <CardDescription>{t("apiKeys.create.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {created ? (
                <CreatedKey created={created} onDone={() => setCreated(null)} />
              ) : null}
              <div className="grid gap-4 md:grid-cols-2">
                <TextField
                  id="api-key-name"
                  label={t("apiKeys.create.name")}
                  required
                  value={name}
                  maxLength={100}
                  placeholder={t("apiKeys.create.namePlaceholder")}
                  error={nameError}
                  onValueChange={setName}
                />
                <SelectField<ExpiryOption>
                  id="api-key-expiry"
                  label={t("apiKeys.create.expiry")}
                  value={expiry}
                  options={EXPIRY_OPTIONS.map((option) => ({
                    value: option,
                    label: t(`apiKeys.create.expiry.${option}` as I18nKey),
                  }))}
                  onValueChange={setExpiry}
                />
              </div>
              <Fieldset
                legend={t("apiKeys.create.scope")}
                role="radiogroup"
                error={agentsError}
                className="space-y-2"
              >
                <div className="flex flex-wrap gap-2">
                  {(["selected", "all"] as const).map((option) => (
                    <label
                      key={option}
                      className="flex cursor-pointer items-center gap-2 rounded-md border border-border-control bg-surface px-3 py-2 text-sm text-fg has-[:checked]:border-accent-emphasis has-[:checked]:bg-accent-subtle"
                    >
                      <input
                        type="radio"
                        name="api-key-scope"
                        value={option}
                        checked={scope === option}
                        onChange={() => setScope(option)}
                        className="accent-accent-emphasis"
                      />
                      {option === "all" ? t("apiKeys.create.scopeAll") : t("apiKeys.create.scopeSelected")}
                    </label>
                  ))}
                </div>
                {scope === "selected" ? (
                  <SearchableMultiSelect
                    id="api-key-agents"
                    label={t("apiKeys.create.agents")}
                    labelHidden
                    options={(agents.data?.agents ?? []).map((agent) => ({
                      value: agent.id,
                      label: agent.name,
                      description: agent.id,
                    }))}
                    value={agentIds}
                    onValueChange={setAgentIds}
                    invalid={Boolean(agentsError)}
                  />
                ) : null}
              </Fieldset>
              <FormActionBar
                ariaLabel={t("apiKeys.create.title")}
                primaryActions={[
                  {
                    id: "create",
                    label: t("apiKeys.create.submit"),
                    icon: KeySquare,
                    loading: create.isPending,
                    onClick: submit,
                    testId: "api-key-create",
                  },
                ]}
                status={create.error ? <FormStatus tone="danger" message={create.error.message} /> : null}
              />
            </CardContent>
          </Card>
        ) : null}

        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("apiKeys.list.title")}</CardTitle>
          </CardHeader>
          <CardContent>
            {keys.isLoading ? (
              <TimedLoadingState label={t("loading.apiKeys")} testId="api-keys-loading">
                <TableSkeleton columns={5} />
              </TimedLoadingState>
            ) : keys.error ? (
              <FormStatus tone="danger" message={keys.error.message} />
            ) : (keys.data?.keys ?? []).length === 0 ? (
              <EmptyState title={t("apiKeys.list.empty")} hint={t("apiKeys.list.emptyHint")} />
            ) : (
              <KeysTable
                keys={keys.data?.keys ?? []}
                agentNames={agentNames}
                canManage={canManage}
                onDelete={(key) => void confirmDelete(key)}
              />
            )}
          </CardContent>
        </Card>
      </PageBody>
    </>
  );
}

function CreatedKey({ created, onDone }: { created: ApiKeyCreated; onDone: () => void }) {
  return (
    <Banner severity="success" title={t("apiKeys.created.title")}>
      <div className="space-y-2" data-testid="api-key-created">
        <p>{t("apiKeys.created.description")}</p>
        <code className="block break-all rounded-md bg-surface px-3 py-2 font-mono text-sm text-fg" data-testid="api-key-token">
          {created.token}
        </code>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" icon={Copy} onClick={() => void copyText(created.token)}>
            {t("apiKeys.created.copy")}
          </Button>
          <Button size="sm" variant="secondary" icon={Check} onClick={onDone}>
            {t("apiKeys.created.done")}
          </Button>
        </div>
      </div>
    </Banner>
  );
}

function KeysTable({
  keys,
  agentNames,
  canManage,
  onDelete,
}: {
  keys: ApiKey[];
  agentNames: Map<string, string>;
  canManage: boolean;
  onDelete: (key: ApiKey) => void;
}) {
  const columns: DataTableColumn<ApiKey>[] = [
    {
      key: "name",
      header: t("apiKeys.column.name"),
      rowHeader: true,
      className: "min-w-48",
      render: (key) => (
        <>
          <span className="block break-words font-medium text-fg">{key.name}</span>
          <span className="block break-all font-mono text-xs text-fg-muted">{`${key.token_prefix}…`}</span>
        </>
      ),
    },
    {
      key: "agents",
      header: t("apiKeys.column.agents"),
      className: "max-w-72 text-xs text-fg",
      render: (key) =>
        key.agent_ids === null
          ? t("apiKeys.allAgents")
          : key.agent_ids.map((agentId) => agentNames.get(agentId) ?? agentId).join("、"),
    },
    {
      key: "expires",
      header: t("apiKeys.column.expires"),
      render: (key) =>
        key.expired ? (
          <StatusBadge variant="danger" label={t("apiKeys.expired")} />
        ) : (
          <span className="whitespace-nowrap text-xs tabular-nums text-fg">
            {key.expires_at ? formatDateTime(key.expires_at) : t("apiKeys.noExpiry")}
          </span>
        ),
    },
    {
      key: "lastUsed",
      header: t("apiKeys.column.lastUsed"),
      className: "whitespace-nowrap text-xs tabular-nums text-fg-muted",
      render: (key) => (key.last_used_at ? formatDateTime(key.last_used_at) : t("apiKeys.neverUsed")),
    },
    {
      key: "owner",
      header: t("apiKeys.column.owner"),
      className: "text-xs text-fg",
      render: (key) => (
        <>
          <span className="block">{key.owner_display_name || key.owner_user_uuid}</span>
          <span className="block tabular-nums text-fg-muted">{formatDateTime(key.created_at)}</span>
        </>
      ),
    },
  ];
  if (canManage) {
    columns.push({
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (key) => (
        <RowActionMenu
          actions={[{ id: "delete", label: t("apiKeys.delete"), icon: Trash2, tone: "danger", onSelect: () => onDelete(key) }]}
          ariaLabel={t("common.entityActions", { name: key.name })}
          testId={`api-key-row-actions-${key.id}`}
        />
      ),
    });
  }
  return (
    <DataTable
      rows={keys}
      columns={columns}
      getRowKey={(key) => key.id}
      ariaLabel={t("apiKeys.list.label")}
      scrollAriaLabel={listScrollLabel(t("apiKeys.list.label"))}
      tableClassName="w-full min-w-[760px]"
      stickyHeader
    />
  );
}
