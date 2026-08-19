import {
  Bot,
  Boxes,
  ContactRound,
  Cpu,
  FileText,
  History,
  Network,
  Route,
  ShieldCheck,
  Users,
  WandSparkles,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "../components/ui/button";
import { cn } from "../lib/utils";
import { managementRouteFamily, type AppRoute } from "./routes";

export interface ManagementCapabilities {
  system_role: "user" | "admin" | "operator" | null;
  team_roles: Readonly<Record<string, "member" | "uploader" | "admin">>;
  available_projects: ReadonlyArray<{
    membership_status: "active" | "revoked" | "missing";
    role: "viewer" | "contributor" | "admin" | null;
  }>;
}

export type ManagementNavItem = {
  route: AppRoute;
  titleKey: string;
  descriptionKey: string;
  icon: LucideIcon;
};

export type ManagementNavGroup = {
  titleKey: string;
  items: ManagementNavItem[];
};

const USERS_ITEM: ManagementNavItem = {
  route: "/admin/users",
  titleKey: "nav.users",
  descriptionKey: "settings.managementUsers",
  icon: Users,
};
const DIRECTORY_ITEM: ManagementNavItem = {
  route: "/admin/directory",
  titleKey: "nav.directory",
  descriptionKey: "settings.managementDirectory",
  icon: ContactRound,
};
const TEAMS_ITEM: ManagementNavItem = {
  route: "/admin/teams",
  titleKey: "nav.teams",
  descriptionKey: "settings.managementTeams",
  icon: Network,
};
const PROJECTS_ITEM: ManagementNavItem = {
  route: "/admin/projects",
  titleKey: "nav.projects",
  descriptionKey: "settings.managementProjects",
  icon: ShieldCheck,
};
const DOCUMENT_LIBRARY_ITEM: ManagementNavItem = {
  route: "/admin/document-library",
  titleKey: "nav.documentLibrary",
  descriptionKey: "settings.managementDocumentLibrary",
  icon: FileText,
};
const MODELS_ITEM: ManagementNavItem = {
  route: "/admin/models",
  titleKey: "nav.models",
  descriptionKey: "settings.managementModels",
  icon: Cpu,
};
const PROMPT_SKILLS_ITEM: ManagementNavItem = {
  route: "/admin/prompt-skills",
  titleKey: "nav.promptSkills",
  descriptionKey: "settings.managementPromptSkills",
  icon: WandSparkles,
};
const PROCESSING_PLUGINS_ITEM: ManagementNavItem = {
  route: "/admin/plugins",
  titleKey: "nav.plugins",
  descriptionKey: "settings.managementPlugins",
  icon: Boxes,
};
const AGENTS_ITEM: ManagementNavItem = {
  route: "/admin/agents",
  titleKey: "nav.agents",
  descriptionKey: "settings.managementAgents",
  icon: Bot,
};
const AUDIT_ITEM: ManagementNavItem = {
  route: "/admin/audit",
  titleKey: "nav.audit",
  descriptionKey: "settings.managementAudit",
  icon: History,
};
const SYSTEM_STATUS_ITEM: ManagementNavItem = {
  route: "/admin/ops",
  titleKey: "nav.ops",
  descriptionKey: "settings.managementOps",
  icon: Route,
};
export const IDENTITY_ACCESS_ITEMS = [
  USERS_ITEM,
  DIRECTORY_ITEM,
  TEAMS_ITEM,
  PROJECTS_ITEM,
];
export const KNOWLEDGE_CONTENT_ITEMS = [DOCUMENT_LIBRARY_ITEM];
export const AI_AUTOMATION_ITEMS = [
  MODELS_ITEM,
  PROMPT_SKILLS_ITEM,
  PROCESSING_PLUGINS_ITEM,
  AGENTS_ITEM,
];
export const SYSTEM_OPERATIONS_ITEMS = [AUDIT_ITEM, SYSTEM_STATUS_ITEM];
export const ADMIN_MANAGEMENT_ITEMS: ManagementNavItem[] = [
  ...IDENTITY_ACCESS_ITEMS,
  ...KNOWLEDGE_CONTENT_ITEMS,
  ...AI_AUTOMATION_ITEMS,
  ...SYSTEM_OPERATIONS_ITEMS,
];
export const OPERATOR_MANAGEMENT_ITEMS: ManagementNavItem[] = [SYSTEM_STATUS_ITEM];

export function managementItemsForRole(
  role: ManagementCapabilities["system_role"],
): ManagementNavItem[] {
  if (role === "admin") return ADMIN_MANAGEMENT_ITEMS;
  if (role === "operator") return OPERATOR_MANAGEMENT_ITEMS;
  return [];
}

export function managementGroupsForCapabilities(
  session: ManagementCapabilities,
): ManagementNavGroup[] {
  if (session.system_role === "admin") {
    return [
      { titleKey: "settings.identityAccess", items: IDENTITY_ACCESS_ITEMS },
      { titleKey: "settings.knowledgeContent", items: KNOWLEDGE_CONTENT_ITEMS },
      { titleKey: "settings.aiAutomation", items: AI_AUTOMATION_ITEMS },
      { titleKey: "settings.systemOperations", items: SYSTEM_OPERATIONS_ITEMS },
    ];
  }
  if (session.system_role === "operator") {
    return [{ titleKey: "settings.systemOperations", items: OPERATOR_MANAGEMENT_ITEMS }];
  }
  const identityItems = [
    ...(hasTeamManagementAccess(session) ? [TEAMS_ITEM] : []),
    ...(hasProjectManagementAccess(session) ? [PROJECTS_ITEM] : []),
  ];
  const knowledgeItems = hasDocumentLibraryAccess(session)
    ? [DOCUMENT_LIBRARY_ITEM]
    : [];
  return [
    ...(identityItems.length > 0
      ? [{ titleKey: "settings.identityAccess", items: identityItems }]
      : []),
    ...(knowledgeItems.length > 0
      ? [{ titleKey: "settings.knowledgeContent", items: knowledgeItems }]
      : []),
  ];
}

export function ManagementNav({
  items,
  groups,
  currentRoute,
  onNavigate,
  className = "",
}: {
  items?: ManagementNavItem[];
  groups?: ManagementNavGroup[];
  currentRoute?: AppRoute;
  onNavigate: (route: AppRoute) => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const visibleGroups = groups ?? [{ titleKey: "", items: items ?? [] }];
  const activeRoute = currentRoute ? managementRouteFamily(currentRoute) ?? currentRoute : undefined;
  return (
    <nav
      aria-label={t("settings.management")}
      className={cn("flex h-full min-h-0 w-full flex-col", className)}
    >
      <div
        data-slot="management-nav-scroll"
        className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-3"
      >
        {visibleGroups.map((group) => (
          <div
            key={group.titleKey || "default"}
            data-slot="management-nav-group"
            className="flex shrink-0 flex-col gap-1"
          >
            {group.titleKey && (
              <div
                data-slot="management-nav-group-label"
                className="px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground"
              >
                {t(group.titleKey)}
              </div>
            )}
            {group.items.map((item) => {
              const Icon = item.icon;
              const isActive = activeRoute === item.route;
              return (
                <Button
                  key={item.route}
                  variant={isActive ? "secondary" : "ghost"}
                  className="h-auto w-full shrink-0 justify-start px-3 py-2 text-left"
                  onClick={() => onNavigate(item.route)}
                  title={t(item.descriptionKey)}
                  aria-current={isActive ? "page" : undefined}
                >
                  <Icon data-icon="inline-start" />
                  <span className="min-w-0 truncate">{t(item.titleKey)}</span>
                </Button>
              );
            })}
          </div>
        ))}
      </div>
    </nav>
  );
}

function hasDocumentLibraryAccess(session: ManagementCapabilities) {
  if (Object.values(session.team_roles).some((role) => role === "uploader" || role === "admin")) {
    return true;
  }
  return session.available_projects.some(
    (project) =>
      project.membership_status === "active" &&
      ["contributor", "admin"].includes(project.role ?? ""),
  );
}

function hasProjectManagementAccess(session: ManagementCapabilities) {
  return session.available_projects.some(
    (project) => project.membership_status === "active" && project.role === "admin",
  );
}

function hasTeamManagementAccess(session: ManagementCapabilities) {
  return Object.values(session.team_roles).some((role) => role === "admin");
}
