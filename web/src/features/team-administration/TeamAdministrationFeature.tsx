import { FileText, Network, Plus, Save, UserRoundPlus, UsersRound, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
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
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Spinner } from "../../components/ui/spinner";
import { useIsMobile } from "../../hooks/use-mobile";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { generatedId } from "../../shared/ids";
import {
  AdminBreadcrumb,
  AdminResourceUnavailable,
  AdminSectionNav,
} from "../../shared/admin-detail";
import { OptionSelect, type OptionSelectItem } from "../../shared/OptionSelect";
import { SearchSelect } from "../../shared/SearchSelect";
import {
  ConfirmActionButton,
  LoadErrorState,
  LoadingState,
  localizedStatusLabel,
  PageHeader,
  StatusBadge,
  TargetSummary,
  activateOnEnterOrSpace,
  clickableCardClassName,
  clickableSurfaceClassName,
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
import { agentAccessApi, type AgentUserStatus } from "../agent-access/index";
import {
  userAdministrationApi,
  type UserAdminSummary,
} from "../user-administration/index";
import { teamAdministrationApi } from "./api";
import {
  SystemTeamDirectoryImportView,
  useSystemTeamDirectoryImportController,
} from "./SystemTeamDirectoryImportController";
import type {
  TeamMembershipRecord,
  TeamRecord,
} from "./types";

type MemberOption = {
  actor_id: string;
  actor_type: "user" | "service_account";
  display_name: string;
  status: string;
};

const NO_PARENT = "__no-parent__";

const teamStatusOptions: OptionSelectItem<"active" | "retired">[] = [
  { value: "active", label: "active" },
  { value: "retired", label: "retired" },
];

const teamMemberRoleOptions: OptionSelectItem<TeamScopeRole>[] = [
  { value: "member", label: "member" },
  { value: "uploader", label: "uploader" },
  { value: "admin", label: "admin" },
];

function buildDescendantTeamIdsByTeamId(teams: TeamRecord[]) {
  const childIdsByParentId = new Map<string, string[]>();
  for (const team of teams) {
    if (!team.parent_team_id) continue;
    const childIds = childIdsByParentId.get(team.parent_team_id) ?? [];
    childIds.push(team.team_id);
    childIdsByParentId.set(team.parent_team_id, childIds);
  }

  return new Map(
    teams.map((team) => {
      const descendantIds = new Set<string>();
      const pendingIds = [...(childIdsByParentId.get(team.team_id) ?? [])];
      while (pendingIds.length > 0) {
        const childId = pendingIds.pop();
        if (!childId || descendantIds.has(childId)) continue;
        descendantIds.add(childId);
        pendingIds.push(...(childIdsByParentId.get(childId) ?? []));
      }
      return [team.team_id, descendantIds] as const;
    }),
  );
}

export function TeamAdministrationFeature({
  detail,
  onNavigate,
  onNotice,
  onRefresh,
}: {
  detail: Extract<AppRouteMatch, { kind: "admin-team-detail" }> | null;
  onNavigate: (route: AppDestination) => void;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<boolean>;
}) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const isMobile = useIsMobile();
  const [teams, setTeams] = useState<TeamRecord[]>([]);
  const [memberships, setMemberships] = useState<TeamMembershipRecord[]>([]);
  const [users, setUsers] = useState<UserAdminSummary[]>([]);
  const [agents, setAgents] = useState<AgentUserStatus[]>([]);
  const [showCreateTeam, setShowCreateTeam] = useState(false);
  const [showEditTeam, setShowEditTeam] = useState(false);
  const [showAddMembers, setShowAddMembers] = useState(false);
  const [memberDirectoryLoaded, setMemberDirectoryLoaded] = useState(false);
  const [memberDirectoryLoading, setMemberDirectoryLoading] = useState(false);
  const [memberDirectoryLoadError, setMemberDirectoryLoadError] = useState("");
  const [teamName, setTeamName] = useState("");
  const [parentTeamId, setParentTeamId] = useState("");
  const [selectedTeamId, setSelectedTeamId] = useState("");
  const [editTeamName, setEditTeamName] = useState("");
  const [editParentTeamId, setEditParentTeamId] = useState("");
  const [editTeamStatus, setEditTeamStatus] = useState<"active" | "retired">("active");
  const [editInheritParentDocuments, setEditInheritParentDocuments] = useState(true);
  const [selectedMemberIds, setSelectedMemberIds] = useState<string[]>([]);
  const [newMemberRole, setNewMemberRole] = useState<TeamScopeRole>("member");
  const [memberSearch, setMemberSearch] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const [actionError, setActionError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const teamEditorGenerationRef = useRef(0);
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  useEffect(() => {
    void refreshTeams();
  }, []);

  const teamById = useMemo(
    () => new Map(teams.map((team) => [team.team_id, team])),
    [teams],
  );
  const descendantTeamIdsByTeamId = useMemo(
    () => buildDescendantTeamIdsByTeamId(teams),
    [teams],
  );
  const selectedTeam =
    detail && detail.teamId === selectedTeamId
      ? teams.find((team) => team.team_id === selectedTeamId) ?? null
      : null;
  const directoryImport = useSystemTeamDirectoryImportController({
    teamId: selectedTeam?.team_id ?? "",
    role: newMemberRole,
    onNotice,
    onRefresh,
    onPostSuccess: async () => {
      await Promise.all([refreshTeams(), refreshMemberDirectory()]);
    },
    onClose: closeAddMembersDialog,
  });
  const memberOptions: MemberOption[] = useMemo(
    () => [
      ...users
        .filter((user) => user.actor_type === "user")
        .map((user) => ({
          actor_id: user.actor_id,
          actor_type: "user" as const,
          display_name: user.display_name,
          status: user.active ? "active" : "inactive",
        })),
      ...agents.map((agent) => ({
        actor_id: agent.actor_id,
        actor_type: "service_account" as const,
        display_name: agent.display_name,
        status: agent.status,
      })),
    ],
    [users, agents],
  );
  const memberById = useMemo(
    () => new Map(memberOptions.map((member) => [member.actor_id, member])),
    [memberOptions],
  );
  const selectedTeamMemberships = useMemo(
    () =>
      selectedTeam
        ? memberships.filter(
            (membership) =>
              membership.team_id === selectedTeam.team_id &&
              membership.status === "active",
          )
        : [],
    [memberships, selectedTeam],
  );
  const activeSelectedTeamMemberIds = useMemo(
    () =>
      new Set(
        selectedTeamMemberships
          .filter((membership) => membership.status === "active")
          .map((membership) => membership.member_actor_id),
      ),
    [selectedTeamMemberships],
  );

  useEffect(() => {
    if (teams.length === 0) {
      setSelectedTeamId("");
      setEditTeamName("");
      setEditParentTeamId("");
      setEditTeamStatus("active");
      setEditInheritParentDocuments(true);
      return;
    }
    if (detail) {
      const target = teams.find((team) => team.team_id === detail.teamId);
      if (!target) {
        setSelectedTeamId("");
        return;
      }
      if (selectedTeamId !== target.team_id) selectTeam(target);
      return;
    }
    if (!selectedTeamId || !teams.some((team) => team.team_id === selectedTeamId)) {
      selectTeam(teams[0]);
      return;
    }
  }, [detail?.teamId, teams, selectedTeamId]);

  useEffect(() => {
    if (!detail || detail.section !== "members" || !selectedTeam) return;
    if (!memberDirectoryLoaded) {
      setMemberDirectoryLoaded(true);
      void refreshMemberDirectory();
    }
  }, [detail?.section, memberDirectoryLoaded, selectedTeam?.team_id]);

  async function refreshTeams() {
    setLoading(true);
    setLoadError("");
    try {
      const teamResult = await teamAdministrationApi.listTeams();
      setTeams(teamResult.teams);
      setMemberships(teamResult.memberships);
      setParentTeamId((current) =>
        current && teamResult.teams.some((team) => team.team_id === current) ? current : "",
      );
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : t("admin.listLoadFailed"));
    } finally {
      setLoading(false);
    }
  }

  async function refreshMemberDirectory(generation = teamEditorGenerationRef.current) {
    setMemberDirectoryLoading(true);
    setMemberDirectoryLoadError("");
    try {
      const [userResult, agentResult] = await Promise.all([
        userAdministrationApi.listUsers(),
        agentAccessApi.listAgents(),
      ]);
      if (generation !== teamEditorGenerationRef.current) return;
      setUsers(userResult.users);
      setAgents(agentResult.agents);
    } catch (err) {
      if (generation !== teamEditorGenerationRef.current) return;
      setMemberDirectoryLoadError(err instanceof Error ? err.message : t("admin.listLoadFailed"));
      setUsers([]);
      setAgents([]);
    } finally {
      if (generation === teamEditorGenerationRef.current) {
        setMemberDirectoryLoading(false);
      }
    }
  }

  function selectTeam(team: TeamRecord) {
    setSelectedTeamId(team.team_id);
    setEditTeamName(team.name);
    setEditParentTeamId(team.parent_team_id ?? "");
    directoryImport.invalidateRequest();
    setShowAddMembers(false);
    setEditTeamStatus(team.status);
    setEditInheritParentDocuments(team.inherit_parent_documents);
  }

  async function runAction(
    actionName: string,
    action: () => Promise<MessageReference>,
    onSuccess?: () => void,
  ) {
    setPendingAction(actionName);
    setActionError("");
    try {
      const result = await action();
      const message = serverMessage(result, t);
      onNotice(result.message_code);
      toast.success(message);
      await refreshTeams();
      await onRefresh();
      onSuccess?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  function toggleMember(actorId: string, checked: boolean) {
    setSelectedMemberIds((current) =>
      checked ? [...new Set([...current, actorId])] : current.filter((id) => id !== actorId),
    );
  }

  const canCreateTeam = Boolean(teamName.trim());
  const canSaveTeam = Boolean(
    selectedTeam &&
      editTeamName.trim() &&
      (editTeamName.trim() !== selectedTeam.name ||
        editParentTeamId !== (selectedTeam.parent_team_id ?? "") ||
        editTeamStatus !== selectedTeam.status ||
        editInheritParentDocuments !== selectedTeam.inherit_parent_documents),
  );
  const availableMemberOptions = memberOptions.filter(
    (member) => !activeSelectedTeamMemberIds.has(member.actor_id),
  );
  const selectedMembers = availableMemberOptions.filter((member) =>
    selectedMemberIds.includes(member.actor_id),
  );
  const memberSearchQuery = memberSearch.trim().toLowerCase();
  const visibleMemberOptions = availableMemberOptions.filter((member) => {
    if (!memberSearchQuery) return true;
    return `${member.display_name} ${member.actor_id} ${member.actor_type}`
      .toLowerCase()
      .includes(memberSearchQuery);
  });
  const memberSelectionEmptyTitle = memberSearchQuery
    ? t("teams.noMatchingMembers")
    : t("teams.noAvailableMembers");
  const memberSelectionEmptyDescription = memberSearchQuery
    ? t("teams.noMatchingMembersDescription")
    : t("teams.noAvailableMembersDescription");
  const memberTypeLabel = (type: MemberOption["actor_type"]) =>
    type === "service_account" ? t("users.serviceAccount") : t("users.humanUser");
  const teamOptions = teams.map((team) => ({
    value: team.team_id,
    label: team.name,
    description: team.status,
  }));
  const parentTeamOptions = [
    { value: NO_PARENT, label: t("teams.noParent") },
    ...teamOptions,
  ];
  const editParentTeamOptions = [
    { value: NO_PARENT, label: t("teams.noParent") },
    ...teams
      .filter((team) => {
        if (!selectedTeam) return true;
        const descendantTeamIds =
          descendantTeamIdsByTeamId.get(selectedTeam.team_id) ?? new Set<string>();
        return team.team_id !== selectedTeam.team_id && !descendantTeamIds.has(team.team_id);
      })
      .map((team) => ({
        value: team.team_id,
        label: team.name,
        description: team.status,
      })),
  ];
  const canAddMembers = Boolean(selectedTeam && selectedMembers.length > 0);

  function resetCreateTeamDraft() {
    setTeamName("");
    setParentTeamId("");
  }

  function openCreateTeamDialog() {
    resetCreateTeamDraft();
    setShowCreateTeam(true);
  }

  function closeCreateTeamDialog() {
    resetCreateTeamDraft();
    setShowCreateTeam(false);
  }

  function handleCreateTeamOpenChange(open: boolean) {
    if (open) {
      openCreateTeamDialog();
      return;
    }
    closeCreateTeamDialog();
  }

  function resetAddMembersDraft() {
    directoryImport.reset();
    setSelectedMemberIds([]);
    setMemberSearch("");
    setNewMemberRole("member");
  }

  function openAddMembersDialog() {
    if (!selectedTeam) return;
    resetAddMembersDraft();
    setActionError("");
    setShowAddMembers(true);
  }

  function closeAddMembersDialog() {
    resetAddMembersDraft();
    setShowAddMembers(false);
  }

  function openTeamEditor(team: TeamRecord) {
    onNavigate(adminTeamDetailRoute(team.team_id, "profile"));
  }

  function openTeamProfileEditor(team: TeamRecord) {
    teamEditorGenerationRef.current += 1;
    selectTeam(team);
    setShowEditTeam(true);
  }

  function closeTeamEditor() {
    teamEditorGenerationRef.current += 1;
    setShowEditTeam(false);
  }

  function teamActions(team: TeamRecord) {
    return (
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={(event) => {
            event.stopPropagation();
            onNavigate(adminTeamDetailRoute(team.team_id, "profile"));
          }}
        >
          {t("admin.edit")}
        </Button>
      </div>
    );
  }

  function memberRoleControl(membership: TeamMembershipRecord) {
    const memberLabel =
      memberById.get(membership.member_actor_id)?.display_name ?? t("teams.unknownMember");
    return (
      <>
        <label
          htmlFor={`system-team-member-role-${membership.membership_id}`}
          className="sr-only"
        >
          {t("teams.changeMemberRole", { name: memberLabel })}
        </label>
        <OptionSelect
          id={`system-team-member-role-${membership.membership_id}`}
          value={membership.role}
          options={teamMemberRoleOptions}
          disabled={pendingAction === `role-${membership.membership_id}`}
          onValueChange={(role) =>
            void runAction(`role-${membership.membership_id}`, () =>
              teamAdministrationApi.addTeamMember(
                membership.team_id,
                membership.member_actor_type,
                membership.member_actor_id,
                role,
              ),
            )
          }
        />
      </>
    );
  }

  function removeMemberAction(membership: TeamMembershipRecord) {
    const memberLabel =
      memberById.get(membership.member_actor_id)?.display_name ?? t("teams.unknownMember");
    return (
      <ConfirmActionButton
        ariaLabel={`${t("teams.removeMember")} ${memberLabel}`}
        icon={<X data-icon="inline-start" />}
        disabled={
          pendingAction === `remove-${membership.membership_id}` ||
          membership.status !== "active"
        }
        confirmTitle={t("admin.destructiveConfirmTitle", {
          action: t("teams.removeMember"),
        })}
        confirmDescription={t("admin.destructiveConfirmDescription", {
          target: memberLabel,
        })}
        confirmLabel={t("teams.removeMember")}
        cancelLabel={t("admin.cancel")}
        onConfirm={() =>
          runAction(
            `remove-${membership.membership_id}`,
            () =>
              teamAdministrationApi.removeTeamMember(
                membership.team_id,
                membership.membership_id,
              ),
          )
        }
      >
        {t("teams.removeMember")}
      </ConfirmActionButton>
    );
  }

  function renderMembersSection() {
    if (memberDirectoryLoading) {
      return (
        <LoadingState
          title={t("projects.membersLoadingTitle")}
        />
      );
    }
    if (memberDirectoryLoadError) {
      return (
        <LoadErrorState
          title={t("admin.listLoadFailed")}
          description={serverMessage(memberDirectoryLoadError, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => void refreshMemberDirectory()}
        />
      );
    }
    return (
      <div className="flex flex-col gap-4">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("teams.membersTitle")}</CardTitle>
          </CardHeader>
          <CardContent>
            {selectedTeamMemberships.length === 0 ? (
              <Empty className="border">
                <EmptyHeader>
                  <EmptyTitle>{t("teams.noMembersTitle")}</EmptyTitle>
                  <EmptyDescription>{t("teams.noMembersDescription")}</EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : isMobile ? (
              <div className="grid gap-3">
                {selectedTeamMemberships.map((membership) => (
                  <div
                    key={membership.membership_id}
                    className="grid gap-3 rounded-md border p-3"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="font-medium">
                          {memberById.get(membership.member_actor_id)?.display_name ??
                            t("teams.unknownMember")}
                        </div>
                        <div className="text-sm text-muted-foreground">
                          {memberTypeLabel(membership.member_actor_type)}
                        </div>
                      </div>
                      <StatusBadge
                        semantic={membership.status === "active" ? "success" : "inactive"}
                        label={localizedStatusLabel(membership.status, t)}
                      />
                    </div>
                    <Field>
                      <FieldLabel htmlFor={`system-team-member-role-${membership.membership_id}`}>
                        {t("settings.role")}
                      </FieldLabel>
                      {memberRoleControl(membership)}
                    </Field>
                    <div>{removeMemberAction(membership)}</div>
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
                      <TableHead>{t("users.status")}</TableHead>
                      <TableHead>{t("users.action")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {selectedTeamMemberships.map((membership) => (
                      <TableRow key={membership.membership_id}>
                        <TableCell>
                          {memberById.get(membership.member_actor_id)?.display_name ??
                            t("teams.unknownMember")}
                        </TableCell>
                        <TableCell>{memberTypeLabel(membership.member_actor_type)}</TableCell>
                        <TableCell className="min-w-40">
                          {memberRoleControl(membership)}
                        </TableCell>
                        <TableCell>
                          <StatusBadge
                            semantic={membership.status === "active" ? "success" : "inactive"}
                            label={localizedStatusLabel(membership.status, t)}
                          />
                        </TableCell>
                        <TableCell>{removeMemberAction(membership)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
        <Button
          type="button"
          className="w-fit"
          onClick={() => void openAddMembersDialog()}
        >
          <UserRoundPlus data-icon="inline-start" />
          {t("teams.addMember")}
        </Button>
      </div>
    );
  }

  return (
    <section className="flex flex-col gap-5">
      {detail ? (
        loading ? (
          <LoadingState
            title={t("teams.loadingTitle")}
          />
        ) : loadError ? (
          <LoadErrorState
            title={t("admin.listLoadFailed")}
            description={serverMessage(loadError, t)}
            retryLabel={t("admin.retry")}
            onRetry={() => void refreshTeams()}
          />
        ) : !selectedTeam ? (
          <AdminResourceUnavailable onBack={() => onNavigate("/admin/teams")} />
        ) : (
          <>
            <AdminBreadcrumb
              items={[
                { label: t("teams.title"), route: "/admin/teams" },
                ...(detail.section === "profile"
                  ? [{ label: selectedTeam.name }]
                  : [
                      {
                        label: selectedTeam.name,
                        route: adminTeamDetailRoute(selectedTeam.team_id, "profile"),
                      },
                      { label: t("admin.membersSection") },
                    ]),
              ]}
              onNavigate={onNavigate}
            />
            <div className="flex flex-wrap items-start justify-between gap-3">
              <PageHeader title={selectedTeam.name} />
              <Button
                variant="outline"
                onClick={() =>
                  onNavigate(
                    documentLibraryDestination("team", selectedTeam.team_id),
                  )
                }
              >
                <FileText data-icon="inline-start" />
                {t("documentLibrary.manageTargetDocuments")}
              </Button>
            </div>
            <AdminSectionNav
              value={detail.section}
              items={[
                { value: "profile", label: t("admin.profileSection"), icon: <Network /> },
                { value: "members", label: t("admin.membersSection"), icon: <UsersRound /> },
              ]}
              onValueChange={(section) =>
                onNavigate(adminTeamDetailRoute(selectedTeam.team_id, section))
              }
            />
            {actionError && (
              <div className="text-sm text-destructive">{serverMessage(actionError, t)}</div>
            )}
            {detail.section === "profile" ? (
              <Card>
                <CardHeader>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <CardTitle>{t("admin.profileSection")}</CardTitle>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => openTeamProfileEditor(selectedTeam)}
                    >
                      {t("admin.editProfile")}
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="grid gap-4 text-sm sm:grid-cols-2">
                  <TargetSummary
                    label={t("teams.parentTeam")}
                    title={
                      selectedTeam.parent_team_id
                        ? teamById.get(selectedTeam.parent_team_id)?.name ??
                          selectedTeam.parent_team_id
                        : t("teams.noParent")
                    }
                  />
                  <TargetSummary label={t("users.status")} title={localizedStatusLabel(selectedTeam.status, t)} />
                  <TargetSummary
                    label={t("teams.documentInheritance")}
                    title={
                      selectedTeam.inherit_parent_documents
                        ? t("teams.inheritParentDocumentsOn")
                        : t("teams.inheritParentDocumentsOff")
                    }
                  />
                </CardContent>
              </Card>
            ) : (
              renderMembersSection()
            )}
          </>
        )
      ) : (
        <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title={t("teams.title")} />
        <Button size="sm" onClick={openCreateTeamDialog}>
          <Plus data-icon="inline-start" />
          {t("teams.createTeam")}
        </Button>
      </div>
      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <LoadingState
              title={t("teams.loadingTitle")}
            />
          ) : loadError ? (
            <LoadErrorState
              title={t("admin.listLoadFailed")}
              description={serverMessage(loadError, t)}
              retryLabel={t("admin.retry")}
              onRetry={() => void refreshTeams()}
            />
          ) : teams.length === 0 ? (
            <Empty className="border">
              <EmptyHeader>
                <EmptyTitle>{t("teams.emptyTitle")}</EmptyTitle>
                <EmptyDescription>{t("teams.emptyDescription")}</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : isMobile ? (
            <div className="grid gap-3">
              {teams.map((team) => (
                <div
                  key={team.team_id}
                  className={`${clickableCardClassName} p-3`}
                  role="button"
                  tabIndex={0}
                  aria-label={team.name}
                  onClick={() => openTeamEditor(team)}
                  onKeyDown={(event) =>
                    activateOnEnterOrSpace(event, () => openTeamEditor(team))
                  }
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="inline-flex max-w-full items-center gap-2 font-medium">
                        <Network />
                        <span className="truncate">{team.name}</span>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {team.parent_team_id
                          ? teamById.get(team.parent_team_id)?.name ?? team.parent_team_id
                          : t("teams.noParent")}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        {team.inherit_parent_documents
                          ? t("teams.inheritParentDocumentsOn")
                          : t("teams.inheritParentDocumentsOff")}
                      </div>
                    </div>
                    <StatusBadge
                      semantic={team.status === "active" ? "success" : "inactive"}
                      label={localizedStatusLabel(team.status, t)}
                    />
                  </div>
                  <div className="mt-3">{teamActions(team)}</div>
                </div>
              ))}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("teams.team")}</TableHead>
                  <TableHead>{t("teams.parentTeam")}</TableHead>
                  <TableHead>{t("teams.documentInheritance")}</TableHead>
                  <TableHead>{t("users.status")}</TableHead>
                  <TableHead>{t("users.action")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {teams.map((team) => (
                  <TableRow
                    key={team.team_id}
                    className={clickableSurfaceClassName}
                    role="button"
                    tabIndex={0}
                    aria-label={team.name}
                    onClick={() => openTeamEditor(team)}
                    onKeyDown={(event) =>
                      activateOnEnterOrSpace(event, () => openTeamEditor(team))
                    }
                  >
                    <TableCell>
                      <span className="inline-flex items-center gap-2">
                        <Network />
                        {team.name}
                      </span>
                    </TableCell>
                    <TableCell>
                      {team.parent_team_id
                        ? teamById.get(team.parent_team_id)?.name ?? team.parent_team_id
                        : t("teams.noParent")}
                    </TableCell>
                    <TableCell>
                      <StatusBadge
                        semantic={team.inherit_parent_documents ? "success" : "inactive"}
                        label={
                          team.inherit_parent_documents
                            ? t("teams.inheritParentDocumentsOn")
                            : t("teams.inheritParentDocumentsOff")
                        }
                      />
                    </TableCell>
                    <TableCell>
                      <StatusBadge
                        semantic={team.status === "active" ? "success" : "inactive"}
                        label={localizedStatusLabel(team.status, t)}
                      />
                    </TableCell>
                    <TableCell>
                      {teamActions(team)}
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
      <Dialog open={showCreateTeam} onOpenChange={handleCreateTeamOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("teams.createTeam")}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("teams.treeDescription")}
            </DialogDescription>
          </DialogHeader>
          <FieldSet>
            <FieldLegend>{t("teams.teamProfile")}</FieldLegend>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="team-name">{t("teams.teamName")}</FieldLabel>
                <Input
                  id="team-name"
                  value={teamName}
                  onChange={(event) => setTeamName(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="parent-team">{t("teams.parentTeam")}</FieldLabel>
                <SearchSelect
                  id="parent-team"
                  value={parentTeamId || NO_PARENT}
                  options={parentTeamOptions}
                  placeholder={t("teams.parentTeam")}
                  emptyText={t("teams.emptyTitle")}
                  onValueChange={(value) => setParentTeamId(value === NO_PARENT ? "" : value)}
                />
                <FieldDescription>{t("teams.depthHelp")}</FieldDescription>
              </Field>
            </FieldGroup>
          </FieldSet>
          <DialogFooter>
            <Button variant="outline" onClick={closeCreateTeamDialog}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() =>
                runAction(
                  "create-team",
                  () =>
                    teamAdministrationApi.createTeam(
                      generatedId("team", teamName),
                      teamName,
                      parentTeamId || null,
                    ),
                  closeCreateTeamDialog,
                )
              }
              disabled={pendingAction === "create-team" || !canCreateTeam}
            >
              {pendingAction === "create-team" ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <Plus data-icon="inline-start" />
              )}
              {t("teams.createTeam")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={showEditTeam}
        onOpenChange={(open) => {
          if (open) {
            setShowEditTeam(true);
          } else {
            closeTeamEditor();
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("teams.editTitle")}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("teams.editDescription")}
            </DialogDescription>
          </DialogHeader>
          {selectedTeam && (
            <>
              <TargetSummary label={t("teams.team")} title={selectedTeam.name}>
                <StatusBadge
                  semantic={selectedTeam.status === "active" ? "success" : "inactive"}
                  label={localizedStatusLabel(selectedTeam.status, t)}
                />
                <StatusBadge
                  semantic={selectedTeam.inherit_parent_documents ? "success" : "inactive"}
                  label={
                    selectedTeam.inherit_parent_documents
                      ? t("teams.inheritParentDocumentsOn")
                      : t("teams.inheritParentDocumentsOff")
                  }
                />
              </TargetSummary>
              <div className="pt-3">
                <FieldSet>
                  <FieldLegend className="sr-only">{t("dialogTabs.profile")}</FieldLegend>
                  <FieldGroup>
                    <Field>
                      <FieldLabel htmlFor="edit-team-name">{t("teams.teamName")}</FieldLabel>
                      <Input
                        id="edit-team-name"
                        value={editTeamName}
                        onChange={(event) => setEditTeamName(event.target.value)}
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="edit-parent-team">{t("teams.parentTeam")}</FieldLabel>
                      <SearchSelect
                        id="edit-parent-team"
                        value={editParentTeamId || NO_PARENT}
                        options={editParentTeamOptions}
                        placeholder={t("teams.parentTeam")}
                        emptyText={t("teams.emptyTitle")}
                        onValueChange={(value) =>
                          setEditParentTeamId(value === NO_PARENT ? "" : value)
                        }
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="edit-team-status">{t("users.status")}</FieldLabel>
                      <OptionSelect
                        id="edit-team-status"
                        value={editTeamStatus}
                        options={teamStatusOptions}
                        onValueChange={setEditTeamStatus}
                      />
                    </Field>
                    <Field orientation="horizontal">
                      <Checkbox
                        id="edit-inherit-parent-documents"
                        checked={editInheritParentDocuments}
                        onCheckedChange={(checked) =>
                          setEditInheritParentDocuments(checked === true)
                        }
                      />
                      <div className="grid gap-1">
                        <FieldLabel htmlFor="edit-inherit-parent-documents">
                          {t("teams.inheritParentDocuments")}
                        </FieldLabel>
                        <FieldDescription>
                          {t("teams.inheritParentDocumentsHelp")}
                        </FieldDescription>
                      </div>
                    </Field>
                    <Button
                      className="w-fit"
                      onClick={() =>
                        selectedTeam &&
                        runAction(
                          "update-team",
                          () =>
                            teamAdministrationApi.updateTeam(
                              selectedTeam.team_id,
                              editTeamName.trim(),
                              editParentTeamId || null,
                              editTeamStatus,
                              editInheritParentDocuments,
                            ),
                          closeTeamEditor,
                        )
                      }
                      disabled={pendingAction === "update-team" || !canSaveTeam}
                    >
                      <Save data-icon="inline-start" />
                      {t("teams.saveTeam")}
                    </Button>
                  </FieldGroup>
                </FieldSet>
              </div>
            </>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={closeTeamEditor}>
              {t("admin.cancel")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={showAddMembers}
        onOpenChange={(open) =>
          open ? void openAddMembersDialog() : closeAddMembersDialog()
        }
      >
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t("teams.addMember")}</DialogTitle>
            <DialogDescription>{t("teams.membersDescription")}</DialogDescription>
          </DialogHeader>
          {(directoryImport.mode === "directory" ? directoryImport.actionError : actionError) && (
            <div role="alert" className="rounded-md border border-destructive/50 p-3 text-sm">
              <div className="font-medium">{t("admin.actionFailed")}</div>
              <div className="text-muted-foreground">
                {serverMessage(
                  directoryImport.mode === "directory" ? directoryImport.actionError : actionError,
                  t,
                )}
              </div>
            </div>
          )}
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="team-add-members-mode">{t("directory.memberSource")}</FieldLabel>
              <OptionSelect
                id="team-add-members-mode"
                value={directoryImport.mode}
                options={[
                  { value: "atlas", label: t("directory.atlasUsers") },
                  { value: "directory", label: t("directory.title") },
                ]}
                onValueChange={(value) => {
                  setActionError("");
                  directoryImport.setMode(value);
                }}
              />
            </Field>
            {directoryImport.mode === "atlas" ? (
              <>
                <Field>
                  <FieldLabel htmlFor="member-search">{t("teams.searchMembers")}</FieldLabel>
                  <Input
                    id="member-search"
                    value={memberSearch}
                    onChange={(event) => setMemberSearch(event.target.value)}
                  />
                </Field>
                <div className="grid max-h-72 gap-2 overflow-y-auto rounded-md border p-2">
                  {visibleMemberOptions.length === 0 ? (
                    <Empty className="min-h-32 border-0 p-4 md:p-6">
                      <EmptyHeader>
                        <EmptyTitle>{memberSelectionEmptyTitle}</EmptyTitle>
                        <EmptyDescription>{memberSelectionEmptyDescription}</EmptyDescription>
                      </EmptyHeader>
                    </Empty>
                  ) : (
                    visibleMemberOptions.map((member) => (
                      <label
                        key={member.actor_id}
                        className="flex min-h-11 items-center gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:bg-muted/50 focus-within:bg-muted/50 focus-within:outline-none focus-within:ring-2 focus-within:ring-ring"
                      >
                        <Checkbox
                          checked={selectedMemberIds.includes(member.actor_id)}
                          onCheckedChange={(checked) =>
                            toggleMember(member.actor_id, checked === true)
                          }
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block font-medium">{member.display_name}</span>
                          <span className="block text-xs text-muted-foreground">
                            {memberTypeLabel(member.actor_type)}
                          </span>
                        </span>
                        <StatusBadge
                          semantic={member.status === "active" ? "success" : "inactive"}
                          label={localizedStatusLabel(member.status, t)}
                        />
                      </label>
                    ))
                  )}
                </div>
              </>
            ) : <SystemTeamDirectoryImportView controller={directoryImport} />}
            <Field>
              <FieldLabel htmlFor="team-member-role">{t("permissions.role")}</FieldLabel>
              <OptionSelect
                id="team-member-role"
                value={newMemberRole}
                options={teamMemberRoleOptions}
                onValueChange={setNewMemberRole}
              />
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={closeAddMembersDialog}>
              {t("admin.cancel")}
            </Button>
            <Button
              type="button"
              onClick={() => {
                if (!selectedTeam) return;
                if (directoryImport.mode === "directory") {
                  void directoryImport.importMembers();
                  return;
                }
                void runAction(
                  "add-members",
                  async () => {
                    const results = await Promise.all(
                      selectedMembers.map((member) =>
                        teamAdministrationApi.addTeamMember(
                          selectedTeam.team_id,
                          member.actor_type,
                          member.actor_id,
                          newMemberRole,
                        ),
                      ),
                    );
                    return {
                      ...results[0],
                      message_code: "team.members_are_active",
                      message_params: {},
                    };
                  },
                  closeAddMembersDialog,
                );
              }}
              disabled={
                pendingAction === "add-members" ||
                directoryImport.importPending ||
                (directoryImport.mode === "atlas"
                  ? !canAddMembers
                  : directoryImport.selectedSubjects.length === 0)
              }
            >
              <UserRoundPlus data-icon="inline-start" />
              {directoryImport.mode === "directory"
                ? t("directory.importSelected")
                : t("teams.addSelectedMembers")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
