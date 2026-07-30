import { BookOpen, Download, RefreshCw, ShieldOff } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "../../components/ui/empty";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { LoadErrorState, LoadingState, PageHeader, serverMessage } from "../../shared/product-ui";
import { knowledgeLibraryApi } from "./api";
import type { KnowledgeDocumentSummary } from "./types";

export function KnowledgeLibraryFeature() {
  const { t } = useTranslation();
  const [documents, setDocuments] = useState<KnowledgeDocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [downloadingId, setDownloadingId] = useState("");

  useEffect(() => {
    void refresh();
  }, []);

  async function refresh() {
    setLoading(true);
    setLoadError("");
    try {
      const result = await knowledgeLibraryApi.listKnowledgeDocuments();
      setDocuments(result.documents);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : t("admin.listLoadFailed"));
    } finally {
      setLoading(false);
    }
  }

  async function download(document: KnowledgeDocumentSummary) {
    setDownloadingId(document.document_id);
    setActionError("");
    try {
      await knowledgeLibraryApi.downloadKnowledgeDocument(
        document.document_id,
        document.source_filename ?? document.title,
      );
      toast.success(t("knowledgeLibrary.downloadStarted"));
    } catch (error) {
      const message = error instanceof Error ? error.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
      await refresh();
    } finally {
      setDownloadingId("");
    }
  }

  return (
    <section className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <PageHeader
        title={t("knowledgeLibrary.title")}
        description={t("knowledgeLibrary.description")}
      />

      {actionError && (
        <Alert variant="destructive">
          <ShieldOff />
          <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
          <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
        </Alert>
      )}

      {loading ? (
        <LoadingState
          title={t("knowledgeLibrary.loadingTitle")}
        />
      ) : loadError ? (
        <LoadErrorState
          title={t("admin.listLoadFailed")}
          description={serverMessage(loadError, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => void refresh()}
        />
      ) : documents.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon"><BookOpen /></EmptyMedia>
            <EmptyTitle>{t("knowledgeLibrary.emptyTitle")}</EmptyTitle>
            <EmptyDescription>{t("knowledgeLibrary.emptyDescription")}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="overflow-hidden rounded-lg border bg-card">
          <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
            <div className="text-sm text-muted-foreground">
              {t("knowledgeLibrary.documentCount", { count: documents.length })}
            </div>
            <Button variant="outline" size="sm" onClick={() => void refresh()}>
              <RefreshCw data-icon="inline-start" />
              {t("admin.retry")}
            </Button>
          </div>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("knowledgeLibrary.document")}</TableHead>
                  <TableHead>{t("knowledgeLibrary.scope")}</TableHead>
                  <TableHead>{t("knowledgeLibrary.file")}</TableHead>
                  <TableHead className="text-right">{t("knowledgeLibrary.actions")}</TableHead>
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
                    <TableCell className="min-w-48 align-top">
                      <div className="flex flex-wrap gap-1.5">
                        {document.authorized_scopes.map((scope) => (
                          <Badge
                            key={`${scope.scope_type}:${scope.scope_id}`}
                            variant="secondary"
                          >
                            {scope.scope_label}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="whitespace-nowrap align-top text-sm text-muted-foreground">
                      <div>{document.source_filename ?? document.title}</div>
                      <div className="text-xs text-muted-foreground">{(document.document_format ?? "document").toUpperCase()}</div>
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
          </div>
        </div>
      )}
    </section>
  );
}

function formatFileSize(size: number | null) {
  if (!size) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
