"use client";

import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  FileUp,
  LogIn,
  RefreshCw,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
} from "../../components/ui/card";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Progress } from "../../components/ui/progress";
import { Spinner } from "../../components/ui/spinner";
import { OptionSelect } from "../../shared/OptionSelect";
import { DOCUMENT_UPLOAD_ACCEPT } from "../../shared/document-upload";
import {
  retainClientRequestId,
  type ClientOperationKey,
} from "../../shared/ids";
import { ApiError } from "../../shared/user-messages";
import { LanguageSwitch } from "../../shared/product-ui";
import {
  documentLibraryApi,
  type DocumentLibrarySummary,
} from "../document-library";
import {
  passwordConfirmationState,
  type SessionState,
} from "../identity-session";
import {
  createRuntimePolicyDraft,
  currentTestedTextDefaultRoute,
  modelRoutingApi,
  parseRuntimePolicy,
  ProviderConnectionFields,
  providerConnectionFieldsValid,
  providerEndpointDefaults,
  type ModelRouteStatus,
  type ProviderConnectionStatus,
  type ProviderType,
} from "../model-routing";
import { opsApi } from "../ops";
import {
  projectGovernanceApi,
  type ProjectAdminSummary,
} from "../project-governance";
import { firstRunSetupApi } from "./api";
import type { ReviewRow, SetupStep } from "./types";

const steps: readonly SetupStep[] = [
  "admin",
  "provider",
  "project",
  "document",
  "review",
];

function isSetupStep(value: string | null): value is SetupStep {
  return steps.includes(value as SetupStep);
}

function projectIdFromTargetRef(targetRef: string | null): string {
  const prefix = "project:";
  if (!targetRef?.startsWith(prefix) || targetRef.length === prefix.length) {
    throw new Error("project.missing_id");
  }
  return targetRef.slice(prefix.length);
}


export function FirstRunSetupFeature({
  session,
  beginSession,
  refreshSession,
}: {
  session: SessionState;
  beginSession: (session: SessionState) => void;
  refreshSession: () => Promise<SessionState>;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const createConnectionOperation = useRef<ClientOperationKey | null>(null);
  const createRouteOperation = useRef<ClientOperationKey | null>(null);
  const createProjectOperation = useRef<ClientOperationKey | null>(null);
  const uploadDocumentOperation = useRef<ClientOperationKey | null>(null);
  const activeMutation = useRef<AbortController | null>(null);
  const displayNameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const passwordConfirmationRef = useRef<HTMLInputElement>(null);
  const requestedStep = searchParams.get("step");
  const step: SetupStep = session.authenticated
    ? requestedStep === "admin" || !isSetupStep(requestedStep)
      ? "provider"
      : requestedStep
    : "admin";
  const needsCanonicalNormalization =
    session.authenticated &&
    session.system_role === "admin" &&
    (requestedStep === "admin" || !isSetupStep(requestedStep));
  const stepIndex = steps.indexOf(step);

  const [status, setStatus] = useState<"loading" | "available" | "unavailable">(
    session.authenticated ? "available" : "loading",
  );
  const [statusAttempt, setStatusAttempt] = useState(0);
  const [normalizingEntry, setNormalizingEntry] = useState(
    needsCanonicalNormalization,
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const passwordState = passwordConfirmationState(password, confirmPassword);

  const [providerType, setProviderType] = useState<ProviderType>("openai_compatible");
  const [connectionName, setConnectionName] = useState("");
  const [endpointUrl, setEndpointUrl] = useState(providerEndpointDefaults.openai_compatible);
  const [apiVersion, setApiVersion] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [providerConnection, setProviderConnection] = useState<ProviderConnectionStatus | null>(
    null,
  );
  const [existingConnection, setExistingConnection] =
    useState<ProviderConnectionStatus | null>(null);
  const requestedProjectId = searchParams.get("project_id");
  const initialProjectId =
    session.available_projects.find(
      (project) =>
        project.membership_status === "active" &&
        project.role === "admin" &&
        (!requestedProjectId || project.project_id === requestedProjectId),
    )?.project_id ??
    session.available_projects.find(
      (project) =>
        project.membership_status === "active" && project.role === "admin",
    )?.project_id ??
    "";
  const [models, setModels] = useState<string[]>([]);
  const [modelName, setModelName] = useState("");
  const [discoveryUnavailable, setDiscoveryUnavailable] = useState(false);
  const [projects, setProjects] = useState<ProjectAdminSummary[]>([]);
  const [projectName, setProjectName] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState(initialProjectId);
  const [projectNeedsRefresh, setProjectNeedsRefresh] = useState(false);
  const [projectLoadState, setProjectLoadState] = useState<
    "idle" | "loading" | "ready" | "unavailable"
  >("idle");

  const [file, setFile] = useState<File | null>(null);
  const [acceptedDocument, setAcceptedDocument] = useState<DocumentLibrarySummary | null>(null);
  const [reviewRows, setReviewRows] = useState<ReviewRow[]>([]);
  const [reviewLoading, setReviewLoading] = useState(false);

  const connectionId = searchParams.get("connection_id");
  const routeId = searchParams.get("route_id");

  const navigate = useCallback(
    (
      nextStep: SetupStep,
      changes: Record<string, string | null> = {},
      mode: "push" | "replace" = "push",
    ) => {
      const next = new URLSearchParams(searchParams.toString());
      next.set("step", nextStep);
      Object.entries(changes).forEach(([key, value]) => {
        if (value) next.set(key, value);
        else next.delete(key);
      });
      router[mode](`/setup?${next.toString()}`);
    },
    [router, searchParams],
  );

  function beginMutation(): AbortController {
    activeMutation.current?.abort();
    const controller = new AbortController();
    activeMutation.current = controller;
    return controller;
  }

  function finishMutation(controller: AbortController) {
    if (activeMutation.current !== controller) return false;
    activeMutation.current = null;
    return true;
  }

  function cancelMutation() {
    activeMutation.current?.abort();
    activeMutation.current = null;
    setPending(false);
  }

  function resetStepAttempt(cancelledStep: SetupStep) {
    cancelMutation();
    if (cancelledStep === "provider") {
      createConnectionOperation.current = null;
      createRouteOperation.current = null;
      setApiKey("");
    } else if (cancelledStep === "project") {
      createProjectOperation.current = null;
      setProjectName("");
    } else if (cancelledStep === "document") {
      uploadDocumentOperation.current = null;
      setFile(null);
    }
  }

  function skipCurrentStep() {
    resetStepAttempt(step);
    navigate(steps[stepIndex + 1]!);
  }

  function returnToPreviousStep() {
    resetStepAttempt(step);
    router.back();
  }

  useEffect(
    () => () => {
      activeMutation.current?.abort();
      activeMutation.current = null;
      createConnectionOperation.current = null;
      createRouteOperation.current = null;
      createProjectOperation.current = null;
      uploadDocumentOperation.current = null;
    },
    [pathname, step],
  );

  useEffect(() => {
    if (
      (!session.authenticated || status === "available") &&
      !normalizingEntry
    ) {
      headingRef.current?.focus();
      setError("");
    }
  }, [normalizingEntry, status, step, session.authenticated]);

  useEffect(() => {
    if (!needsCanonicalNormalization || pathname !== "/setup") {
      setNormalizingEntry(false);
      return;
    }
    let current = true;
    setNormalizingEntry(true);
    void (async () => {
      let nextStep: SetupStep = "provider";
      let projectId: string | null = null;
      try {
        const [connections, routes] = await Promise.all([
          modelRoutingApi.listProviderConnections(),
          modelRoutingApi.listModelRoutes(),
        ]);
        const testedDefault = currentTestedTextDefaultRoute(
          routes.routes,
          routes.text_default_route_id,
        );
        const connectionReady =
          testedDefault &&
          connections.connections.some(
            (connection) =>
              connection.connection_id === testedDefault.connection_id &&
              connection.enabled &&
              connection.status === "verified",
          );
        if (!testedDefault || !connectionReady) throw new Error("provider_incomplete");
        nextStep = "project";

        const projectResult = await projectGovernanceApi.listProjects();
        const activeProjects = projectResult.projects.filter(
          (project) => project.status === "active",
        );
        if (activeProjects.length === 0) throw new Error("project_incomplete");
        projectId =
          activeProjects.find(
            (project) => project.project_id === requestedProjectId,
          )?.project_id ?? activeProjects[0].project_id;
        nextStep = "document";

        const documents = await documentLibraryApi.listDocumentLibrary({
          tag_type: "project",
          tag_id: projectId,
        });
        if (
          !documents.documents.some(
            (document) => document.lifecycle_status === "active",
          )
        ) {
          throw new Error("document_incomplete");
        }
        nextStep = "review";
      } catch {
        // The first owner that is unavailable or incomplete remains actionable.
      }
      if (!current) return;
      const next = new URLSearchParams({ step: nextStep });
      if (projectId) setSelectedProjectId(projectId);
      if (projectId && (nextStep === "document" || nextStep === "review")) {
        next.set("project_id", projectId);
      }
      router.replace(`/setup?${next.toString()}`);
    })();
    return () => {
      current = false;
    };
  }, [
    needsCanonicalNormalization,
    pathname,
    requestedProjectId,
    router,
  ]);


  useEffect(() => {
    if (session.authenticated) {
      if (session.system_role !== "admin") router.replace("/workspace");
      return;
    }
    const controller = new AbortController();
    setStatus("loading");
    void firstRunSetupApi
      .firstAdminStatus(controller.signal)
      .then((result) => {
        if (result.claim_available) setStatus("available");
        else router.replace("/login");
      })
      .catch((caught) => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setStatus("unavailable");
        }
      });
    return () => controller.abort();
  }, [router, session.authenticated, session.system_role, statusAttempt]);

  useEffect(() => {
    if (!session.authenticated || step !== "provider" || !connectionId) return;
    void modelRoutingApi
      .listProviderConnections()
      .then((result) => {
        const existing = result.connections.find(
          (connection) => connection.connection_id === connectionId,
        );
        if (!existing) return;
        setExistingConnection(existing);
        if (existing.status === "verified" && existing.enabled) {
          setProviderConnection(existing);
        }
        setError("");
        setConnectionName(existing.display_name);
        setProviderType(existing.provider_type);
        setEndpointUrl(existing.endpoint_url);
        setApiVersion(existing.api_version ?? "");
      })
      .catch(() => undefined);
  }, [connectionId, session.authenticated, step]);

  const loadProjects = useCallback(() => {
    setProjectLoadState("loading");
    void projectGovernanceApi
      .listProjects()
      .then((result) => {
        const active = result.projects.filter((project) => project.status === "active");
        setProjects(active);
        setProjectLoadState("ready");
        const requested = active.find(
          (project) => project.project_id === requestedProjectId,
        );
        if (requested) setSelectedProjectId(requested.project_id);
        else if (!selectedProjectId && active.length === 1) {
          setSelectedProjectId(active[0].project_id);
        }
      })
      .catch(() => setProjectLoadState("unavailable"));
  }, [requestedProjectId, selectedProjectId]);

  useEffect(() => {
    if (step === "project" && projectLoadState === "idle") loadProjects();
  }, [loadProjects, projectLoadState, step]);

  const loadReview = useCallback(async () => {
    setReviewLoading(true);
    const reviewProjectId = selectedProjectId || null;
    const requests = await Promise.allSettled([
      Promise.all([
        modelRoutingApi.listProviderConnections(),
        modelRoutingApi.listModelRoutes(),
      ]),
      projectGovernanceApi.listProjects(),
      reviewProjectId
        ? documentLibraryApi.listDocumentLibrary({
            tag_type: "project",
            tag_id: reviewProjectId,
          })
        : Promise.resolve(null),
      opsApi.readiness(),
    ]);

    const nextRows: ReviewRow[] = [
      {
        key: "admin",
        state: session.authenticated && session.system_role === "admin" ? "complete" : "incomplete",
        detail: session.actor?.display_name ?? t("firstRun.review.adminMissing"),
      },
    ];

    const providers = requests[0];
    if (providers.status === "rejected") {
      nextRows.push({ key: "provider", state: "unavailable", detail: t("firstRun.review.unavailable") });
    } else {
      const [connectionResult, routeResult] = providers.value;
      const testedDefault = currentTestedTextDefaultRoute(
        routeResult.routes,
        routeResult.text_default_route_id,
      );
      const testedConnection = testedDefault
        ? connectionResult.connections.find(
            (connection) =>
              connection.connection_id === testedDefault.connection_id &&
              connection.enabled &&
              connection.status === "verified",
          )
        : null;
      nextRows.push({
        key: "provider",
        state: testedDefault && testedConnection ? "complete" : "incomplete",
        detail: testedDefault?.display_name ?? t("firstRun.review.providerMissing"),
      });
    }

    const projectResult = requests[1];
    if (projectResult.status === "rejected") {
      nextRows.push({ key: "project", state: "unavailable", detail: t("firstRun.review.unavailable") });
    } else {
      const activeProjects = projectResult.value.projects.filter(
        (project) => project.status === "active",
      );
      nextRows.push({
        key: "project",
        state: activeProjects.length > 0 ? "complete" : "incomplete",
        detail: activeProjects[0]?.name ?? t("firstRun.review.projectMissing"),
      });
    }

    const documentResult = requests[2];
    if (documentResult.status === "rejected") {
      nextRows.push({ key: "document", state: "unavailable", detail: t("firstRun.review.unavailable") });
    } else if (!documentResult.value) {
      nextRows.push({ key: "document", state: "incomplete", detail: t("firstRun.review.documentMissing") });
    } else {
      const activeDocument = documentResult.value.documents.find(
        (document) => document.lifecycle_status === "active",
      );
      const activeDocumentState = activeDocument
        ? activeDocument.failure_code || activeDocument.current_stage === "failed"
          ? "failed"
          : activeDocument.intake_status === "searchable" ||
              activeDocument.current_stage === "searchable" ||
              activeDocument.current_stage === "indexed"
            ? "searchable"
            : "processing"
        : null;
      nextRows.push({
        key: "document",
        state: activeDocument ? "complete" : "incomplete",
        detail: activeDocument
          ? `${activeDocument.title} · ${t(`firstRun.documentState.${activeDocumentState}`)}`
          : t("firstRun.review.documentMissing"),
      });
    }

    const readinessResult = requests[3];
    if (readinessResult.status === "rejected") {
      nextRows.push({ key: "readiness", state: "unavailable", detail: t("firstRun.review.unavailable") });
    } else {
      nextRows.push({
        key: "readiness",
        state: readinessResult.value.ready ? "complete" : "incomplete",
        detail: readinessResult.value.ready
          ? t("firstRun.review.ready")
          : t("firstRun.review.blockers", {
              count: readinessResult.value.setup_blockers.length,
            }),
      });
    }
    setReviewRows(nextRows);
    setReviewLoading(false);
  }, [selectedProjectId, session.actor?.display_name, session.authenticated, session.system_role, t]);

  useEffect(() => {
    if (step === "review") void loadReview();
  }, [loadReview, step]);

  async function claimAdmin(event: FormEvent) {
    event.preventDefault();
    if (!displayName.trim()) {
      displayNameRef.current?.focus();
      return;
    }
    if (!email.trim()) {
      emailRef.current?.focus();
      return;
    }
    if (password.length < 12) {
      passwordRef.current?.focus();
      return;
    }
    if (!passwordState.valid) {
      passwordConfirmationRef.current?.focus();
      return;
    }
    const controller = beginMutation();
    setPending(true);
    setError("");
    try {
      const nextSession = await firstRunSetupApi.claimFirstAdmin(
        {
          displayName: displayName.trim(),
          email: email.trim(),
          password,
        },
        controller.signal,
      );
      controller.signal.throwIfAborted();
      beginSession(nextSession);
      router.replace("/setup?step=provider");
    } catch (caught) {
      if (controller.signal.aborted) return;
      if (caught instanceof ApiError && caught.status === 409) {
        router.replace("/login?setup=claimed");
        return;
      }
      setError(t("firstRun.admin.claimFailed"));
    } finally {
      if (finishMutation(controller)) {
        setPending(false);
        setPassword("");
        setConfirmPassword("");
      }
    }
  }

  async function testConnectionAndDiscover(event: FormEvent) {
    event.preventDefault();
    const fields = { displayName: connectionName, providerType, endpointUrl, apiVersion, apiKey };
    if (!providerConnectionFieldsValid(fields, Boolean(existingConnection))) return;
    const controller = beginMutation();
    setPending(true);
    setError("");
    try {
      let current = providerConnection ?? existingConnection;
      if (connectionId) {
        const listed = await modelRoutingApi.listProviderConnections(controller.signal);
        current =
          listed.connections.find(
            (item) => item.connection_id === connectionId,
          ) ?? null;
        if (current) setExistingConnection(current);
      }
      if (!current) {
        const input = {
          displayName: connectionName.trim(),
          providerType,
          endpointUrl: endpointUrl.trim(),
          apiVersion: apiVersion.trim() || undefined,
          apiKey: apiKey.trim(),
        };
        const operation = retainClientRequestId(
          createConnectionOperation.current,
          "provider-connection-create",
          JSON.stringify(input),
        );
        createConnectionOperation.current = operation;
        current = await modelRoutingApi.createProviderConnection(
          input,
          operation.idempotencyKey,
          controller.signal,
        );
        createConnectionOperation.current = null;
        navigate(
          "provider",
          { connection_id: current.connection_id },
          "replace",
        );
      } else {
        if (current.provider_type !== providerType) {
          throw new Error("provider.type_cannot_change");
        }
        const nextApiVersion = apiVersion.trim() || null;
        if (
          current.display_name !== connectionName.trim() ||
          current.endpoint_url !== endpointUrl.trim() ||
          current.api_version !== nextApiVersion ||
          Boolean(apiKey.trim())
        ) {
          current = await modelRoutingApi.updateProviderConnection(
            {
              connectionId: current.connection_id,
              displayName: connectionName.trim(),
              endpointUrl: endpointUrl.trim(),
              apiVersion: apiVersion.trim(),
              apiKey: apiKey.trim() || undefined,
              expectedRevision: current.revision,
            },
            controller.signal,
          );
          setExistingConnection(current);
        }
      }
      const tested = await modelRoutingApi.testProviderConnection(
        current.connection_id,
        current.revision,
        controller.signal,
      );
      if (tested.validation_status !== "passed") throw new Error("provider.test_failed");
      setProviderConnection(tested.connection);
      try {
        const available = await modelRoutingApi.listAvailableModels(
          current.connection_id,
          controller.signal,
        );
        if (available.discovery_status === "available" && available.models.length > 0) {
          setModels(available.models);
          setModelName((value) => value || available.models[0]);
          setDiscoveryUnavailable(false);
        } else {
          setDiscoveryUnavailable(true);
        }
      } catch {
        if (controller.signal.aborted) return;
        setDiscoveryUnavailable(true);
      }
      setApiKey("");
    } catch {
      if (controller.signal.aborted) return;
      setError(t("firstRun.provider.connectionFailed"));
    } finally {
      if (finishMutation(controller)) setPending(false);
    }
  }

  async function saveTextModel(event: FormEvent) {
    event.preventDefault();
    if (!providerConnection || !modelName.trim()) return;
    const controller = beginMutation();
    setPending(true);
    setError("");
    try {
      const runtimePolicy = parseRuntimePolicy(createRuntimePolicyDraft);
      if (!runtimePolicy) throw new Error("provider.invalid_runtime_policy");
      let route: ModelRouteStatus | undefined;
      if (routeId) {
        const existing = await modelRoutingApi.listModelRoutes(controller.signal);
        route = existing.routes.find(
          (item) => item.route_id === routeId,
        );
      }
      if (!route) {
        const input = {
          displayName: modelName.trim(),
          modelName: modelName.trim(),
          connectionId: providerConnection.connection_id,
          enabled: true,
          supportsVision: false,
          runtimePolicy,
        };
        const operation = retainClientRequestId(
          createRouteOperation.current,
          "model-route-create",
          JSON.stringify(input),
        );
        createRouteOperation.current = operation;
        route = await modelRoutingApi.configureModelRoute(
          input,
          operation.idempotencyKey,
          controller.signal,
        );
        createRouteOperation.current = null;
        navigate("provider", { route_id: route.route_id }, "replace");
      } else {
        const { revision: _revision, ...currentRuntimePolicy } =
          route.runtime_policy;
        if (
          route.display_name !== modelName.trim() ||
          route.model_name !== modelName.trim() ||
          route.connection_id !== providerConnection.connection_id ||
          !route.enabled ||
          route.supports_vision ||
          JSON.stringify(currentRuntimePolicy) !==
            JSON.stringify(runtimePolicy)
        ) {
          route = await modelRoutingApi.updateModelRoute(
            {
              routeId: route.route_id,
              displayName: modelName.trim(),
              modelName: modelName.trim(),
              connectionId: providerConnection.connection_id,
              enabled: true,
              supportsVision: false,
              runtimePolicy,
              expectedRevision: route.revision,
            },
            controller.signal,
          );
        }
      }
      const tested = route.status === "test_passed"
        ? route
        : await modelRoutingApi.testModelRoute(
            route.route_id,
            route.revision,
            controller.signal,
          );
      if (tested.status !== "test_passed") throw new Error("provider.route_test_failed");
      await modelRoutingApi.setDefaultModelRoute(
        tested.route_id,
        "text",
        tested.revision,
        controller.signal,
      );
      controller.signal.throwIfAborted();
      navigate("project", { route_id: tested.route_id });
    } catch {
      if (controller.signal.aborted) return;
      setError(t("firstRun.provider.routeFailed"));
    } finally {
      if (finishMutation(controller)) setPending(false);
    }
  }

  async function createProject(event: FormEvent) {
    event.preventDefault();
    const name = projectName.trim();
    if (!name) return;
    const controller = beginMutation();
    setPending(true);
    setError("");
    try {
      const operation = retainClientRequestId(
        createProjectOperation.current,
        "project-create",
        name,
      );
      createProjectOperation.current = operation;
      const result = await projectGovernanceApi.createProject(
        name,
        operation.idempotencyKey,
        controller.signal,
      );
      const projectId = projectIdFromTargetRef(result.target_ref);
      createProjectOperation.current = null;
      setSelectedProjectId(projectId);
      navigate("project", { project_id: projectId }, "replace");
      setProjects((current) =>
        current.some((project) => project.project_id === projectId)
          ? current
          : [
              ...current,
              {
                project_id: projectId,
                name,
                policy_profile_id: "policy-default-governed",
                status: "active",
              },
            ],
      );
      setProjectNeedsRefresh(true);
      try {
        await refreshSession();
        controller.signal.throwIfAborted();
        setProjectNeedsRefresh(false);
        navigate("document", { project_id: projectId });
      } catch {
        if (controller.signal.aborted) return;
        setError(t("firstRun.project.refreshFailed"));
      }
    } catch {
      if (controller.signal.aborted) return;
      setError(t("firstRun.project.createFailed"));
    } finally {
      if (finishMutation(controller)) setPending(false);
    }
  }

  async function useSelectedProject() {
    if (!selectedProjectId) return;
    const controller = beginMutation();
    setPending(true);
    setError("");
    try {
      await refreshSession();
      controller.signal.throwIfAborted();
      setProjectNeedsRefresh(false);
      navigate("document", { project_id: selectedProjectId });
    } catch {
      if (controller.signal.aborted) return;
      setProjectNeedsRefresh(true);
      setError(t("firstRun.project.refreshFailed"));
    } finally {
      if (finishMutation(controller)) setPending(false);
    }
  }

  async function uploadDocument(event: FormEvent) {
    event.preventDefault();
    if (!file || !selectedProjectId || file.size === 0) return;
    const controller = beginMutation();
    const operation = retainClientRequestId(
      uploadDocumentOperation.current,
      "document-upload",
      JSON.stringify({
        projectId: selectedProjectId,
        name: file.name,
        size: file.size,
        type: file.type,
      }),
    );
    uploadDocumentOperation.current = operation;
    setPending(true);
    setError("");
    try {
      const result = await documentLibraryApi.uploadDocumentLibraryFile(
        {
          clientKey: operation.idempotencyKey,
          scopeType: "project",
          scopeId: selectedProjectId,
          tagRefs: [{ tag_type: "project", tag_id: selectedProjectId }],
          file,
          description: "",
          allowMemberDownload: false,
        },
        controller.signal,
      );
      controller.signal.throwIfAborted();
      const accepted = result.document ?? undefined;
      if (!accepted) throw new Error("document.not_accepted");
      uploadDocumentOperation.current = null;
      setAcceptedDocument(accepted);
      navigate("review");
    } catch {
      if (controller.signal.aborted) return;
      setError(t("firstRun.document.uploadFailed"));
    } finally {
      if (finishMutation(controller)) setPending(false);
    }
  }


  const stepTitle = t(`firstRun.steps.${step}.title`);
  const stepDescription = t(`firstRun.steps.${step}.description`);
  const firstIncompleteOptionalStep = (
    ["provider", "project", "document"] as const
  ).find((candidate) =>
    reviewRows.some(
      (row) => row.key === candidate && row.state !== "complete",
    ),
  );

  if (!session.authenticated && status === "loading") {
    return <SetupFrame><Spinner className="size-6" /></SetupFrame>;
  }

  if (!session.authenticated && status === "unavailable") {
    return (
      <SetupFrame>
        <h1 className="sr-only">{t("firstRun.statusUnavailable.title")}</h1>
        <Alert variant="destructive">
          <AlertCircle />
          <AlertTitle>{t("firstRun.statusUnavailable.title")}</AlertTitle>
          <AlertDescription className="flex flex-col items-start gap-3">
            <span>{t("firstRun.statusUnavailable.description")}</span>
            <Button
              type="button"
              variant="outline"
              onClick={() => setStatusAttempt((attempt) => attempt + 1)}
            >
              <RefreshCw data-icon="inline-start" />
              {t("common.retry")}
            </Button>
          </AlertDescription>
        </Alert>
      </SetupFrame>
    );
  }
  if (session.authenticated && normalizingEntry) {
    return <SetupFrame><Spinner className="size-6" /></SetupFrame>;
  }

  return (
    <main className="min-h-screen bg-muted/30 px-4 py-8 text-foreground sm:py-12">
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
        <div className="sm:hidden">
          <div className="mb-2 flex items-center justify-between text-sm text-muted-foreground">
            <span>{t("firstRun.progress", { current: stepIndex + 1, total: steps.length })}</span>
            <span>{stepTitle}</span>
          </div>
          <Progress value={((stepIndex + 1) / steps.length) * 100} />
        </div>
        <ol className="hidden grid-cols-5 gap-2 sm:grid" aria-label={t("firstRun.progressLabel")}>
          {steps.map((item, index) => {
            const complete = index < stepIndex;
            return (
              <li
                key={item}
                aria-current={item === step ? "step" : undefined}
                className="flex flex-col gap-2 text-xs text-muted-foreground"
              >
                <span className="flex items-center gap-2">
                  <Badge variant={item === step ? "default" : "outline"}>
                    {complete ? t("firstRun.complete") : index + 1}
                  </Badge>
                  <span className={item === step ? "font-medium text-foreground" : undefined}>
                    {t(`firstRun.steps.${item}.short`)}
                  </span>
                </span>
              </li>
            );
          })}
        </ol>

        <Card>
          <CardHeader>
            <div className="flex justify-end">
              <LanguageSwitch />
            </div>
            <h1 ref={headingRef} tabIndex={-1} className="text-2xl font-semibold tracking-tight outline-none">
              {stepTitle}
            </h1>
            <CardDescription>{stepDescription}</CardDescription>
          </CardHeader>
          <CardContent>
            {error && (
              <Alert variant="destructive" className="mb-6">
                <AlertCircle />
                <AlertTitle>{t("common.requestFailed")}</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {step === "admin" && (
              <form id="setup-admin-form" className="flex flex-col gap-6" onSubmit={claimAdmin}>
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor="setup-display-name">{t("firstRun.admin.displayName")}</FieldLabel>
                    <Input ref={displayNameRef} id="setup-display-name" autoComplete="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} required />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="setup-email">{t("firstRun.admin.email")}</FieldLabel>
                    <Input ref={emailRef} id="setup-email" type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required />
                  </Field>
                  <Field data-invalid={passwordState.tooShort}>
                    <FieldLabel htmlFor="setup-password">{t("firstRun.admin.password")}</FieldLabel>
                    <Input ref={passwordRef} id="setup-password" type="password" autoComplete="new-password" value={password} aria-invalid={passwordState.tooShort} onChange={(event) => setPassword(event.target.value)} required />
                    <FieldDescription>{t("firstRun.admin.passwordHelp")}</FieldDescription>
                  </Field>
                  <Field data-invalid={passwordState.mismatch}>
                    <FieldLabel htmlFor="setup-password-confirmation">{t("firstRun.admin.confirmPassword")}</FieldLabel>
                    <Input ref={passwordConfirmationRef} id="setup-password-confirmation" type="password" autoComplete="new-password" value={confirmPassword} aria-invalid={passwordState.mismatch} onChange={(event) => setConfirmPassword(event.target.value)} required />
                    {passwordState.mismatch && <FieldError>{t("firstRun.admin.passwordMismatch")}</FieldError>}
                  </Field>
                </FieldGroup>
              </form>
            )}

            {step === "provider" && !providerConnection && (
              <form id="setup-provider-form" className="flex flex-col gap-6" onSubmit={testConnectionAndDiscover}>
                <ProviderConnectionFields
                  idPrefix="setup"
                  values={{
                    displayName: connectionName,
                    providerType,
                    endpointUrl,
                    apiVersion,
                    apiKey,
                  }}
                  providerTypeLocked={Boolean(existingConnection)}
                  credentialAlreadyConfigured={Boolean(
                    existingConnection?.credential_configured,
                  )}
                  onDisplayNameChange={setConnectionName}
                  onProviderTypeChange={setProviderType}
                  onEndpointUrlChange={setEndpointUrl}
                  onApiVersionChange={setApiVersion}
                  onApiKeyChange={setApiKey}
                />
              </form>
            )}

            {step === "provider" && providerConnection && (
              <form id="setup-route-form" className="flex flex-col gap-6" onSubmit={saveTextModel}>
                <Alert>
                  <CheckCircle2 />
                  <AlertTitle>{t("firstRun.provider.connectionTested")}</AlertTitle>
                  <AlertDescription>{providerConnection.display_name}</AlertDescription>
                </Alert>
                <Field>
                  <FieldLabel htmlFor="setup-model-name">{t("firstRun.provider.modelName")}</FieldLabel>
                  {models.length > 0 ? (
                    <OptionSelect id="setup-model-name" value={modelName} options={models.map((model) => ({ value: model, label: model }))} onValueChange={setModelName} />
                  ) : (
                    <Input id="setup-model-name" value={modelName} onChange={(event) => setModelName(event.target.value)} required />
                  )}
                  {discoveryUnavailable && <FieldDescription>{t("firstRun.provider.discoveryUnavailable")}</FieldDescription>}
                </Field>
              </form>
            )}

            {step === "project" && (
              <div className="flex flex-col gap-8">
                {projectLoadState === "loading" && <Spinner className="size-6" />}
                {projectLoadState === "unavailable" && (
                  <Alert variant="destructive">
                    <AlertCircle />
                    <AlertTitle>{t("firstRun.project.listUnavailable")}</AlertTitle>
                    <AlertDescription>
                      <Button type="button" variant="outline" onClick={loadProjects}>
                        <RefreshCw data-icon="inline-start" /> {t("common.retry")}
                      </Button>
                    </AlertDescription>
                  </Alert>
                )}
                {projectLoadState === "ready" && projects.length > 0 && (
                  <Field>
                    <FieldLabel htmlFor="setup-project-select">{t("firstRun.project.existing")}</FieldLabel>
                    <OptionSelect id="setup-project-select" value={selectedProjectId} placeholder={t("firstRun.project.choose")} options={projects.map((project) => ({ value: project.project_id, label: project.name }))} onValueChange={setSelectedProjectId} />
                    <Button type="button" onClick={useSelectedProject} disabled={!selectedProjectId || pending}>
                      {pending ? <Spinner data-icon="inline-start" /> : <ChevronRight data-icon="inline-end" />}
                      {t("firstRun.project.useExisting")}
                    </Button>
                  </Field>
                )}
                {!projectNeedsRefresh && (
                  <form id="setup-project-form" className="flex flex-col gap-4" onSubmit={createProject}>
                    <Field>
                      <FieldLabel htmlFor="setup-project-name">{t("firstRun.project.name")}</FieldLabel>
                      <Input id="setup-project-name" value={projectName} onChange={(event) => setProjectName(event.target.value)} required />
                    </Field>
                    <Button type="submit" disabled={!projectName.trim() || pending}>
                      {pending && <Spinner data-icon="inline-start" />}
                      {t("firstRun.project.create")}
                    </Button>
                  </form>
                )}
              </div>
            )}

            {step === "document" && (
              <form id="setup-document-form" className="flex flex-col gap-6" onSubmit={uploadDocument}>
                {!selectedProjectId && (
                  <Alert>
                    <AlertCircle />
                    <AlertTitle>{t("firstRun.document.projectRequired")}</AlertTitle>
                    <AlertDescription>{t("firstRun.document.projectRequiredHelp")}</AlertDescription>
                  </Alert>
                )}
                <Field data-invalid={Boolean(file && file.size === 0)}>
                  <FieldLabel htmlFor="setup-document-file">{t("firstRun.document.file")}</FieldLabel>
                  <Input
                    id="setup-document-file"
                    type="file"
                    accept={DOCUMENT_UPLOAD_ACCEPT}
                    onChange={(event) => {
                      setFile(event.currentTarget.files?.[0] ?? null);
                      uploadDocumentOperation.current = null;
                    }}
                    required
                  />
                  <FieldDescription>{t("firstRun.document.help")}</FieldDescription>
                  {file?.size === 0 && <FieldError>{t("firstRun.document.empty")}</FieldError>}
                </Field>
                <Button type="submit" disabled={!file || file.size === 0 || !selectedProjectId || pending}>
                  {pending ? <Spinner data-icon="inline-start" /> : <FileUp data-icon="inline-start" />}
                  {t("firstRun.document.upload")}
                </Button>
              </form>
            )}

            {step === "review" && (
              <div className="flex flex-col gap-5">
                {acceptedDocument && (
                  <Alert>
                    <CheckCircle2 />
                    <AlertTitle>{t("firstRun.document.accepted")}</AlertTitle>
                    <AlertDescription>{acceptedDocument.title}</AlertDescription>
                  </Alert>
                )}
                {reviewLoading ? (
                  <Spinner className="size-6" />
                ) : (
                  <ul className="divide-y rounded-lg border">
                    {reviewRows.map((row) => (
                      <li key={row.key} className="flex items-start justify-between gap-4 p-4">
                        <div>
                          <p className="font-medium">{t(`firstRun.review.${row.key}`)}</p>
                          <p className="text-sm text-muted-foreground">{row.detail}</p>
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-2">
                          <Badge variant={row.state === "complete" ? "default" : "outline"}>
                            {t(`firstRun.review.state.${row.state}`)}
                          </Badge>
                          <Button
                            type="button"
                            variant="ghost"
                            onClick={() => {
                              if (row.key === "admin") router.push("/settings");
                              else if (row.key === "readiness") router.push("/admin/ops");
                              else navigate(row.key);
                            }}
                          >
                            {t("firstRun.review.open")}
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                <Button type="button" variant="outline" onClick={() => void loadReview()} disabled={reviewLoading}>
                  <RefreshCw data-icon="inline-start" />
                  {t("firstRun.review.refresh")}
                </Button>
                {firstIncompleteOptionalStep && (
                  <Button
                    type="button"
                    onClick={() => navigate(firstIncompleteOptionalStep)}
                  >
                    {t("firstRun.review.continue")}
                  </Button>
                )}
              </div>
            )}
          </CardContent>
          <CardFooter className="flex flex-wrap justify-between gap-3 border-t">
            <div>
              {session.authenticated && stepIndex > 1 && step !== "review" && (
                <Button type="button" variant="ghost" onClick={returnToPreviousStep}>
                  <ChevronLeft data-icon="inline-start" />
                  {t("common.back")}
                </Button>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              {session.authenticated && step !== "review" && step !== "admin" && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={skipCurrentStep}
                >
                  {t("firstRun.skip")}
                </Button>
              )}
              {step === "admin" && (
                <Button form="setup-admin-form" type="submit" disabled={pending}>
                  {pending ? <Spinner data-icon="inline-start" /> : <ChevronRight data-icon="inline-end" />}
                  {t("firstRun.admin.create")}
                </Button>
              )}
              {step === "provider" && !providerConnection && (
                <Button form="setup-provider-form" type="submit" disabled={pending}>
                  {pending && <Spinner data-icon="inline-start" />}
                  {t("firstRun.provider.test")}
                </Button>
              )}
              {step === "provider" && providerConnection && (
                <Button form="setup-route-form" type="submit" disabled={pending}>
                  {pending && <Spinner data-icon="inline-start" />}
                  {t("firstRun.provider.saveRoute")}
                </Button>
              )}
              {step === "review" && (
                <Button type="button" onClick={() => router.replace("/workspace")}>
                  <LogIn data-icon="inline-start" />
                  {t("firstRun.enterAtlas")}
                </Button>
              )}
            </div>
          </CardFooter>
        </Card>
      </div>
    </main>
  );
}

function SetupFrame({ children }: { children: ReactNode }) {
  return (
    <main className="grid min-h-screen place-items-center bg-muted/30 px-4 py-12">
      <div className="w-full max-w-2xl">{children}</div>
    </main>
  );
}
