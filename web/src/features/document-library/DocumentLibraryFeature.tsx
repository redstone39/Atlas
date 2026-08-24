import {
  DatabaseZap,
  Download,
  FileClock,
  FileUp,
  ListRestart,
  RotateCcw,
  Search,
  ShieldOff,
  SlidersHorizontal,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "../../components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "../../components/ui/empty";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Spinner } from "../../components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { Textarea } from "../../components/ui/textarea";
import type { MessageReference } from "../../shared/user-messages";
import type { AuditEvent } from "../conversation-audit/index";
import type { DocumentTagRef } from "../../shared/document-contracts";
import { clientRequestId } from "../../shared/ids";
import { OptionSelect, type OptionSelectItem } from "../../shared/OptionSelect";
import {
  DOCUMENT_UPLOAD_ACCEPT,
  titleFromFilename,
} from "../../shared/document-upload";
import {
  documentLibraryProductStatus,
  documentLibraryProductStatusLabel,
  documentLibraryProductStatusSemantic,
  intakeStatusLabel,
} from "../../shared/document-status";
import {
  LoadErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  TargetSummary,
  serverMessage,
} from "../../shared/product-ui";
import type {
  DocumentProjectView,
  DocumentTeamView,
  DocumentLibrarySessionView,
  DocumentLibrarySummary,
  LoadDocumentTeams,
  LoadWorkspaceDocumentScope,
} from "./types";
import { documentLibraryApi } from "./api";
import {
  ACTIVE_PROCESSING_STATUSES,
  ProcessingJobPanel,
  useProcessingJobs,
} from "../document-processing";

type ScopeKey = "all" | `team:${string}` | `project:${string}`;
type TagKey = Exclude<ScopeKey, "all">;

type UploadItem = {
  clientKey: string;
  file: File;
};

export function DocumentLibraryFeature({
  session,
  initialScope,
  loadTeams,
  loadWorkspaceScope,
  onNotice,
  onRefresh,
}: {
  session: DocumentLibrarySessionView;
  initialScope: DocumentTagRef | null;
  loadTeams: LoadDocumentTeams;
  loadWorkspaceScope: LoadWorkspaceDocumentScope;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const [documents, setDocuments] = useState<DocumentLibrarySummary[]>([]);
  const [teams, setTeams] = useState<DocumentTeamView[]>([]);
  const [selectedScopeKey, setSelectedScopeKey] = useState<ScopeKey>(() =>
    initialScope ? `${initialScope.tag_type}:${initialScope.tag_id}` : "all",
  );
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [detailEvents, setDetailEvents] = useState<AuditEvent[]>([]);
  const [descriptionDraft, setDescriptionDraft] = useState("");
  const [showUploadDialog, setShowUploadDialog] = useState(false);
  const [uploadItems, setUploadItems] = useState<UploadItem[]>([]);
  const [uploadDescription, setUploadDescription] = useState("");
  const [uploadAllowMemberDownload, setUploadAllowMemberDownload] = useState(false);
  const [uploadOwnerTagKey, setUploadOwnerTagKey] = useState<TagKey | null>(null);
  const [uploadAdditionalTagKeys, setUploadAdditionalTagKeys] = useState<TagKey[]>([]);
  const [showAdditionalTargets, setShowAdditionalTargets] = useState(false);
  const [uploadFileInputKey, setUploadFileInputKey] = useState(0);
  const [uploadProgress, setUploadProgress] = useState<{
    current: number;
    total: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const [scopeLoading, setScopeLoading] = useState(true);
  const [scopeLoadError, setScopeLoadError] = useState("");
  const [documentsReady, setDocumentsReady] = useState(false);
  const {
    jobs: processingJobs,
    error: processingJobsError,
    refresh: refreshProcessingJobs,
  } = useProcessingJobs(() => {
    void refreshDocuments();
  });
  const initialLoading = scopeLoading || !documentsReady;
  const pageLoadError = scopeLoadError || loadError;

  const scopeOptions = useMemo(
    () => documentScopeOptions(session, teams, t),
    [session, teams, t],
  );
  const selectedScope = scopeRefFromKey(selectedScopeKey);
  const selectedDocument =
    documents.find((document) => document.document_id === selectedDocumentId) ?? null;
  const selectedJob =
    processingJobs.find((job) => job.job_id === selectedDocument?.job_id) ??
    processingJobs.find((job) => job.document_id === selectedDocumentId && job.is_current) ??
    null;
  const uploadTagOptions = scopeOptions.filter(
    (option): option is OptionSelectItem<TagKey> => option.value !== "all",
  );
  const uploadOwnerOption =
    uploadTagOptions.find((option) => option.value === uploadOwnerTagKey) ?? null;
  const uploadAdditionalTagOptions = uploadTagOptions.filter(
    (option) => option.value !== uploadOwnerTagKey,
  );
  const uploadPending = pendingAction === "upload";
  const canUpload = Boolean(uploadItems.length > 0 && uploadOwnerTagKey);
  const actorId = session.actor?.actor_id ?? "";
  const selectedProcessingIsActive = Boolean(
    selectedDocument &&
      (selectedJob
        ? ACTIVE_PROCESSING_STATUSES.has(selectedJob.status)
        : ["queued", "processing", "processing_queued"].includes(
            selectedDocument.intake_status,
          )),
  );
  const selectedProductStatus = selectedDocument
    ? documentLibraryProductStatus({
        intakeStatus: selectedDocument.intake_status,
        evidenceCount: selectedDocument.evidence_count,
        processingStatus: selectedJob?.status,
      })
    : null;

  useEffect(() => {
    void refreshTeams();
  }, []);

  useEffect(() => {
    if (
      !scopeLoading &&
      scopeOptions.length > 0 &&
      !scopeOptions.some((option) => option.value === selectedScopeKey)
    ) {
      setSelectedScopeKey(scopeOptions[0].value);
    }
  }, [scopeLoading, scopeOptions, selectedScopeKey]);

  useEffect(() => {
    if (
      scopeLoading ||
      !scopeOptions.some((option) => option.value === selectedScopeKey)
    ) {
      return;
    }
    void refreshDocuments();
  }, [scopeLoading, scopeOptions, selectedScopeKey]);

  useEffect(() => {
    if (!selectedDocument) {
      setDescriptionDraft("");
      setDetailEvents([]);
      return;
    }
    setDescriptionDraft(selectedDocument.description ?? "");
    void refreshEvents(selectedDocument.document_id);
  }, [selectedDocumentId]);

  async function refreshTeams() {
    setScopeLoading(true);
    setScopeLoadError("");
    try {
      const teamResult = await loadTeams();
      setTeams(teamResult.teams.filter((team) => team.status === "active"));
    } catch {
      try {
        const scope = await loadWorkspaceScope();
        setTeams(
          scope.tags
            .filter(
              (tag) =>
                tag.tag_type === "team" &&
                (session.system_role === "admin" ||
                  ["uploader", "admin"].includes(session.team_roles[tag.tag_id] ?? "")),
            )
            .map((tag) => ({
              team_id: tag.tag_id,
              name: tag.label,
              parent_team_id: null,
              status: "active",
              created_at: "",
              inherit_parent_documents: true,
            })),
        );
      } catch {
        setTeams([]);
        setScopeLoadError(t("documentLibrary.scopeLoadFailed"));
      }
    } finally {
      setScopeLoading(false);
    }
  }

  async function refreshDocuments() {
    setLoading(true);
    setLoadError("");
    setActionError("");
    try {
      const result = await documentLibraryApi.listDocumentLibrary(selectedScope ?? undefined);
      setDocuments(result.documents);
      setSelectedDocumentId((current) =>
        current && result.documents.some((document) => document.document_id === current)
          ? current
          : "",
      );
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : t("admin.listLoadFailed"));
    } finally {
      setLoading(false);
      setDocumentsReady(true);
    }
  }

  async function refreshEvents(documentId: string) {
    setEventsLoading(true);
    try {
      const result = await documentLibraryApi.listDocumentLibraryEvents(documentId);
      setDetailEvents(result.events);
    } catch {
      setDetailEvents([]);
    } finally {
      setEventsLoading(false);
    }
  }

  async function runAction(
    actionName: string,
    action: () => Promise<MessageReference>,
    onAccepted?: () => void,
  ): Promise<void> {
    setPendingAction(actionName);
    setActionError("");
    try {
      const result = await action();
      onNotice(result.message_code);
      toast.success(serverMessage(result, t));
      onAccepted?.();
      await refreshDocuments();
      await refreshProcessingJobs();
      await onRefresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  function resetUploadDraft(ownerTagKey: TagKey | null = null) {
    setUploadItems([]);
    setUploadDescription("");
    setUploadAllowMemberDownload(false);
    setUploadOwnerTagKey(ownerTagKey);
    setUploadAdditionalTagKeys([]);
    setShowAdditionalTargets(false);
    setUploadFileInputKey((value) => value + 1);
    setUploadProgress(null);
  }

  function openUploadDialog() {
    if (selectedScopeKey === "all") return;
    const ownerTagKey = uploadTagOptions.find(
      (option) => option.value === selectedScopeKey,
    )?.value;
    if (!ownerTagKey) return;
    setActionError("");
    resetUploadDraft(ownerTagKey);
    setShowUploadDialog(true);
  }

  function closeUploadDialog() {
    setActionError("");
    resetUploadDraft();
    setShowUploadDialog(false);
  }

  function handleUploadDialogOpenChange(open: boolean) {
    if (open) {
      setShowUploadDialog(true);
    } else if (pendingAction !== "upload") {
      closeUploadDialog();
    }
  }

  async function uploadDocuments() {
    const ownerRef = uploadOwnerTagKey
      ? scopeRefFromKey(uploadOwnerTagKey)
      : null;
    if (!ownerRef || uploadItems.length === 0) return;
    const additionalRefs = uploadTagOptions
      .filter((option) => uploadAdditionalTagKeys.includes(option.value))
      .map((option) => scopeRefFromKey(option.value))
      .filter((tag): tag is DocumentTagRef => tag !== null);
    const tagRefs = [ownerRef, ...additionalRefs];
    const items = uploadItems;
    const description = uploadDescription;
    const allowMemberDownload = uploadAllowMemberDownload;
    let acceptedCount = 0;
    let lastMessageCode = "";

    setPendingAction("upload");
    setActionError("");
    try {
      try {
        for (const [index, item] of items.entries()) {
          setUploadProgress({ current: index + 1, total: items.length });
          const result = await documentLibraryApi.uploadDocumentLibraryFile({
            clientKey: item.clientKey,
            scopeType: ownerRef.tag_type,
            scopeId: ownerRef.tag_id,
            tagRefs,
            file: item.file,
            description,
            allowMemberDownload,
          });
          lastMessageCode = result.message_code;
          acceptedCount += 1;
          setUploadItems((current) =>
            current.filter((candidate) => candidate.clientKey !== item.clientKey),
          );
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : t("admin.actionFailed");
        setActionError(message);
        toast.error(serverMessage(message, t));
        if (acceptedCount > 0) {
          setUploadFileInputKey((value) => value + 1);
        }
        return;
      }

      onNotice(lastMessageCode);
      toast.success(t("documentLibrary.uploadAccepted", { count: acceptedCount }));
      closeUploadDialog();
      try {
        await refreshDocuments();
        await refreshProcessingJobs();
        await onRefresh();
      } catch (err) {
        const message = err instanceof Error ? err.message : t("admin.actionFailed");
        toast.error(serverMessage(message, t));
      }
    } finally {
      setPendingAction("");
      setUploadProgress(null);
    }
  }

  async function downloadDocument(item: DocumentLibrarySummary) {
    setPendingAction(`download-${item.document_id}`);
    setActionError("");
    try {
      await documentLibraryApi.downloadDocument(
        item.document_id,
        item.source_filename ?? item.title,
      );
      toast.success(t("documentLibrary.downloadStarted"));
    } catch (err) {
      const message = err instanceof Error ? err.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  function scopeLabel(document: DocumentLibrarySummary) {
    if (document.scope_type === "project") {
      return projectLabel(session.available_projects, document.scope_id);
    }
    return teamLabel(teams, document.scope_id);
  }

  function directTagLabels(document: DocumentLibrarySummary) {
    return document.direct_tags
      .map((tag) =>
        tag.tag_type === "project"
          ? `${t("ingestion.projectTag")}: ${tag.label}`
          : `${t("ingestion.teamTag")}: ${tag.label}`,
      )
      .join(", ");
  }

  function canAdminister(document: DocumentLibrarySummary) {
    if (session.system_role === "admin") return true;
    if (document.scope_type === "team") {
      return session.team_roles[document.scope_id] === "admin";
    }
    return session.available_projects.some(
      (project) =>
        project.project_id === document.scope_id &&
        project.membership_status === "active" &&
        project.role === "admin",
    );
  }

  function canEditContent(document: DocumentLibrarySummary) {
    return canAdminister(document) || document.uploader_actor_id === actorId;
  }

  if (initialLoading) {
    return (
      <section className="flex flex-col gap-5">
        <PageHeader title={t("documentLibrary.title")} />
        <LoadingState
          title={t("documentLibrary.loadingTitle")}
        />
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-5">
      <PageHeader title={t("documentLibrary.title")} />

      {!pageLoadError && (
        <div className="flex flex-col gap-3 rounded-md border bg-card p-4 md:flex-row md:items-end md:justify-between">
          <Field className="min-w-0 md:w-80">
            <FieldLabel htmlFor="document-library-scope">
              {t("documentLibrary.target")}
            </FieldLabel>
            <OptionSelect
              id="document-library-scope"
              value={selectedScopeKey}
              options={scopeOptions}
              onValueChange={setSelectedScopeKey}
            />
            {selectedScopeKey === "all" && (
              <FieldDescription>
                {t("documentLibrary.selectTargetBeforeUpload")}
              </FieldDescription>
            )}
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void refreshDocuments()} disabled={loading}>
              {loading ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <ListRestart data-icon="inline-start" />
              )}
              {t("admin.retry")}
            </Button>
            <Button
              onClick={openUploadDialog}
              disabled={
                selectedScopeKey === "all" ||
                !scopeOptions.some((option) => option.value === selectedScopeKey)
              }
            >
              <FileUp data-icon="inline-start" />
              {t("documentLibrary.upload")}
            </Button>
          </div>
        </div>
      )}

      {actionError && (
        <Alert variant="destructive">
          <ShieldOff />
          <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
          <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
        </Alert>
      )}

      {processingJobsError && (
        <Alert variant="destructive">
          <DatabaseZap />
          <AlertTitle>{t("processing.refreshFailed")}</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
            <span>{serverMessage(processingJobsError, t)}</span>
            <Button size="sm" variant="outline" onClick={() => void refreshProcessingJobs()}>
              {t("admin.retry")}
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {loading ? (
        <LoadingState
          title={t("documentLibrary.loadingTitle")}
        />
      ) : pageLoadError ? (
        <LoadErrorState
          title={t("admin.listLoadFailed")}
          description={serverMessage(pageLoadError, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => {
            if (scopeLoadError) {
              void refreshTeams();
              return;
            }
            void refreshDocuments();
          }}
        />
      ) : documents.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>{t("documentLibrary.emptyTitle")}</EmptyTitle>
            <EmptyDescription>{t("documentLibrary.emptyDescription")}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("documentLibrary.document")}</TableHead>
                <TableHead>{t("documentLibrary.scope")}</TableHead>
                <TableHead>{t("users.status")}</TableHead>
                <TableHead>{t("documentLibrary.memberDownload")}</TableHead>
                <TableHead>{t("documentLibrary.file")}</TableHead>
                <TableHead className="text-right">{t("documentLibrary.actions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {documents.map((document) => {
                const currentJob =
                  processingJobs.find(
                    (job) => job.document_id === document.document_id && job.is_current,
                  ) ?? null;
                const productStatus = documentLibraryProductStatus({
                  intakeStatus: document.intake_status,
                  evidenceCount: document.evidence_count,
                  processingStatus: currentJob?.status,
                });
                return (
                <TableRow key={document.document_id}>
                  <TableCell className="min-w-64">
                    <div className="font-medium">{document.title}</div>
                    {document.description && (
                      <div className="text-xs text-muted-foreground">{document.description}</div>
                    )}
                  </TableCell>
                  <TableCell>{directTagLabels(document)}</TableCell>
                  <TableCell>
                    <div className="flex flex-col items-start gap-1">
                      <StatusBadge
                        semantic={documentLibraryProductStatusSemantic(productStatus)}
                        label={documentLibraryProductStatusLabel(productStatus, t)}
                      />
                      <StatusBadge
                        semantic={
                          document.lifecycle_status === "active"
                            ? "success"
                            : document.lifecycle_status === "restoring"
                              ? "progress"
                              : "inactive"
                        }
                        label={t(`documentLibrary.lifecycle.${document.lifecycle_status}`)}
                      />
                    </div>
                  </TableCell>
                  <TableCell>
                    {document.allow_member_download
                      ? t("documentLibrary.allowed")
                      : t("documentLibrary.notAllowed")}
                  </TableCell>
                  <TableCell>{formatFileSize(document.source_byte_size)}</TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-2">
                      {document.download_available && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void downloadDocument(document)}
                          disabled={pendingAction === `download-${document.document_id}`}
                        >
                          <Download data-icon="inline-start" />
                          {t("documentLibrary.download")}
                        </Button>
                      )}
                      {canEditContent(document) && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setSelectedDocumentId(document.document_id)}
                        >
                          <SlidersHorizontal data-icon="inline-start" />
                          {t("documentLibrary.manage")}
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={showUploadDialog} onOpenChange={handleUploadDialogOpenChange}>
        <DialogContent
          showCloseButton={!uploadPending}
          onEscapeKeyDown={(event) => uploadPending && event.preventDefault()}
          onPointerDownOutside={(event) => uploadPending && event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{t("documentLibrary.upload")}</DialogTitle>
            <DialogDescription>{t("documentLibrary.uploadDescription")}</DialogDescription>
          </DialogHeader>
          <FieldSet disabled={uploadPending}>
            <FieldGroup>
              {uploadOwnerOption && (
                <TargetSummary
                  label={t("documentLibrary.uploadTarget")}
                  title={uploadOwnerOption.label}
                />
              )}
              <Field>
                <FieldLabel htmlFor="document-library-file">
                  {t("documentLibrary.files")}
                </FieldLabel>
                <Input
                  key={uploadFileInputKey}
                  id="document-library-file"
                  type="file"
                  multiple
                  accept={DOCUMENT_UPLOAD_ACCEPT}
                  onChange={(event) =>
                    setUploadItems(
                      Array.from(event.currentTarget.files ?? []).map((file) => ({
                        clientKey: clientRequestId("document-upload"),
                        file,
                      })),
                    )
                  }
                />
                <FieldDescription>
                  {t("documentLibrary.fileRequirements")}
                </FieldDescription>
                {uploadItems.length > 0 && (
                  <div className="max-h-40 space-y-1 overflow-y-auto">
                    {uploadItems.map((item) => (
                      <FieldDescription key={item.clientKey}>
                        {item.file.name} · {formatFileSize(item.file.size)}
                      </FieldDescription>
                    ))}
                  </div>
                )}
              </Field>
              <Field>
                <FieldLabel htmlFor="document-library-description">
                  {t("ingestion.documentDescription")}
                </FieldLabel>
                <Textarea
                  id="document-library-description"
                  value={uploadDescription}
                  onChange={(event) => setUploadDescription(event.target.value)}
                />
              </Field>
              {uploadAdditionalTagOptions.length > 0 && (
                <Collapsible
                  open={showAdditionalTargets}
                  onOpenChange={setShowAdditionalTargets}
                >
                  <CollapsibleTrigger asChild>
                    <Button type="button" variant="ghost">
                      {t("documentLibrary.additionalTargets")}
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <FieldSet className="pt-3">
                      <FieldLegend className="sr-only">
                        {t("documentLibrary.additionalTargets")}
                      </FieldLegend>
                      <FieldGroup>
                        {uploadAdditionalTagOptions.map((option) => (
                          <label
                            key={option.value}
                            className="flex items-center gap-2 text-sm"
                          >
                            <Checkbox
                              checked={uploadAdditionalTagKeys.includes(option.value)}
                              onCheckedChange={(checked) =>
                                setUploadAdditionalTagKeys((current) =>
                                  checked === true
                                    ? [...new Set([...current, option.value])]
                                    : current.filter(
                                        (value) => value !== option.value,
                                      ),
                                )
                              }
                            />
                            {option.label}
                          </label>
                        ))}
                      </FieldGroup>
                    </FieldSet>
                  </CollapsibleContent>
                </Collapsible>
              )}
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={uploadAllowMemberDownload}
                  onCheckedChange={(checked) => setUploadAllowMemberDownload(checked === true)}
                />
                {t("documentLibrary.allowMemberDownload")}
              </label>
            </FieldGroup>
          </FieldSet>
          {actionError && (
            <Alert variant="destructive">
              <ShieldOff />
              <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
              <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={closeUploadDialog}
              disabled={uploadPending}
            >
              {t("admin.cancel")}
            </Button>
            <Button
              onClick={() => void uploadDocuments()}
              disabled={!canUpload || uploadPending}
            >
              {uploadPending ? (
                <>
                  <Spinner data-icon="inline-start" />
                  {t(
                    "documentLibrary.uploadingFiles",
                    uploadProgress ?? { current: 1, total: uploadItems.length },
                  )}
                </>
              ) : (
                <>
                  <FileUp data-icon="inline-start" />
                  {t("documentLibrary.uploadSelected")}
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(selectedDocument)} onOpenChange={(open) => !open && setSelectedDocumentId("")}>
        <DialogContent>
          {selectedDocument && (
            <>
              <DialogHeader>
                <DialogTitle>{selectedDocument.title}</DialogTitle>
                <DialogDescription>{directTagLabels(selectedDocument)}</DialogDescription>
              </DialogHeader>
              {selectedJob ? (
                <ProcessingJobPanel
                  job={selectedJob}
                  displayStatus={selectedProductStatus ?? undefined}
                  onChanged={async () => {
                    await Promise.all([refreshDocuments(), refreshProcessingJobs()]);
                    await onRefresh();
                  }}
                />
              ) : (
                <Alert variant={selectedProductStatus === "failed" ? "destructive" : undefined}>
                  <DatabaseZap />
                  <AlertTitle>{t("ingestion.processingRunTitle")}</AlertTitle>
                  <AlertDescription>
                    <span className="block">
                      {t("plugins.status")}: {selectedProductStatus
                        ? documentLibraryProductStatusLabel(selectedProductStatus, t)
                        : intakeStatusLabel(selectedDocument.intake_status, t)}
                    </span>
                    <span className="block">
                      {t("plugins.profile")}: {processingProfileLabel(selectedDocument)}
                      {` · ${selectedDocument.document_format.toUpperCase()}`}
                    </span>
                    {selectedDocument.warning_codes.length > 0 && (
                      <span className="mt-1 block text-warning">
                        {t("ingestion.processingWarnings", {
                          warnings: selectedDocument.warning_codes
                            .map((code) => processingCodeLabel(code, t))
                            .join(", "),
                        })}
                      </span>
                    )}
                    {selectedDocument.failure_code && (
                      <span className="mt-1 block text-destructive">
                        {processingCodeLabel(selectedDocument.failure_code, t)}
                      </span>
                    )}
                  </AlertDescription>
                </Alert>
              )}
              <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(240px,0.8fr)]">
                <FieldSet>
                  <FieldGroup>
                    <Field>
                      <FieldLabel htmlFor="document-library-detail-description">
                        {t("ingestion.documentDescription")}
                      </FieldLabel>
                      <Textarea
                        id="document-library-detail-description"
                        value={descriptionDraft}
                        onChange={(event) => setDescriptionDraft(event.target.value)}
                        disabled={!canEditContent(selectedDocument)}
                      />
                    </Field>
                    {canAdminister(selectedDocument) && (
                      <label className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={selectedDocument.allow_member_download}
                          onCheckedChange={(checked) =>
                            void runAction(`download-policy-${selectedDocument.document_id}`, () =>
                              documentLibraryApi.updateDocumentLibrary(selectedDocument.document_id, {
                                allowMemberDownload: checked === true,
                              }),
                            )
                          }
                        />
                        {t("documentLibrary.allowMemberDownload")}
                      </label>
                    )}
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        onClick={() =>
                          void runAction(`description-${selectedDocument.document_id}`, () =>
                            documentLibraryApi.updateDocumentLibrary(selectedDocument.document_id, {
                              description: descriptionDraft,
                            }),
                          )
                        }
                        disabled={!canEditContent(selectedDocument)}
                      >
                        {t("documentLibrary.saveDescription")}
                      </Button>
                      {!(["failed", "cancelled"].includes(
                        selectedJob?.status ?? selectedDocument.intake_status,
                      )) && (
                        <Button
                          variant="outline"
                          onClick={() =>
                            void runAction(`refresh-${selectedDocument.document_id}`, () =>
                              documentLibraryApi.refreshDocumentLibraryContent(selectedDocument.document_id),
                            )
                          }
                          disabled={
                            !canEditContent(selectedDocument) ||
                            selectedDocument.lifecycle_status === "disabled" ||
                            selectedProcessingIsActive ||
                            pendingAction === `refresh-${selectedDocument.document_id}`
                          }
                        >
                          <Search data-icon="inline-start" />
                          {t("documentLibrary.refreshSearchableContent")}
                        </Button>
                      )}
                      {canAdminister(selectedDocument) && (
                        selectedDocument.lifecycle_status === "disabled" ? (
                          <Button
                            variant="outline"
                            onClick={() =>
                              void runAction(`restore-${selectedDocument.document_id}`, () =>
                                documentLibraryApi.restoreDocumentLibraryItem(selectedDocument.document_id),
                              )
                            }
                          >
                            <RotateCcw data-icon="inline-start" />
                            {t("documentLibrary.restore")}
                          </Button>
                        ) : (
                          <Button
                            variant="destructive"
                            onClick={() =>
                              void runAction(`disable-${selectedDocument.document_id}`, () =>
                                documentLibraryApi.disableDocumentLibraryItem(selectedDocument.document_id),
                              )
                            }
                          >
                            <ShieldOff data-icon="inline-start" />
                            {t("documentLibrary.disable")}
                          </Button>
                        )
                      )}
                    </div>
                  </FieldGroup>
                </FieldSet>
                <div className="min-w-0">
                  <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                    <FileClock data-icon="inline-start" />
                    {t("documentLibrary.events")}
                  </div>
                  {eventsLoading ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Spinner />
                      {t("documentLibrary.eventsLoading")}
                    </div>
                  ) : detailEvents.length === 0 ? (
                    <div className="rounded-md border p-3 text-sm text-muted-foreground">
                      {t("documentLibrary.noEvents")}
                    </div>
                  ) : (
                    <div className="max-h-72 overflow-y-auto rounded-md border">
                      {detailEvents.map((event) => (
                        <div key={event.event_id} className="border-b p-3 text-sm last:border-b-0">
                          <div className="font-medium">
                            {serverMessage(event, t)}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {event.event_type} · {formatDateTime(event.created_at, i18n.language)}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}


function scopeRefFromKey(scopeKey: ScopeKey): DocumentTagRef | null {
  if (scopeKey === "all") return null;
  const [tagType, ...rest] = scopeKey.split(":");
  const tagId = rest.join(":");
  if ((tagType === "team" || tagType === "project") && tagId) {
    return { tag_type: tagType, tag_id: tagId };
  }
  return null;
}

function documentScopeOptions(
  session: DocumentLibrarySessionView,
  teams: DocumentTeamView[],
  t: (key: string) => string,
): OptionSelectItem<ScopeKey>[] {
  const teamIds = new Set(
    session.system_role === "admin"
      ? teams.map((team) => team.team_id)
      : Object.entries(session.team_roles)
          .filter(([, role]) => role === "uploader" || role === "admin")
          .map(([teamId]) => teamId),
  );
  const teamOptions = [...teamIds].sort().map((teamId) => ({
    value: `team:${teamId}` as ScopeKey,
    label: `${t("ingestion.teamTag")}: ${teamLabel(teams, teamId)}`,
  }));
  const projectOptions = session.available_projects
    .filter(
      (project) =>
        session.system_role === "admin" ||
        (project.membership_status === "active" &&
          ["contributor", "admin"].includes(project.role ?? "")),
    )
    .map((project) => ({
      value: `project:${project.project_id}` as ScopeKey,
      label: `${t("ingestion.projectTag")}: ${project.name}`,
    }));
  const scopedOptions = [...projectOptions, ...teamOptions];
  if (session.system_role === "admin") {
    return [{ value: "all", label: t("documentLibrary.allScopes") }, ...scopedOptions];
  }
  return scopedOptions;
}

function teamLabel(teams: DocumentTeamView[], teamId: string) {
  return teams.find((team) => team.team_id === teamId)?.name ?? teamId;
}

function projectLabel(projects: DocumentProjectView[], projectId: string) {
  return projects.find((project) => project.project_id === projectId)?.name ?? projectId;
}

function formatFileSize(size: number | null) {
  if (!size) return "-";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDateTime(value: string, locale: string) {
  if (!value) return "";
  return new Date(value).toLocaleString(locale);
}

function processingProfileLabel(document: DocumentLibrarySummary) {
  if (!document.profile_id) return "—";
  return document.profile_revision
    ? `${document.profile_id} r${document.profile_revision}`
    : document.profile_id;
}

const PROCESSING_CODE_LABELS: Record<string, string> = {
  office_preview_unavailable: "Office preview unavailable",
  office_preview_page_mapping_missing: "Office preview page mapping missing",
  image_ocr_failed: "Image OCR failed",
  visual_interpretation_failed: "Visual interpretation failed",
  optional_processor_failed: "Optional processor failed",
  legacy_converter_unavailable: "Legacy converter unavailable",
  no_searchable_evidence: "No searchable evidence",
};

function processingCodeLabel(value: string, t: TFunction) {
  if (!value) return "";
  if (value === "pdf_preview_unavailable") {
    return t("ingestion.pdfPreviewUnavailable");
  }
  return (
    PROCESSING_CODE_LABELS[value] ??
    value
      .replace(/[:_]+/g, " ")
      .replace(/\b\w/g, (character) => character.toUpperCase())
  );
}
