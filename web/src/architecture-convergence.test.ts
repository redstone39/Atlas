import { existsSync, readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { en } from "./locales/en";
import { zhTW } from "./locales/zh-TW";

const webRoot = process.cwd();
const productionRoot = resolve(webRoot, "..");

function activeUiSourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = resolve(directory, entry.name);
    if (entry.isDirectory()) return activeUiSourceFiles(entryPath);
    if (!/\.(?:jsx|tsx)$/.test(entry.name) || entry.name.includes(".test.")) return [];
    return [entryPath];
  });
}

describe("frontend compatibility convergence", () => {
  it("keeps the simplified Workspace labels and source-warning contract", () => {
    expect(zhTW["workspace.reasoningMode"]).toBe("回答方式");
    expect(zhTW["workspace.reasoningModeStandard"]).toBe("一般");
    expect(zhTW["workspace.reasoningModeDeep"]).toBe("深入");
    expect(zhTW["workspace.needsHumanReview"]).toBe("請確認來源");
    expect(en["workspace.reasoningMode"]).toBe("Answer style");
    expect(en["workspace.reasoningModeStandard"]).toBe("General");
    expect(en["workspace.reasoningModeDeep"]).toBe("In-depth");
    expect(en["workspace.needsHumanReview"]).toBe("Check sources");

    const evidenceSummary = readFileSync(
      resolve(webRoot, "src/features/workspace/AnswerEvidenceSummary.tsx"),
      "utf8",
    );
    expect(evidenceSummary).not.toContain('t("workspace.evidenceAligned")');
    expect(evidenceSummary).toContain('t("workspace.needsHumanReview")');
  });

  it("removes root facades and stale upper-layer navigation files", () => {
    for (const relative of [
      "src/api.ts",
      "src/types.ts",
      "src/app/ManagementNav.tsx",
      "src/app/routes.ts",
      "src/shared/setup-recovery.tsx",
      "src/shared/TargetDocumentsPanel.tsx",
      "src/pages/AdminIngestionPage.tsx",
      "src/pages/AdminPermissionsPage.tsx",
      "src/shared/download-center.tsx",
      "src/shared/download-delivery.ts",
    ]) {
      expect(existsSync(resolve(webRoot, relative)), relative).toBe(false);
    }
    expect(
      readFileSync(resolve(webRoot, "src/pages/SettingsPage.tsx"), "utf8"),
    ).not.toContain('from "../app/');

    const routeSource = readFileSync(resolve(webRoot, "src/shared/routes.ts"), "utf8");
    const navigationSource = readFileSync(
      resolve(webRoot, "src/shared/navigation.tsx"),
      "utf8",
    );
    const testHandlers = readFileSync(
      resolve(webRoot, "src/App.test-support.ts"),
      "utf8",
    );
    for (const retiredPath of ["/admin/ingestion", "/admin/permissions"]) {
      expect(routeSource).not.toContain(retiredPath);
      expect(navigationSource).not.toContain(retiredPath);
      expect(testHandlers).not.toContain(retiredPath);
    }
    expect(testHandlers).not.toContain("download-deliveries");

    const humanSmoke = readFileSync(
      resolve(productionRoot, "infra/scripts/browser_smoke_human_operable.mjs"),
      "utf8",
    );
    for (const retiredConsumer of [
      'name: "Permissions"',
      "set permission",
      "create permission",
      'name: /documents/i',
      'getByLabel("Document title")',
      "prepare evidence",
    ]) {
      expect(humanSmoke).not.toContain(retiredConsumer);
    }
    expect(humanSmoke).toContain('name: "Document Library"');
    expect(humanSmoke).toContain('getByLabel("Add existing user")');

    for (const locale of ["en.ts", "zh-TW.ts"]) {
      const localeSource = readFileSync(resolve(webRoot, "src/locales", locale), "utf8");
      expect(localeSource).not.toContain('"nav.permissions"');
      expect(localeSource).not.toContain('"recovery.openPermissions"');
      expect(localeSource).not.toContain('"settings.managementPermissions"');
    }
  });

  it("keeps Project Members on the canonical access-grant response shape", () => {
    const typeSource = readFileSync(
      resolve(webRoot, "src/features/project-governance/types.ts"),
      "utf8",
    );
    const apiSource = readFileSync(
      resolve(webRoot, "src/features/project-governance/api.ts"),
      "utf8",
    );

    const grantContract = typeSource.match(
      /export interface ProjectAccessGrant \{([\s\S]*?)\n\}/,
    )?.[1];
    expect(grantContract).toBeDefined();
    expect(typeSource).toContain("grants: ProjectAccessGrant[]");
    expect(grantContract).not.toContain("display_name");
    expect(grantContract).not.toContain("display_detail");
    expect(apiSource).toMatch(/removeProjectMember:[\s\S]*requestJson<ProjectAccessGrant>/);
  });

  it("keeps contextual Project access mutations on Project and Agent surfaces", () => {
    const projectSource = readFileSync(
      resolve(webRoot, "src/features/project-governance/ProjectGovernanceFeature.tsx"),
      "utf8",
    );
    const teamSource = readFileSync(
      resolve(webRoot, "src/features/team-administration/TeamAdministrationFeature.tsx"),
      "utf8",
    );
    const userSource = readFileSync(
      resolve(webRoot, "src/features/user-administration/UserAdministrationFeature.tsx"),
      "utf8",
    );
    const agentSource = readFileSync(
      resolve(webRoot, "src/features/agent-access/AgentAccessFeature.tsx"),
      "utf8",
    );

    expect(projectSource).toContain("projectGovernanceApi.addProjectMember");
    expect(projectSource).toContain("projectGovernanceApi.removeProjectMember");
    expect(agentSource).toContain("projectGovernanceApi.addProjectMember");
    expect(agentSource).toContain("projectGovernanceApi.removeProjectMember");
    expect(agentSource).toContain('"service_account"');
    expect(agentSource).not.toContain("permissionManagementApi");
    expect(agentSource).not.toMatch(/value:\s*["']owner["']/);
    for (const source of [teamSource, userSource]) {
      expect(source).not.toContain("projectGovernanceApi");
      expect(source).not.toContain("addProjectMember");
      expect(source).not.toContain("removeProjectMember");
    }
    for (const source of [teamSource, projectSource]) {
      expect(source).not.toContain("onOpenDocumentLibrary");
      expect(source).not.toMatch(/TabsTrigger value=["']documents["']/);
    }
  });

  it("registers Ops, closes compatibility exceptions, and empties baseline", () => {
    const registry = JSON.parse(
      readFileSync(resolve(productionRoot, "architecture-boundaries.json"), "utf8"),
    ) as {
      owners: Array<{ id: string; public_contracts: string[] }>;
      ownership_exceptions: Array<{ id: string }>;
    };
    const frontendFeatures = registry.owners.find(
      (owner) => owner.id === "frontend_features",
    );
    expect(frontendFeatures?.public_contracts).toContain(
      "web/src/features/ops/index.ts",
    );
    expect(registry.owners.some((owner) => owner.id === "frontend_compatibility")).toBe(false);
    expect(
      registry.ownership_exceptions.some((item) => item.id.startsWith("frontend-cross-domain")),
    ).toBe(false);

    const baseline = JSON.parse(
      readFileSync(resolve(productionRoot, "architecture-baseline.json"), "utf8"),
    ) as { frozen_violations: unknown[] };
    expect(baseline.frozen_violations).toEqual([]);
  });

  it("keeps browser-native choice lists out of active UI source", () => {
    for (const file of activeUiSourceFiles(resolve(webRoot, "src"))) {
      const source = readFileSync(file, "utf8");
      expect(source, file).not.toMatch(/<(?:select|datalist)\b/);
      expect(source, file).not.toMatch(/\blist\s*=/);
    }
  });

  it("keeps active tabs on the shared line-style default", () => {
    const tabsSource = readFileSync(
      resolve(webRoot, "src/components/ui/tabs.tsx"),
      "utf8",
    );
    expect(tabsSource).toContain('variant = "line"');
    expect(tabsSource).toMatch(/defaultVariants:\s*{\s*variant: "line"/);
    expect(tabsSource).toContain("data-[state=inactive]:border-transparent");
    expect(tabsSource).toContain("data-[state=inactive]:shadow-none");
    expect(tabsSource).toContain("focus-visible:ring-[3px]");
    expect(tabsSource).toContain("focus-visible:outline-ring");

    for (const file of activeUiSourceFiles(resolve(webRoot, "src"))) {
      if (file.endsWith("/components/ui/tabs.tsx")) continue;
      const source = readFileSync(file, "utf8");
      expect(source, file).not.toMatch(/<TabsList\b[^>]*variant=["']default["']/);
      expect(source, file).not.toMatch(/<TabsList\b[^>]*className=/);
    }
  });

  it("keeps every centered modal on the shared large width", () => {
    const dialogSource = readFileSync(
      resolve(webRoot, "src/components/ui/dialog.tsx"),
      "utf8",
    );
    const alertDialogSource = readFileSync(
      resolve(webRoot, "src/components/ui/alert-dialog.tsx"),
      "utf8",
    );
    expect(dialogSource).toContain("sm:max-w-3xl");
    expect(dialogSource).toContain('size?: "default" | "wide"');
    expect(dialogSource).toContain("data-[size=wide]:sm:max-w-6xl");
    expect(alertDialogSource).toContain("sm:max-w-3xl");
    expect(alertDialogSource).not.toMatch(/data-\[size=sm\]:max-w-/);

    for (const file of activeUiSourceFiles(resolve(webRoot, "src"))) {
      if (
        file.endsWith("/components/ui/dialog.tsx") ||
        file.endsWith("/components/ui/alert-dialog.tsx")
      ) continue;
      const source = readFileSync(file, "utf8");
      expect(source, file).not.toMatch(
        /<(?:DialogContent|AlertDialogContent)\b[^>]*(?:sm:)?max-w-/s,
      );
    }
  });

  it("owns shared sidebar chrome without a global authenticated header", () => {
    const accountMenuSource = readFileSync(
      resolve(webRoot, "src/app/AccountMenu.tsx"),
      "utf8",
    );
    const productShellSource = readFileSync(
      resolve(webRoot, "src/app/ProductShell.tsx"),
      "utf8",
    );
    const documentContentSource = readFileSync(
      resolve(webRoot, "src/shared/document-content.ts"),
      "utf8",
    );
    const workspaceSource = readFileSync(
      resolve(webRoot, "src/features/workspace/WorkspaceFeature.tsx"),
      "utf8",
    );
    const appSource = readFileSync(resolve(webRoot, "src/App.tsx"), "utf8");

    expect(accountMenuSource).toContain("export function AccountMenu");
    expect(accountMenuSource).not.toContain("download-center");
    expect(accountMenuSource).not.toContain('t("downloads.title")');
    expect(productShellSource).toContain('from "./AccountMenu"');
    expect(productShellSource).not.toContain("DownloadCenter");
    expect(productShellSource).not.toContain("<Bell");
    expect(documentContentSource).toContain("/api/v1/library/documents/");
    expect(documentContentSource).toContain('method: "HEAD"');
    expect(documentContentSource).not.toContain("download-deliveries");
    expect(documentContentSource).not.toMatch(/requestBlob|createObjectURL|response\.blob/);
    expect(appSource).toContain('from "./app/AccountMenu"');
    expect(appSource).toContain("renderAccountMenu={(options)");
    expect(workspaceSource).toContain("renderAccountMenu({");
    expect(workspaceSource).toContain('t("nav.knowledgeLibrary")');
    expect(workspaceSource).toContain('activeView === "/library"');
    expect(workspaceSource).toContain("onCollapseSidebar");
    expect(workspaceSource).toContain('data-slot="workspace-conversation-controls"');
    expect(workspaceSource).toContain('data-slot="workspace-composer"');
    expect(workspaceSource).not.toContain('from "../../app');
    expect(productShellSource).not.toMatch(/<header\b/);
    expect(productShellSource).toContain("export function SidebarHeader");
    expect(productShellSource).toContain('route.startsWith("/workspace/conversations/")');
    expect(productShellSource).not.toContain('{ route: "/library" as const');
    expect(accountMenuSource).not.toContain('onNavigate("/library")');
    expect(appSource).toContain("renderSidebarHeader={(options)");

    const accountMenuDefinitions = activeUiSourceFiles(resolve(webRoot, "src"))
      .filter((file) => readFileSync(file, "utf8").includes("function AccountMenu"));
    expect(accountMenuDefinitions).toEqual([resolve(webRoot, "src/app/AccountMenu.tsx")]);
  });

  it("keeps every data-backed route on an explicit initial loading surface", () => {
    const loadingStateSource = readFileSync(
      resolve(webRoot, "src/shared/product-ui.tsx"),
      "utf8",
    );
    expect(loadingStateSource).toContain('role="status"');
    expect(loadingStateSource).toContain('aria-busy="true"');

    for (const relative of [
      "src/features/workspace/WorkspaceFeature.tsx",
      "src/features/knowledge-library/KnowledgeLibraryFeature.tsx",
      "src/features/document-library/DocumentLibraryFeature.tsx",
      "src/features/user-administration/UserAdministrationFeature.tsx",
      "src/features/team-administration/TeamAdministrationFeature.tsx",
      "src/features/team-administration/ScopedTeamAdministrationFeature.tsx",
      "src/features/project-governance/ProjectGovernanceFeature.tsx",
      "src/features/model-routing/ModelRoutingFeature.tsx",
      "src/features/processing-plugins/ProcessingPluginsFeature.tsx",
      "src/features/agent-access/AgentStatusList.tsx",
      "src/features/conversation-audit/ConversationAuditFeature.tsx",
      "src/pages/OpsPage.tsx",
    ]) {
      expect(readFileSync(resolve(webRoot, relative), "utf8"), relative)
        .toContain("LoadingState");
    }

    const documentLibrarySource = readFileSync(
      resolve(webRoot, "src/features/document-library/DocumentLibraryFeature.tsx"),
      "utf8",
    );
    const knowledgeLibrarySource = readFileSync(
      resolve(webRoot, "src/features/knowledge-library/KnowledgeLibraryFeature.tsx"),
      "utf8",
    );
    expect(documentLibrarySource).toContain("scopeLoading || !documentsReady");
    expect(documentLibrarySource).toContain("scopeLoadError");
    for (const source of [documentLibrarySource, knowledgeLibrarySource]) {
      expect(source).toContain("document.download_available");
      expect(source).not.toMatch(/document\.content_type|application\/pdf|openxmlformats/);
    }

    const workspaceSource = readFileSync(
      resolve(webRoot, "src/features/workspace/WorkspaceFeature.tsx"),
      "utf8",
    );
    expect(workspaceSource).toContain("historyLoadError");

    const appSource = readFileSync(resolve(webRoot, "src/App.tsx"), "utf8");
    const opsPageSource = readFileSync(resolve(webRoot, "src/pages/OpsPage.tsx"), "utf8");
    expect(appSource).not.toContain("readinessLoadError");
    expect(opsPageSource).toContain("loadError");
  });
});
