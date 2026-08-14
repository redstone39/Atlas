import { BookOpen, Download, RefreshCw, ShieldOff } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import {
  CardAction,
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
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
import { useIsMobile } from "../../hooks/use-mobile";
import type { DocumentTagSummary } from "../../shared/document-contracts";
import {
  LoadErrorState,
  LoadingState,
  PageHeader,
  serverMessage,
} from "../../shared/product-ui";
import { knowledgeLibraryApi } from "./api";
import type { KnowledgeDocumentSummary } from "./types";

export function KnowledgeLibraryFeature({
  scope,
}: {
  scope: DocumentTagSummary;
}): ReactNode {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [documents, setDocuments] = useState<KnowledgeDocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [downloadingId, setDownloadingId] = useState("");
  const documentRequestGeneration = useRef(0);
  const mounted = useRef(true);
  const scopeKey = `${scope.tag_type}:${scope.tag_id}`;
  const currentScopeKey = useRef(scopeKey);
  currentScopeKey.current = scopeKey;

  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    void refresh(scope);
    return () => {
      documentRequestGeneration.current += 1;
    };
  }, [scope.tag_id, scope.tag_type]);

  async function refresh(requestedScope = scope) {
    const generation = ++documentRequestGeneration.current;
    setDocuments([]);
    setLoading(true);
    setLoadError("");
    try {
      const result = await knowledgeLibraryApi.listKnowledgeDocuments();
      if (generation !== documentRequestGeneration.current) return;
      setDocuments(
        result.documents.filter((document) =>
          document.authorized_scopes.some(
            ({ scope_type, scope_id }) =>
              scope_type === requestedScope.tag_type &&
              scope_id === requestedScope.tag_id,
          ),
        ),
      );
    } catch (error) {
      if (generation !== documentRequestGeneration.current) return;
      setLoadError(
        error instanceof Error ? error.message : t("admin.listLoadFailed"),
      );
    } finally {
      if (generation === documentRequestGeneration.current) setLoading(false);
    }
  }

  async function download(document: KnowledgeDocumentSummary) {
    const requestedScopeKey = scopeKey;
    setDownloadingId(document.document_id);
    setActionError("");
    try {
      await knowledgeLibraryApi.downloadKnowledgeDocument(
        document.document_id,
        document.source_filename ?? document.title,
      );
      if (mounted.current && currentScopeKey.current === requestedScopeKey) {
        toast.success(t("knowledgeLibrary.downloadStarted"));
      }
    } catch (error) {
      if (!mounted.current || currentScopeKey.current !== requestedScopeKey) return;
      const message =
        error instanceof Error ? error.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
      await refresh(scope);
    } finally {
      if (mounted.current && currentScopeKey.current === requestedScopeKey) {
        setDownloadingId("");
      }
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={scope.label}
        description={t("knowledgeScope.knowledgeDescription")}
      />

      {actionError && (
        <Alert variant="destructive">
          <ShieldOff />
          <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
          <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
        </Alert>
      )}

      {loading ? (
        <LoadingState title={t("knowledgeScope.documentsLoadingTitle")} />
      ) : loadError ? (
        <LoadErrorState
          title={t("knowledgeScope.documentsLoadFailed")}
          description={serverMessage(loadError, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => void refresh(scope)}
        />
      ) : documents.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <BookOpen />
            </EmptyMedia>
            <EmptyTitle>{t("knowledgeScope.documentsEmptyTitle")}</EmptyTitle>
            <EmptyDescription>
              {t("knowledgeScope.documentsEmptyDescription", { name: scope.label })}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <Card>
          <CardHeader>
            <div className="flex flex-col gap-1.5">
              <CardTitle>{t("knowledgeScope.knowledgeTitle")}</CardTitle>
              <CardDescription>
                {t("knowledgeLibrary.documentCount", { count: documents.length })}
              </CardDescription>
            </div>
            <CardAction>
              <Button variant="outline" size="sm" onClick={() => void refresh(scope)}>
                <RefreshCw data-icon="inline-start" />
                {t("admin.retry")}
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent>
            {isMobile ? (
              <div className="grid gap-3">
                {documents.map((document) => (
                  <Card key={document.document_id}>
                    <CardHeader>
                      <CardTitle>{document.title}</CardTitle>
                      {document.description && (
                        <CardDescription>{document.description}</CardDescription>
                      )}
                    </CardHeader>
                    <CardContent className="flex flex-col gap-1 text-sm text-muted-foreground">
                      <span>{document.source_filename ?? document.title}</span>
                      <span>
                        {(document.document_format ?? "document").toUpperCase()}
                        {formatFileSize(document.source_byte_size)
                          ? ` · ${formatFileSize(document.source_byte_size)}`
                          : ""}
                      </span>
                    </CardContent>
                    <CardFooter className="justify-end">
                      {document.download_available ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void download(document)}
                          disabled={downloadingId === document.document_id}
                        >
                          <Download data-icon="inline-start" />
                          {t("knowledgeLibrary.download")}
                        </Button>
                      ) : (
                        <span className="text-sm text-muted-foreground">
                          {t("knowledgeLibrary.readOnly")}
                        </span>
                      )}
                    </CardFooter>
                  </Card>
                ))}
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("knowledgeLibrary.document")}</TableHead>
                    <TableHead>{t("knowledgeLibrary.file")}</TableHead>
                    <TableHead className="text-right">
                      {t("knowledgeLibrary.actions")}
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {documents.map((document) => (
                    <TableRow key={document.document_id}>
                      <TableCell className="min-w-64 align-top">
                        <div className="font-medium">{document.title}</div>
                        {document.description && (
                          <div className="mt-1 max-w-xl text-sm text-muted-foreground">
                            {document.description}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="whitespace-nowrap align-top text-sm text-muted-foreground">
                        <div>{document.source_filename ?? document.title}</div>
                        <div className="text-xs text-muted-foreground">
                          {(document.document_format ?? "document").toUpperCase()}
                        </div>
                        <div>{formatFileSize(document.source_byte_size)}</div>
                      </TableCell>
                      <TableCell className="align-top text-right">
                        {document.download_available ? (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => void download(document)}
                            disabled={downloadingId === document.document_id}
                          >
                            <Download data-icon="inline-start" />
                            {t("knowledgeLibrary.download")}
                          </Button>
                        ) : (
                          <span className="text-sm text-muted-foreground">
                            {t("knowledgeLibrary.readOnly")}
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function formatFileSize(size: number | null) {
  if (!size) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
