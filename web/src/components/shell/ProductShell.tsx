import { MessageSquareText, PanelLeft, PanelLeftClose, Settings } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "../ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "../ui/sheet";
import type { SessionState } from "../../features/identity-session/index";
import {
  ManagementNav,
  type ManagementNavGroup,
} from "../../shared/navigation";
import { managementRouteFamily, type AppRoute } from "../../shared/routes";
import { AccountMenu } from "./AccountMenu";

export function SidebarHeader({
  onNavigate,
  onOpenWorkspace,
  onCollapseSidebar,
  presentation = "full",
}: {
  onNavigate: (route: AppRoute) => void;
  onOpenWorkspace?: () => void;
  onCollapseSidebar?: () => void;
  presentation?: "full" | "compact";
}) {
  const { t } = useTranslation();
  const compact = presentation === "compact";

  return (
    <div
      data-slot="contextual-sidebar-header"
      className={compact
        ? "flex shrink-0 flex-col items-center gap-1 border-b p-2"
        : "flex min-h-14 shrink-0 items-center gap-1 border-b px-3"}
    >
      <Button
        type="button"
        variant="ghost"
        size={compact ? "icon-sm" : "sm"}
        className={compact ? "shrink-0" : "min-w-0 flex-1 justify-start"}
        onClick={onOpenWorkspace ?? (() => onNavigate("/workspace"))}
        aria-label={t("app.brand")}
        title={t("nav.workspace")}
      >
        <span className="font-semibold">
          {compact ? t("app.brandShort") : t("app.brand")}
        </span>
      </Button>
      {!compact && onCollapseSidebar && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={onCollapseSidebar}
          aria-label={t("workspace.closeHistory")}
          title={t("workspace.closeHistory")}
          className="shrink-0"
        >
          <PanelLeftClose />
        </Button>
      )}
    </div>
  );
}

export function ProductShell({
  route,
  session,
  managementGroups,
  onNavigate,
  onLogout,
  children,
}: {
  route: AppRoute;
  session: SessionState;
  managementGroups: ManagementNavGroup[];
  onNavigate: (route: AppRoute) => void;
  onLogout: () => Promise<void>;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const isWorkspaceSurface =
    route === "/workspace" ||
    route === "/library" ||
    route.startsWith("/workspace/conversations/");
  const managementRoute = managementRouteFamily(route);
  const currentManagementRouteIsVisible = managementGroups.some((group) =>
    group.items.some((item) => item.route === managementRoute),
  );
  const showManagementNavigation =
    (route === "/settings" && managementGroups.length > 0) ||
    (managementRoute !== null && currentManagementRouteIsVisible);

  function navigateFromMobile(nextRoute: AppRoute) {
    setMobileNavigationOpen(false);
    onNavigate(nextRoute);
  }

  function logoutFromMobile() {
    setMobileNavigationOpen(false);
    return onLogout();
  }

  const navigation = showManagementNavigation ? (
    <ManagementNav
      groups={managementGroups}
      currentRoute={route}
      onNavigate={onNavigate}
      className="min-h-0 flex-1"
    />
  ) : (
    <ProductNav route={route} onNavigate={onNavigate} />
  );
  const mobileNavigation = showManagementNavigation ? (
    <ManagementNav
      groups={managementGroups}
      currentRoute={route}
      onNavigate={navigateFromMobile}
      className="min-h-0 flex-1"
    />
  ) : (
    <ProductNav route={route} onNavigate={navigateFromMobile} />
  );
  const navigationTitle = showManagementNavigation
    ? t("settings.management")
    : t("nav.product");
  return (
    <div className="min-h-dvh bg-background text-foreground">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:rounded-md focus:bg-background focus:px-4 focus:py-2 focus:ring-2"
        >
          {t("app.skip")}
        </a>
        <main id="main-content" className="min-w-0">
          {isWorkspaceSurface ? (
            children
          ) : (
            <>
              <Sheet open={mobileNavigationOpen} onOpenChange={setMobileNavigationOpen}>
                <SheetContent
                  side="left"
                  className="w-[20rem] max-w-[85vw] gap-0 p-0"
                  showCloseButton={false}
                >
                  <SheetHeader className="sr-only">
                    <SheetTitle>{navigationTitle}</SheetTitle>
                    <SheetDescription>{t("nav.navigationDescription")}</SheetDescription>
                  </SheetHeader>
                  <SidebarHeader onNavigate={navigateFromMobile} />
                  {mobileNavigation}
                  <ContextualSidebarFooter>
                    <AccountMenu
                      session={session}
                      onNavigate={navigateFromMobile}
                      onLogout={logoutFromMobile}
                      className="w-full"
                    />
                  </ContextualSidebarFooter>
                </SheetContent>
              </Sheet>
              <div className="relative flex h-dvh min-h-[32rem] overflow-hidden bg-background">
                <Button
                  type="button"
                  variant="outline"
                  size="icon-sm"
                  className="absolute left-3 top-3 md:hidden"
                  onClick={() => setMobileNavigationOpen(true)}
                  aria-label={t("nav.openNavigation")}
                  title={t("nav.openNavigation")}
                >
                  <PanelLeft />
                </Button>
                <aside
                  data-slot="contextual-sidebar"
                  aria-hidden={mobileNavigationOpen || undefined}
                  className="hidden h-full w-64 shrink-0 flex-col border-r bg-muted/20 md:flex"
                >
                  <SidebarHeader onNavigate={onNavigate} />
                  {navigation}
                  <ContextualSidebarFooter>
                    <AccountMenu
                      session={session}
                      onNavigate={onNavigate}
                      onLogout={onLogout}
                      className="w-full"
                    />
                  </ContextualSidebarFooter>
                </aside>
                <div className="min-w-0 flex-1 overflow-y-auto px-3 pb-4 pt-16 md:px-6 md:py-4">
                  {children}
                </div>
              </div>
            </>
          )}
        </main>
    </div>
  );
}

function ProductNav({
  route,
  onNavigate,
}: {
  route: AppRoute;
  onNavigate: (route: AppRoute) => void;
}) {
  const { t } = useTranslation();
  const items = [
    { route: "/workspace" as const, label: t("nav.workspace"), icon: MessageSquareText },
    { route: "/settings" as const, label: t("nav.settings"), icon: Settings },
  ];

  return (
    <nav aria-label={t("nav.product")} className="min-h-0 flex-1 overflow-y-auto p-3">
      <div className="flex flex-col gap-1">
        {items.map((item) => {
          const Icon = item.icon;
          const isActive = route === item.route;
          return (
            <Button
              key={item.route}
              type="button"
              variant={isActive ? "secondary" : "ghost"}
              className="w-full justify-start"
              onClick={() => onNavigate(item.route)}
              aria-current={isActive ? "page" : undefined}
            >
              <Icon data-icon="inline-start" />
              {item.label}
            </Button>
          );
        })}
      </div>
    </nav>
  );
}

function ContextualSidebarFooter({ children }: { children: ReactNode }) {
  return (
    <div
      data-slot="contextual-sidebar-footer"
      className="shrink-0 border-t p-3"
    >
      {children}
    </div>
  );
}
