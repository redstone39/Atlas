import { Clipboard, FilterX, RefreshCw, RotateCcw, Save, Search, UserPlus, UserX } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import {
  retainClientRequestId,
  type ClientOperationKey,
} from "../../shared/ids";
import { Button } from "../../components/ui/button";
import {
  AdminBreadcrumb,
  AdminResourceUnavailable,
} from "../../shared/admin-detail";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "../../components/ui/empty";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
  FieldTitle,
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
import { Spinner } from "../../components/ui/spinner";
import { useIsMobile } from "../../hooks/use-mobile";
import { OptionSelect, type OptionSelectItem } from "../../shared/OptionSelect";
import type { MessageReference } from "../../shared/user-messages";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import {
  ConfirmActionButton,
  LoadErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  activateOnEnterOrSpace,
  clickableCardClassName,
  clickableSurfaceClassName,
  serverMessage,
} from "../../shared/product-ui";
import { DirectoryUserImportFeature } from "../directory-administration";
import { userAdministrationApi } from "./api";
import type {
  EditableSystemRole,
  UserAdminFilters,
  UserAdminSummary,
} from "./types";
import {
  adminUserDetailRoute,
  type AppRoute,
  type AppRouteMatch,
} from "../../shared/routes";

type UserAction = MessageReference & {
  local_pilot_acceptance?: { acceptance_url: string } | null;
};


const editableSystemRoleValues: EditableSystemRole[] = ["user", "admin"];
export function UserAdministrationFeature({
  currentActorId,
  detail,
  onNavigate,
  onNotice,
  onRefresh,
}: {
  currentActorId: string | null;
  detail: Extract<AppRouteMatch, { kind: "admin-user-detail" }> | null;
  onNavigate: (route: AppRoute) => void;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [users, setUsers] = useState<UserAdminSummary[]>([]);
  const [showInviteForm, setShowInviteForm] = useState(false);
  const [showEditUser, setShowEditUser] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const createInviteOperation = useRef<ClientOperationKey | null>(null);
  const [selectedUserId, setSelectedUserId] = useState("");
  const [editDisplayName, setEditDisplayName] = useState("");
  const [editSystemRole, setEditSystemRole] = useState<EditableSystemRole>("user");
  const [inviteLink, setInviteLink] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const [actionError, setActionError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [appliedFilters, setAppliedFilters] = useState<UserAdminFilters>({});
  const [filterQuery, setFilterQuery] = useState("");
  const [filterSource, setFilterSource] = useState<"" | "local" | "directory">("");
  const [filterActive, setFilterActive] = useState<"" | "true" | "false">("");
  const [filterProfileStatus, setFilterProfileStatus] =
    useState<"" | "current" | "stale" | "missing" | "disabled">("");
  const [filterConnectionId, setFilterConnectionId] = useState("");
  const [filterGroup, setFilterGroup] = useState("");
  const [filterDepartment, setFilterDepartment] = useState("");
  const [filterTitle, setFilterTitle] = useState("");
  const [filterEmployeeId, setFilterEmployeeId] = useState("");

  useEffect(() => {
    void refreshUsers();
  }, []);

  const editableUsers = useMemo(
    () => users.filter((user) => user.actor_type === "user"),
    [users],
  );
  const selectedUser =
    detail && detail.actorId === selectedUserId
      ? editableUsers.find((user) => user.actor_id === selectedUserId) ?? null
      : null;

  useEffect(() => {
    if (editableUsers.length === 0 || !detail) {
      setSelectedUserId("");
      setEditDisplayName("");
      setEditSystemRole("user");
      setShowEditUser(false);
      return;
    }
    const routedUser = editableUsers.find((user) => user.actor_id === detail.actorId);
    if (routedUser) {
      setSelectedUserId(routedUser.actor_id);
      setEditDisplayName(routedUser.display_name);
      if (editableSystemRoleValues.includes(routedUser.system_role as EditableSystemRole)) {
        setEditSystemRole(routedUser.system_role as EditableSystemRole);
      } else {
        setEditSystemRole("user");
      }
      setShowEditUser(false);
    } else {
      setSelectedUserId("");
      setEditDisplayName("");
      setEditSystemRole("user");
      setShowEditUser(false);
    }
  }, [editableUsers, detail?.actorId]);

  async function refreshUsers(filters: UserAdminFilters = appliedFilters) {
    setLoading(true);
    setLoadError("");
    try {
      const userResult = await userAdministrationApi.listUsers(filters);
      setUsers(userResult.users);
      return userResult.users;
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : t("admin.listLoadFailed"));
      return null;
    } finally {
      setLoading(false);
    }
  }

  async function runAction(
    actionName: string,
    action: () => Promise<UserAction>,
    onSuccess?: () => void,
    requireCanonicalRefresh = false,
  ) {
    setPendingAction(actionName);
    setActionError("");
    try {
      const result = await action();
      const message = serverMessage(result, t);
      if ("local_pilot_acceptance" in result) {
        setInviteLink(result.local_pilot_acceptance?.acceptance_url ?? "");
      }
      if (actionName.startsWith("invite-revoke-")) {
        setInviteLink("");
      }
      if (!requireCanonicalRefresh) {
        onNotice(result.message_code);
        toast.success(message);
      }
      const refreshedUsers = await refreshUsers();
      await onRefresh();
      if (requireCanonicalRefresh && !refreshedUsers) {
        const refreshError = t("common.requestFailed");
        setActionError(refreshError);
        toast.error(refreshError);
        return;
      }
      if (requireCanonicalRefresh) {
        onNotice(result.message_code);
        toast.success(message);
      }
      if (
        detail &&
        refreshedUsers &&
        !refreshedUsers.some(
          (user) => user.actor_type === "user" && user.actor_id === detail.actorId,
        )
      ) {
        onNavigate("/admin/users");
      }
      onSuccess?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }
  async function refreshDirectoryProfile(user: UserAdminSummary) {
    setPendingAction(`directory-refresh-${user.actor_id}`);
    setActionError("");
    try {
      await userAdministrationApi.refreshDirectoryProfile(user.actor_id);
      toast.success(t("users.directoryProfileRefreshed"));
      await refreshUsers();
      await onRefresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
      await refreshUsers();
      await onRefresh();
    } finally {
      setPendingAction("");
    }
  }

  async function copyInviteLink() {
    if (!inviteLink) return;
    const absoluteLink = `${window.location.origin}${inviteLink}`;
    await navigator.clipboard?.writeText(absoluteLink);
    toast.success(t("admin.inviteCopied"));
  }

  const activeAdminCount = users.filter(
    (user) => user.actor_type === "user" && user.system_role === "admin" && user.active,
  ).length;
  const canCreateInvite = Boolean(displayName.trim() && email.trim());
  const trimmedEditDisplayName = editDisplayName.trim();
  const displayNameChanged = Boolean(
    selectedUser &&
      selectedUser.account_source === "local" &&
      editDisplayName !== selectedUser.display_name &&
      trimmedEditDisplayName &&
      trimmedEditDisplayName !== selectedUser.display_name,
  );
  const systemRoleChanged = Boolean(
    selectedUser &&
      canEditSystemRole(selectedUser) &&
      editSystemRole !== selectedUser.system_role,
  );
  const canSaveUser = displayNameChanged || systemRoleChanged;

  function canToggleLifecycle(user: UserAdminSummary) {
    if (user.actor_type !== "user") return false;
    if (user.actor_id === currentActorId) return false;
    if (user.system_role === "admin" && user.active && activeAdminCount <= 1) return false;
    return true;
  }

  function userContactLabel(user: UserAdminSummary) {
    return user.email ?? (user.active ? t("users.active") : t("users.inactive"));
  }

  function canRevokeInvite(user: UserAdminSummary) {
    return Boolean(user.invite_id && user.invite_status === "pending");
  }

  function canEditSystemRole(user: UserAdminSummary) {
    return (
      user.actor_type === "user" &&
      user.actor_id !== currentActorId &&
      editableSystemRoleValues.includes(user.system_role as EditableSystemRole) &&
      user.invite_status !== "pending"
    );
  }

  function canEditDisplayName(user: UserAdminSummary) {
    return user.actor_type === "user" && user.account_source === "local";
  }

  function canOpenUserEditor(user: UserAdminSummary) {
    return canEditDisplayName(user) || canEditSystemRole(user);
  }

  function systemRoleLabel(user: UserAdminSummary) {
    return t(`users.role.${user.system_role}`);
  }

  function systemRoleDescription(user: UserAdminSummary) {
    if (canEditSystemRole(user)) return t("users.systemRoleEditable");
    if (user.actor_id === currentActorId) return t("users.systemRoleReadonlySelf");
    if (user.system_role === "operator") return t("users.systemRoleReadonlyOperator");
    if (user.invite_status === "pending") return t("users.systemRoleReadonlyPending");
    return t("users.systemRoleReadonly");
  }

  function resetUserDraft(user: UserAdminSummary) {
    setEditDisplayName(user.display_name);
    setEditSystemRole(
      editableSystemRoleValues.includes(user.system_role as EditableSystemRole)
        ? (user.system_role as EditableSystemRole)
        : "user",
    );
  }

  function showUserEditor(user: UserAdminSummary) {
    resetUserDraft(user);
    setShowEditUser(true);
  }

  function closeUserEditor() {
    if (selectedUser) resetUserDraft(selectedUser);
    setShowEditUser(false);
  }

  function resetInviteDraft() {
    setDisplayName("");
    setEmail("");
    setInviteLink("");
  }

  function openInviteDialog() {
    resetInviteDraft();
    setShowInviteForm(true);
  }

  function closeInviteDialog() {
    createInviteOperation.current = null;
    resetInviteDraft();
    setShowInviteForm(false);
  }
  function currentFilterDraft(): UserAdminFilters {
    return {
      q: filterQuery.trim() || undefined,
      account_source: filterSource || undefined,
      active: filterActive ? filterActive === "true" : undefined,
      directory_profile_status: filterProfileStatus || undefined,
      directory_connection_id: filterConnectionId.trim() || undefined,
      directory_group: filterGroup.trim() || undefined,
      department: filterDepartment.trim() || undefined,
      title: filterTitle.trim() || undefined,
      employee_id: filterEmployeeId.trim() || undefined,
    };
  }

  function applyFilters() {
    const filters = currentFilterDraft();
    setAppliedFilters(filters);
    void refreshUsers(filters);
  }

  function clearFilters() {
    setFilterQuery("");
    setFilterSource("");
    setFilterActive("");
    setFilterProfileStatus("");
    setFilterConnectionId("");
    setFilterGroup("");
    setFilterDepartment("");
    setFilterTitle("");
    setFilterEmployeeId("");
    setAppliedFilters({});
    void refreshUsers({});
  }

  async function refreshImportedUsers() {
    await refreshUsers();
    await onRefresh();
  }

  function sourceBadge(user: UserAdminSummary) {
    return (
      <div className="flex flex-wrap items-center gap-1">
        <Badge variant="outline">{t(`users.source.${user.account_source}`)}</Badge>
        {user.directory_profile ? (
          <StatusBadge
            semantic={
              user.directory_profile.status === "current"
                ? "success"
                : user.directory_profile.status === "stale"
                  ? "attention"
                  : "inactive"
            }
            label={t(`users.directoryStatus.${user.directory_profile.status}`)}
          />
        ) : null}
      </div>
    );
  }

  function openUserEditor(user: UserAdminSummary) {
    if (user.actor_type !== "user") return;
    onNavigate(adminUserDetailRoute(user.actor_id));
  }

  function userActions(user: UserAdminSummary) {
    return (
      <div className="flex flex-wrap gap-2">
        {canRevokeInvite(user) && (
          <ConfirmActionButton
            ariaLabel={`${t("users.revokeInvite")} ${user.display_name}`}
            icon={<UserX data-icon="inline-start" />}
            disabled={pendingAction === `invite-revoke-${user.invite_id}`}
            confirmTitle={t("admin.destructiveConfirmTitle", {
              action: t("users.revokeInvite"),
            })}
            confirmDescription={t("admin.destructiveConfirmDescription", {
              target: user.display_name,
            })}
            confirmLabel={t("users.revokeInvite")}
            cancelLabel={t("admin.cancel")}
            onConfirm={() =>
              runAction(
                `invite-revoke-${user.invite_id}`,
                () => userAdministrationApi.revokeInvite(user.invite_id!),
              )
            }
          >
            {t("users.revokeInvite")}
          </ConfirmActionButton>
        )}
        {canToggleLifecycle(user) && (
          user.active ? (
            <ConfirmActionButton
              ariaLabel={`${t("users.removeUser")} ${user.display_name}`}
              icon={<UserX data-icon="inline-start" />}
              disabled={pendingAction === `user-${user.actor_id}`}
              confirmTitle={t("admin.destructiveConfirmTitle", {
                action: t("users.removeUser"),
              })}
              confirmDescription={t("admin.destructiveConfirmDescription", {
                target: user.display_name,
              })}
              confirmLabel={t("users.removeUser")}
              cancelLabel={t("admin.cancel")}
              onConfirm={() =>
                runAction(
                  `user-${user.actor_id}`,
                  () => userAdministrationApi.updateUserActive(user.actor_id, false),
                )
              }
            >
              {t("users.removeUser")}
            </ConfirmActionButton>
          ) : (
            <Button
              variant="outline"
              size="sm"
              aria-label={`${t("users.reactivate")} ${user.display_name}`}
              onClick={(event) => {
                event.stopPropagation();
                runAction(
                  `user-${user.actor_id}`,
                  () => userAdministrationApi.updateUserActive(user.actor_id, true),
                );
              }}
              disabled={pendingAction === `user-${user.actor_id}`}
            >
              <RotateCcw data-icon="inline-start" />
              {t("users.reactivate")}
            </Button>
          )
        )}
      </div>
    );
  }

  return (
    <section className="flex flex-col gap-5">
      {detail ? (
        loading ? (
          <LoadingState
            title={t("users.loadingTitle")}
          />
        ) : loadError ? (
          <LoadErrorState
            title={t("admin.listLoadFailed")}
            description={serverMessage(loadError, t)}
            retryLabel={t("admin.retry")}
            onRetry={() => void refreshUsers()}
          />
        ) : !selectedUser ? (
          <AdminResourceUnavailable onBack={() => onNavigate("/admin/users")} />
        ) : (
          <>
            <AdminBreadcrumb
              items={[
                { label: t("users.title"), route: "/admin/users" },
                { label: selectedUser.display_name },
              ]}
              onNavigate={onNavigate}
            />
            <div className="flex flex-wrap items-start justify-between gap-3">
              <PageHeader
                title={selectedUser.display_name}
                description={userContactLabel(selectedUser)}
              />
              {canOpenUserEditor(selectedUser) ? (
                <Button
                  variant="outline"
                  onClick={() => showUserEditor(selectedUser)}
                >
                  <Save data-icon="inline-start" />
                  {t("users.editTitle")}
                </Button>
              ) : null}
            </div>
            {actionError && (
              <Alert variant="destructive">
                <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
                <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
              </Alert>
            )}
            <Card>
              <CardHeader>
                <CardTitle>{t("admin.profileSection")}</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-4 sm:grid-cols-2">
                <div>
                  <div className="text-sm text-muted-foreground">{t("users.name")}</div>
                  <div className="font-medium">{selectedUser.display_name}</div>
                </div>
                <div>
                  <div className="text-sm text-muted-foreground">{t("users.email")}</div>
                  <div className="break-all font-medium">{selectedUser.email ?? "-"}</div>
                </div>
                <div>
                  <div className="text-sm text-muted-foreground">{t("users.systemRole")}</div>
                  <Badge variant="outline">{systemRoleLabel(selectedUser)}</Badge>
                </div>
                <div>
                  <div className="text-sm text-muted-foreground">{t("users.accountSource")}</div>
                  {sourceBadge(selectedUser)}
                </div>
                <div>
                  <div className="text-sm text-muted-foreground">{t("users.status")}</div>
                  <StatusBadge
                    semantic={selectedUser.active ? "success" : "inactive"}
                    label={selectedUser.active ? t("users.active") : t("users.inactive")}
                  />
                </div>
                <div>
                  <div className="text-sm text-muted-foreground">{t("users.inviteStatus")}</div>
                  <StatusBadge
                    semantic={
                      selectedUser.invite_status === "accepted"
                        ? "success"
                        : selectedUser.invite_status === "pending"
                          ? "progress"
                          : selectedUser.invite_status
                            ? "inactive"
                            : "unknown"
                    }
                    label={
                      selectedUser.invite_status
                        ? t(`admin.inviteStatus.${selectedUser.invite_status}`)
                        : t("users.notInvited")
                    }
                  />
                </div>
                <div className="sm:col-span-2">{userActions(selectedUser)}</div>
              </CardContent>
            </Card>
            {selectedUser.directory_profile ? (
              <Card>
                <CardHeader>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <CardTitle>{t("users.directoryProfile")}</CardTitle>
                      <div className="mt-1 text-sm text-muted-foreground">
                        {selectedUser.directory_profile.connection_display_name}
                      </div>
                    </div>
                    <Button
                      variant="outline"
                      disabled={pendingAction === `directory-refresh-${selectedUser.actor_id}`}
                      onClick={() => void refreshDirectoryProfile(selectedUser)}
                    >
                      {pendingAction === `directory-refresh-${selectedUser.actor_id}` ? (
                        <Spinner data-icon="inline-start" />
                      ) : (
                        <RefreshCw data-icon="inline-start" />
                      )}
                      {t("users.refreshDirectoryProfile")}
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="grid gap-4 sm:grid-cols-2">
                  <ProfileValue label={t("users.directoryUsername")} value={selectedUser.directory_profile.username} />
                  <ProfileValue label={t("users.email")} value={selectedUser.directory_profile.email} />
                  <ProfileValue label={t("users.department")} value={selectedUser.directory_profile.department} />
                  <ProfileValue label={t("users.jobTitle")} value={selectedUser.directory_profile.title} />
                  <ProfileValue label={t("users.employeeId")} value={selectedUser.directory_profile.employee_id} />
                  <ProfileValue label={t("users.lastRefreshed")} value={new Date(selectedUser.directory_profile.last_refreshed_at).toLocaleString()} />
                  <div className="sm:col-span-2">
                    <div className="text-sm text-muted-foreground">{t("users.groups")}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {selectedUser.directory_profile.groups.length > 0
                        ? selectedUser.directory_profile.groups.map((group) => <Badge key={group} variant="outline">{group}</Badge>)
                        : "-"}
                    </div>
                  </div>
                </CardContent>
              </Card>
            ) : null}
          </>
        )
      ) : (
      <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title={t("users.title")} />
        <Button onClick={openInviteDialog}>
          <UserPlus data-icon="inline-start" />
          {t("admin.createInvite")}
        </Button>
      </div>
      <DirectoryUserImportFeature
        onImported={refreshImportedUsers}
        onNotice={onNotice}
      />
      <Card>
        <CardHeader>
          <CardTitle>{t("users.filters")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field>
              <FieldLabel htmlFor="user-filter-query">{t("users.search")}</FieldLabel>
              <Input id="user-filter-query" value={filterQuery} onChange={(event) => setFilterQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") applyFilters(); }} />
            </Field>
            <Field>
              <FieldLabel htmlFor="user-filter-source">{t("users.accountSource")}</FieldLabel>
              <Select value={filterSource} onValueChange={(value) => setFilterSource(value as typeof filterSource)}>
                <SelectTrigger id="user-filter-source" className="w-full"><SelectValue placeholder={t("users.allSources")} /></SelectTrigger>
                <SelectContent><SelectGroup><SelectItem value="local">{t("users.source.local")}</SelectItem><SelectItem value="directory">{t("users.source.directory")}</SelectItem></SelectGroup></SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="user-filter-active">{t("users.status")}</FieldLabel>
              <Select value={filterActive} onValueChange={(value) => setFilterActive(value as typeof filterActive)}>
                <SelectTrigger id="user-filter-active" className="w-full"><SelectValue placeholder={t("users.allStatuses")} /></SelectTrigger>
                <SelectContent><SelectGroup><SelectItem value="true">{t("users.active")}</SelectItem><SelectItem value="false">{t("users.inactive")}</SelectItem></SelectGroup></SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="user-filter-profile-status">{t("users.directoryProfileStatus")}</FieldLabel>
              <Select value={filterProfileStatus} onValueChange={(value) => setFilterProfileStatus(value as typeof filterProfileStatus)}>
                <SelectTrigger id="user-filter-profile-status" className="w-full"><SelectValue placeholder={t("users.allStatuses")} /></SelectTrigger>
                <SelectContent><SelectGroup>{(["current", "stale", "missing", "disabled"] as const).map((status) => <SelectItem key={status} value={status}>{t(`users.directoryStatus.${status}`)}</SelectItem>)}</SelectGroup></SelectContent>
              </Select>
            </Field>
            <FilterTextField id="user-filter-connection" label={t("users.directoryConnectionId")} value={filterConnectionId} onChange={setFilterConnectionId} />
            <FilterTextField id="user-filter-group" label={t("users.group")} value={filterGroup} onChange={setFilterGroup} />
            <FilterTextField id="user-filter-department" label={t("users.department")} value={filterDepartment} onChange={setFilterDepartment} />
            <FilterTextField id="user-filter-title" label={t("users.jobTitle")} value={filterTitle} onChange={setFilterTitle} />
            <FilterTextField id="user-filter-employee" label={t("users.employeeId")} value={filterEmployeeId} onChange={setFilterEmployeeId} />
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" onClick={clearFilters}><FilterX data-icon="inline-start" />{t("users.clearFilters")}</Button>
            <Button onClick={applyFilters}><Search data-icon="inline-start" />{t("users.applyFilters")}</Button>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-6">
            {loading ? (
              <LoadingState
                title={t("users.loadingTitle")}
              />
            ) : loadError ? (
              <LoadErrorState
                title={t("admin.listLoadFailed")}
                description={serverMessage(loadError, t)}
                retryLabel={t("admin.retry")}
                onRetry={() => void refreshUsers()}
              />
            ) : users.length === 0 ? (
              <Empty className="border">
                <EmptyHeader>
                  <EmptyTitle>{Object.keys(appliedFilters).length > 0 ? t("users.noFilterMatchesTitle") : t("users.emptyTitle")}</EmptyTitle>
                  <EmptyDescription>{Object.keys(appliedFilters).length > 0 ? t("users.noFilterMatchesDescription") : t("users.emptyDescription")}</EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : isMobile ? (
              <div className="grid gap-3">
                {users.map((user) => (
                  <div
                    key={user.actor_id}
                    className={
                      user.actor_type === "user"
                        ? `${clickableCardClassName} p-3`
                        : "rounded-md border p-3"
                    }
                    role={user.actor_type === "user" ? "button" : undefined}
                    tabIndex={user.actor_type === "user" ? 0 : undefined}
                    aria-label={user.actor_type === "user" ? user.display_name : undefined}
                    onClick={user.actor_type === "user" ? () => openUserEditor(user) : undefined}
                    onKeyDown={
                      user.actor_type === "user"
                        ? (event) => activateOnEnterOrSpace(event, () => openUserEditor(user))
                        : undefined
                    }
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-medium">{user.display_name}</div>
                        <div className="break-all text-xs text-muted-foreground">
                          {userContactLabel(user)}
                        </div>
                      </div>
                      <Badge variant="outline">{systemRoleLabel(user)}</Badge>
                    </div>
                    <div className="mt-3 grid gap-2 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-muted-foreground">{t("users.accountSource")}</span>
                        {sourceBadge(user)}
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-muted-foreground">{t("users.status")}</span>
                        <StatusBadge
                          semantic={user.active ? "success" : "inactive"}
                          label={user.active ? t("users.active") : t("users.inactive")}
                        />
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-muted-foreground">{t("users.inviteStatus")}</span>
                        <StatusBadge
                          semantic={
                            user.invite_status === "accepted"
                              ? "success"
                              : user.invite_status === "pending"
                                ? "progress"
                                : user.invite_status
                                  ? "inactive"
                                  : "unknown"
                          }
                          label={
                            user.invite_status
                              ? t(`admin.inviteStatus.${user.invite_status}`)
                              : t("users.notInvited")
                          }
                        />
                      </div>
                    </div>
                    <div className="mt-3">{userActions(user)}</div>
                  </div>
                ))}
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("users.name")}</TableHead>
                    <TableHead>{t("users.email")}</TableHead>
                    <TableHead>{t("users.accountSource")}</TableHead>
                    <TableHead>{t("users.systemRole")}</TableHead>
                    <TableHead>{t("users.status")}</TableHead>
                    <TableHead>{t("users.inviteStatus")}</TableHead>
                    <TableHead>{t("users.action")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {users.map((user) => (
                    <TableRow
                      key={user.actor_id}
                      className={user.actor_type === "user" ? clickableSurfaceClassName : undefined}
                      role={user.actor_type === "user" ? "button" : undefined}
                      tabIndex={user.actor_type === "user" ? 0 : undefined}
                      aria-label={user.actor_type === "user" ? user.display_name : undefined}
                      onClick={user.actor_type === "user" ? () => openUserEditor(user) : undefined}
                      onKeyDown={
                        user.actor_type === "user"
                          ? (event) => activateOnEnterOrSpace(event, () => openUserEditor(user))
                          : undefined
                      }
                    >
                      <TableCell>{user.display_name}</TableCell>
                      <TableCell>{userContactLabel(user)}</TableCell>
                      <TableCell>{sourceBadge(user)}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{systemRoleLabel(user)}</Badge>
                      </TableCell>
                      <TableCell>
                        <StatusBadge
                          semantic={user.active ? "success" : "inactive"}
                          label={user.active ? t("users.active") : t("users.inactive")}
                        />
                      </TableCell>
                      <TableCell>
                        <StatusBadge
                          semantic={
                            user.invite_status === "accepted"
                              ? "success"
                              : user.invite_status === "pending"
                                ? "progress"
                                : user.invite_status
                                  ? "inactive"
                                  : "unknown"
                          }
                          label={
                            user.invite_status
                              ? t(`admin.inviteStatus.${user.invite_status}`)
                              : t("users.notInvited")
                          }
                        />
                      </TableCell>
                      <TableCell>
                        {userActions(user)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </>
      )}
      <Dialog
        open={showInviteForm}
        onOpenChange={(open) => {
          if (open) {
            openInviteDialog();
          } else {
            closeInviteDialog();
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("users.inviteUser")}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("users.manageDescription")}
            </DialogDescription>
          </DialogHeader>
          <FieldSet>
            <FieldLegend className="sr-only">{t("users.inviteUser")}</FieldLegend>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="invite-display-name">{t("admin.memberName")}</FieldLabel>
                <Input
                  id="invite-display-name"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="invite-email">{t("admin.memberEmail")}</FieldLabel>
                <Input
                  id="invite-email"
                  value={email}
                  onChange={(event) => {
                    setEmail(event.target.value);
                    setInviteLink("");
                  }}
                />
              </Field>
            </FieldGroup>
          </FieldSet>
          {inviteLink && (
            <Alert>
              <Clipboard />
              <AlertTitle>{t("admin.inviteReady")}</AlertTitle>
              <AlertDescription className="flex flex-col gap-3">
                <span>{t("admin.inviteReadyDescription")}</span>
                <Input
                  aria-label={t("admin.inviteAcceptanceLink")}
                  readOnly
                  value={`${window.location.origin}${inviteLink}`}
                />
                <Button variant="outline" size="sm" onClick={copyInviteLink}>
                  <Clipboard data-icon="inline-start" />
                  {t("admin.copyInvite")}
                </Button>
              </AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={closeInviteDialog}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() =>
                runAction("invite", () => {
                  const canonicalDisplayName = displayName.trim();
                  const canonicalEmail = email.trim();
                  const operation = retainClientRequestId(
                    createInviteOperation.current,
                    "invite-create",
                    JSON.stringify([canonicalDisplayName, canonicalEmail]),
                  );
                  createInviteOperation.current = operation;
                  return userAdministrationApi.createInvite(
                    canonicalDisplayName,
                    canonicalEmail,
                    undefined,
                    operation.idempotencyKey,
                  ).then((result) => {
                    createInviteOperation.current = null;
                    return result;
                  });
                })
              }
              disabled={pendingAction === "invite" || !canCreateInvite}
            >
              {pendingAction === "invite" ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <UserPlus data-icon="inline-start" />
              )}
              {t("admin.createInvite")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={showEditUser}
        onOpenChange={(open) => {
          if (!open) closeUserEditor();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("users.editTitle")}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("users.manageDescription")}
            </DialogDescription>
          </DialogHeader>
          <FieldSet>
            <FieldLegend className="sr-only">{t("users.editTitle")}</FieldLegend>
            <FieldGroup>
              <Field>
                <FieldTitle>{t("users.humanUser")}</FieldTitle>
                <div className="rounded-md border bg-muted/50 px-3 py-2">
                  <div className="font-medium">{selectedUser?.display_name ?? "-"}</div>
                  <div className="text-sm text-muted-foreground">
                    {selectedUser ? userContactLabel(selectedUser) : "-"}
                  </div>
                </div>
              </Field>
              {selectedUser && canEditDisplayName(selectedUser) ? (
                <Field>
                  <FieldLabel htmlFor="edit-user-name">{t("users.name")}</FieldLabel>
                  <Input
                    id="edit-user-name"
                    value={editDisplayName}
                    onChange={(event) => setEditDisplayName(event.target.value)}
                    disabled={pendingAction === `user-details-${selectedUser.actor_id}`}
                  />
                </Field>
              ) : null}
              {selectedUser && canEditSystemRole(selectedUser) ? (
                <Field>
                  <FieldLabel htmlFor="edit-user-system-role">{t("users.systemRole")}</FieldLabel>
                  <OptionSelect<EditableSystemRole>
                    id="edit-user-system-role"
                    value={editSystemRole}
                    options={
                      [
                        { value: "user", label: t("users.role.user") },
                        { value: "admin", label: t("users.role.admin") },
                      ] satisfies OptionSelectItem<EditableSystemRole>[]
                    }
                    onValueChange={setEditSystemRole}
                    disabled={pendingAction === `user-details-${selectedUser.actor_id}`}
                  />
                  <FieldDescription>{systemRoleDescription(selectedUser)}</FieldDescription>
                </Field>
              ) : selectedUser ? (
                <Field>
                  <FieldTitle>{t("users.systemRole")}</FieldTitle>
                  <Badge variant="outline">{systemRoleLabel(selectedUser)}</Badge>
                  <FieldDescription>{systemRoleDescription(selectedUser)}</FieldDescription>
                </Field>
              ) : null}
            </FieldGroup>
          </FieldSet>
          <DialogFooter>
            <Button variant="outline" onClick={closeUserEditor}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() => {
                if (!selectedUser) return;
                const updates = {
                  ...(displayNameChanged ? { displayName: trimmedEditDisplayName } : {}),
                  ...(systemRoleChanged ? { systemRole: editSystemRole } : {}),
                };
                void runAction(
                  `user-details-${selectedUser.actor_id}`,
                  () => userAdministrationApi.updateUserDetails(selectedUser.actor_id, updates),
                  closeUserEditor,
                  true,
                );
              }}
              disabled={
                !selectedUser ||
                !canSaveUser ||
                pendingAction === `user-details-${selectedUser.actor_id}`
              }
            >
              {selectedUser &&
              pendingAction === `user-details-${selectedUser.actor_id}` ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <Save data-icon="inline-start" />
              )}
              {t("users.saveUser")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function ProfileValue({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="break-all font-medium">{value ?? "-"}</div>
    </div>
  );
}

function FilterTextField({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input id={id} value={value} onChange={(event) => onChange(event.target.value)} />
    </Field>
  );
}
