import { Copy, FileText, MailPlus, ShieldCheck, Trash2, UserRoundPlus, UsersRound } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "../../components/ui/empty";
import { Field, FieldGroup, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { useIsMobile } from "../../hooks/use-mobile";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { OptionSelect, type OptionSelectItem } from "../../shared/OptionSelect";
import {
  AdminBreadcrumb,
  AdminResourceUnavailable,
} from "../../shared/admin-detail";
import {
  ConfirmActionButton,
  LoadErrorState,
  LoadingState,
  PageHeader,
  clickableCardClassName,
  serverMessage,
} from "../../shared/product-ui";
import {
  adminTeamDetailRoute,
  documentLibraryDestination,
  type AppDestination,
  type AppRouteMatch,
} from "../../shared/routes";
import type { TeamScopeRole } from "../../shared/identity-access-contracts";
import type { MessageReference } from "../../shared/user-messages";
import { userAdministrationApi } from "../user-administration/index";
import { teamAdministrationApi } from "./api";
import type { TeamMemberCandidate, TeamMemberSummary, TeamRecord } from "./types";

const roleOptions: OptionSelectItem<TeamScopeRole>[] = [
  { value: "member", label: "member" },
  { value: "uploader", label: "uploader" },
  { value: "admin", label: "admin" },
];

export function ScopedTeamAdministrationFeature({
  detail,
  onNavigate,
  onNotice,
  onRefresh,
}: {
  detail: Extract<AppRouteMatch, { kind: "admin-team-detail" }> | null;
  onNavigate: (route: AppDestination) => void;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [teams, setTeams] = useState<TeamRecord[]>([]);
  const [selectedTeamId, setSelectedTeamId] = useState("");
  const [members, setMembers] = useState<TeamMemberSummary[]>([]);
  const [candidates, setCandidates] = useState<TeamMemberCandidate[]>([]);
  const [candidateId, setCandidateId] = useState("");
  const [candidateRole, setCandidateRole] = useState<TeamScopeRole>("member");
  const [inviteName, setInviteName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<TeamScopeRole>("member");
  const [inviteUrl, setInviteUrl] = useState("");
  const [showAddMember, setShowAddMember] = useState(false);
  const [showInvite, setShowInvite] = useState(false);
  const [loading, setLoading] = useState(true);
  const [membersLoading, setMembersLoading] = useState(false);
  const [membersLoadError, setMembersLoadError] = useState("");
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [candidatesLoadError, setCandidatesLoadError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const selectedTeamIdRef = useRef("");
  const membersRequestIdRef = useRef(0);
  const candidatesRequestIdRef = useRef(0);

  const selectedTeam =
    detail && detail.teamId === selectedTeamId
      ? teams.find((team) => team.team_id === selectedTeamId) ?? null
      : null;
  const candidateOptions = candidates.map((candidate) => ({
    value: candidate.subject_id,
    label: candidate.display_detail
      ? `${candidate.display_name} · ${candidate.display_detail}`
      : candidate.display_name,
  }));
  const humanAdmins = useMemo(
    () => members.filter((member) => member.subject_type === "user" && member.role === "admin"),
    [members],
  );

  useEffect(() => {
    void refreshTeams();
  }, []);

  useEffect(() => {
    selectedTeamIdRef.current = selectedTeamId;
    membersRequestIdRef.current += 1;
    candidatesRequestIdRef.current += 1;
    setMembers([]);
    setCandidates([]);
    setCandidateId("");
    if (selectedTeamId) {
      void refreshMembers(selectedTeamId);
      void refreshCandidates(selectedTeamId);
    }
  }, [selectedTeamId]);

  async function refreshTeams() {
    setLoading(true);
    setLoadError("");
    try {
      const result = await teamAdministrationApi.listTeams();
      const activeTeams = result.teams.filter((team) => team.status === "active");
      setTeams(activeTeams);
      setSelectedTeamId(
        detail && activeTeams.some((team) => team.team_id === detail.teamId)
          ? detail.teamId
          : "",
      );
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : t("admin.listLoadFailed"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!detail) {
      setSelectedTeamId("");
      return;
    }
    if (teams.some((team) => team.team_id === detail.teamId)) {
      setSelectedTeamId(detail.teamId);
    } else {
      setSelectedTeamId("");
    }
  }, [detail?.teamId, teams]);

  async function refreshMembers(teamId: string) {
    const requestId = ++membersRequestIdRef.current;
    setMembersLoading(true);
    setMembersLoadError("");
    try {
      const memberResult = await teamAdministrationApi.listTeamMembers(teamId);
      if (requestId !== membersRequestIdRef.current || selectedTeamIdRef.current !== teamId) return;
      setMembers(memberResult.members);
    } catch (error) {
      if (requestId !== membersRequestIdRef.current || selectedTeamIdRef.current !== teamId) return;
      setMembersLoadError(error instanceof Error ? error.message : t("admin.listLoadFailed"));
      setMembers([]);
    } finally {
      if (requestId === membersRequestIdRef.current && selectedTeamIdRef.current === teamId) {
        setMembersLoading(false);
      }
    }
  }

  async function refreshCandidates(teamId: string) {
    const requestId = ++candidatesRequestIdRef.current;
    setCandidatesLoading(true);
    setCandidatesLoadError("");
    try {
      const result = await teamAdministrationApi.listTeamMemberCandidates(teamId);
      if (
        requestId !== candidatesRequestIdRef.current ||
        selectedTeamIdRef.current !== teamId
      ) return;
      setCandidates(result.users);
      setCandidateId((current) =>
        result.users.some((candidate) => candidate.subject_id === current)
          ? current
          : result.users[0]?.subject_id ?? "",
      );
    } catch (error) {
      if (
        requestId !== candidatesRequestIdRef.current ||
        selectedTeamIdRef.current !== teamId
      ) return;
      setCandidatesLoadError(error instanceof Error ? error.message : t("admin.listLoadFailed"));
      setCandidates([]);
      setCandidateId("");
    } finally {
      if (
        requestId === candidatesRequestIdRef.current &&
        selectedTeamIdRef.current === teamId
      ) {
        setCandidatesLoading(false);
      }
    }
  }

  async function runAction(
    actionName: string,
    action: () => Promise<MessageReference>,
    onSuccess?: () => void,
  ) {
    if (!selectedTeamId) return;
    setPendingAction(actionName);
    setActionError("");
    try {
      const result = await action();
      onNotice(result.message_code);
      toast.success(serverMessage(result, t));
      await onRefresh();
      if (
        window.location.pathname ===
        adminTeamDetailRoute(selectedTeamId, "members")
      ) {
        await Promise.all([refreshMembers(selectedTeamId), refreshCandidates(selectedTeamId)]);
      }
      onSuccess?.();
    } catch (error) {
      const message = error instanceof Error ? error.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  async function createScopedInvite() {
    if (!selectedTeamId || !inviteName.trim() || !inviteEmail.trim()) return;
    setPendingAction("invite");
    setActionError("");
    try {
      const result = await userAdministrationApi.createInvite(
        inviteName.trim(),
        inviteEmail.trim(),
        {
          scopeType: "team",
          scopeId: selectedTeamId,
          scopeRole: inviteRole,
        },
      );
      setInviteUrl(result.local_pilot_acceptance?.acceptance_url ?? "");
      setInviteName("");
      setInviteEmail("");
      onNotice(result.message_code);
      toast.success(serverMessage(result, t));
    } catch (error) {
      const message = error instanceof Error ? error.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  function memberRoleControl(member: TeamMemberSummary) {
    const isReadOnly = member.subject_type === "service_account";
    const isLastHumanAdmin = member.role === "admin" && humanAdmins.length <= 1;
    if (isReadOnly || isLastHumanAdmin) {
      return <span className="text-sm">{member.role}</span>;
    }
    return (
      <>
        <label
          htmlFor={`team-member-role-${member.membership_id}`}
          className="sr-only"
        >
          {t("teams.changeMemberRole", { name: member.display_name })}
        </label>
        <OptionSelect
          id={`team-member-role-${member.membership_id}`}
          value={member.role}
          options={roleOptions}
          disabled={pendingAction === `role-${member.membership_id}`}
          onValueChange={(role) =>
            void runAction(`role-${member.membership_id}`, () =>
              teamAdministrationApi.addTeamMember(
                selectedTeamId,
                "user",
                member.subject_id,
                role,
              ),
            )
          }
        />
      </>
    );
  }

  function memberAction(member: TeamMemberSummary) {
    const isReadOnly = member.subject_type === "service_account";
    const isLastHumanAdmin = member.role === "admin" && humanAdmins.length <= 1;
    if (isReadOnly) {
      return <span className="text-sm text-muted-foreground">{t("teams.readOnlyMember")}</span>;
    }
    if (isLastHumanAdmin) {
      return <span className="text-sm text-muted-foreground">{t("teams.requiredAdmin")}</span>;
    }
    return (
      <ConfirmActionButton
        ariaLabel={t("teams.removeNamedMember", { name: member.display_name })}
        icon={<Trash2 data-icon="inline-start" />}
        disabled={pendingAction === `remove-${member.membership_id}`}
        confirmTitle={t("admin.destructiveConfirmTitle", {
          action: t("teams.removeMember"),
        })}
        confirmDescription={t("admin.destructiveConfirmDescription", {
          target: member.display_name,
        })}
        confirmLabel={t("teams.removeMember")}
        cancelLabel={t("admin.cancel")}
        onConfirm={() =>
          runAction(`remove-${member.membership_id}`, () =>
            teamAdministrationApi.removeTeamMember(
              selectedTeamId,
              member.membership_id,
            ),
          )
        }
      >
        {t("teams.removeMember")}
      </ConfirmActionButton>
    );
  }

  if (loading) {
    return <LoadingState title={t("teams.scopedLoadingTitle")} />;
  }
  if (loadError) {
    return (
      <LoadErrorState
        title={t("admin.listLoadFailed")}
        description={serverMessage(loadError, t)}
        retryLabel={t("admin.retry")}
        onRetry={() => void refreshTeams()}
      />
    );
  }

  if (!detail) {
    return (
      <section className="flex flex-col gap-5">
        <PageHeader title={t("teams.scopedTitle")} description={t("teams.scopedDescription")} />
        {teams.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon"><UsersRound /></EmptyMedia>
              <EmptyTitle>{t("teams.scopedEmptyTitle")}</EmptyTitle>
              <EmptyDescription>{t("teams.scopedEmptyDescription")}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {teams.map((team) => (
              <button
                key={team.team_id}
                type="button"
                className={`${clickableCardClassName} p-4 text-left`}
                onClick={() => onNavigate(adminTeamDetailRoute(team.team_id, "members"))}
              >
                <div className="flex items-center gap-2 font-medium">
                  <UsersRound />
                  <span className="truncate">{team.name}</span>
                </div>
                <div className="mt-2 text-sm text-muted-foreground">
                  {t("admin.membersSection")}
                </div>
              </button>
            ))}
          </div>
        )}
      </section>
    );
  }

  if (!selectedTeam) {
    return <AdminResourceUnavailable onBack={() => onNavigate("/admin/teams")} />;
  }

  return (
    <section className="flex flex-col gap-5">
      <AdminBreadcrumb
        items={[
          { label: t("teams.title"), route: "/admin/teams" },
          { label: selectedTeam.name },
        ]}
        onNavigate={onNavigate}
      />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader
          title={selectedTeam.name}
          description={t("teams.membersForTeamDescription", { team: selectedTeam.name })}
        />
        <Button
          variant="outline"
          onClick={() =>
            onNavigate(documentLibraryDestination("team", selectedTeam.team_id))
          }
        >
          <FileText data-icon="inline-start" />
          {t("documentLibrary.manageTargetDocuments")}
        </Button>
      </div>

      {actionError && (
        <Alert variant="destructive">
          <ShieldCheck />
          <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
          <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
        </Alert>
      )}

      <>
          <div className="flex flex-col gap-4">
            <Card>
              <CardHeader>
                <CardTitle>{t("teams.membersTitle")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-5">
                {membersLoading ? (
                  <LoadingState title={t("teams.membersLoadingTitle")} />
                ) : membersLoadError ? (
                  <LoadErrorState
                    title={t("admin.listLoadFailed")}
                    description={serverMessage(membersLoadError, t)}
                    retryLabel={t("admin.retry")}
                    onRetry={() => void refreshMembers(selectedTeamId)}
                  />
                ) : members.length === 0 ? (
                  <Empty className="border">
                    <EmptyHeader>
                      <EmptyTitle>{t("teams.noMembersTitle")}</EmptyTitle>
                      <EmptyDescription>{t("teams.noMembersDescription")}</EmptyDescription>
                    </EmptyHeader>
                  </Empty>
                ) : isMobile ? (
                  <div className="grid gap-3">
                    {members.map((member) => (
                      <div
                        key={member.membership_id}
                        className="grid gap-3 rounded-md border p-3"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <div className="font-medium">{member.display_name}</div>
                            {member.display_detail && (
                              <div className="text-xs text-muted-foreground">
                                {member.display_detail}
                              </div>
                            )}
                          </div>
                          <Badge variant="secondary">
                            {member.subject_type === "service_account"
                              ? t("users.serviceAccount")
                              : t("users.humanUser")}
                          </Badge>
                        </div>
                        <div className="grid gap-1">
                          <div className="text-xs text-muted-foreground">
                            {t("settings.role")}
                          </div>
                          {memberRoleControl(member)}
                        </div>
                        <div>{memberAction(member)}</div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="overflow-x-auto rounded-md border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>{t("teams.member")}</TableHead>
                          <TableHead>{t("teams.memberType")}</TableHead>
                          <TableHead>{t("settings.role")}</TableHead>
                          <TableHead className="text-right">{t("documentLibrary.actions")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {members.map((member) => {
                          const isReadOnly = member.subject_type === "service_account";
                          return (
                            <TableRow key={member.membership_id}>
                              <TableCell>
                                <div className="font-medium">{member.display_name}</div>
                                {member.display_detail && (
                                  <div className="text-xs text-muted-foreground">{member.display_detail}</div>
                                )}
                              </TableCell>
                              <TableCell>
                                <Badge variant="secondary">
                                  {isReadOnly ? t("users.serviceAccount") : t("users.humanUser")}
                                </Badge>
                              </TableCell>
                              <TableCell className="min-w-36">{memberRoleControl(member)}</TableCell>
                              <TableCell className="text-right">{memberAction(member)}</TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>
                )}

              </CardContent>
            </Card>
            <div className="flex flex-wrap gap-3">
              <Button
                onClick={() => {
                  setCandidateRole("member");
                  setActionError("");
                  setShowAddMember(true);
                }}
              >
                <UserRoundPlus data-icon="inline-start" />
                {t("teams.addMember")}
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setInviteName("");
                  setInviteEmail("");
                  setInviteRole("member");
                  setInviteUrl("");
                  setActionError("");
                  setShowInvite(true);
                }}
              >
                <MailPlus data-icon="inline-start" />
                {t("teams.createInvite")}
              </Button>
            </div>
          </div>
        </>
      <Dialog open={showAddMember} onOpenChange={setShowAddMember}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("teams.addExistingMember")}</DialogTitle>
            <DialogDescription>
              {t("teams.membersForTeamDescription", { team: selectedTeam.name })}
            </DialogDescription>
          </DialogHeader>
          {actionError && (
            <Alert variant="destructive">
              <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
              <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
            </Alert>
          )}
          {candidatesLoading ? (
            <LoadingState title={t("teams.membersLoadingTitle")} />
          ) : candidatesLoadError ? (
            <LoadErrorState
              title={t("admin.listLoadFailed")}
              description={serverMessage(candidatesLoadError, t)}
              retryLabel={t("admin.retry")}
              onRetry={() => void refreshCandidates(selectedTeamId)}
            />
          ) : candidates.length === 0 ? (
            <div className="text-sm text-muted-foreground">{t("teams.noCandidateHumans")}</div>
          ) : (
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="team-member-candidate">{t("teams.member")}</FieldLabel>
                <OptionSelect
                  id="team-member-candidate"
                  value={candidateId}
                  options={candidateOptions}
                  onValueChange={setCandidateId}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="team-member-role">{t("settings.role")}</FieldLabel>
                <OptionSelect
                  id="team-member-role"
                  value={candidateRole}
                  options={roleOptions}
                  onValueChange={setCandidateRole}
                />
              </Field>
            </FieldGroup>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowAddMember(false)}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() =>
                void runAction(
                  "add-member",
                  () =>
                    teamAdministrationApi.addTeamMember(
                      selectedTeamId,
                      "user",
                      candidateId,
                      candidateRole,
                    ),
                  () => setShowAddMember(false),
                )
              }
              disabled={!candidateId || pendingAction === "add-member"}
            >
              <UserRoundPlus data-icon="inline-start" />
              {t("teams.addMember")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={showInvite} onOpenChange={setShowInvite}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("teams.inviteTitle")}</DialogTitle>
            <DialogDescription>
              {t("teams.inviteDescription", { team: selectedTeam.name })}
            </DialogDescription>
          </DialogHeader>
          {actionError && (
            <Alert variant="destructive">
              <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
              <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
            </Alert>
          )}
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="team-invite-name">{t("users.name")}</FieldLabel>
              <Input id="team-invite-name" value={inviteName} onChange={(event) => setInviteName(event.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="team-invite-email">{t("login.email")}</FieldLabel>
              <Input id="team-invite-email" type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="team-invite-role">{t("settings.role")}</FieldLabel>
              <OptionSelect id="team-invite-role" value={inviteRole} options={roleOptions} onValueChange={setInviteRole} />
            </Field>
            {inviteUrl && (
              <div className="rounded-md border bg-muted/30 p-3">
                <div className="mb-2 text-sm font-medium">{t("teams.inviteLink")}</div>
                <div className="flex gap-2">
                  <Input readOnly value={inviteUrl} aria-label={t("teams.inviteLink")} />
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label={t("teams.copyInviteLink")}
                    onClick={() => {
                      void navigator.clipboard.writeText(inviteUrl);
                      toast.success(t("teams.inviteLinkCopied"));
                    }}
                  >
                    <Copy />
                  </Button>
                </div>
              </div>
            )}
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowInvite(false)}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() => void createScopedInvite()}
              disabled={!inviteName.trim() || !inviteEmail.trim() || pendingAction === "invite"}
            >
              <MailPlus data-icon="inline-start" />
              {t("teams.createInvite")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
