import { Ellipsis, FolderKanban, MessageSquarePlus, Trash2, UsersRound } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { Spinner } from "../../components/ui/spinner";
import { cn } from "../../lib/utils";
import type { ConversationSummary } from "./types";

export function ConversationHistorySidebar({
  className,
  header,
  onOpenProjects,
  onOpenTeams,
  activeSection,
  conversations,
  activeConversationId,
  initialLoading,
  loadError,
  loading,
  archivingConversationId,
  onSelect,
  onDelete,
  onNew,
  onRetryHistory,
  footer,
}: {
  className?: string;
  header?: ReactNode;
  onOpenProjects: () => void;
  onOpenTeams: () => void;
  activeSection: "conversation" | "project" | "team";
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  initialLoading: boolean;
  loadError: boolean;
  loading: boolean;
  archivingConversationId: string | null;
  onSelect: (conversationId: string) => void;
  onDelete: (conversation: ConversationSummary) => void;
  onNew: () => void;
  onRetryHistory: () => void;
  footer?: ReactNode;
}) {
  const { t } = useTranslation();
  const [deleteCandidate, setDeleteCandidate] = useState<ConversationSummary | null>(null);
  const newConversationActive =
    activeSection === "conversation" && activeConversationId === null;
  return (
    <div className={cn("flex h-full min-h-0 flex-col bg-muted/20", className)}>
      {header}
      <div className="flex flex-col gap-1 p-3 pb-1">
        <Button
          variant={newConversationActive ? "secondary" : "ghost"}
          className="w-full justify-start"
          onClick={onNew}
          aria-current={newConversationActive ? "page" : undefined}
        >
          <MessageSquarePlus data-icon="inline-start" />
          {t("workspace.newConversation")}
        </Button>
        <Button
          variant={activeSection === "project" ? "secondary" : "ghost"}
          className="w-full justify-start"
          onClick={onOpenProjects}
          aria-current={activeSection === "project" ? "page" : undefined}
        >
          <FolderKanban data-icon="inline-start" />
          {t("nav.projects")}
        </Button>
        <Button
          variant={activeSection === "team" ? "secondary" : "ghost"}
          className="w-full justify-start"
          onClick={onOpenTeams}
          aria-current={activeSection === "team" ? "page" : undefined}
        >
          <UsersRound data-icon="inline-start" />
          {t("nav.teams")}
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
        {initialLoading ? (
          <div
            role="status"
            aria-live="polite"
            aria-busy="true"
            aria-label={t("workspace.historyLoading")}
            className="flex items-center gap-2 text-sm text-muted-foreground"
          >
            <Spinner aria-hidden="true" />
            {t("workspace.historyLoading")}
          </div>
        ) : loadError ? (
          <Alert variant="destructive">
            <AlertTitle>{t("workspace.historyLoadErrorTitle")}</AlertTitle>
            <AlertDescription>
              <Button type="button" variant="outline" size="sm" className="mt-2" onClick={onRetryHistory}>
                {t("workspace.historyRetry")}
              </Button>
            </AlertDescription>
          </Alert>
        ) : conversations.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("workspace.noConversations")}</p>
        ) : null}
        {!initialLoading && conversations.map((conversation) => {
          const active = conversation.conversation_id === activeConversationId;
          const archiving = conversation.conversation_id === archivingConversationId;
          return (
            <div
              key={conversation.conversation_id}
              data-slot="workspace-conversation-item"
              className={cn(
                "group relative rounded-md transition-colors hover:bg-accent/50 focus-within:bg-accent/50",
                active && "bg-secondary hover:bg-secondary focus-within:bg-secondary",
              )}
            >
              <Button
                variant="ghost"
                className={cn(
                  "h-auto min-w-0 w-full justify-start px-3 py-2 text-left hover:bg-transparent",
                  conversation.last_turn_status === "processing" ? "pr-16" : "pr-11",
                )}
                disabled={loading || archiving}
                aria-current={active ? "page" : undefined}
                onClick={() => onSelect(conversation.conversation_id)}
              >
                <span id={`conversation-title-${conversation.conversation_id}`} className="truncate font-medium">
                  {conversation.title}
                </span>
              </Button>
              {conversation.last_turn_status === "processing" && (
                <Spinner
                  className="absolute end-10 top-1/2 size-3 -translate-y-1/2 text-muted-foreground"
                  data-slot="conversation-processing-indicator"
                />
              )}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="absolute end-1 top-1/2 -translate-y-1/2 cursor-pointer opacity-0 group-hover:opacity-70 focus-visible:opacity-100 hover:bg-accent hover:opacity-100 data-[state=open]:bg-accent data-[state=open]:opacity-100"
                    disabled={loading || archiving}
                    aria-label={t("workspace.conversationActions")}
                    aria-describedby={`conversation-title-${conversation.conversation_id}`}
                  >
                    {archiving ? <Spinner aria-hidden="true" /> : <Ellipsis />}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuGroup>
                    <DropdownMenuItem
                      variant="destructive"
                      className="cursor-pointer hover:bg-destructive/10 hover:text-destructive"
                      disabled={conversation.last_turn_status === "processing"}
                      onSelect={() => setDeleteCandidate(conversation)}
                    >
                      <Trash2 data-icon="inline-start" />
                      {t("workspace.deleteConversation")}
                    </DropdownMenuItem>
                  </DropdownMenuGroup>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          );
        })}
      </div>
      {footer && (
        <div data-slot="contextual-sidebar-footer" className="shrink-0 border-t p-3">
          {footer}
        </div>
      )}
      <AlertDialog
        open={deleteCandidate !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteCandidate(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("workspace.deleteConversationConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("workspace.deleteConversationConfirmDescription", { title: deleteCandidate?.title ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="cursor-pointer">
              {t("workspace.deleteConversationCancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              className="cursor-pointer"
              onClick={() => {
                const conversation = deleteCandidate;
                setDeleteCandidate(null);
                if (conversation) onDelete(conversation);
              }}
            >
              {t("workspace.deleteConversationConfirmAction")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
