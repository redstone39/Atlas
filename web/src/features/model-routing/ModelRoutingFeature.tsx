import {
  CheckCircle2,
  CloudCog,
  Cpu,
  KeyRound,
  MessageSquareText,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Server,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import {
  Autocomplete,
  AutocompleteContent,
  AutocompleteEmpty,
  AutocompleteInput,
  AutocompleteItem,
  AutocompleteList,
} from "../../components/ui/autocomplete";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "../../components/ui/empty";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { Switch } from "../../components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { Textarea } from "../../components/ui/textarea";
import { generatedId } from "../../shared/ids";
import { ApiError, type MessageReference } from "../../shared/user-messages";
import {
  LoadErrorState,
  LoadingState,
  localizedStatusLabel,
  PageHeader,
  StatusBadge,
  serverMessage,
} from "../../shared/product-ui";
import { modelRoutingApi } from "./api";
import type {
  AnswerBehaviorStatus,
  ModelRouteRuntimePolicy,
  ModelRouteRuntimePolicyInput,
  ModelRouteStatus,
  ModelRoutingFeatureProps,
  ProviderConnectionStatus,
  ProviderType,
} from "./types";

type SafeAction = MessageReference;
type ModelManagementTab = "connections" | "models" | "answer-behavior";

const providerDefaults: Record<ProviderType, string> = {
  openai_compatible: "https://api.openai.com/v1",
  azure_openai: "https://example.openai.azure.com/openai/v1",
};

type RuntimePolicyDraft = Record<
  | "tokenizer_profile"
  | "max_tool_executions"
  | "max_provider_invocations"
  | "max_catalog_pages"
  | "max_search_rounds"
  | "max_unique_evidence"
  | "max_retrieval_repairs"
  | "max_schema_retries_per_turn"
  | "max_selected_anchor_pages_per_round"
  | "provider_invocation_timeout_seconds"
  | "tool_execution_timeout_seconds"
  | "turn_timeout_seconds"
  | "context_window_tokens"
  | "max_input_tokens_per_invocation"
  | "max_output_tokens_per_invocation"
  | "max_tool_result_tokens_per_execution"
  | "max_total_tokens_per_conversation",
  string
>;

const createRuntimePolicyDraft: RuntimePolicyDraft = {
  tokenizer_profile: "cl100k_base",
  max_tool_executions: "12",
  max_provider_invocations: "14",
  max_catalog_pages: "5",
  max_search_rounds: "6",
  max_unique_evidence: "40",
  max_retrieval_repairs: "3",
  max_schema_retries_per_turn: "3",
  max_selected_anchor_pages_per_round: "20",
  provider_invocation_timeout_seconds: "60",
  tool_execution_timeout_seconds: "45",
  turn_timeout_seconds: "240",
  context_window_tokens: "400000",
  max_input_tokens_per_invocation: "272000",
  max_output_tokens_per_invocation: "16000",
  max_tool_result_tokens_per_execution: "64000",
  max_total_tokens_per_conversation: "1000000",
};

function runtimePolicyDraft(policy: ModelRouteRuntimePolicy): RuntimePolicyDraft {
  return {
    tokenizer_profile: policy.tokenizer_profile,
    max_tool_executions: String(policy.max_tool_executions),
    max_provider_invocations: String(policy.max_provider_invocations),
    max_catalog_pages: String(policy.max_catalog_pages),
    max_search_rounds: String(policy.max_search_rounds),
    max_unique_evidence: String(policy.max_unique_evidence),
    max_retrieval_repairs: String(policy.max_retrieval_repairs),
    max_schema_retries_per_turn: String(policy.max_schema_retries_per_turn),
    max_selected_anchor_pages_per_round: String(policy.max_selected_anchor_pages_per_round),
    provider_invocation_timeout_seconds: String(policy.provider_invocation_timeout_seconds),
    tool_execution_timeout_seconds: String(policy.tool_execution_timeout_seconds),
    turn_timeout_seconds: String(policy.turn_timeout_seconds),
    context_window_tokens: String(policy.context_window_tokens),
    max_input_tokens_per_invocation: String(policy.max_input_tokens_per_invocation),
    max_output_tokens_per_invocation: String(policy.max_output_tokens_per_invocation),
    max_tool_result_tokens_per_execution: String(policy.max_tool_result_tokens_per_execution),
    max_total_tokens_per_conversation: String(policy.max_total_tokens_per_conversation),
  };
}

function currentTestedDefaultRoute(
  routes: ModelRouteStatus[],
  defaultRouteId: string | null,
) {
  return routes.find(
    (route) =>
      route.route_id === defaultRouteId &&
      route.is_default &&
      route.enabled &&
      route.status === "test_passed",
  );
}

function parseRuntimePolicy(draft: RuntimePolicyDraft): ModelRouteRuntimePolicyInput | null {
  const numericKeys = Object.keys(draft).filter(
    (key) => key !== "tokenizer_profile",
  ) as Array<Exclude<keyof RuntimePolicyDraft, "tokenizer_profile">>;
  const values = Object.fromEntries(
    numericKeys.map((key) => [key, Number(draft[key])]),
  ) as Record<(typeof numericKeys)[number], number>;
  if (
    !draft.tokenizer_profile.trim() ||
    numericKeys.some((key) => !draft[key].trim() || !Number.isInteger(values[key]) || values[key] <= 0)
  ) return null;
  if (values.max_provider_invocations < values.max_tool_executions + 2) return null;
  if (values.max_retrieval_repairs > 3 || values.max_schema_retries_per_turn > 3) return null;
  if (values.max_selected_anchor_pages_per_round > 20) return null;
  if (values.max_provider_invocations < 14) return null;
  if (
    values.max_input_tokens_per_invocation + values.max_output_tokens_per_invocation >
    values.context_window_tokens
  ) return null;
  if (values.max_tool_result_tokens_per_execution > values.max_input_tokens_per_invocation) return null;
  if (
    values.max_total_tokens_per_conversation <
    values.max_input_tokens_per_invocation + values.max_output_tokens_per_invocation
  ) return null;
  if (
    values.turn_timeout_seconds < values.provider_invocation_timeout_seconds ||
    values.turn_timeout_seconds < values.tool_execution_timeout_seconds
  ) return null;
  return {
    schema_version: "model-route-runtime-policy-v4",
    tokenizer_profile: draft.tokenizer_profile.trim(),
    ...values,
  };
}

function PolicyNumberField({
  id,
  label,
  value,
  onChange,
}: {
  id: keyof RuntimePolicyDraft;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input
        id={id}
        type="number"
        inputMode="numeric"
        min={1}
        step={1}
        required
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  );
}

function readableTime(value: string | null, fallback: string, locale: string) {
  if (!value) return fallback;
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString(locale);
}

export function ModelRoutingFeature({
  onNotice,
  onRefresh,
}: ModelRoutingFeatureProps) {
  const { t, i18n } = useTranslation();
  const [connections, setConnections] = useState<ProviderConnectionStatus[]>([]);
  const [routes, setRoutes] = useState<ModelRouteStatus[]>([]);
  const [defaultRouteId, setDefaultRouteId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [connectionRefreshError, setConnectionRefreshError] = useState("");
  const [routesLoading, setRoutesLoading] = useState(false);
  const [routesLoadError, setRoutesLoadError] = useState("");
  const [routesLoaded, setRoutesLoaded] = useState(false);
  const [actionError, setActionError] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const [activeTab, setActiveTab] = useState<ModelManagementTab>("connections");
  const [answerBehavior, setAnswerBehavior] =
    useState<AnswerBehaviorStatus | null>(null);
  const [answerBehaviorDraft, setAnswerBehaviorDraft] = useState("");
  const [answerBehaviorLoading, setAnswerBehaviorLoading] = useState(false);
  const [answerBehaviorLoaded, setAnswerBehaviorLoaded] = useState(false);
  const [answerBehaviorError, setAnswerBehaviorError] = useState("");

  const [connectionDialogOpen, setConnectionDialogOpen] = useState(false);
  const [editingConnection, setEditingConnection] =
    useState<ProviderConnectionStatus | null>(null);
  const [connectionName, setConnectionName] = useState("");
  const [providerType, setProviderType] =
    useState<ProviderType>("openai_compatible");
  const [endpointUrl, setEndpointUrl] = useState(providerDefaults.openai_compatible);
  const [apiKey, setApiKey] = useState("");
  const [connectionEnabled, setConnectionEnabled] = useState(true);

  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const [editingRoute, setEditingRoute] = useState<ModelRouteStatus | null>(null);
  const [routeName, setRouteName] = useState("");
  const [modelName, setModelName] = useState("");
  const [modelConnectionId, setModelConnectionId] = useState("");
  const [modelEnabled, setModelEnabled] = useState(true);
  const [modelSupportsVision, setModelSupportsVision] = useState(false);
  const [runtimePolicy, setRuntimePolicy] = useState<RuntimePolicyDraft>({
    ...createRuntimePolicyDraft,
  });
  const [runtimePolicyTemplateRouteName, setRuntimePolicyTemplateRouteName] =
    useState<string | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [discoveryStatus, setDiscoveryStatus] =
    useState<"idle" | "loading" | "available" | "unavailable">("idle");
  const [discoveryMessage, setDiscoveryMessage] = useState("");
  const discoveryRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    void refreshConnections();
  }, []);

  useEffect(() => {
    if (!modelDialogOpen || !modelConnectionId) return;
    void discoverModels(modelConnectionId);
  }, [modelDialogOpen, modelConnectionId]);

  useEffect(() => () => discoveryRequest.current?.abort(), []);

  async function refreshConnections(showPageLoading = true) {
    if (showPageLoading) {
      setLoading(true);
      setLoadError("");
    } else {
      setConnectionRefreshError("");
    }
    try {
      const connectionResult = await modelRoutingApi.listProviderConnections();
      setConnections(connectionResult.connections);
    } catch (error) {
      const message = error instanceof Error ? error.message : t("admin.listLoadFailed");
      if (showPageLoading) setLoadError(message);
      else setConnectionRefreshError(message);
    } finally {
      if (showPageLoading) setLoading(false);
    }
  }

  async function refreshRoutes() {
    setRoutesLoading(true);
    setRoutesLoadError("");
    try {
      const result = await modelRoutingApi.listModelRoutes();
      setRoutes(result.routes);
      setDefaultRouteId(result.default_route_id);
      setRoutesLoaded(true);
    } catch (error) {
      setRoutesLoadError(error instanceof Error ? error.message : t("admin.listLoadFailed"));
    } finally {
      setRoutesLoading(false);
    }
  }

  async function refreshAnswerBehavior() {
    setAnswerBehaviorLoading(true);
    setAnswerBehaviorError("");
    try {
      const result = await modelRoutingApi.getAnswerBehavior();
      setAnswerBehavior(result);
      setAnswerBehaviorDraft(result.custom_guidance ?? "");
      setAnswerBehaviorLoaded(true);
    } catch (error) {
      setAnswerBehaviorError(
        error instanceof Error ? error.message : t("admin.listLoadFailed"),
      );
    } finally {
      setAnswerBehaviorLoading(false);
    }
  }

  async function updateAnswerBehavior(customGuidance: string | null) {
    if (!answerBehavior) return;
    setPendingAction("answer-behavior");
    setAnswerBehaviorError("");
    try {
      const result = await modelRoutingApi.updateAnswerBehavior({
        customGuidance,
        expectedRevision: answerBehavior.revision,
      });
      setAnswerBehavior(result);
      setAnswerBehaviorDraft(result.custom_guidance ?? "");
      onNotice(
        t(
          customGuidance === null
            ? "models.answerBehaviorCleared"
            : "models.answerBehaviorSaved",
        ),
      );
      await onRefresh();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await refreshAnswerBehavior();
        setAnswerBehaviorError(
          "answer_behavior.revision_changed_before_update",
        );
      } else {
        setAnswerBehaviorError(
          error instanceof Error ? error.message : t("admin.actionFailed"),
        );
      }
    } finally {
      setPendingAction("");
    }
  }

  async function discoverModels(connectionId: string) {
    discoveryRequest.current?.abort();
    const controller = new AbortController();
    discoveryRequest.current = controller;
    setDiscoveryStatus("loading");
    setDiscoveryMessage("");
    setAvailableModels([]);
    try {
      const result = await modelRoutingApi.listAvailableModels(connectionId, controller.signal);
      if (controller.signal.aborted) return;
      setAvailableModels(result.models);
      setDiscoveryStatus(result.discovery_status);
      setDiscoveryMessage(result.message_code);
    } catch (error) {
      if (controller.signal.aborted) return;
      setDiscoveryStatus("unavailable");
      setDiscoveryMessage(
        error instanceof Error ? error.message : t("models.discoveryUnavailableDescription"),
      );
    }
  }

  async function runAction(
    actionName: string,
    action: () => Promise<SafeAction>,
    onSuccess?: () => void,
  ) {
    setPendingAction(actionName);
    setActionError("");
    try {
      const result = await action();
      const message = serverMessage(result, t);
      onNotice(result.message_code);
      toast.success(message);
      await Promise.all([
        refreshConnections(false),
        routesLoaded || activeTab === "models" ? refreshRoutes() : Promise.resolve(),
      ]);
      await onRefresh();
      onSuccess?.();
    } catch (error) {
      const message = error instanceof Error ? error.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setApiKey("");
      setPendingAction("");
    }
  }

  function openCreateConnection() {
    setEditingConnection(null);
    setConnectionName("");
    setProviderType("openai_compatible");
    setEndpointUrl(providerDefaults.openai_compatible);
    setApiKey("");
    setConnectionEnabled(true);
    setConnectionDialogOpen(true);
  }

  function openEditConnection(connection: ProviderConnectionStatus) {
    setEditingConnection(connection);
    setConnectionName(connection.display_name);
    setProviderType(connection.provider_type);
    setEndpointUrl(connection.endpoint_url);
    setApiKey("");
    setConnectionEnabled(connection.enabled);
    setConnectionDialogOpen(true);
  }

  function closeConnectionDialog() {
    setApiKey("");
    setConnectionDialogOpen(false);
    setEditingConnection(null);
  }

  function openCreateModel(connectionId?: string) {
    const defaultConnectionId =
      connections.find((connection) => connection.credential_configured)?.connection_id ??
      connections[0]?.connection_id ??
      "";
    const templateRoute = currentTestedDefaultRoute(routes, defaultRouteId);
    setEditingRoute(null);
    setRouteName("");
    setModelName("");
    setModelConnectionId(connectionId ?? defaultConnectionId);
    setModelEnabled(true);
    setModelSupportsVision(false);
    setRuntimePolicy(
      templateRoute
        ? runtimePolicyDraft(templateRoute.runtime_policy)
        : { ...createRuntimePolicyDraft },
    );
    setRuntimePolicyTemplateRouteName(templateRoute?.display_name ?? null);
    setModelDialogOpen(true);
  }

  function openEditModel(route: ModelRouteStatus) {
    setEditingRoute(route);
    setRouteName(route.display_name);
    setModelName(route.model_name);
    setModelConnectionId(route.connection_id);
    setModelEnabled(route.enabled);
    setModelSupportsVision(route.supports_vision);
    setRuntimePolicy(runtimePolicyDraft(route.runtime_policy));
    setRuntimePolicyTemplateRouteName(null);
    setModelDialogOpen(true);
  }

  function closeModelDialog() {
    discoveryRequest.current?.abort();
    setModelDialogOpen(false);
    setEditingRoute(null);
    setAvailableModels([]);
    setDiscoveryStatus("idle");
    setDiscoveryMessage("");
    setRuntimePolicy({ ...createRuntimePolicyDraft });
    setModelSupportsVision(false);
    setRuntimePolicyTemplateRouteName(null);
  }

  const canSaveConnection = Boolean(
    connectionName.trim() && endpointUrl.trim() && (editingConnection || apiKey.trim()),
  );
  const parsedRuntimePolicy = parseRuntimePolicy(runtimePolicy);
  const runtimePolicyComplete = Object.values(runtimePolicy).every((value) => value.trim());
  const runtimePolicyInvalid = Boolean(runtimePolicyComplete && !parsedRuntimePolicy);
  const canSaveModel = Boolean(
    routeName.trim() && modelName.trim() && modelConnectionId && parsedRuntimePolicy,
  );
  const answerBehaviorLength = Array.from(answerBehaviorDraft).length;
  const normalizedAnswerBehaviorDraft = answerBehaviorDraft.trim();
  const canSaveAnswerBehavior = Boolean(
    answerBehavior &&
      normalizedAnswerBehaviorDraft &&
      answerBehaviorLength <= 2000 &&
      normalizedAnswerBehaviorDraft !== (answerBehavior.custom_guidance ?? ""),
  );

  return (
    <section className="flex flex-col gap-5">
      <PageHeader title={t("models.title")} />

      {actionError ? (
        <Alert variant="destructive">
          <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
          <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
        </Alert>
      ) : null}

      {loading ? (
        <LoadingState
          title={t("models.loadingTitle")}
        />
      ) : loadError ? (
        <LoadErrorState
          title={t("admin.listLoadFailed")}
          description={serverMessage(loadError, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => void refreshConnections()}
        />
      ) : (
        <Tabs
          value={activeTab}
          onValueChange={(value) => {
            const nextTab = value as ModelManagementTab;
            setActiveTab(nextTab);
            if (nextTab === "models" && !routesLoaded && !routesLoading) {
              void refreshRoutes();
            }
            if (
              nextTab === "answer-behavior" &&
              !answerBehaviorLoaded &&
              !answerBehaviorLoading
            ) {
              void refreshAnswerBehavior();
            }
          }}
          className="gap-5"
        >
          <TabsList variant="line" aria-label={t("models.tabsLabel")}>
            <TabsTrigger value="connections">
              <CloudCog />
              {t("models.connectionsTitle")}
            </TabsTrigger>
            <TabsTrigger value="models">
              <Cpu />
              {t("models.directoryTitle")}
            </TabsTrigger>
            <TabsTrigger value="answer-behavior">
              <MessageSquareText />
              {t("models.answerBehaviorTitle")}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="connections" className="flex flex-col gap-4">
            {connectionRefreshError && (
              <Alert variant="destructive">
                <AlertTitle>{t("admin.listLoadFailed")}</AlertTitle>
                <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
                  <span>{serverMessage(connectionRefreshError, t)}</span>
                  <Button variant="outline" size="sm" onClick={() => void refreshConnections(false)}>
                    {t("admin.retry")}
                  </Button>
                </AlertDescription>
              </Alert>
            )}
            <div className="flex flex-wrap justify-end gap-2">
                <Button variant="outline" onClick={() => void refreshConnections(false)}>
                  <RefreshCw data-icon="inline-start" />
                  {t("models.refresh")}
                </Button>
                <Button onClick={openCreateConnection}>
                  <Plus data-icon="inline-start" />
                  {t("models.addConnection")}
                </Button>
            </div>

            {connections.length === 0 ? (
              <Empty className="border">
                <EmptyHeader>
                  <EmptyTitle>{t("models.emptyConnectionsTitle")}</EmptyTitle>
                  <EmptyDescription>{t("models.emptyConnectionsDescription")}</EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <div className="grid gap-4">
                {connections.map((connection) => {
                  const credentialRequired =
                    connection.status === "credential_required" ||
                    !connection.credential_configured;
                  return (
                    <Card key={connection.connection_id}>
                      <CardHeader>
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                          <div className="min-w-0 flex flex-col gap-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <CardTitle>{connection.display_name}</CardTitle>
                              <StatusBadge
                                semantic={connectionStatusSemantic(connection.status)}
                                label={localizedStatusLabel(connection.status, t)}
                              />
                              {!connection.enabled ? (
                                <Badge variant="outline">{t("models.disabled")}</Badge>
                              ) : null}
                            </div>
                            <CardDescription className="break-all">
                              {connection.provider_type === "azure_openai"
                                ? t("models.providerAzure")
                                : t("admin.providerOpenAICompatible")}
                              {" · "}
                              {connection.endpoint_url}
                            </CardDescription>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => openEditConnection(connection)}
                            >
                              {credentialRequired ? (
                                <KeyRound data-icon="inline-start" />
                              ) : (
                                <Pencil data-icon="inline-start" />
                              )}
                              {credentialRequired
                                ? t("models.setApiKey")
                                : t("models.editConnection")}
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={
                                pendingAction === `test-connection-${connection.connection_id}`
                              }
                              onClick={() =>
                                void runAction(
                                  `test-connection-${connection.connection_id}`,
                                  () =>
                                    modelRoutingApi.testProviderConnection(
                                      connection.connection_id,
                                      connection.revision,
                                    ),
                                )
                              }
                            >
                              <CheckCircle2 data-icon="inline-start" />
                              {t("models.testConnection")}
                            </Button>
                          </div>
                        </div>
                      </CardHeader>
                      <CardContent className="flex flex-col gap-3">
                        <div className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-3">
                          <div>
                            {t("models.credentialState")}: {connection.credential_configured
                              ? t("models.configured")
                              : t("models.apiKeyRequired")}
                          </div>
                          <div>
                            {t("models.lastVerified")}: {readableTime(
                              connection.last_verified_at,
                              t("models.never"),
                              i18n.language,
                            )}
                          </div>
                          <div>{t("models.linkedModels", { count: connection.linked_model_count })}</div>
                        </div>
                        {credentialRequired ? (
                          <Alert>
                            <KeyRound />
                            <AlertTitle>{t("models.apiKeyRequired")}</AlertTitle>
                            <AlertDescription>
                              {t("models.apiKeyRequiredDescription")}
                            </AlertDescription>
                          </Alert>
                        ) : null}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}
          </TabsContent>

          <TabsContent value="models" className="flex flex-col gap-4">
            {routesLoading ? (
              <LoadingState
                title={t("models.loadingTitle")}
              />
            ) : routesLoadError ? (
              <LoadErrorState
                title={t("admin.listLoadFailed")}
                description={serverMessage(routesLoadError, t)}
                retryLabel={t("admin.retry")}
                onRetry={() => void refreshRoutes()}
              />
            ) : (
            <>
            <div className="flex flex-wrap justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => void Promise.all([refreshConnections(false), refreshRoutes()])}
                >
                  <RefreshCw data-icon="inline-start" />
                  {t("models.refresh")}
                </Button>
                <Button onClick={() => openCreateModel()} disabled={connections.length === 0}>
                  <Plus data-icon="inline-start" />
                  {t("models.addModel")}
                </Button>
            </div>

            {routes.length === 0 ? (
              <Empty className="border">
                <EmptyHeader>
                  <EmptyTitle>{t("models.emptyTitle")}</EmptyTitle>
                  <EmptyDescription>{t("models.emptyDescription")}</EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <Card>
                <CardContent className="p-0">
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>{t("models.modelOption")}</TableHead>
                          <TableHead>{t("admin.modelName")}</TableHead>
                          <TableHead>{t("models.connection")}</TableHead>
                          <TableHead>{t("users.status")}</TableHead>
                          <TableHead>{t("models.runtimePolicy")}</TableHead>
                          <TableHead>{t("models.executionLimits")}</TableHead>
                          <TableHead>{t("models.conversationTokenCap")}</TableHead>
                          <TableHead>{t("models.default")}</TableHead>
                          <TableHead>{t("users.action")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {routes.map((route) => (
                          <TableRow key={route.route_id}>
                            <TableCell>
                              <div className="font-medium">{route.display_name}</div>
                              <div className="font-mono text-xs text-muted-foreground">
                                {route.route_id}
                              </div>
                            </TableCell>
                            <TableCell>
                              <div>{route.model_name}</div>
                              {route.supports_vision ? (
                                <Badge variant="outline">{t("models.visionBadge")}</Badge>
                              ) : null}
                            </TableCell>
                            <TableCell>
                              {connections.find(
                                (connection) => connection.connection_id === route.connection_id,
                              )?.display_name ?? t("models.connectionUnavailable")}
                            </TableCell>
                            <TableCell>
                              <StatusBadge
                                semantic={routeStatusSemantic(route.status)}
                                label={localizedStatusLabel(route.status, t)}
                              />
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-col gap-1">
                                <Badge variant="outline">
                                  {t("models.policyRevision", { revision: route.runtime_policy.revision })}
                                </Badge>
                                <span className="text-xs text-muted-foreground">
                                  {t("models.policyReady")}
                                </span>
                              </div>
                            </TableCell>
                            <TableCell>
                              {t("models.toolProviderLimits", {
                                tools: route.runtime_policy.max_tool_executions,
                                providers: route.runtime_policy.max_provider_invocations,
                              })}
                            </TableCell>
                            <TableCell>
                              {route.runtime_policy.max_total_tokens_per_conversation.toLocaleString(i18n.language)}
                            </TableCell>
                            <TableCell>
                              {route.is_default || route.route_id === defaultRouteId ? (
                                <StatusBadge semantic="success" label={t("models.default")} />
                              ) : (
                                <Badge variant="outline">{t("models.notDefault")}</Badge>
                              )}
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-wrap gap-2">
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => openEditModel(route)}
                                >
                                  <Pencil data-icon="inline-start" />
                                  {t("admin.edit")}
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() =>
                                    void runAction(
                                      `test-route-${route.route_id}`,
                                      () =>
                                        modelRoutingApi.testModelRoute(
                                          route.route_id,
                                          route.revision,
                                        ),
                                    )
                                  }
                                  disabled={pendingAction === `test-route-${route.route_id}`}
                                >
                                  <CheckCircle2 data-icon="inline-start" />
                                  {t("admin.testRoute")}
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() =>
                                    void runAction(
                                      `default-route-${route.route_id}`,
                                      () =>
                                        modelRoutingApi.setDefaultModelRoute(
                                          route.route_id,
                                          route.revision,
                                        ),
                                    )
                                  }
                                  disabled={
                                    pendingAction === `default-route-${route.route_id}` ||
                                    route.status !== "test_passed" ||
                                    route.is_default ||
                                    !route.enabled
                                  }
                                >
                                  <Cpu data-icon="inline-start" />
                                  {t("models.setDefault")}
                                </Button>
                              </div>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </CardContent>
              </Card>
            )}
            </>
            )}
          </TabsContent>

          <TabsContent value="answer-behavior" className="flex flex-col gap-4">
            {answerBehaviorLoading ? (
              <LoadingState title={t("models.answerBehaviorLoading")} />
            ) : answerBehaviorError && !answerBehavior ? (
              <LoadErrorState
                title={t("admin.listLoadFailed")}
                description={serverMessage(answerBehaviorError, t)}
                retryLabel={t("admin.retry")}
                onRetry={() => void refreshAnswerBehavior()}
              />
            ) : answerBehavior ? (
              <Card>
                <CardHeader>
                  <CardTitle>{t("models.answerBehaviorTitle")}</CardTitle>
                  <CardDescription>
                    {t("models.answerBehaviorDescription")}
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col gap-5">
                  {answerBehaviorError ? (
                    <Alert variant="destructive">
                      <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
                      <AlertDescription>
                        {serverMessage(answerBehaviorError, t)}
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  <Alert>
                    <MessageSquareText />
                    <AlertTitle>{t("models.answerBehaviorCoreRulesTitle")}</AlertTitle>
                    <AlertDescription>
                      {t("models.answerBehaviorCoreRulesDescription")}
                    </AlertDescription>
                  </Alert>
                  <Field>
                    <FieldLabel htmlFor="answer-behavior-guidance">
                      {t("models.answerBehaviorGuidanceLabel")}
                    </FieldLabel>
                    <Textarea
                      id="answer-behavior-guidance"
                      rows={9}
                      value={answerBehaviorDraft}
                      onChange={(event) =>
                        setAnswerBehaviorDraft(event.target.value)
                      }
                      aria-invalid={answerBehaviorLength > 2000}
                    />
                    {answerBehaviorLength > 2000 ? (
                      <FieldError>
                        {t("models.answerBehaviorTooLong")}
                      </FieldError>
                    ) : null}
                    <FieldDescription>
                      {t("models.answerBehaviorGuidanceHelp")}
                    </FieldDescription>
                    <div className="text-right text-xs text-muted-foreground">
                      {t("models.answerBehaviorCharacterCount", {
                        count: answerBehaviorLength,
                      })}
                    </div>
                  </Field>
                  <div className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-2">
                    <div>
                      {t("models.answerBehaviorRevision", {
                        revision: answerBehavior.revision,
                      })}
                    </div>
                    <div className="break-all">
                      {t("models.answerBehaviorDigest")}:{" "}
                      {answerBehavior.guidance_digest ??
                        t("models.answerBehaviorNotConfigured")}
                    </div>
                    <div>
                      {t("models.answerBehaviorUpdatedBy")}:{" "}
                      {answerBehavior.updated_by ??
                        t("models.answerBehaviorNotConfigured")}
                    </div>
                    <div>
                      {t("models.answerBehaviorUpdatedAt")}:{" "}
                      {readableTime(
                        answerBehavior.updated_at,
                        t("models.answerBehaviorNotConfigured"),
                        i18n.language,
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    <Button
                      variant="outline"
                      onClick={() => void refreshAnswerBehavior()}
                      disabled={pendingAction === "answer-behavior"}
                    >
                      <RefreshCw data-icon="inline-start" />
                      {t("models.refresh")}
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => void updateAnswerBehavior(null)}
                      disabled={
                        pendingAction === "answer-behavior" ||
                        answerBehavior.custom_guidance === null
                      }
                    >
                      <Trash2 data-icon="inline-start" />
                      {t("models.answerBehaviorClear")}
                    </Button>
                    <Button
                      onClick={() =>
                        void updateAnswerBehavior(normalizedAnswerBehaviorDraft)
                      }
                      disabled={
                        pendingAction === "answer-behavior" ||
                        !canSaveAnswerBehavior
                      }
                    >
                      <Save data-icon="inline-start" />
                      {t("models.answerBehaviorSave")}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ) : null}
          </TabsContent>
        </Tabs>
      )}

      <Dialog open={connectionDialogOpen} onOpenChange={(open) => !open && closeConnectionDialog()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editingConnection ? t("models.editConnection") : t("models.addConnection")}
            </DialogTitle>
            <DialogDescription>{t("models.connectionDialogDescription")}</DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="connection-name">{t("models.connectionName")}</FieldLabel>
              <Input id="connection-name" value={connectionName} onChange={(event) => setConnectionName(event.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="provider-type">{t("admin.providerType")}</FieldLabel>
              <Select
                value={providerType}
                disabled={Boolean(editingConnection)}
                onValueChange={(value) => {
                  const nextType = value as ProviderType;
                  setProviderType(nextType);
                  setEndpointUrl(providerDefaults[nextType]);
                }}
              >
                <SelectTrigger id="provider-type" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value="openai_compatible">{t("admin.providerOpenAICompatible")}</SelectItem>
                    <SelectItem value="azure_openai">{t("models.providerAzure")}</SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
              {editingConnection ? <FieldDescription>{t("models.providerTypeLocked")}</FieldDescription> : null}
            </Field>
            <Field>
              <FieldLabel htmlFor="endpoint-url">{t("admin.endpointUrl")}</FieldLabel>
              <Input id="endpoint-url" value={endpointUrl} onChange={(event) => setEndpointUrl(event.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="api-key">{t("models.apiKey")}</FieldLabel>
              <Input id="api-key" type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
              <FieldDescription>
                {editingConnection ? t("models.apiKeyBlankPreserves") : t("models.apiKeyNeverShown")}
              </FieldDescription>
            </Field>
            {editingConnection ? (
              <Field orientation="horizontal">
                <div>
                  <FieldLabel htmlFor="connection-enabled">{t("models.connectionEnabled")}</FieldLabel>
                  <FieldDescription>{t("models.connectionEnabledDescription")}</FieldDescription>
                </div>
                <Switch id="connection-enabled" checked={connectionEnabled} onCheckedChange={setConnectionEnabled} />
              </Field>
            ) : null}
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={closeConnectionDialog}>{t("admin.cancel")}</Button>
            <Button
              disabled={!canSaveConnection || pendingAction === "save-connection"}
              onClick={() => {
                const connectionId = editingConnection?.connection_id ?? generatedId("connection", connectionName);
                void runAction(
                  "save-connection",
                  () => editingConnection
                    ? modelRoutingApi.updateProviderConnection({
                        connectionId,
                        displayName: connectionName.trim(),
                        endpointUrl: endpointUrl.trim(),
                        apiKey: apiKey.trim() || undefined,
                        enabled: connectionEnabled,
                        expectedRevision: editingConnection.revision,
                      })
                    : modelRoutingApi.createProviderConnection({
                        connectionId,
                        displayName: connectionName.trim(),
                        providerType,
                        endpointUrl: endpointUrl.trim(),
                        apiKey: apiKey.trim(),
                      }),
                  closeConnectionDialog,
                );
              }}
            >
              <CloudCog data-icon="inline-start" />
              {t("models.saveConnection")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={modelDialogOpen} onOpenChange={(open) => !open && closeModelDialog()}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingRoute ? t("models.editModel") : t("models.addModel")}</DialogTitle>
            <DialogDescription>{t("models.modelDialogDescription")}</DialogDescription>
          </DialogHeader>
          <FieldGroup>
            {editingRoute ? (
              <Field>
                <FieldLabel htmlFor="route-id">{t("admin.routeId")}</FieldLabel>
                <Input id="route-id" value={editingRoute.route_id} readOnly />
                <FieldDescription>{t("models.routeIdLocked")}</FieldDescription>
              </Field>
            ) : null}
            <Field>
              <FieldLabel htmlFor="route-name">{t("admin.routeName")}</FieldLabel>
              <Input id="route-name" value={routeName} onChange={(event) => setRouteName(event.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="model-connection">{t("models.connection")}</FieldLabel>
              <Select value={modelConnectionId} onValueChange={setModelConnectionId}>
                <SelectTrigger id="model-connection" className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {connections.map((connection) => (
                      <SelectItem key={connection.connection_id} value={connection.connection_id}>
                        {connection.display_name}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="model-name">{t("models.modelOrDeploymentName")}</FieldLabel>
              <Autocomplete
                items={availableModels}
                value={modelName}
                onValueChange={setModelName}
                openOnInputClick
              >
                <AutocompleteInput
                  id="model-name"
                  className="w-full"
                  placeholder={t("models.modelOrDeploymentName")}
                  triggerLabel={t("models.showModelSuggestions")}
                />
                <AutocompleteContent>
                  <AutocompleteEmpty>{t("models.modelSuggestionsEmpty")}</AutocompleteEmpty>
                  <AutocompleteList>
                    {(model) => (
                      <AutocompleteItem key={model} value={model}>
                        {model}
                      </AutocompleteItem>
                    )}
                  </AutocompleteList>
                </AutocompleteContent>
              </Autocomplete>
              <FieldDescription>
                {discoveryStatus === "loading" ? t("models.discoveryLoading") : t("models.modelNameHelp")}
              </FieldDescription>
            </Field>
            {discoveryStatus === "unavailable" ? (
              <Alert>
                <Server />
                <AlertTitle>{t("models.discoveryUnavailable")}</AlertTitle>
                <AlertDescription>{serverMessage(discoveryMessage, t)} {t("models.discoveryUnavailableDescription")}</AlertDescription>
              </Alert>
            ) : null}
            <Field orientation="horizontal">
              <div>
                <FieldLabel htmlFor="model-enabled">{t("models.modelEnabled")}</FieldLabel>
                <FieldDescription>{t("models.modelEnabledDescription")}</FieldDescription>
              </div>
              <Switch id="model-enabled" checked={modelEnabled} onCheckedChange={setModelEnabled} />
            </Field>
            <Field orientation="horizontal">
              <div>
                <FieldLabel htmlFor="model-supports-vision">{t("models.visionEnabled")}</FieldLabel>
                <FieldDescription>{t("models.visionEnabledDescription")}</FieldDescription>
              </div>
              <Switch
                id="model-supports-vision"
                checked={modelSupportsVision}
                onCheckedChange={setModelSupportsVision}
              />
            </Field>
            <FieldSet>
              <FieldLegend>{t("models.execution")}</FieldLegend>
              <FieldDescription>{t("models.executionDescription")}</FieldDescription>
              {runtimePolicyTemplateRouteName ? (
                <FieldDescription>
                  {t("models.runtimePolicyTemplate", {
                    route: runtimePolicyTemplateRouteName,
                  })}
                </FieldDescription>
              ) : null}
              <FieldGroup className="grid gap-4 sm:grid-cols-2">
                <Field>
                  <FieldLabel htmlFor="tokenizer_profile">{t("models.tokenizerProfile")}</FieldLabel>
                  <Input
                    id="tokenizer_profile"
                    required
                    value={runtimePolicy.tokenizer_profile}
                    onChange={(event) => setRuntimePolicy((current) => ({
                      ...current,
                      tokenizer_profile: event.target.value,
                    }))}
                  />
                </Field>
                {editingRoute ? (
                  <Field>
                    <FieldLabel htmlFor="runtime-policy-revision">{t("models.policyRevisionLabel")}</FieldLabel>
                    <Input
                      id="runtime-policy-revision"
                      value={editingRoute.runtime_policy.revision}
                      readOnly
                    />
                    <FieldDescription>{t("models.policyRevisionReadOnly")}</FieldDescription>
                  </Field>
                ) : null}
                <PolicyNumberField
                  id="max_tool_executions"
                  label={t("models.maxToolExecutions")}
                  value={runtimePolicy.max_tool_executions}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_tool_executions: value }))}
                />
                <PolicyNumberField
                  id="max_provider_invocations"
                  label={t("models.maxProviderInvocations")}
                  value={runtimePolicy.max_provider_invocations}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_provider_invocations: value }))}
                />
                <PolicyNumberField
                  id="max_catalog_pages"
                  label={t("models.maxCatalogPages")}
                  value={runtimePolicy.max_catalog_pages}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_catalog_pages: value }))}
                />
                <PolicyNumberField
                  id="max_search_rounds"
                  label={t("models.maxSearchRounds")}
                  value={runtimePolicy.max_search_rounds}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_search_rounds: value }))}
                />
                <PolicyNumberField
                  id="max_unique_evidence"
                  label={t("models.maxUniqueEvidence")}
                  value={runtimePolicy.max_unique_evidence}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_unique_evidence: value }))}
                />
                <PolicyNumberField
                  id="max_retrieval_repairs"
                  label={t("models.maxRetrievalRepairs")}
                  value={runtimePolicy.max_retrieval_repairs}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_retrieval_repairs: value }))}
                />
                <PolicyNumberField
                  id="max_schema_retries_per_turn"
                  label={t("models.maxSchemaRetriesPerTurn")}
                  value={runtimePolicy.max_schema_retries_per_turn}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_schema_retries_per_turn: value }))}
                />
                <PolicyNumberField
                  id="max_selected_anchor_pages_per_round"
                  label={t("models.maxSelectedAnchorPagesPerRound")}
                  value={runtimePolicy.max_selected_anchor_pages_per_round}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_selected_anchor_pages_per_round: value }))}
                />
              </FieldGroup>
            </FieldSet>
            <FieldSet>
              <FieldLegend>{t("models.timeouts")}</FieldLegend>
              <FieldGroup className="grid gap-4 sm:grid-cols-3">
                <PolicyNumberField
                  id="provider_invocation_timeout_seconds"
                  label={t("models.providerTimeout")}
                  value={runtimePolicy.provider_invocation_timeout_seconds}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, provider_invocation_timeout_seconds: value }))}
                />
                <PolicyNumberField
                  id="tool_execution_timeout_seconds"
                  label={t("models.toolTimeout")}
                  value={runtimePolicy.tool_execution_timeout_seconds}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, tool_execution_timeout_seconds: value }))}
                />
                <PolicyNumberField
                  id="turn_timeout_seconds"
                  label={t("models.turnTimeout")}
                  value={runtimePolicy.turn_timeout_seconds}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, turn_timeout_seconds: value }))}
                />
              </FieldGroup>
            </FieldSet>
            <FieldSet data-invalid={runtimePolicyInvalid || undefined}>
              <FieldLegend>{t("models.tokenBudgets")}</FieldLegend>
              <FieldGroup className="grid gap-4 sm:grid-cols-2">
                <PolicyNumberField
                  id="context_window_tokens"
                  label={t("models.contextWindowTokens")}
                  value={runtimePolicy.context_window_tokens}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, context_window_tokens: value }))}
                />
                <PolicyNumberField
                  id="max_input_tokens_per_invocation"
                  label={t("models.maxInputTokens")}
                  value={runtimePolicy.max_input_tokens_per_invocation}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_input_tokens_per_invocation: value }))}
                />
                <PolicyNumberField
                  id="max_output_tokens_per_invocation"
                  label={t("models.maxOutputTokens")}
                  value={runtimePolicy.max_output_tokens_per_invocation}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_output_tokens_per_invocation: value }))}
                />
                <PolicyNumberField
                  id="max_tool_result_tokens_per_execution"
                  label={t("models.maxToolResultTokens")}
                  value={runtimePolicy.max_tool_result_tokens_per_execution}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_tool_result_tokens_per_execution: value }))}
                />
                <PolicyNumberField
                  id="max_total_tokens_per_conversation"
                  label={t("models.maxConversationTokens")}
                  value={runtimePolicy.max_total_tokens_per_conversation}
                  onChange={(value) => setRuntimePolicy((current) => ({ ...current, max_total_tokens_per_conversation: value }))}
                />
              </FieldGroup>
              {runtimePolicyInvalid ? <FieldError>{t("models.runtimePolicyInvalid")}</FieldError> : null}
            </FieldSet>
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={closeModelDialog}>{t("admin.cancel")}</Button>
            <Button
              disabled={!canSaveModel || pendingAction === "save-model"}
              onClick={() => {
                if (!parsedRuntimePolicy) return;
                const routeId = editingRoute?.route_id ?? generatedId("route", routeName || modelName);
                void runAction(
                  "save-model",
                  () => editingRoute
                    ? modelRoutingApi.updateModelRoute({
                        routeId,
                        displayName: routeName.trim(),
                        modelName: modelName.trim(),
                        connectionId: modelConnectionId,
                        enabled: modelEnabled,
                        supportsVision: modelSupportsVision,
                        runtimePolicy: parsedRuntimePolicy,
                        expectedRevision: editingRoute.revision,
                      })
                    : modelRoutingApi.configureModelRoute({
                        routeId,
                        displayName: routeName.trim(),
                        modelName: modelName.trim(),
                        connectionId: modelConnectionId,
                        enabled: modelEnabled,
                        supportsVision: modelSupportsVision,
                        runtimePolicy: parsedRuntimePolicy,
                      }),
                  closeModelDialog,
                );
              }}
            >
              <Cpu data-icon="inline-start" />
              {t("models.saveModel")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function connectionStatusSemantic(status: ProviderConnectionStatus["status"]) {
  if (status === "verified") return "success" as const;
  if (status === "configured" || status === "credential_required") return "attention" as const;
  if (status === "verification_failed") return "failure" as const;
  return "inactive" as const;
}

function routeStatusSemantic(status: ModelRouteStatus["status"]) {
  if (status === "test_passed") return "success" as const;
  if (status === "configured") return "attention" as const;
  if (status === "test_failed") return "failure" as const;
  return "inactive" as const;
}
