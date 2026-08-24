import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { Field, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { OptionSelect } from "../../shared/OptionSelect";
import { LoadErrorState, LoadingState, serverMessage } from "../../shared/product-ui";
import { adminTeamDetailRoute } from "../../shared/routes";
import { clientRequestId } from "../../shared/ids";
import { teamAdministrationApi } from "./api";
import type {
  TeamDirectoryConnectionListResult,
  TeamDirectoryUserSearchResult,
} from "./types";
import type { TeamScopeRole } from "../../shared/identity-access-contracts";

type DirectoryMode = "atlas" | "directory";
type DirectorySearchMode = "department" | "member";

type SystemTeamDirectoryImportControllerOptions = {
  teamId: string;
  role: TeamScopeRole;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<boolean>;
  onPostSuccess: (teamId: string) => Promise<void>;
  onClose: () => void;
};

export function useSystemTeamDirectoryImportController({
  teamId,
  role,
  onNotice,
  onRefresh,
  onPostSuccess,
  onClose,
}: SystemTeamDirectoryImportControllerOptions) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;
  const requestIdRef = useRef(0);
  const [mode, setModeState] = useState<DirectoryMode>("atlas");
  const [connections, setConnections] = useState<
    TeamDirectoryConnectionListResult["connections"]
  >([]);
  const [connectionsLoadError, setConnectionsLoadError] = useState("");
  const [connectionId, setConnectionIdState] = useState("");
  const [searchMode, setSearchModeState] = useState<DirectorySearchMode>("member");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<TeamDirectoryUserSearchResult | null>(null);
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
    if (!teamId) return;
    const requestId = requestIdRef.current;
    setLoading(true);
    setConnectionsLoadError("");
    setActionError("");
    try {
      const result = await teamAdministrationApi.listDirectoryConnections(teamId);
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
    if (!teamId || !connectionId || !query.trim()) return;
    invalidateRequest();
    const requestId = requestIdRef.current;
    setLoading(true);
    setActionError("");
    clearSearchDraft();
    try {
      const result = await teamAdministrationApi.searchDirectoryUsers(
        teamId,
        connectionId,
        searchMode,
        query.trim(),
      );
      if (requestId !== requestIdRef.current) return;
      setSearch(result);
      setIdempotencyKey(clientRequestId("team-directory-import"));
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
  function toggleAllSubjects(checked: boolean) {
    const resultSubjects = search?.users.map((user) => user.external_subject) ?? [];
    setSelectedSubjects(checked ? resultSubjects : []);
  }


  async function importMembers() {
    if (!teamId || !connectionId || selectedSubjects.length === 0) return;
    const requestId = requestIdRef.current;
    const originRoute = adminTeamDetailRoute(teamId, "members");
    setImportPending(true);
    setActionError("");
    try {
      const result = await teamAdministrationApi.importDirectoryMembers(
        teamId,
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
      await onPostSuccess(teamId);
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
    toggleAllSubjects,
    importMembers,
  };
}

export type SystemTeamDirectoryImportController = ReturnType<
  typeof useSystemTeamDirectoryImportController
>;

export function SystemTeamDirectoryImportView({
  controller,
}: {
  controller: SystemTeamDirectoryImportController;
}) {
  const { t } = useTranslation();
  const resultSubjects =
    controller.search?.users.map((user) => user.external_subject) ?? [];
  const selectedResultCount = resultSubjects.filter((subject) =>
    controller.selectedSubjects.includes(subject),
  ).length;
  const allResultsSelected =
    resultSubjects.length > 0 && selectedResultCount === resultSubjects.length;
  const someResultsSelected = selectedResultCount > 0 && !allResultsSelected;
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
        <FieldLabel htmlFor="system-team-directory-source">{t("directory.source")}</FieldLabel>
        <OptionSelect
          id="system-team-directory-source"
          value={controller.connectionId}
          options={controller.connections.map((connection) => ({
            value: connection.connection_id,
            label: connection.display_name,
          }))}
          onValueChange={controller.setConnectionId}
        />
      </Field>
      <Field>
        <FieldLabel htmlFor="system-team-directory-search-mode">{t("directory.searchMode")}</FieldLabel>
        <OptionSelect
          id="system-team-directory-search-mode"
          value={controller.searchMode}
          options={[
            { value: "member", label: t("directory.memberSearch") },
            { value: "department", label: t("directory.department") },
          ]}
          onValueChange={controller.setSearchMode}
        />
      </Field>
      <Field>
        <FieldLabel htmlFor="system-team-directory-query">
          {controller.searchMode === "department"
            ? t("directory.department")
            : t("directory.searchQuery")}
        </FieldLabel>
        <div className="flex gap-2">
          <Input
            id="system-team-directory-query"
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
        <div className="rounded-md border p-3 text-sm">
          <div className="font-medium">{t("directory.limitReachedTitle")}</div>
          <div className="text-muted-foreground">
            {t("directory.limitReachedDescription")}
          </div>
        </div>
      )}
      {controller.search && (
        <div className="grid max-h-64 gap-2 overflow-y-auto rounded-md border p-2">
          {controller.search.users.length === 0 ? (
            <div className="p-3 text-sm text-muted-foreground">
              {t("directory.noMatchesDescription")}
            </div>
          ) : (
            <>
              {controller.searchMode === "department" && (
                <label className="flex min-h-11 items-center gap-3 rounded-md bg-muted/50 px-3 py-2 font-medium">
                  <Checkbox
                    checked={
                      allResultsSelected ? true : someResultsSelected ? "indeterminate" : false
                    }
                    onCheckedChange={(checked) =>
                      controller.toggleAllSubjects(checked === true)
                    }
                  />
                  <span>{t("directory.selectAllResults")}</span>
                </label>
              )}
              {controller.search.users.map((user) => (
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
              ))}
            </>
          )}
        </div>
      )}
      <div aria-live="polite" className="text-sm text-muted-foreground">
        {t("directory.selectedCount", { count: controller.selectedSubjects.length })}
      </div>
    </>
  );
}
