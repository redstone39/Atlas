import { MessageSquareText } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import {
  Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle,
} from "../../components/ui/empty";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "../../components/ui/table";
import { serverMessage } from "../../shared/product-ui";
import {
  adminAuditConversationRoute, type AppRoute,
} from "../../shared/routes";
import type { ConversationSummary } from "../workspace/index";
import { formatDateTime } from "./AuditPresentationUtils";

export function ConversationDirectoryView({
  conversations,
  isMobile,
  loading,
  nextConversationCursor,
  moreConversationsLoading,
  conversationPageError,
  locale,
  onNavigate,
  onLoadMore,
}: {
  conversations: ConversationSummary[];
  isMobile: boolean;
  loading: boolean;
  nextConversationCursor: string | null;
  moreConversationsLoading: boolean;
  conversationPageError: string;
  locale: string;
  onNavigate: (route: AppRoute) => void;
  onLoadMore: (cursor: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  return (
              <nav
                aria-label={t("audit.conversationIndex")}
                className="flex min-w-0 flex-col gap-2"
              >
                {conversations.length === 0 && !loading ? (
                  <Empty className="border">
                    <EmptyHeader>
                      <EmptyMedia variant="icon">
                        <MessageSquareText />
                      </EmptyMedia>
                      <EmptyTitle>{t("audit.noConversations")}</EmptyTitle>
                      <EmptyDescription>{t("audit.selectConversation")}</EmptyDescription>
                    </EmptyHeader>
                  </Empty>
                ) : (
                  <>
                  {isMobile ? (
                    <div className="grid gap-3">
                      {conversations.map((conversation) => (
                        <Card key={conversation.conversation_id}>
                          <CardContent className="grid gap-2 pt-4">
                            <div className="font-medium">{conversation.title}</div>
                            <div className="break-all text-xs text-muted-foreground">
                              {conversation.owner_actor_id}
                            </div>
                            <Button
                              variant="outline"
                              className="justify-start"
                              aria-label={`${t("audit.openConversation")} ${conversation.title}`}
                              onClick={() =>
                                onNavigate(
                                  adminAuditConversationRoute(
                                    conversation.conversation_id,
                                    "transcript",
                                  ),
                                )}
                            >
                              {t("audit.openConversation")}
                            </Button>
                          </CardContent>
                        </Card>
                      ))}
                    </div>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>{t("audit.conversation")}</TableHead>
                          <TableHead>{t("audit.owner")}</TableHead>
                          <TableHead>{t("audit.updatedAtLabel")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {conversations.map((conversation) => (
                          <TableRow key={conversation.conversation_id}>
                            <TableCell>
                              <Button
                                variant="ghost"
                                className="h-auto justify-start px-0 text-left"
                                onClick={() =>
                                  onNavigate(
                                    adminAuditConversationRoute(
                                      conversation.conversation_id,
                                      "transcript",
                                    ),
                                  )}
                              >
                                {conversation.title}
                              </Button>
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {conversation.owner_actor_id}
                            </TableCell>
                            <TableCell>
                              {formatDateTime(
                                conversation.updated_at,
                                locale,
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                  {nextConversationCursor && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={moreConversationsLoading}
                      onClick={() => void onLoadMore(nextConversationCursor)}
                    >
                      {moreConversationsLoading
                        ? t("audit.loadingMoreConversations")
                        : t("audit.loadMoreConversations")}
                    </Button>
                  )}
                  {conversationPageError && (
                    <p role="alert" className="text-sm text-destructive">
                      {serverMessage(conversationPageError, t)}
                    </p>
                  )}
                  </>
                )}
              </nav>
  );
}
