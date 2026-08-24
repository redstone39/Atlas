import { Copy, FileText, Pencil, Plus, Save, Trash2, UserPlus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "../../components/ui/empty";
import { Item, ItemGroup } from "../../components/ui/item";
import { Checkbox } from "../../components/ui/checkbox";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "../../components/ui/field";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import { Spinner } from "../../components/ui/spinner";
import {
  retainClientRequestId,
  type ClientOperationKey,
} from "../../shared/ids";
import { useIsMobile } from "../../hooks/use-mobile";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import {
  AdminBreadcrumb,
  AdminResourceHeader,
  AdminResourceUnavailable,
  AdminSection,
  AdminSectionNav,
} from "../../shared/admin-detail";
import { OptionSelect, type OptionSelectItem } from "../../shared/OptionSelect";
import { SearchSelect, type SearchSelectOption } from "../../shared/SearchSelect";
import {
  ConfirmActionButton,
  LoadErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  TargetSummary,
  localizedStatusLabel,
  serverMessage,
} from "../../shared/product-ui";
import type { AdminActionResult } from "../../shared/api-contracts";
import {
  adminProjectDetailRoute,
  documentLibraryDestination,
} from "../../shared/routes";
import { projectGovernanceApi } from "./api";
import {
  ProjectDirectoryImportView,
  useProjectDirectoryImportController,
} from "./ProjectDirectoryImportController";
import type {
  ProjectGovernanceFeatureProps,
  ProjectAccessGrant,
  ProjectMemberCandidate,
  ProjectMemberEffect,
  ProjectMemberRole,
  ProjectAdminSummary,
} from "./types";

const EMPTY_PROJECT_MEMBER_CANDIDATES: {
  users: ProjectMemberCandidate[];
  teams: ProjectMemberCandidate[];
  service_accounts: ProjectMemberCandidate[];
} = { users: [], teams: [], service_accounts: [] };

export function ProjectGovernanceFeature({
  session,
  canManageProjectProfile,
  onNotice,
  onRefresh,
  createInvite,
  detail,
  onNavigate,
}: ProjectGovernanceFeatureProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const pathname = usePathname();
  const [projects, setProjects] = useState<ProjectAdminSummary[]>([]);
  const [showCreateProject, setShowCreateProject] = useState(false);
  const [showEditProject, setShowEditProject] = useState(false);
  const [showAddAccess, setShowAddAccess] = useState(false);
  const [showInviteUser, setShowInviteUser] = useState(false);
  const [accessSubjectType, setAccessSubjectType] =
    useState<"user" | "team" | "service_account">("user");
  const [projectName, setProjectName] = useState("");
  const createProjectOperation = useRef<ClientOperationKey | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [editProjectName, setEditProjectName] = useState("");
  const [editProjectStatus, setEditProjectStatus] =
    useState<"active" | "retired">("active");
  const [projectAccessGrants, setProjectAccessGrants] = useState<ProjectAccessGrant[]>([]);
  const [projectAccessSubjects, setProjectAccessSubjects] = useState<ProjectMemberCandidate[]>([]);
  const [memberCandidates, setMemberCandidates] = useState(EMPTY_PROJECT_MEMBER_CANDIDATES);
  const [membersLoading, setMembersLoading] = useState(false);
  const [membersLoadError, setMembersLoadError] = useState("");
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [candidatesLoadError, setCandidatesLoadError] = useState("");
  const [selectedUserCandidateId, setSelectedUserCandidateId] = useState("");
  const [selectedTeamCandidateId, setSelectedTeamCandidateId] = useState("");
  const [selectedServiceAccountCandidateId, setSelectedServiceAccountCandidateId] = useState("");
  const [newUserRole, setNewUserRole] = useState<ProjectMemberRole>("viewer");
  const [newTeamRole, setNewTeamRole] = useState<ProjectMemberRole>("viewer");
  const [newServiceAccountRole, setNewServiceAccountRole] = useState<ProjectMemberRole>("viewer");
  const [newUserEffect, setNewUserEffect] = useState<ProjectMemberEffect>("allow");
  const [newTeamEffect, setNewTeamEffect] = useState<ProjectMemberEffect>("allow");
  const [newServiceAccountEffect, setNewServiceAccountEffect] = useState<ProjectMemberEffect>("allow");
  const [inviteName, setInviteName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<ProjectMemberRole>("viewer");
  const [inviteLink, setInviteLink] = useState("");
  const createProjectInviteOperation = useRef<ClientOperationKey | null>(null);
  const [actionError, setActionError] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const projectEditorGenerationRef = useRef(0);
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  const selectedProject =
    detail && detail.projectId === selectedProjectId
      ? projects.find((project) => project.project_id === selectedProjectId) ?? null
      : null;
  const isSystemAdmin = session.system_role === "admin";
  const directoryImport = useProjectDirectoryImportController({
    projectId: selectedProject?.project_id ?? "",
    role: newUserRole,
    onNotice,
    onRefresh,
    onPostSuccess: async (projectId) => {
      await Promise.all([
        refreshProjectMembers(projectId),
        refreshProjectCandidates(projectId),
      ]);
    },
    onClose: closeAddAccessDialog,
  });
  const memberRoleOptions: OptionSelectItem<ProjectMemberRole>[] = [
    { value: "viewer", label: t("roleValues.viewer") },
    { value: "contributor", label: t("roleValues.contributor") },
    { value: "admin", label: t("roleValues.admin") },
  ];
  const memberEffectOptions: OptionSelectItem<ProjectMemberEffect>[] = [
    { value: "allow", label: t("permissions.allow") },
    { value: "deny", label: t("permissions.deny") },
  ];
  const projectStatusOptions: OptionSelectItem<"active" | "retired">[] = [
    { value: "active", label: localizedStatusLabel("active", t) },
    { value: "retired", label: localizedStatusLabel("retired", t) },
  ];
  const userCandidateOptions = memberCandidates.users.map(candidateOption);
  const teamCandidateOptions = memberCandidates.teams.map(candidateOption);
  const serviceAccountCandidateOptions = memberCandidates.service_accounts.map(candidateOption);

  useEffect(() => {
    void refreshProjects();
  }, []);


  useEffect(() => {
    if (projects.length === 0 || !detail) {
      setSelectedProjectId("");
      setEditProjectName("");
      setEditProjectStatus("active");
      return;
    }
    const routedProject = projects.find((project) => project.project_id === detail.projectId);
    if (routedProject) {
      selectProject(routedProject);
    } else {
      projectEditorGenerationRef.current += 1;
      setSelectedProjectId("");
      setEditProjectName("");
      setProjectAccessGrants([]);
      setProjectAccessSubjects([]);
      setMemberCandidates(EMPTY_PROJECT_MEMBER_CANDIDATES);
    }
  }, [projects, detail?.projectId]);

  useEffect(() => {
    if (!detail || detail.section !== "access" || !selectedProject) return;
    if (selectedProject.status !== "active") {
      onNavigate(adminProjectDetailRoute(selectedProject.project_id, "profile"));
      return;
    }
    projectEditorGenerationRef.current += 1;
    const generation = projectEditorGenerationRef.current;
    void refreshProjectMembers(selectedProject.project_id, generation);
    void refreshProjectCandidates(selectedProject.project_id, generation);
  }, [detail?.projectId, detail?.section, selectedProject?.project_id, selectedProject?.status]);

  async function refreshProjects() {
    setLoading(true);
    setLoadError("");
    try {
      const projectResult = await projectGovernanceApi.listProjects();
      setProjects(projectResult.projects);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : t("admin.listLoadFailed"));
    } finally {
      setLoading(false);
    }
  }

  async function refreshProjectMembers(
    projectId: string,
    generation = projectEditorGenerationRef.current,
  ) {
    setMembersLoading(true);
    setMembersLoadError("");
    try {
      const memberResult = await projectGovernanceApi.listProjectMembers(projectId);
      if (generation !== projectEditorGenerationRef.current) return;
      setProjectAccessGrants(memberResult.grants);
      setProjectAccessSubjects(memberResult.subjects);
    } catch (err) {
      if (generation !== projectEditorGenerationRef.current) return;
      setMembersLoadError(err instanceof Error ? err.message : t("admin.listLoadFailed"));
      setProjectAccessGrants([]);
      setProjectAccessSubjects([]);
    } finally {
      if (generation === projectEditorGenerationRef.current) {
        setMembersLoading(false);
      }
    }
  }

  async function refreshProjectCandidates(
    projectId: string,
    generation = projectEditorGenerationRef.current,
  ) {
    setCandidatesLoading(true);
    setCandidatesLoadError("");
    try {
      const result = await projectGovernanceApi.listProjectMemberCandidates(projectId);
      if (generation !== projectEditorGenerationRef.current) return;
      setMemberCandidates(result);
    } catch (err) {
      if (generation !== projectEditorGenerationRef.current) return;
      setCandidatesLoadError(err instanceof Error ? err.message : t("admin.listLoadFailed"));
      setMemberCandidates(EMPTY_PROJECT_MEMBER_CANDIDATES);
    } finally {
      if (generation === projectEditorGenerationRef.current) {
        setCandidatesLoading(false);
      }
    }
  }

  function selectProject(project: ProjectAdminSummary) {
    setSelectedProjectId(project.project_id);
    setEditProjectName(project.name);
    setEditProjectStatus(project.status);
    directoryImport.invalidateRequest();
    setShowAddAccess(false);
    setShowInviteUser(false);
    setInviteLink("");
  }

  function resetProjectMemberDraft() {
    setSelectedUserCandidateId("");
    setSelectedTeamCandidateId("");
    setSelectedServiceAccountCandidateId("");
    setNewUserRole("viewer");
    setNewTeamRole("viewer");
    setNewServiceAccountRole("viewer");
    setNewUserEffect("allow");
    setNewTeamEffect("allow");
    setNewServiceAccountEffect("allow");
    setInviteName("");
    setInviteEmail("");
    setInviteRole("viewer");
    setInviteLink("");
  }
  function openAddAccessDialog() {
    if (!selectedProject) return;
    directoryImport.reset();
    setNewUserRole("viewer");
    setActionError("");
    setShowAddAccess(true);
  }

  function closeAddAccessDialog() {
    directoryImport.reset();
    setNewUserRole("viewer");
    setShowAddAccess(false);
  }
  function openInviteUserDialog() {
    createProjectInviteOperation.current = null;
    setInviteName("");
    setInviteEmail("");
    setInviteRole("viewer");
    setInviteLink("");
    setActionError("");
    setShowInviteUser(true);
  }

  function closeInviteUserDialog() {
    setInviteLink("");
    setShowInviteUser(false);
  }

  function openProjectEditor(project: ProjectAdminSummary) {
    onNavigate(adminProjectDetailRoute(project.project_id, "profile"));
  }

  function closeProjectEditor() {
    setShowEditProject(false);
  }

  function resetProjectDraft() {
    setProjectName("");
  }

  function openCreateProjectDialog() {
    resetProjectDraft();
    setShowCreateProject(true);
  }

  function closeCreateProjectDialog() {
    createProjectOperation.current = null;
    resetProjectDraft();
    setShowCreateProject(false);
  }

  async function runAction(
    actionName: string,
    action: () => Promise<AdminActionResult>,
    onSuccess?: () => void | Promise<void>,
  ) {
    setPendingAction(actionName);
    setActionError("");
    try {
      const result = await action();
      const message = serverMessage(result, t);
      onNotice(result.message_code);
      toast.success(message);
      await refreshProjects();
      await onRefresh();
      await onSuccess?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  async function runProjectMemberAction(
    actionName: string,
    successMessage: string,
    action: () => Promise<void>,
  ): Promise<boolean> {
    setPendingAction(actionName);
    setActionError("");
    const originRoute = selectedProject
      ? adminProjectDetailRoute(selectedProject.project_id, "access")
      : null;
    try {
      await action();
      onNotice(successMessage);
      toast.success(successMessage);
      const routeRetained = await onRefresh();
      if (
        routeRetained &&
        selectedProject &&
        originRoute &&
        pathnameRef.current === originRoute
      ) {
        await Promise.all([
          refreshProjectMembers(selectedProject.project_id),
          refreshProjectCandidates(selectedProject.project_id),
        ]);
      }
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
      return false;
    } finally {
      setPendingAction("");
    }
  }

  async function addProjectMember(
    subjectType: "user" | "team" | "service_account",
    subjectId: string,
    role: ProjectMemberRole,
    effect: ProjectMemberEffect,
  ) {
    if (!selectedProject) return false;
    return runProjectMemberAction(
      `project-member-add-${subjectType}`,
      t("projects.memberActive"),
      async () => {
        await projectGovernanceApi.addProjectMember(
          selectedProject.project_id,
          subjectType,
          subjectId,
          role,
          effect,
        );
        if (subjectType === "user") {
          setSelectedUserCandidateId("");
          setNewUserRole("viewer");
          setNewUserEffect("allow");
        } else if (subjectType === "team") {
          setSelectedTeamCandidateId("");
          setNewTeamRole("viewer");
          setNewTeamEffect("allow");
        } else {
          setSelectedServiceAccountCandidateId("");
          setNewServiceAccountRole("viewer");
          setNewServiceAccountEffect("allow");
        }
      },
    );
  }

  function updateProjectMemberAccess(
    member: ProjectAccessGrant,
    updates: { role?: ProjectMemberRole; effect?: ProjectMemberEffect },
  ) {
    if (!selectedProject) return;
    const role = updates.role ?? member.role;
    const effect = updates.effect ?? member.effect;
    if (member.role === role && member.effect === effect) return;
    void runProjectMemberAction(
      `project-member-role-${member.grant_id}`,
      t("projects.memberRoleUpdated"),
      async () => {
        const updatedMember = await projectGovernanceApi.updateProjectMember(
          selectedProject.project_id,
          member.grant_id,
          role,
          effect,
        );
        setProjectAccessGrants((members) =>
          members.map((candidate) =>
            candidate.grant_id === updatedMember.grant_id ? updatedMember : candidate,
          ),
        );
      },
    );
  }

  function removeProjectMember(member: ProjectAccessGrant) {
    if (!selectedProject) return;
    void runProjectMemberAction(
      `project-member-remove-${member.grant_id}`,
      t("projects.memberRevoked"),
      async () => {
        const revokedGrant = await projectGovernanceApi.removeProjectMember(
          selectedProject.project_id,
          member.grant_id,
        );
        setProjectAccessGrants((grants) =>
          grants.map((grant) =>
            grant.grant_id === revokedGrant.grant_id ? revokedGrant : grant,
          ),
        );
      },
    );
  }

  function inviteProjectUser() {
    if (!selectedProject) return;
    void (async () => {
      setPendingAction("project-member-invite");
      setActionError("");
      try {
        const scope = {
          scopeType: "project" as const,
          scopeId: selectedProject.project_id,
          scopeRole: inviteRole,
        };
        const operation = retainClientRequestId(
          createProjectInviteOperation.current,
          "project-invite-create",
          JSON.stringify([inviteName.trim(), inviteEmail.trim(), scope]),
        );
        createProjectInviteOperation.current = operation;
        const result = await createInvite(
          inviteName.trim(),
          inviteEmail.trim(),
          scope,
          operation.idempotencyKey,
        );
        const message = serverMessage(result, t);
        setInviteLink(result.local_pilot_acceptance?.acceptance_url ?? "");
        onNotice(result.message_code);
        toast.success(message);
        createProjectInviteOperation.current = null;
        setInviteName("");
        setInviteEmail("");
        setInviteRole("viewer");
        await onRefresh();
      } catch (err) {
        const message = err instanceof Error ? err.message : t("admin.actionFailed");
        setActionError(message);
        toast.error(serverMessage(message, t));
      } finally {
        setPendingAction("");
      }
    })();
  }

  const canCreateProject = Boolean(canManageProjectProfile && isSystemAdmin && projectName.trim());
  const canEditProjectProfile = Boolean(
    selectedProject && (isSystemAdmin || selectedProject.status === "active"),
  );
  const canSaveProject = Boolean(
    canEditProjectProfile &&
    selectedProject &&
      editProjectName.trim() &&
      (editProjectName.trim() !== selectedProject.name ||
        (isSystemAdmin && editProjectStatus !== selectedProject.status)),
  );
  const projectUpdates = selectedProject
    ? {
        ...(editProjectName.trim() !== selectedProject.name
          ? { name: editProjectName.trim() }
          : {}),
        ...(isSystemAdmin && editProjectStatus !== selectedProject.status
          ? { status: editProjectStatus }
          : {}),
      }
    : {};
  const canInviteProjectUser = Boolean(selectedProject && inviteName.trim() && inviteEmail.trim());
  const accessCandidateId =
    accessSubjectType === "user"
      ? selectedUserCandidateId
      : accessSubjectType === "team"
        ? selectedTeamCandidateId
        : selectedServiceAccountCandidateId;
  const accessCandidateOptions =
    accessSubjectType === "user"
      ? userCandidateOptions
      : accessSubjectType === "team"
        ? teamCandidateOptions
        : serviceAccountCandidateOptions;
  const accessRole =
    accessSubjectType === "user"
      ? newUserRole
      : accessSubjectType === "team"
        ? newTeamRole
        : newServiceAccountRole;
  const accessEffect =
    accessSubjectType === "user"
      ? newUserEffect
      : accessSubjectType === "team"
        ? newTeamEffect
        : newServiceAccountEffect;
  function policyProfileLabel(policyProfileId: string) {
    return policyProfileId === "policy-default-governed"
      ? t("projects.defaultGovernance")
      : t("projects.customGovernance");
  }

  function memberTypeLabel(member: ProjectAccessGrant) {
    if (member.subject_type === "team") return t("projects.memberType.team");
    if (member.subject_type === "service_account") return t("users.serviceAccount");
    return t("projects.memberType.user");
  }

  function memberSubject(member: ProjectAccessGrant) {
    return projectAccessSubjects.find(
      (subject) =>
        subject.subject_type === member.subject_type &&
        subject.subject_id === member.subject_id,
    );
  }

  function renderProjectMembersTab() {
    if (!selectedProject) return null;
    const activeProjectMembers = projectAccessGrants.filter(
      (grant) => grant.status === "active",
    );
    return (
      <>
        {actionError && (
          <Alert variant="destructive">
            <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
            <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
          </Alert>
        )}
        <AdminSection
          title={t("projects.currentMembers")}
          actions={
            <>
              <Button
                size="sm"
                onClick={() => void openAddAccessDialog()}
              >
                <Plus data-icon="inline-start" />
                {t("admin.addAccess")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={openInviteUserDialog}
              >
                <UserPlus data-icon="inline-start" />
                {t("projects.inviteNewUser")}
              </Button>
            </>
          }
        >
          <div className="grid gap-4">
            {candidatesLoadError && (
              <LoadErrorState
                title={t("admin.listLoadFailed")}
                description={serverMessage(candidatesLoadError, t)}
                retryLabel={t("admin.retry")}
                onRetry={() => void refreshProjectCandidates(selectedProject.project_id)}
              />
            )}
            {membersLoading ? (
              <LoadingState title={t("projects.membersLoadingTitle")} />
            ) : membersLoadError ? (
              <LoadErrorState
                title={t("admin.listLoadFailed")}
                description={serverMessage(membersLoadError, t)}
                retryLabel={t("admin.retry")}
                onRetry={() => void refreshProjectMembers(selectedProject.project_id)}
              />
            ) : activeProjectMembers.length === 0 ? (
              <Empty>
                <EmptyHeader>
                  <EmptyTitle>{t("projects.noMembersTitle")}</EmptyTitle>
                  <EmptyDescription>{t("projects.noMembersDescription")}</EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : isMobile ? (
              <ItemGroup>
                {activeProjectMembers.map((member) => {
                  const subject = memberSubject(member);
                  const subjectName =
                    subject?.display_name ?? t("projects.unavailableSubject");
                  return (
                    <Item
                      key={member.grant_id}
                      role="listitem"
                      className="grid gap-3 rounded-none px-0 py-4"
                    >
                      <div>
                        <div className="font-medium">{subjectName}</div>
                        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <Badge variant="outline">{memberTypeLabel(member)}</Badge>
                          {subject?.display_detail && <span>{subject.display_detail}</span>}
                        </div>
                      </div>
                      <Field>
                        <FieldLabel htmlFor={`project-member-role-${member.grant_id}`}>
                          {t("projects.role")}
                        </FieldLabel>
                        <OptionSelect
                          id={`project-member-role-${member.grant_id}`}
                          value={member.role}
                          options={memberRoleOptions}
                          onValueChange={(role) =>
                            updateProjectMemberAccess(member, { role })
                          }
                          disabled={pendingAction === `project-member-role-${member.grant_id}`}
                        />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor={`project-member-effect-${member.grant_id}`}>
                          {t("permissions.effect")}
                        </FieldLabel>
                        <OptionSelect
                          id={`project-member-effect-${member.grant_id}`}
                          value={member.effect}
                          options={memberEffectOptions}
                          onValueChange={(effect) =>
                            updateProjectMemberAccess(member, { effect })
                          }
                          disabled={pendingAction === `project-member-role-${member.grant_id}`}
                        />
                      </Field>
                      <ConfirmActionButton
                        ariaLabel={`${t("projects.remove")} ${subjectName}`}
                        icon={<Trash2 data-icon="inline-start" />}
                        disabled={pendingAction === `project-member-remove-${member.grant_id}`}
                        confirmTitle={t("admin.destructiveConfirmTitle", {
                          action: t("projects.remove"),
                        })}
                        confirmDescription={t("admin.destructiveConfirmDescription", {
                          target: subjectName,
                        })}
                        confirmLabel={t("projects.remove")}
                        cancelLabel={t("admin.cancel")}
                        onConfirm={() => removeProjectMember(member)}
                      >
                        {t("projects.remove")}
                      </ConfirmActionButton>
                    </Item>
                  );
                })}
              </ItemGroup>
            ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("projects.projectMember")}</TableHead>
                      <TableHead>{t("projects.role")}</TableHead>
                      <TableHead>{t("permissions.effect")}</TableHead>
                      <TableHead>{t("users.action")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {activeProjectMembers.map((member) => {
                      const subject = memberSubject(member);
                      const subjectName =
                        subject?.display_name ?? t("projects.unavailableSubject");
                      return (
                        <TableRow key={member.grant_id}>
                          <TableCell>
                            <div className="min-w-0">
                              <div className="font-medium">{subjectName}</div>
                              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                <Badge variant="outline">{memberTypeLabel(member)}</Badge>
                                {subject?.display_detail && <span>{subject.display_detail}</span>}
                              </div>
                            </div>
                          </TableCell>
                          <TableCell className="min-w-40">
                            <label
                              htmlFor={`project-member-role-${member.grant_id}`}
                              className="sr-only"
                            >
                              {t("projects.roleForMember", { name: subjectName })}
                            </label>
                            <OptionSelect
                              id={`project-member-role-${member.grant_id}`}
                              value={member.role}
                              options={memberRoleOptions}
                              onValueChange={(role) =>
                                updateProjectMemberAccess(member, { role })
                              }
                              disabled={pendingAction === `project-member-role-${member.grant_id}`}
                            />
                          </TableCell>
                          <TableCell className="min-w-32">
                            <label
                              htmlFor={`project-member-effect-${member.grant_id}`}
                              className="sr-only"
                            >
                              {t("permissions.effect")} {subjectName}
                            </label>
                            <OptionSelect
                              id={`project-member-effect-${member.grant_id}`}
                              value={member.effect}
                              options={memberEffectOptions}
                              onValueChange={(effect) =>
                                updateProjectMemberAccess(member, { effect })
                              }
                              disabled={pendingAction === `project-member-role-${member.grant_id}`}
                            />
                          </TableCell>
                          <TableCell>
                            <ConfirmActionButton
                              ariaLabel={`${t("projects.remove")} ${subjectName}`}
                              icon={<Trash2 data-icon="inline-start" />}
                              disabled={pendingAction === `project-member-remove-${member.grant_id}`}
                              confirmTitle={t("admin.destructiveConfirmTitle", {
                                action: t("projects.remove"),
                              })}
                              confirmDescription={t("admin.destructiveConfirmDescription", {
                                target: subjectName,
                              })}
                              confirmLabel={t("projects.remove")}
                              cancelLabel={t("admin.cancel")}
                              onConfirm={() => removeProjectMember(member)}
                            >
                              {t("projects.remove")}
                            </ConfirmActionButton>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
            )}
          </div>
        </AdminSection>
      </>
    );
  }

  return (
    <section className="flex flex-col gap-5">
      {detail ? (
        loading ? (
          <LoadingState
            title={t("projects.loadingTitle")}
          />
        ) : loadError ? (
          <LoadErrorState
            title={t("admin.listLoadFailed")}
            description={serverMessage(loadError, t)}
            retryLabel={t("admin.retry")}
            onRetry={() => void refreshProjects()}
          />
        ) : !selectedProject ? (
          <AdminResourceUnavailable onBack={() => onNavigate("/admin/projects")} />
        ) : (
          <>
            <AdminBreadcrumb
              items={[
                { label: t("projects.title"), route: "/admin/projects" },
                {
                  label: selectedProject.name,
                  route:
                    detail.section === "access"
                      ? adminProjectDetailRoute(selectedProject.project_id, "profile")
                      : undefined,
                },
                ...(detail.section === "access"
                  ? [{ label: t("admin.accessSection") }]
                  : []),
              ]}
              onNavigate={onNavigate}
            />
            <AdminResourceHeader
              title={selectedProject.name}
              description={policyProfileLabel(selectedProject.policy_profile_id)}
              actions={
                selectedProject.status === "active" ? (
                  <Button
                    variant="outline"
                    onClick={() =>
                      onNavigate(
                        documentLibraryDestination(
                          "project",
                          selectedProject.project_id,
                        ),
                      )
                    }
                  >
                    <FileText data-icon="inline-start" />
                    {t("documentLibrary.manageTargetDocuments")}
                  </Button>
                ) : undefined
              }
            />
            <AdminSectionNav
              value={detail.section}
              items={[
                { value: "profile", label: t("admin.profileSection") },
                ...(selectedProject.status === "active"
                  ? [{ value: "access" as const, label: t("admin.accessSection") }]
                  : []),
              ]}
              onValueChange={(section) =>
                onNavigate(adminProjectDetailRoute(selectedProject.project_id, section))
              }
            />
            {detail.section === "profile" || selectedProject.status !== "active" ? (
              <AdminSection
                title={t("admin.profileSection")}
                actions={
                  canEditProjectProfile ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setActionError("");
                        setEditProjectName(selectedProject.name);
                        setEditProjectStatus(selectedProject.status);
                        setShowEditProject(true);
                      }}
                    >
                      <Pencil data-icon="inline-start" />
                      {t("admin.editProfile")}
                    </Button>
                  ) : undefined
                }
              >
                <div className="grid gap-4 sm:grid-cols-2">
                  <TargetSummary
                    variant="flat"
                    label={t("admin.projectName")}
                    title={selectedProject.name}
                  />
                  <TargetSummary
                    label={t("projects.governanceMode")}
                    variant="flat"
                    title={policyProfileLabel(selectedProject.policy_profile_id)}
                  />
                  <TargetSummary
                    label={t("users.status")}
                    variant="flat"
                    title={localizedStatusLabel(selectedProject.status, t)}
                  />
                  {!canEditProjectProfile && (
                    <Alert className="sm:col-span-2">
                      <AlertTitle>{t("projects.profileReadOnlyTitle")}</AlertTitle>
                      <AlertDescription>{t("projects.profileReadOnlyDescription")}</AlertDescription>
                    </Alert>
                  )}
                </div>
              </AdminSection>
            ) : (
              renderProjectMembersTab()
            )}
          </>
        )
      ) : (
      <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title={t("projects.title")} />
        {canManageProjectProfile && (
          <Button onClick={openCreateProjectDialog}>
            <Plus data-icon="inline-start" />
            {t("admin.createProject")}
          </Button>
        )}
      </div>
      <div
        data-slot="project-directory-layout"
        className="flex w-full flex-col gap-5"
      >
            {loading ? (
              <LoadingState
                title={t("projects.loadingTitle")}
              />
            ) : loadError ? (
              <LoadErrorState
                title={t("admin.listLoadFailed")}
                description={serverMessage(loadError, t)}
                retryLabel={t("admin.retry")}
                onRetry={() => void refreshProjects()}
              />
            ) : projects.length === 0 ? (
              <Empty>
                <EmptyHeader>
                  <EmptyTitle>{t("projects.emptyTitle")}</EmptyTitle>
                  <EmptyDescription>{t("projects.emptyDescription")}</EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : isMobile ? (
              <ItemGroup>
                {projects.map((project) => (
                  <Item
                    key={project.project_id}
                    role="listitem"
                    className="grid gap-3 rounded-none px-0 py-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <Button
                          variant="link"
                          className="min-h-11 max-w-full justify-start px-0 text-left"
                          onClick={() => openProjectEditor(project)}
                        >
                          <span className="truncate">{project.name}</span>
                        </Button>
                        <div className="text-xs text-muted-foreground">
                          {t("projects.governanceMode")}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge variant="outline">
                          {policyProfileLabel(project.policy_profile_id)}
                        </Badge>
                        <StatusBadge
                          semantic={project.status === "active" ? "success" : "inactive"}
                          label={localizedStatusLabel(project.status, t)}
                        />
                      </div>
                    </div>
                  </Item>
                ))}
              </ItemGroup>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("admin.projectName")}</TableHead>
                    <TableHead>{t("projects.governanceMode")}</TableHead>
                    <TableHead>{t("users.status")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {projects.map((project) => (
                    <TableRow key={project.project_id}>
                      <TableCell>
                        <Button
                          variant="link"
                          size="sm"
                          className="h-auto px-0"
                          onClick={() => openProjectEditor(project)}
                        >
                          {project.name}
                        </Button>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">
                          {policyProfileLabel(project.policy_profile_id)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <StatusBadge
                          semantic={project.status === "active" ? "success" : "inactive"}
                          label={localizedStatusLabel(project.status, t)}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
      </div>
      </>
      )}
      <Dialog
        open={showCreateProject}
        onOpenChange={(open) => {
          if (open) {
            openCreateProjectDialog();
          } else {
            closeCreateProjectDialog();
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("admin.createProject")}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("projects.directoryDescription")}
            </DialogDescription>
          </DialogHeader>
          <FieldSet>
            <FieldLegend>{t("setup.project")}</FieldLegend>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="project-name">{t("admin.projectName")}</FieldLabel>
                <Input
                  id="project-name"
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                />
              </Field>
            </FieldGroup>
          </FieldSet>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={closeCreateProjectDialog}
            >
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() =>
                runAction(
                  "project",
                  () => {
                    const name = projectName.trim();
                    if (!name) throw new Error("project.name_is_required");
                    const operation = retainClientRequestId(
                      createProjectOperation.current,
                      "project-create",
                      name,
                    );
                    createProjectOperation.current = operation;
                    return projectGovernanceApi.createProject(
                      name,
                      operation.idempotencyKey,
                    );
                  },
                  () => {
                    createProjectOperation.current = null;
                    closeCreateProjectDialog();
                  },
                )
              }
              disabled={pendingAction === "project" || !canCreateProject}
            >
              {pendingAction === "project" ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <Plus data-icon="inline-start" />
              )}
              {t("admin.createProject")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={showEditProject}
        onOpenChange={(open) => {
          if (open) {
            setShowEditProject(true);
          } else {
            closeProjectEditor();
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("projects.editTitle")}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("projects.directoryDescription")}
            </DialogDescription>
          </DialogHeader>
          {actionError && (
            <Alert variant="destructive">
              <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
              <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
            </Alert>
          )}
          {selectedProject && (
            <>
              <TargetSummary
                label={t("setup.project")}
                title={selectedProject.name}
                description={policyProfileLabel(selectedProject.policy_profile_id)}
              />
              <Field>
                <FieldLabel htmlFor="edit-project-name">{t("admin.projectName")}</FieldLabel>
                <Input
                  id="edit-project-name"
                  value={editProjectName}
                  onChange={(event) => setEditProjectName(event.target.value)}
                />
              </Field>
              {isSystemAdmin && (
                <Field>
                  <FieldLabel htmlFor="edit-project-status">{t("users.status")}</FieldLabel>
                  <OptionSelect
                    id="edit-project-status"
                    value={editProjectStatus}
                    options={projectStatusOptions}
                    onValueChange={setEditProjectStatus}
                  />
                </Field>
              )}
            </>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={closeProjectEditor}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() =>
                selectedProject &&
                runAction(
                  "project-update",
                  () =>
                    projectGovernanceApi.updateProject(
                      selectedProject.project_id,
                      projectUpdates,
                    ),
                  closeProjectEditor,
                )
              }
              disabled={pendingAction === "project-update" || !canSaveProject}
            >
              {pendingAction === "project-update" ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <Save data-icon="inline-start" />
              )}
              {t("projects.saveProject")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={showAddAccess}
        onOpenChange={(open) => (open ? void openAddAccessDialog() : closeAddAccessDialog())}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("admin.addAccess")}</DialogTitle>
            <DialogDescription>{t("projects.membersLoadingDescription")}</DialogDescription>
          </DialogHeader>
          {(directoryImport.mode === "directory" ? directoryImport.actionError : actionError) && (
            <Alert variant="destructive">
              <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
              <AlertDescription>
                {serverMessage(
                  directoryImport.mode === "directory" ? directoryImport.actionError : actionError,
                  t,
                )}
              </AlertDescription>
            </Alert>
          )}
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="project-add-access-mode">{t("directory.memberSource")}</FieldLabel>
              <OptionSelect
                id="project-add-access-mode"
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
                  <FieldLabel htmlFor="project-access-subject-type">
                    {t("admin.subjectType")}
                  </FieldLabel>
                  <OptionSelect
                    id="project-access-subject-type"
                    value={accessSubjectType}
                    options={[
                      { value: "user", label: t("projects.memberType.user") },
                      { value: "team", label: t("projects.memberType.team") },
                      { value: "service_account", label: t("users.serviceAccount") },
                    ]}
                    onValueChange={setAccessSubjectType}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="project-access-subject">
                    {t("projects.projectMember")}
                  </FieldLabel>
                  <SearchSelect
                    id="project-access-subject"
                    value={accessCandidateId}
                    options={accessCandidateOptions}
                    placeholder={t("admin.chooseSubjectType")}
                    emptyText={t("projects.noUserCandidates")}
                    onValueChange={(value) => {
                      if (accessSubjectType === "user") setSelectedUserCandidateId(value);
                      else if (accessSubjectType === "team") setSelectedTeamCandidateId(value);
                      else setSelectedServiceAccountCandidateId(value);
                    }}
                  />
                </Field>
              </>
            ) : <ProjectDirectoryImportView controller={directoryImport} />}
            <Field>
              <FieldLabel htmlFor="project-access-role">{t("permissions.role")}</FieldLabel>
              <OptionSelect
                id="project-access-role"
                value={directoryImport.mode === "directory" ? newUserRole : accessRole}
                options={memberRoleOptions}
                onValueChange={(value) => {
                  if (directoryImport.mode === "directory" || accessSubjectType === "user") {
                    setNewUserRole(value);
                  } else if (accessSubjectType === "team") setNewTeamRole(value);
                  else setNewServiceAccountRole(value);
                }}
              />
            </Field>
            {directoryImport.mode === "atlas" && (
              <Field>
                <FieldLabel htmlFor="project-access-effect">{t("permissions.effect")}</FieldLabel>
                <OptionSelect
                  id="project-access-effect"
                  value={accessEffect}
                  options={memberEffectOptions}
                  onValueChange={(value) => {
                    if (accessSubjectType === "user") setNewUserEffect(value);
                    else if (accessSubjectType === "team") setNewTeamEffect(value);
                    else setNewServiceAccountEffect(value);
                  }}
                />
              </Field>
            )}
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={closeAddAccessDialog}>
              {t("admin.cancel")}
            </Button>
            <Button
              disabled={
                directoryImport.importPending ||
                pendingAction === `project-member-add-${accessSubjectType}` ||
                (directoryImport.mode === "directory"
                  ? directoryImport.selectedSubjects.length === 0
                  : !accessCandidateId)
              }
              onClick={async () => {
                if (directoryImport.mode === "directory") {
                  await directoryImport.importMembers();
                  return;
                }
                const succeeded = await addProjectMember(
                  accessSubjectType,
                  accessCandidateId,
                  accessRole,
                  accessEffect,
                );
                if (succeeded) closeAddAccessDialog();
              }}
            >
              {directoryImport.importPending ||
              pendingAction === `project-member-add-${accessSubjectType}` ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <Plus data-icon="inline-start" />
              )}
              {directoryImport.mode === "directory"
                ? t("directory.importSelected")
                : t("admin.addAccess")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={showInviteUser}
        onOpenChange={(open) => (open ? openInviteUserDialog() : closeInviteUserDialog())}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("projects.inviteNewUser")}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("projects.membersLoadingDescription")}
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
              <FieldLabel htmlFor="focused-project-invite-name">{t("projects.inviteName")}</FieldLabel>
              <Input
                id="focused-project-invite-name"
                value={inviteName}
                onChange={(event) => setInviteName(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="focused-project-invite-email">{t("projects.inviteEmail")}</FieldLabel>
              <Input
                id="focused-project-invite-email"
                type="email"
                value={inviteEmail}
                onChange={(event) => setInviteEmail(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="focused-project-invite-role">{t("projects.inviteRole")}</FieldLabel>
              <OptionSelect
                id="focused-project-invite-role"
                value={inviteRole}
                options={memberRoleOptions}
                onValueChange={setInviteRole}
              />
            </Field>
            {inviteLink && (
              <div className="rounded-md border bg-muted/30 p-3">
                <div className="mb-2 text-sm font-medium">
                  {t("admin.inviteAcceptanceLink")}
                </div>
                <div className="flex gap-2">
                  <Input
                    readOnly
                    value={inviteLink}
                    aria-label={t("admin.inviteAcceptanceLink")}
                  />
                  <Button
                    variant="outline"
                    size="icon-sm"
                    aria-label={t("admin.copyInvite")}
                    onClick={() => {
                      void navigator.clipboard.writeText(inviteLink);
                      toast.success(t("admin.inviteCopied"));
                    }}
                  >
                    <Copy />
                  </Button>
                </div>
              </div>
            )}
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={closeInviteUserDialog}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={inviteProjectUser}
              disabled={pendingAction === "project-member-invite" || !canInviteProjectUser}
            >
              {pendingAction === "project-member-invite" ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <UserPlus data-icon="inline-start" />
              )}
              {t("projects.inviteNewUser")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}


function candidateOption(candidate: ProjectMemberCandidate): SearchSelectOption {
  return {
    value: candidate.subject_id,
    label: candidate.display_name,
    description: candidate.display_detail ?? undefined,
  };
}
