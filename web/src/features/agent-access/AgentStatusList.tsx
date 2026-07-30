import { Bot, KeyRound, Pencil, ShieldCheck, UserX } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
} from "../../components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "../../components/ui/empty";
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
  localizedStatusLabel,
  StatusBadge,
  StatusRow,
  activateOnEnterOrSpace,
  clickableCardClassName,
  serverMessage,
} from "../../shared/product-ui";
import type { AgentUserStatus } from "./types";

export function AgentStatusList({
  agents,
  loading,
  loadError,
  pendingAction,
  onRefreshAgents,
  onSelectAgent,
  projectLabelById,
  onIssueToken,
  onGrantAccess,
  onRevokeToken,
  onRevokeGrant,
}: {
  agents: AgentUserStatus[];
  loading: boolean;
  loadError: string;
  pendingAction: string;
  onRefreshAgents: () => void;
  onSelectAgent: (agent: AgentUserStatus) => void;
  projectLabelById: Map<string, string>;
  onIssueToken: (agent: AgentUserStatus) => void;
  onGrantAccess: (agent: AgentUserStatus) => void;
  onRevokeToken: (tokenId: string) => void;
  onRevokeGrant: (projectId: string, grantId: string) => void;
}) {
  const { t } = useTranslation();

  return (
    <Card>
      <CardContent className="flex flex-col gap-4 pt-6">
        {loading ? (
          <LoadingState
            title={t("agents.loadingTitle")}
          />
        ) : loadError ? (
          <LoadErrorState
            title={t("admin.listLoadFailed")}
            description={serverMessage(loadError, t)}
            retryLabel={t("admin.retry")}
            onRetry={onRefreshAgents}
          />
        ) : agents.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Bot />
              </EmptyMedia>
              <EmptyTitle>{t("agents.emptyTitle")}</EmptyTitle>
              <EmptyDescription>{t("agents.emptyDescription")}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="grid gap-4">
            {agents.map((agent) => (
              <AgentStatusCard
                key={agent.actor_id}
                agent={agent}
                pendingAction={pendingAction}
                onSelectAgent={onSelectAgent}
                projectLabelById={projectLabelById}
                onIssueToken={onIssueToken}
                onGrantAccess={onGrantAccess}
                onRevokeToken={onRevokeToken}
                onRevokeGrant={onRevokeGrant}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AgentStatusCard({
  agent,
  pendingAction,
  onSelectAgent,
  projectLabelById,
  onIssueToken,
  onGrantAccess,
  onRevokeToken,
  onRevokeGrant,
}: {
  agent: AgentUserStatus;
  pendingAction: string;
  onSelectAgent: (agent: AgentUserStatus) => void;
  projectLabelById: Map<string, string>;
  onIssueToken: (agent: AgentUserStatus) => void;
  onGrantAccess: (agent: AgentUserStatus) => void;
  onRevokeToken: (tokenId: string) => void;
  onRevokeGrant: (projectId: string, grantId: string) => void;
}) {
  const { t } = useTranslation();
  const activeGrants = agent.project_grants;
  return (
    <div
      className={`${clickableCardClassName} p-4`}
      role="button"
      tabIndex={0}
      aria-label={agent.display_name}
      onClick={() => onSelectAgent(agent)}
      onKeyDown={(event) => activateOnEnterOrSpace(event, () => onSelectAgent(agent))}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium">{agent.display_name}</div>
          <div className="text-sm text-muted-foreground">{t("users.serviceAccount")}</div>
        </div>
        <StatusBadge
          semantic={agent.status === "active" ? "success" : "inactive"}
          label={localizedStatusLabel(agent.status, t)}
        />
      </div>
      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <StatusRow
          label={t("agents.projectAccess")}
          value={
            activeGrants.some((grant) => grant.effect === "allow")
              ? t("workspace.active")
              : t("workspace.needed")
          }
          good={activeGrants.some((grant) => grant.effect === "allow")}
        />
        <StatusRow
          label={t("agents.activeTokens")}
          value={String(agent.tokens.filter((token) => token.status === "active").length)}
          good={agent.tokens.some((token) => token.status === "active")}
        />
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          aria-label={`${t("admin.edit")} ${agent.display_name}`}
          onClick={(event) => {
            event.stopPropagation();
            onSelectAgent(agent);
          }}
        >
          <Pencil data-icon="inline-start" />
          {t("admin.edit")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          aria-label={`${t("agents.issueToken")} ${agent.display_name}`}
          onClick={(event) => {
            event.stopPropagation();
            onIssueToken(agent);
          }}
          disabled={agent.status !== "active"}
        >
          <KeyRound data-icon="inline-start" />
          {t("agents.issueToken")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          aria-label={`${t("agents.grantAccess")} ${agent.display_name}`}
          onClick={(event) => {
            event.stopPropagation();
            onGrantAccess(agent);
          }}
          disabled={agent.status !== "active"}
        >
          <ShieldCheck data-icon="inline-start" />
          {t("agents.grantAccess")}
        </Button>
      </div>
      <div className="mt-4 grid gap-3">
        {activeGrants.map((grant) => (
          <ConfirmActionButton
            key={grant.grant_id}
            ariaLabel={t("agents.revokeAccess", {
              projectId:
                projectLabelById.get(grant.project_id) ??
                readableProjectId(grant.project_id),
            })}
            icon={<UserX data-icon="inline-start" />}
            disabled={pendingAction === `revoke-agent-grant-${grant.grant_id}`}
            confirmTitle={t("admin.destructiveConfirmTitle", {
              action: t("agents.revokeAccess", {
                projectId:
                  projectLabelById.get(grant.project_id) ??
                  readableProjectId(grant.project_id),
              }),
            })}
            confirmDescription={t("admin.destructiveConfirmDescription", {
              target: agent.display_name,
            })}
            confirmLabel={t("agents.revokeAccess", {
              projectId:
                projectLabelById.get(grant.project_id) ??
                readableProjectId(grant.project_id),
            })}
            cancelLabel={t("admin.cancel")}
            onConfirm={() => onRevokeGrant(grant.project_id, grant.grant_id)}
          >
            {`${t("agents.revokeAccess", {
              projectId:
                projectLabelById.get(grant.project_id) ??
                readableProjectId(grant.project_id),
            })} · ${grant.role} · ${grant.effect}`}
          </ConfirmActionButton>
        ))}
      </div>
      {agent.tokens.length > 0 && (
        <div className="mt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("agents.token")}</TableHead>
                <TableHead>{t("agents.status")}</TableHead>
                <TableHead>{t("agents.action")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {agent.tokens.map((token, index) => {
                const tokenLabel = t("agents.tokenLabel", { index: index + 1 });
                return (
                  <TableRow key={token.token_id}>
                    <TableCell>{tokenLabel}</TableCell>
                    <TableCell>
                      <StatusBadge
                        semantic={token.status === "active" ? "success" : "inactive"}
                        label={localizedStatusLabel(token.status, t)}
                      />
                    </TableCell>
                    <TableCell>
                      <ConfirmActionButton
                        ariaLabel={t("agents.revokeTokenLabel", {
                          agent: agent.display_name,
                          token: tokenLabel,
                        })}
                        icon={<UserX data-icon="inline-start" />}
                        disabled={
                          token.status !== "active" || pendingAction === "revoke-agent-token"
                        }
                        confirmTitle={t("admin.destructiveConfirmTitle", {
                          action: t("agents.revokeToken"),
                        })}
                        confirmDescription={t("admin.destructiveConfirmDescription", {
                          target: `${agent.display_name} / ${tokenLabel}`,
                        })}
                        confirmLabel={t("agents.revokeToken")}
                        cancelLabel={t("admin.cancel")}
                        onConfirm={() => onRevokeToken(token.token_id)}
                      >
                        {t("agents.revokeToken")}
                      </ConfirmActionButton>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

function readableProjectId(projectId: string) {
  return projectId
    .replace(/^proj-/, "")
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
