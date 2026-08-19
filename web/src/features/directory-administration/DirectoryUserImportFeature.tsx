import { RefreshCw, Search, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "../../components/ui/empty";
import { Field, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Spinner } from "../../components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { serverMessage } from "../../shared/product-ui";
import { directoryAdministrationApi } from "./api";
import type { DirectoryConnectionStatus, DirectoryUserCandidate } from "./types";

export function DirectoryUserImportFeature({
  onImported,
  onNotice,
}: {
  onImported: () => Promise<void>;
  onNotice: (message: string) => void;
}) {
  const { t } = useTranslation();
  const [connections, setConnections] = useState<DirectoryConnectionStatus[]>([]);
  const [connectionId, setConnectionId] = useState("");
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<DirectoryUserCandidate[]>([]);
  const [selectedSubjects, setSelectedSubjects] = useState<Set<string>>(new Set());
  const [searchCompleted, setSearchCompleted] = useState(false);
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [pendingAction, setPendingAction] = useState("");
  const [actionError, setActionError] = useState("");

  const enabledConnections = useMemo(
    () => connections.filter((connection) => connection.enabled),
    [connections],
  );

  useEffect(() => {
    void refreshConnections();
  }, []);

  async function refreshConnections() {
    setLoadingConnections(true);
    setActionError("");
    try {
      const result = await directoryAdministrationApi.listConnections();
      setConnections(result.connections);
      setConnectionId((current) =>
        result.connections.some(
          (connection) => connection.connection_id === current && connection.enabled,
        )
          ? current
          : result.connections.find((connection) => connection.enabled)?.connection_id ?? "",
      );
    } catch (error) {
      setActionError(error instanceof Error ? error.message : t("admin.listLoadFailed"));
    } finally {
      setLoadingConnections(false);
    }
  }

  async function searchUsers() {
    if (!connectionId || !query.trim()) return;
    setPendingAction("search-users");
    setActionError("");
    try {
      const result = await directoryAdministrationApi.searchUsers(connectionId, query.trim());
      setCandidates(result.users);
      setSelectedSubjects(new Set());
      setSearchCompleted(true);
    } catch (error) {
      const message = error instanceof Error ? error.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  async function importUsers() {
    if (!connectionId || selectedSubjects.size === 0) return;
    setPendingAction("import-users");
    setActionError("");
    try {
      const result = await directoryAdministrationApi.importUsers(
        connectionId,
        [...selectedSubjects],
      );
      onNotice(result.message_code);
      toast.success(serverMessage(result, t));
      await onImported();
      setCandidates([]);
      setSelectedSubjects(new Set());
      setSearchCompleted(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  function toggleCandidate(subject: string, checked: boolean) {
    setSelectedSubjects((current) => {
      const next = new Set(current);
      if (checked) next.add(subject);
      else next.delete(subject);
      return next;
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("directory.importTitle")}</CardTitle>
        <CardDescription>{t("directory.importDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {actionError ? (
          <Alert variant="destructive">
            <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
            <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
          </Alert>
        ) : null}

        {loadingConnections ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Spinner />
            {t("directory.loading")}
          </div>
        ) : enabledConnections.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyTitle>{t("directory.emptyTitle")}</EmptyTitle>
              <EmptyDescription>{t("directory.emptyDescription")}</EmptyDescription>
            </EmptyHeader>
            <Button variant="outline" onClick={() => void refreshConnections()}>
              <RefreshCw data-icon="inline-start" />
              {t("directory.refresh")}
            </Button>
          </Empty>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-[minmax(12rem,18rem)_1fr_auto] sm:items-end">
              <Field>
                <FieldLabel htmlFor="directory-user-import-source">{t("directory.source")}</FieldLabel>
                <Select value={connectionId} onValueChange={setConnectionId}>
                  <SelectTrigger id="directory-user-import-source" className="w-full">
                    <SelectValue placeholder={t("directory.selectSource")} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {enabledConnections.map((connection) => (
                        <SelectItem key={connection.connection_id} value={connection.connection_id}>
                          {connection.display_name}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel htmlFor="directory-user-import-query">{t("directory.searchQuery")}</FieldLabel>
                <Input
                  id="directory-user-import-query"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void searchUsers();
                  }}
                />
              </Field>
              <Button
                disabled={!connectionId || !query.trim() || pendingAction === "search-users"}
                onClick={() => void searchUsers()}
              >
                {pendingAction === "search-users" ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <Search data-icon="inline-start" />
                )}
                {t("directory.search")}
              </Button>
            </div>

            {searchCompleted && candidates.length === 0 ? (
              <Empty className="border">
                <EmptyHeader>
                  <EmptyTitle>{t("directory.noMatchesTitle")}</EmptyTitle>
                  <EmptyDescription>{t("directory.noMatchesDescription")}</EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : candidates.length > 0 ? (
              <>
                <div className="overflow-x-auto rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-10">
                          <span className="sr-only">{t("directory.select")}</span>
                        </TableHead>
                        <TableHead>{t("directory.user")}</TableHead>
                        <TableHead>{t("directory.department")}</TableHead>
                        <TableHead>{t("directory.groups")}</TableHead>
                        <TableHead>{t("directory.directoryStatus")}</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {candidates.map((candidate) => (
                        <TableRow key={candidate.external_subject}>
                          <TableCell>
                            <Checkbox
                              aria-label={t("directory.selectUser", { name: candidate.display_name })}
                              checked={selectedSubjects.has(candidate.external_subject)}
                              onCheckedChange={(checked) =>
                                toggleCandidate(candidate.external_subject, checked === true)
                              }
                            />
                          </TableCell>
                          <TableCell>
                            <div className="font-medium">{candidate.display_name}</div>
                            <div className="text-xs text-muted-foreground">
                              {candidate.username}
                              {candidate.email ? ` · ${candidate.email}` : ""}
                            </div>
                          </TableCell>
                          <TableCell>
                            {candidate.department ?? "-"}
                            {candidate.title ? (
                              <div className="text-xs text-muted-foreground">{candidate.title}</div>
                            ) : null}
                          </TableCell>
                          <TableCell className="max-w-64">
                            <div className="flex flex-wrap gap-1">
                              {candidate.groups.slice(0, 4).map((group) => (
                                <Badge key={group} variant="outline">{group}</Badge>
                              ))}
                              {candidate.groups.length > 4 ? (
                                <Badge variant="outline">+{candidate.groups.length - 4}</Badge>
                              ) : null}
                            </div>
                          </TableCell>
                          <TableCell>
                            <Badge variant={candidate.directory_enabled === false ? "destructive" : "outline"}>
                              {candidate.directory_enabled === false
                                ? t("directory.disabled")
                                : t("directory.available")}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="text-sm text-muted-foreground">
                    {t("directory.selectedCount", { count: selectedSubjects.size })}
                  </div>
                  <Button
                    disabled={selectedSubjects.size === 0 || pendingAction === "import-users"}
                    onClick={() => void importUsers()}
                  >
                    {pendingAction === "import-users" ? (
                      <Spinner data-icon="inline-start" />
                    ) : (
                      <Upload data-icon="inline-start" />
                    )}
                    {t("directory.importSelected")}
                  </Button>
                </div>
              </>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
