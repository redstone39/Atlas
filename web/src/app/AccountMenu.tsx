import { ChevronsUpDown, LogOut, Settings, UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import type { SessionState } from "../features/identity-session/index";
import { cn } from "../lib/utils";
import type { AppRoute } from "../shared/routes";

export function AccountMenu({
  session,
  onNavigate,
  onLogout,
  presentation = "full",
  className,
  menuAlign = "start",
  menuSide = "top",
}: {
  session: SessionState;
  onNavigate: (route: AppRoute) => void;
  onLogout: () => Promise<void>;
  presentation?: "full" | "compact";
  className?: string;
  menuAlign?: "start" | "center" | "end";
  menuSide?: "top" | "right" | "bottom" | "left";
}) {
  const { t } = useTranslation();
  const displayName = session.actor?.display_name ?? t("app.unknownRole");
  const roleLabel = session.system_role ?? t("app.unknownRole");
  const isCompact = presentation === "compact";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size={isCompact ? "icon-sm" : "sm"}
          className={cn(
            isCompact
              ? "shrink-0"
              : "h-auto min-h-10 w-full max-w-full justify-start gap-2 px-2 py-1.5",
            className,
          )}
          aria-label={t("nav.accountMenu")}
          title={t("nav.accountMenu")}
          data-presentation={presentation}
        >
          <UserRound data-icon="inline-start" />
          {!isCompact && (
            <>
              <span className="flex min-w-0 flex-1 flex-col items-start leading-tight">
                <span className="max-w-full truncate font-medium">
                  {displayName}
                </span>
                <span className="max-w-full truncate text-xs font-normal text-muted-foreground">
                  {roleLabel}
                </span>
              </span>
              <ChevronsUpDown
                data-icon="inline-end"
                className="text-muted-foreground"
              />
            </>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side={menuSide} align={menuAlign} className="w-60">
        <DropdownMenuLabel className="min-w-0">
          <span className="block truncate">{displayName}</span>
          <span className="block truncate text-xs font-normal text-muted-foreground">
            {roleLabel}
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onSelect={() => onNavigate("/settings")}>
            <Settings data-icon="inline-start" />
            {t("nav.settings")}
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => {
              void onLogout().catch(() => {
                toast.error(t("common.requestFailed"));
              });
            }}
          >
            <LogOut data-icon="inline-start" />
            {t("nav.signOut")}
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
