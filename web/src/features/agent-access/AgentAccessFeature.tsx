import { Bot, Clipboard, KeyRound, Plus, RotateCcw, Save, ShieldCheck, UserX } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
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
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Spinner } from "../../components/ui/spinner";
import {
  retainClientRequestId,
  type ClientOperationKey,
} from "../../shared/ids";
import type { MessageReference } from "../../shared/user-messages";
import { OptionSelect, type OptionSelectItem } from "../../shared/OptionSelect";
import { SearchSelect } from "../../shared/SearchSelect";
import {
  ConfirmActionButton,
  localizedStatusLabel,
  PageHeader,
  StatusBadge,
  serverMessage,
} from "../../shared/product-ui";
import {
  projectGovernanceApi,
  type ProjectMemberEffect,
  type ProjectMemberRole,
} from "../project-governance/index";
import { agentAccessApi } from "./api";
import { AgentStatusList } from "./AgentStatusList";
import type {
  AgentAccessFeatureProps,
  AgentTokenIssueResult,
  AgentUserStatus,
} from "./types";

const projectRoleOptions: OptionSelectItem<ProjectMemberRole>[] = [
  { value: "viewer", label: "viewer" },
  { value: "contributor", label: "contributor" },
  { value: "admin", label: "admin" },
];

function AgentDialogTarget({ agent }: { agent: AgentUserStatus | null }) {
  const { t } = useTranslation();
  if (!agent) return null;
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="font-medium">{agent.display_name}</span>
      <StatusBadge
        semantic={agent.status === "active" ? "success" : "inactive"}
        label={localizedStatusLabel(agent.status, t)}
      />
    </div>
  );
}
export function AgentAccessFeature({
  projects,
  onNotice,

  onRefresh,
}: AgentAccessFeatureProps) {
  const { t } = useTranslation();
  const [showCreateAgent, setShowCreateAgent] = useState(false);
  const [showEditAgent, setShowEditAgent] = useState(false);
  const [showIssueToken, setShowIssueToken] = useState(false);
  const [showAgentPermission, setShowAgentPermission] = useState(false);
  const [newAgentName, setNewAgentName] = useState("");
  const createAgentOperation = useRef<ClientOperationKey | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [editAgentName, setEditAgentName] = useState("");
  const [projectId, setProjectId] = useState(() => projects[0]?.project_id ?? "");
  const [projectRole, setProjectRole] = useState<ProjectMemberRole>("viewer");
  const [projectEffect, setProjectEffect] = useState<ProjectMemberEffect>("allow");
  const [agents, setAgents] = useState<AgentUserStatus[]>([]);
  const [issuedToken, setIssuedToken] = useState<AgentTokenIssueResult | null>(null);
  const [actionError, setActionError] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const selectedAgent =
    agents.find((agent) => agent.actor_id === selectedAgentId) ?? null;

  function closeIssueTokenDialog(open: boolean) {
    setShowIssueToken(open);
    if (!open) {
      setIssuedToken(null);
    }
  }

  function resetCreateAgentDraft() {
    setNewAgentName("");
  }

  function openCreateAgentDialog() {
    resetCreateAgentDraft();
    setShowCreateAgent(true);
  }

  function closeCreateAgentDialog() {
    createAgentOperation.current = null;
    resetCreateAgentDraft();
    setShowCreateAgent(false);
  }

  async function loadAgents({ clearIssuedToken = true }: { clearIssuedToken?: boolean } = {}) {
    if (clearIssuedToken) {
      setIssuedToken(null);
    }
    setLoading(true);
    setLoadError("");
    try {
      const result = await agentAccessApi.listAgents();
      setAgents(result.agents);
      const nextSelected =
        result.agents.find((agent) => agent.actor_id === selectedAgentId) ?? null;
      if (nextSelected) {
        setSelectedAgentId(nextSelected.actor_id);
        setEditAgentName(nextSelected.display_name);
      } else {
        setSelectedAgentId("");
        setEditAgentName("");
      }
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : t("admin.listLoadFailed"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAgents();
  }, []);

  useEffect(() => {
    setProjectId((current) =>
      current && projects.some((project) => project.project_id === current)
        ? current
        : projects[0]?.project_id ?? "",
    );
  }, [projects]);

  async function runAction<T extends MessageReference>(
    actionName: string,
    action: () => Promise<T>,
    onSuccess?: (result: T) => void,
  ) {
    setPendingAction(actionName);
    setActionError("");
    try {
      const result = await action();
      onSuccess?.(result);
      onNotice(result.message_code);
      toast.success(serverMessage(result, t));
      await loadAgents({ clearIssuedToken: actionName !== "issue-agent-token" });
      await onRefresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  async function runProjectAccessAction(
    actionName: string,
    successMessage: string,
    action: () => Promise<void>,
  ) {
    setPendingAction(actionName);
    setActionError("");
    try {
      await action();
      onNotice(successMessage);
      toast.success(successMessage);
      await loadAgents();
      await onRefresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  function openAgent(agent: AgentUserStatus) {
    setIssuedToken(null);
    setSelectedAgentId(agent.actor_id);
    setEditAgentName(agent.display_name);
    setShowEditAgent(true);
  }

  function selectAgent(agent: AgentUserStatus) {
    setIssuedToken(null);
    setSelectedAgentId(agent.actor_id);
    setEditAgentName(agent.display_name);
  }

  function openIssueToken(agent: AgentUserStatus) {
    selectAgent(agent);
    setShowIssueToken(true);
  }

  function resetAgentPermissionDraft() {
    setProjectId(projects[0]?.project_id ?? "");
    setProjectRole("viewer");
    setProjectEffect("allow");
  }

  function closeAgentPermissionDialog() {
    resetAgentPermissionDraft();
    setShowAgentPermission(false);
  }

  function openAgentPermission(agent: AgentUserStatus) {
    selectAgent(agent);
    resetAgentPermissionDraft();
    setShowAgentPermission(true);
  }

  async function copyIssuedToken() {
    if (!issuedToken) return;
    await navigator.clipboard?.writeText(issuedToken.raw_token);
    toast.success(t("agents.tokenCopied"));
  }

  const projectLabelById = new Map(
    projects.map((project) => [project.project_id, project.name]),
  );
  const projectOptions = projects.map((project) => ({
    value: project.project_id,
    label: project.name,
    description: project.role ?? project.membership_status,
  }));
  const canCreateAgent = Boolean(newAgentName.trim());
  const canSaveAgent = Boolean(
    selectedAgent &&
      editAgentName.trim() &&
      editAgentName.trim() !== selectedAgent.display_name,
  );
  const canIssueToken = Boolean(selectedAgent && selectedAgent.status === "active");
  const canGrantAccess = Boolean(
    selectedAgent && selectedAgent.status === "active" && projectId,
  );

  return (
    <section className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title={t("agents.title")} />
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => void loadAgents({ clearIssuedToken: true })}
          >
            {t("ops.refresh")}
          </Button>
          <Button onClick={openCreateAgentDialog}>
            <Plus data-icon="inline-start" />
            {t("agents.createAgent")}
          </Button>
        </div>
      </div>
      <AgentStatusList
        agents={agents}
        loading={loading}
        loadError={loadError}
        pendingAction={pendingAction}
        onRefreshAgents={() => void loadAgents({ clearIssuedToken: true })}
        onSelectAgent={openAgent}
        projectLabelById={projectLabelById}
        onIssueToken={openIssueToken}
        onGrantAccess={openAgentPermission}
        onRevokeToken={(tokenId) =>
          runAction("revoke-agent-token", () => agentAccessApi.revokeAgentToken(tokenId))
        }
        onRevokeGrant={(grantProjectId, grantId) =>
          runProjectAccessAction(
            `revoke-agent-grant-${grantId}`,
            t("projects.memberRevoked"),
            async () => {
              await projectGovernanceApi.removeProjectMember(grantProjectId, grantId);
            },
          )
        }
      />
      <Dialog
        open={showCreateAgent}
        onOpenChange={(open) => {
          if (open) {
            openCreateAgentDialog();
          } else {
            closeCreateAgentDialog();
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("agents.createAgent")}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("agents.manageDescription")}
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="new-agent-name">{t("agents.agentName")}</FieldLabel>
              <Input
                id="new-agent-name"
                value={newAgentName}
                onChange={(event) => setNewAgentName(event.target.value)}
              />
              <FieldDescription>{t("agents.idAutomatic")}</FieldDescription>
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={closeCreateAgentDialog}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() =>
                runAction(
                  "create-agent",
                  () => {
                    const displayName = newAgentName.trim();
                    const operation = retainClientRequestId(
                      createAgentOperation.current,
                      "agent-create",
                      displayName,
                    );
                    createAgentOperation.current = operation;
                    return agentAccessApi.createAgent(
                      displayName,
                      operation.idempotencyKey,
                    );
                  },
                  () => {
                    createAgentOperation.current = null;
                    closeCreateAgentDialog();
                  },
                )
              }
              disabled={pendingAction === "create-agent" || !canCreateAgent}
            >
              {pendingAction === "create-agent" ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <Bot data-icon="inline-start" />
              )}
              {t("agents.createAgent")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={showEditAgent} onOpenChange={setShowEditAgent}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("agents.editTitle")}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("agents.manageDescription")}
            </DialogDescription>
          </DialogHeader>
          <AgentDialogTarget agent={selectedAgent} />
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="edit-agent-name">{t("agents.agentName")}</FieldLabel>
              <Input
                id="edit-agent-name"
                value={editAgentName}
                onChange={(event) => setEditAgentName(event.target.value)}
                disabled={!selectedAgent}
              />
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowEditAgent(false)}>
              {t("admin.cancel")}
            </Button>
            {selectedAgent && (
              selectedAgent.status === "active" ? (
                <ConfirmActionButton
                  ariaLabel={`${t("agents.deactivate")} ${selectedAgent.display_name}`}
                  icon={<UserX data-icon="inline-start" />}
                  disabled={pendingAction === "toggle-agent"}
                  size="default"
                  confirmTitle={t("admin.destructiveConfirmTitle", {
                    action: t("agents.deactivate"),
                  })}
                  confirmDescription={t("admin.destructiveConfirmDescription", {
                    target: selectedAgent.display_name,
                  })}
                  confirmLabel={t("agents.deactivate")}
                  cancelLabel={t("admin.cancel")}
                  onConfirm={() =>
                    runAction("toggle-agent", () =>
                      agentAccessApi.updateAgent(selectedAgent.actor_id, {
                        active: false,
                      }),
                    )
                  }
                >
                  {t("agents.deactivate")}
                </ConfirmActionButton>
              ) : (
                <Button
                  variant="outline"
                  onClick={() =>
                    runAction("toggle-agent", () =>
                      agentAccessApi.updateAgent(selectedAgent.actor_id, {
                        active: true,
                      }),
                    )
                  }
                  disabled={pendingAction === "toggle-agent"}
                >
                  <RotateCcw data-icon="inline-start" />
                  {t("agents.reactivate")}
                </Button>
              )
            )}
            <Button
              onClick={() =>
                selectedAgent &&
                runAction(
                  "update-agent",
                  () =>
                    agentAccessApi.updateAgent(selectedAgent.actor_id, {
                      displayName: editAgentName.trim(),
                    }),
                  () => setShowEditAgent(false),
                )
              }
              disabled={pendingAction === "update-agent" || !canSaveAgent}
            >
              <Save data-icon="inline-start" />
              {t("agents.saveAgent")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={showIssueToken} onOpenChange={closeIssueTokenDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("agents.issueTokenTitle")}</DialogTitle>
            <DialogDescription>{t("agents.issueTokenDescription")}</DialogDescription>
          </DialogHeader>
          <AgentDialogTarget agent={selectedAgent} />
          {issuedToken && (
            <Alert>
              <KeyRound />
              <AlertTitle>{t("agents.copyNow")}</AlertTitle>
              <AlertDescription className="flex flex-col gap-3">
                <span>{serverMessage(issuedToken, t)}</span>
                <Input
                  aria-label={t("agents.issuedToken")}
                  readOnly
                  value={issuedToken.raw_token}
                />
                <span className="text-xs text-muted-foreground">
                  {t("agents.fingerprint")}: {issuedToken.token.token_fingerprint}
                </span>
                <Button variant="outline" size="sm" onClick={copyIssuedToken}>
                  <Clipboard data-icon="inline-start" />
                  {t("agents.copyToken")}
                </Button>
              </AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => closeIssueTokenDialog(false)}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() =>
                selectedAgent &&
                runAction(
                  "issue-agent-token",
                  () => agentAccessApi.issueAgentToken(selectedAgent.actor_id),
                  (result) => {
                    setIssuedToken(result);
                  },
                )
              }
              disabled={pendingAction === "issue-agent-token" || !canIssueToken}
            >
              {pendingAction === "issue-agent-token" ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <KeyRound data-icon="inline-start" />
              )}
              {t("agents.issueToken")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={showAgentPermission}
        onOpenChange={(open) => {
          if (open) {
            setShowAgentPermission(true);
          } else {
            closeAgentPermissionDialog();
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("agents.accessDialogTitle")}</DialogTitle>
            <DialogDescription>{t("agents.accessDialogDescription")}</DialogDescription>
          </DialogHeader>
          <AgentDialogTarget agent={selectedAgent} />
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="agent-project">{t("workspace.project")}</FieldLabel>
              <SearchSelect
                id="agent-project"
                value={projectId}
                options={projectOptions}
                placeholder={t("workspace.chooseProject")}
                emptyText={t("workspace.noProjectsTitle")}
                onValueChange={setProjectId}
              />
              <FieldDescription>{t("agents.projectHelp")}</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="agent-project-role">{t("permissions.role")}</FieldLabel>
              <OptionSelect
                id="agent-project-role"
                value={projectRole}
                options={projectRoleOptions}
                onValueChange={setProjectRole}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="agent-project-effect">{t("permissions.effect")}</FieldLabel>
              <OptionSelect
                id="agent-project-effect"
                value={projectEffect}
                options={[
                  { value: "allow", label: t("permissions.allow") },
                  { value: "deny", label: t("permissions.deny") },
                ]}
                onValueChange={setProjectEffect}
              />
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={closeAgentPermissionDialog}>
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() =>
                selectedAgent &&
                runProjectAccessAction(
                  "grant-agent-access",
                  t("projects.memberActive"),
                  async () => {
                    await projectGovernanceApi.addProjectMember(
                      projectId,
                      "service_account",
                      selectedAgent.actor_id,
                      projectRole,
                      projectEffect,
                    );
                    closeAgentPermissionDialog();
                  },
                )
              }
              disabled={pendingAction === "grant-agent-access" || !canGrantAccess}
            >
              {pendingAction === "grant-agent-access" ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <ShieldCheck data-icon="inline-start" />
              )}
              {t("agents.grantAccess")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
