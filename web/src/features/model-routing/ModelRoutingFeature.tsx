import { CloudCog, Cpu, MessageSquareText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { ApiError, type MessageReference } from "../../shared/user-messages";
import {
  LoadErrorState,
  LoadingState,
  PageHeader,
  serverMessage,
} from "../../shared/product-ui";
import {
  retainClientRequestId,
  type ClientOperationKey,
} from "../../shared/ids";
import { modelRoutingApi } from "./api";
import { AnswerBehaviorTab } from "./AnswerBehaviorTab";
import { ConnectionDialog } from "./ConnectionDialog";
import { ConnectionsTab } from "./ConnectionsTab";
import { ModelDialog } from "./ModelDialog";
import { ModelsTab } from "./ModelsTab";
import {
  createRuntimePolicyDraft,
  currentTestedTextDefaultRoute,
  parseRuntimePolicy,
  runtimePolicyDraft,
  type RuntimePolicyDraft,
} from "./runtimePolicy";
import {
  providerConnectionFieldsValid,
  providerEndpointDefaults,
} from "./provider-connection-fields";
import type {
  AnswerBehaviorStatus,
  ModelRouteStatus,
  ModelRoutingFeatureProps,
  ProviderConnectionStatus,
  ProviderType,
} from "./types";

type SafeAction = MessageReference;
type ModelManagementTab = "connections" | "models" | "answer-behavior";


export function ModelRoutingFeature({
  onNotice,
  onRefresh,
}: ModelRoutingFeatureProps) {
  const { t, i18n } = useTranslation();
  const [connections, setConnections] = useState<ProviderConnectionStatus[]>([]);
  const [routes, setRoutes] = useState<ModelRouteStatus[]>([]);
  const [textDefaultRouteId, setTextDefaultRouteId] = useState<string | null>(null);
  const [visionDefaultRouteId, setVisionDefaultRouteId] = useState<string | null>(null);
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
  const [endpointUrl, setEndpointUrl] = useState(
    providerEndpointDefaults.openai_compatible,
  );
  const [apiVersion, setApiVersion] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [connectionEnabled, setConnectionEnabled] = useState(true);
  const createConnectionOperation = useRef<ClientOperationKey | null>(null);

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
  const createRouteOperation = useRef<ClientOperationKey | null>(null);

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
      setTextDefaultRouteId(result.text_default_route_id);
      setVisionDefaultRouteId(result.vision_default_route_id);
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
    setEndpointUrl(providerEndpointDefaults.openai_compatible);
    setApiVersion("");
    setApiKey("");
    setConnectionEnabled(true);
    setConnectionDialogOpen(true);
  }

  function openEditConnection(connection: ProviderConnectionStatus) {
    setEditingConnection(connection);
    setConnectionName(connection.display_name);
    setProviderType(connection.provider_type);
    setEndpointUrl(connection.endpoint_url);
    setApiVersion(connection.api_version ?? "");
    setApiKey("");
    setConnectionEnabled(connection.enabled);
    setConnectionDialogOpen(true);
  }

  function closeConnectionDialog() {
    createConnectionOperation.current = null;
    setApiKey("");
    setApiVersion("");
    setConnectionDialogOpen(false);
    setEditingConnection(null);
  }

  function openCreateModel(connectionId?: string) {
    const defaultConnectionId =
      connections.find((connection) => connection.credential_configured)?.connection_id ??
      connections[0]?.connection_id ??
      "";
    const templateRoute = currentTestedTextDefaultRoute(routes, textDefaultRouteId);
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
    createRouteOperation.current = null;
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

  function testConnection(connection: ProviderConnectionStatus) {
    return runAction(
      `test-connection-${connection.connection_id}`,
      () => modelRoutingApi.testProviderConnection(
        connection.connection_id,
        connection.revision,
      ),
    );
  }

  function testRoute(route: ModelRouteStatus) {
    return runAction(
      `test-route-${route.route_id}`,
      () => modelRoutingApi.testModelRoute(route.route_id, route.revision),
    );
  }

  function setDefaultRoute(
    route: ModelRouteStatus,
    capability: "text" | "vision",
  ) {
    return runAction(
      `default-${capability}-route-${route.route_id}`,
      () => modelRoutingApi.setDefaultModelRoute(
        route.route_id,
        capability,
        route.revision,
      ),
    );
  }

  function submitConnection() {
    const displayName = connectionName.trim();
    const normalizedEndpointUrl = endpointUrl.trim();
    const normalizedApiVersion = apiVersion.trim();
    void runAction(
      "save-connection",
      () => {
        if (editingConnection) {
          return modelRoutingApi.updateProviderConnection({
            connectionId: editingConnection.connection_id,
            displayName: displayName !== editingConnection.display_name
              ? displayName
              : undefined,
            endpointUrl: normalizedEndpointUrl !== editingConnection.endpoint_url
              ? normalizedEndpointUrl
              : undefined,
            apiVersion: providerType === "azure_openai" &&
              normalizedApiVersion !== (editingConnection.api_version ?? "")
              ? normalizedApiVersion
              : undefined,
            apiKey: apiKey.trim() || undefined,
            enabled: connectionEnabled !== editingConnection.enabled
              ? connectionEnabled
              : undefined,
            expectedRevision: editingConnection.revision,
          });
        }
        const input = {
          displayName,
          providerType,
          endpointUrl: normalizedEndpointUrl,
          apiVersion: providerType === "azure_openai"
            ? normalizedApiVersion
            : undefined,
          apiKey: apiKey.trim(),
        };
        const operation = retainClientRequestId(
          createConnectionOperation.current,
          "provider-connection-create",
          JSON.stringify(input),
        );
        createConnectionOperation.current = operation;
        return modelRoutingApi.createProviderConnection(
          input,
          operation.idempotencyKey,
        );
      },
      () => {
        createConnectionOperation.current = null;
        closeConnectionDialog();
      },
    );
  }

  function submitModel() {
    const parsedPolicy = parseRuntimePolicy(runtimePolicy);
    if (!parsedPolicy) return;
    void runAction(
      "save-model",
      () => {
        if (editingRoute) {
          return modelRoutingApi.updateModelRoute({
            routeId: editingRoute.route_id,
            displayName: routeName.trim(),
            modelName: modelName.trim(),
            connectionId: modelConnectionId,
            enabled: modelEnabled,
            supportsVision: modelSupportsVision,
            runtimePolicy: parsedPolicy,
            expectedRevision: editingRoute.revision,
          });
        }
        const input = {
          displayName: routeName.trim(),
          modelName: modelName.trim(),
          connectionId: modelConnectionId,
          enabled: modelEnabled,
          supportsVision: modelSupportsVision,
          runtimePolicy: parsedPolicy,
        };
        const operation = retainClientRequestId(
          createRouteOperation.current,
          "model-route-create",
          JSON.stringify(input),
        );
        createRouteOperation.current = operation;
        return modelRoutingApi.configureModelRoute(
          input,
          operation.idempotencyKey,
        );
      },
      () => {
        createRouteOperation.current = null;
        closeModelDialog();
      },
    );
  }

  const canSaveConnection = providerConnectionFieldsValid(
    {
      displayName: connectionName,
      providerType,
      endpointUrl,
      apiVersion,
      apiKey,
    },
    Boolean(editingConnection),
  );
  const parsedRuntimePolicy = parseRuntimePolicy(runtimePolicy);
  const runtimePolicyComplete = Object.values(runtimePolicy).every((value) => value.trim());
  const runtimePolicyInvalid = Boolean(runtimePolicyComplete && !parsedRuntimePolicy);
  const canSaveModel = Boolean(
    routeName.trim() && modelName.trim() && modelConnectionId && parsedRuntimePolicy,
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
            <TabsTrigger value="connections"><CloudCog />{t("models.connectionsTitle")}</TabsTrigger>
            <TabsTrigger value="models"><Cpu />{t("models.directoryTitle")}</TabsTrigger>
            <TabsTrigger value="answer-behavior"><MessageSquareText />{t("models.answerBehaviorTitle")}</TabsTrigger>
          </TabsList>
          <TabsContent value="connections">
            <ConnectionsTab
              connections={connections}
              refreshError={connectionRefreshError}
              pendingAction={pendingAction}
              locale={i18n.language}
              onRefresh={() => refreshConnections(false)}
              onCreate={openCreateConnection}
              onEdit={openEditConnection}
              onTest={testConnection}
            />
          </TabsContent>
          <TabsContent value="models">
            <ModelsTab
              routes={routes}
              connections={connections}
              textDefaultRouteId={textDefaultRouteId}
              visionDefaultRouteId={visionDefaultRouteId}
              loading={routesLoading}
              loadError={routesLoadError}
              pendingAction={pendingAction}
              locale={i18n.language}
              onRefresh={() => Promise.all([
                refreshConnections(false),
                refreshRoutes(),
              ]).then(() => undefined)}
              onRefreshRoutes={refreshRoutes}
              onCreate={() => openCreateModel()}
              onEdit={openEditModel}
              onTest={testRoute}
              onSetDefault={setDefaultRoute}
            />
          </TabsContent>
          <TabsContent value="answer-behavior">
            <AnswerBehaviorTab
              answerBehavior={answerBehavior}
              draft={answerBehaviorDraft}
              loading={answerBehaviorLoading}
              error={answerBehaviorError}
              pendingAction={pendingAction}
              locale={i18n.language}
              onDraftChange={setAnswerBehaviorDraft}
              onRefresh={refreshAnswerBehavior}
              onUpdate={updateAnswerBehavior}
            />
          </TabsContent>
        </Tabs>
      )}

      <ConnectionDialog
        connectionDialogOpen={connectionDialogOpen}
        editingConnection={editingConnection}
        connectionName={connectionName}
        providerType={providerType}
        endpointUrl={endpointUrl}
        apiVersion={apiVersion}
        apiKey={apiKey}
        connectionEnabled={connectionEnabled}
        canSaveConnection={canSaveConnection}
        pendingAction={pendingAction}
        onClose={closeConnectionDialog}
        onConnectionNameChange={setConnectionName}
        onProviderTypeChange={setProviderType}
        onEndpointUrlChange={setEndpointUrl}
        onApiVersionChange={setApiVersion}
        onApiKeyChange={setApiKey}
        onConnectionEnabledChange={setConnectionEnabled}
        onSubmit={submitConnection}
      />
      <ModelDialog
        modelDialogOpen={modelDialogOpen}
        editingRoute={editingRoute}
        routeName={routeName}
        modelName={modelName}
        modelConnectionId={modelConnectionId}
        modelEnabled={modelEnabled}
        modelSupportsVision={modelSupportsVision}
        runtimePolicy={runtimePolicy}
        runtimePolicyTemplateRouteName={runtimePolicyTemplateRouteName}
        availableModels={availableModels}
        discoveryStatus={discoveryStatus}
        discoveryMessage={discoveryMessage}
        connections={connections}
        runtimePolicyInvalid={runtimePolicyInvalid}
        canSaveModel={canSaveModel}
        pendingAction={pendingAction}
        onClose={closeModelDialog}
        onRouteNameChange={setRouteName}
        onModelNameChange={setModelName}
        onModelConnectionIdChange={setModelConnectionId}
        onModelEnabledChange={setModelEnabled}
        onModelSupportsVisionChange={setModelSupportsVision}
        onRuntimePolicyChange={setRuntimePolicy}
        onSubmit={submitModel}
      />
    </section>
  );
}
