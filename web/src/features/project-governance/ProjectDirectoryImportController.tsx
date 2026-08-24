import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { Field, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { OptionSelect } from "../../shared/OptionSelect";
import { LoadErrorState, LoadingState, serverMessage } from "../../shared/product-ui";
import { adminProjectDetailRoute } from "../../shared/routes";
import { clientRequestId } from "../../shared/ids";
import { projectGovernanceApi } from "./api";
import type {
  ProjectDirectoryConnectionListResult,
  ProjectDirectoryUserSearchResult,
  ProjectMemberRole,
} from "./types";

type DirectoryMode = "atlas" | "directory";
type DirectorySearchMode = "department" | "member";

type ProjectDirectoryImportControllerOptions = {
  projectId: string;
  role: ProjectMemberRole;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<boolean>;
  onPostSuccess: (projectId: string) => Promise<void>;
  onClose: () => void;
};

export function useProjectDirectoryImportController({
  projectId,
  role,
  onNotice,
  onRefresh,
  onPostSuccess,
  onClose,
}: ProjectDirectoryImportControllerOptions) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;
  const requestIdRef = useRef(0);
  const [mode, setModeState] = useState<DirectoryMode>("atlas");
  const [connections, setConnections] = useState<
    ProjectDirectoryConnectionListResult["connections"]
  >([]);
  const [connectionsLoadError, setConnectionsLoadError] = useState("");
  const [connectionId, setConnectionIdState] = useState("");
  const [searchMode, setSearchModeState] = useState<DirectorySearchMode>("member");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<ProjectDirectoryUserSearchResult | null>(null);
  const [selectedSubjects, setSelectedSubjects] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [actionError, setActionError] = useState("");
  const [importPending, setImportPending] = useState(false);

  function invalidateRequest() {
    requestIdRef.current += 1;
    setImportPending(false);
  }

  useEffect(() => invalidateRequest, []);

  function clearSearchDraft() {
    setSearch(null);
    setSelectedSubjects([]);
  }

  function reset() {
    invalidateRequest();
    setModeState("atlas");
    setConnections([]);
    setConnectionsLoadError("");
    setConnectionIdState("");
    setSearchModeState("member");
    setQuery("");
    clearSearchDraft();
    setLoading(false);
    setIdempotencyKey("");
    setActionError("");
  }

  async function loadConnections() {
    if (!projectId) return;
    const requestId = requestIdRef.current;
    setLoading(true);
    setConnectionsLoadError("");
    setActionError("");
    try {
      const result = await projectGovernanceApi.listDirectoryConnections(projectId);
      if (requestId !== requestIdRef.current) return;
      setConnections(result.connections);
      setConnectionIdState(result.connections[0]?.connection_id ?? "");
    } catch (error) {
      if (requestId !== requestIdRef.current) return;
      setConnectionsLoadError(error instanceof Error ? error.message : t("admin.listLoadFailed"));
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }

  function setMode(value: DirectoryMode) {
    invalidateRequest();
    setModeState(value);
    setActionError("");
    clearSearchDraft();
    if (value === "directory") void loadConnections();
  }

  function setConnectionId(value: string) {
    invalidateRequest();
    setConnectionIdState(value);
    clearSearchDraft();
  }

  function setSearchMode(value: DirectorySearchMode) {
    invalidateRequest();
    setSearchModeState(value);
    clearSearchDraft();
  }

  async function searchDirectory() {
    if (!projectId || !connectionId || !query.trim()) return;
    invalidateRequest();
    const requestId = requestIdRef.current;
    setLoading(true);
    setActionError("");
    clearSearchDraft();
    try {
      const result = await projectGovernanceApi.searchDirectoryUsers(
        projectId,
        connectionId,
        searchMode,
        query.trim(),
      );
      if (requestId !== requestIdRef.current) return;
      setSearch(result);
      setIdempotencyKey(clientRequestId("project-directory-import"));
    } catch (error) {
      if (requestId !== requestIdRef.current) return;
      setActionError(error instanceof Error ? error.message : t("admin.actionFailed"));
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }

  function toggleSubject(externalSubject: string, checked: boolean) {
    setSelectedSubjects((current) =>
      checked
        ? [...current, externalSubject]
        : current.filter((subject) => subject !== externalSubject),
    );
  }

  async function importMembers() {
    if (!projectId || !connectionId || selectedSubjects.length === 0) return;
    const requestId = requestIdRef.current;
    const originRoute = adminProjectDetailRoute(projectId, "access");
    setImportPending(true);
    setActionError("");
    try {
      const result = await projectGovernanceApi.importDirectoryMembers(
        projectId,
        connectionId,
        selectedSubjects,
        role,
        idempotencyKey,
      );
      if (requestId !== requestIdRef.current || pathnameRef.current !== originRoute) return;
      const successMessage = t("directory.importSuccess", { count: result.applied_count });
      onNotice(successMessage);
      toast.success(successMessage);
      const routeRetained = await onRefresh();
      if (
        requestId !== requestIdRef.current ||
        !routeRetained ||
        pathnameRef.current !== originRoute
      ) return;
      await onPostSuccess(projectId);
      onClose();
    } catch (error) {
      if (requestId !== requestIdRef.current) return;
      const message = error instanceof Error ? error.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      if (requestId === requestIdRef.current) setImportPending(false);
    }
  }

  return {
    mode,
    connections,
    connectionsLoadError,
    connectionId,
    searchMode,
    query,
    search,
    selectedSubjects,
    loading,
    actionError,
    importPending,
    reset,
    invalidateRequest,
    setMode,
    setConnectionId,
    setSearchMode,
    setQuery,
    loadConnections,
    searchDirectory,
    toggleSubject,
    importMembers,
  };
}

export type ProjectDirectoryImportController = ReturnType<
  typeof useProjectDirectoryImportController
>;

export function ProjectDirectoryImportView({
  controller,
}: {
  controller: ProjectDirectoryImportController;
}) {
  const { t } = useTranslation();
  return (
    <>
      {controller.loading && !controller.search ? (
        <LoadingState title={t("directory.loading")} />
      ) : controller.connectionsLoadError ? (
        <LoadErrorState
          title={t("admin.listLoadFailed")}
          description={serverMessage(controller.connectionsLoadError, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => void controller.loadConnections()}
        />
      ) : controller.connections.length === 0 ? (
        <div className="text-sm text-muted-foreground">{t("directory.noScopedSources")}</div>
      ) : null}
      <Field>
        <FieldLabel htmlFor="project-directory-source">{t("directory.source")}</FieldLabel>
        <OptionSelect
          id="project-directory-source"
          value={controller.connectionId}
          options={controller.connections.map((connection) => ({
            value: connection.connection_id,
            label: connection.display_name,
          }))}
          onValueChange={controller.setConnectionId}
        />
      </Field>
      <Field>
        <FieldLabel htmlFor="project-directory-search-mode">{t("directory.searchMode")}</FieldLabel>
        <OptionSelect
          id="project-directory-search-mode"
          value={controller.searchMode}
          options={[
            { value: "member", label: t("directory.memberSearch") },
            { value: "department", label: t("directory.department") },
          ]}
          onValueChange={controller.setSearchMode}
        />
      </Field>
      <Field>
        <FieldLabel htmlFor="project-directory-query">
          {controller.searchMode === "department"
            ? t("directory.department")
            : t("directory.searchQuery")}
        </FieldLabel>
        <div className="flex gap-2">
          <Input
            id="project-directory-query"
            value={controller.query}
            onChange={(event) => controller.setQuery(event.target.value)}
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => void controller.searchDirectory()}
            disabled={controller.loading || !controller.connectionId || !controller.query.trim()}
          >
            {t("directory.search")}
          </Button>
        </div>
      </Field>
      {controller.search?.limit_reached && (
        <Alert>
          <AlertTitle>{t("directory.limitReachedTitle")}</AlertTitle>
          <AlertDescription>{t("directory.limitReachedDescription")}</AlertDescription>
        </Alert>
      )}
      {controller.search && (
        <div className="grid max-h-64 gap-2 overflow-y-auto rounded-md border p-2">
          {controller.search.users.length === 0 ? (
            <div className="p-3 text-sm text-muted-foreground">
              {t("directory.noMatchesDescription")}
            </div>
          ) : (
            controller.search.users.map((user) => (
              <label
                key={user.external_subject}
                className="flex min-h-11 items-center gap-3 rounded-md border px-3 py-2"
              >
                <Checkbox
                  checked={controller.selectedSubjects.includes(user.external_subject)}
                  onCheckedChange={(checked) =>
                    controller.toggleSubject(user.external_subject, checked === true)
                  }
                />
                <span className="min-w-0">
                  <span className="block font-medium">{user.display_name}</span>
                  <span className="block text-xs text-muted-foreground">
                    {user.email ?? user.username}
                  </span>
                </span>
              </label>
            ))
          )}
        </div>
      )}
      <div aria-live="polite" className="text-sm text-muted-foreground">
        {t("directory.selectedCount", { count: controller.selectedSubjects.length })}
      </div>
    </>
  );
}
