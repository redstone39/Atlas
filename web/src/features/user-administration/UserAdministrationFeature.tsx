import { Clipboard, RotateCcw, Save, UserPlus, UserX } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
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
import { Spinner } from "../../components/ui/spinner";
import { useIsMobile } from "../../hooks/use-mobile";
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
import { userAdministrationApi } from "./api";
import type { UserAdminSummary } from "./types";
import {
  adminUserDetailRoute,
  type AppRoute,
  type AppRouteMatch,
} from "../../shared/routes";

type UserAction = MessageReference & {
  local_pilot_acceptance?: { acceptance_url: string } | null;
};

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
  const [selectedUserId, setSelectedUserId] = useState("");
  const [editDisplayName, setEditDisplayName] = useState("");
  const [inviteLink, setInviteLink] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const [actionError, setActionError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

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
      return;
    }
    const routedUser = editableUsers.find((user) => user.actor_id === detail.actorId);
    if (routedUser) {
      setSelectedUserId(routedUser.actor_id);
      setEditDisplayName(routedUser.display_name);
    } else {
      setSelectedUserId("");
      setEditDisplayName("");
    }
  }, [editableUsers, detail?.actorId]);

  async function refreshUsers() {
    setLoading(true);
    setLoadError("");
    try {
      const userResult = await userAdministrationApi.listUsers();
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
      onNotice(result.message_code);
      toast.success(message);
      const refreshedUsers = await refreshUsers();
      await onRefresh();
      if (
        detail &&
        refreshedUsers &&
        !refreshedUsers.some(
          (user) => user.actor_type === "user" && user.actor_id === detail.actorId,
        )
      ) {
        window.history.replaceState({}, "", "/admin/users");
        window.dispatchEvent(new PopStateEvent("popstate"));
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
  const canSaveUser = Boolean(
    selectedUser &&
      editDisplayName.trim() &&
      editDisplayName.trim() !== selectedUser.display_name,
  );

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
    resetInviteDraft();
    setShowInviteForm(false);
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
              <Button
                variant="outline"
                onClick={() => {
                  setEditDisplayName(selectedUser.display_name);
                  setShowEditUser(true);
                }}
              >
                <Save data-icon="inline-start" />
                {t("admin.editProfile")}
              </Button>
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
                  <Badge variant="outline">{selectedUser.system_role}</Badge>
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
                  <EmptyTitle>{t("users.emptyTitle")}</EmptyTitle>
                  <EmptyDescription>{t("users.emptyDescription")}</EmptyDescription>
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
                      <Badge variant="outline">{user.system_role}</Badge>
                    </div>
                    <div className="mt-3 grid gap-2 text-sm">
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
                      <TableCell>
                        <Badge variant="outline">{user.system_role}</Badge>
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
                runAction("invite", () => userAdministrationApi.createInvite(displayName, email))
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
      <Dialog open={showEditUser} onOpenChange={setShowEditUser}>
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
              <Field>
                <FieldLabel htmlFor="edit-user-name">{t("users.name")}</FieldLabel>
                <Input
                  id="edit-user-name"
                  value={editDisplayName}
                  onChange={(event) => setEditDisplayName(event.target.value)}
                  disabled={!selectedUser}
                />
              </Field>
              <Field>
                <FieldTitle>{t("users.systemRole")}</FieldTitle>
                <Badge variant="outline">{selectedUser?.system_role ?? "-"}</Badge>
                <FieldDescription>{t("users.systemRoleLocked")}</FieldDescription>
              </Field>
            </FieldGroup>
          </FieldSet>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowEditUser(false)}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() =>
                selectedUser &&
                runAction(
                  `user-profile-${selectedUser.actor_id}`,
                  () =>
                    userAdministrationApi.updateUserProfile(
                      selectedUser.actor_id,
                      editDisplayName.trim(),
                    ),
                  () => setShowEditUser(false),
                )
              }
              disabled={
                !selectedUser ||
                !canSaveUser ||
                pendingAction === `user-profile-${selectedUser.actor_id}`
              }
            >
              <Save data-icon="inline-start" />
              {t("users.saveUser")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
