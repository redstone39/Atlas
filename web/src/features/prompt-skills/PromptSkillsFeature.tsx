"use client";

import type { TFunction } from "i18next";
import { useEffect, useState } from "react";
import {
  ArrowLeft,
  BrainCircuit,
  Eye,
  FileCode2,
  ListChecks,
  MessageSquareText,
  Plus,
  RefreshCw,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "../../components/ui/accordion";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../../components/ui/dialog";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "../../components/ui/empty";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { Separator } from "../../components/ui/separator";
import { Spinner } from "../../components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { LoadingState, PageHeader, StatusBadge } from "../../shared/product-ui";
import { ApiError, localizeMessage } from "../../shared/user-messages";
import { promptSkillsApi } from "./api";
import type {
  PromptSkillCategory,
  PromptSkillRevision,
  PromptSkillSummary,
} from "./types";

const SLOT_CATEGORIES = [
  {
    category: "understanding",
    icon: BrainCircuit,
    titleKey: "promptSkills.slotUnderstanding",
    descriptionKey: "promptSkills.slotUnderstandingDescription",
  },
  {
    category: "planner",
    icon: ListChecks,
    titleKey: "promptSkills.slotPlanner",
    descriptionKey: "promptSkills.slotPlannerDescription",
  },
  {
    category: "answer",
    icon: MessageSquareText,
    titleKey: "promptSkills.slotAnswer",
    descriptionKey: "promptSkills.slotAnswerDescription",
  },
] as const satisfies ReadonlyArray<{
  category: PromptSkillCategory;
  icon: typeof BrainCircuit;
  titleKey: string;
  descriptionKey: string;
}>;

function categoryLabel(t: TFunction, category: PromptSkillCategory) {
  return t(
    category === "understanding"
      ? "promptSkills.categoryUnderstanding"
      : category === "planner"
        ? "promptSkills.categoryPlanner"
        : "promptSkills.categoryAnswer",
  );
}
type SkillEnabledHandler = (
  skill: PromptSkillSummary,
  revision: number,
  enabled: boolean,
) => Promise<void>;
type RevisionViewHandler = (
  skill: PromptSkillSummary,
  revision: number,
) => Promise<void>;
type UploadMode = "new" | "revision";
type PendingUpload = {
  name: string;
  file: File;
  expectedHeadRevision: number;
  idempotencyKey: string;
};

export function PromptSkillsFeature() {
  const { t } = useTranslation();
  const [selectedCategory, setSelectedCategory] =
    useState<PromptSkillCategory | null>(null);

  if (selectedCategory !== null) {
    return (
      <PromptSkillsManager
        key={selectedCategory}
        category={selectedCategory}
        onBack={() => setSelectedCategory(null)}
      />
    );
  }

  return (
    <section className="flex flex-col gap-5">
      <PageHeader
        title={t("promptSkills.title")}
        description={t("promptSkills.description")}
      />
      <div className="grid gap-4 lg:grid-cols-3">
        {SLOT_CATEGORIES.map((slot) => {
          const Icon = slot.icon;
          return (
            <Card key={slot.category}>
              <CardHeader>
                <div className="flex items-center gap-3">
                  <Icon aria-hidden="true" />
                  <CardTitle>{t(slot.titleKey)}</CardTitle>
                </div>
                <CardDescription>{t(slot.descriptionKey)}</CardDescription>
              </CardHeader>
              <CardContent>
                <Badge variant="outline">{slot.category}</Badge>
              </CardContent>
              <CardFooter>
                <Button onClick={() => setSelectedCategory(slot.category)}>
                  {t("promptSkills.manageSlot", {
                    category: categoryLabel(t, slot.category),
                  })}
                </Button>
              </CardFooter>
            </Card>
          );
        })}
      </div>
    </section>
  );
}

function PromptSkillsManager({
  category,
  onBack,
}: {
  category: PromptSkillCategory;
  onBack: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [skills, setSkills] = useState<PromptSkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadMode, setUploadMode] = useState<UploadMode>("new");
  const [uploadName, setUploadName] = useState("");
  const [uploadSelectedName, setUploadSelectedName] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [pendingUpload, setPendingUpload] = useState<PendingUpload | null>(null);
  const [uploadNeedsRetry, setUploadNeedsRetry] = useState(false);
  const [revisionDetail, setRevisionDetail] = useState<PromptSkillRevision | null>(null);

  async function refresh() {
    setLoading(true);
    setLoadError(null);
    try {
      const result = await promptSkillsApi.list(category);
      setSkills(result.items);
    } catch (cause) {
      setLoadError(
        cause instanceof ApiError
          ? localizeMessage(cause.reference, t, "promptSkills.loadFailed")
          : t("promptSkills.loadFailed"),
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [category]);

  async function upload(requestToRetry: PendingUpload | null = null) {
    const selectedSkill = skills.find(
      (skill) => skill.control.name === uploadSelectedName,
    );
    const name =
      uploadMode === "new" ? uploadName.trim() : selectedSkill?.control.name;
    const expectedHeadRevision =
      uploadMode === "new" ? 0 : selectedSkill?.control.head_revision;
    if (
      !requestToRetry &&
      (!uploadFile || !name || expectedHeadRevision === undefined)
    ) {
      return;
    }
    const request =
      requestToRetry ?? {
        name: name!,
        file: uploadFile!,
        expectedHeadRevision: expectedHeadRevision!,
        idempotencyKey: crypto.randomUUID(),
      };
    setPendingUpload(request);
    setUploadNeedsRetry(false);
    setBusyKey("upload");
    try {
      await promptSkillsApi.upload(
        category,
        request.name,
        request.file,
        request.expectedHeadRevision,
        request.idempotencyKey,
      );
      setPendingUpload(null);
      setUploadOpen(false);
      setUploadMode("new");
      setUploadName("");
      setUploadSelectedName("");
      setUploadFile(null);
      toast.success(t("promptSkills.uploaded"));
      await refresh();
    } catch (cause) {
      if (cause instanceof ApiError) {
        setPendingUpload(null);
        if (cause.status === 412) {
          toast.error(t("promptSkills.staleReloaded"));
          await refresh();
        } else {
          toast.error(
            localizeMessage(
              cause.reference,
              t,
              "promptSkills.actionFailed",
            ),
          );
        }
      } else {
        setUploadNeedsRetry(true);
        toast.error(t("promptSkills.uploadUncertain"));
      }
    } finally {
      setBusyKey(null);
    }
  }

  async function setEnabled(
    skill: PromptSkillSummary,
    revision: number,
    enabled: boolean,
  ) {
    const key = `${skill.control.name}:${revision}:${enabled ? "enable" : "disable"}`;
    setBusyKey(key);
    try {
      await promptSkillsApi.setEnabled(category, skill, revision, enabled);
      toast.success(
        t(enabled ? "promptSkills.enabled" : "promptSkills.disabled", {
          name: skill.control.name,
          revision,
          category: categoryLabel(t, category),
        }),
      );
      await refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 412) {
        toast.error(t("promptSkills.staleReloaded"));
        await refresh();
      } else {
        toast.error(
          cause instanceof ApiError
            ? localizeMessage(cause.reference, t, "promptSkills.actionFailed")
            : t("promptSkills.actionFailed"),
        );
      }
    } finally {
      setBusyKey(null);
    }
  }
  async function viewRevision(skill: PromptSkillSummary, revision: number) {
    const key = `${skill.control.name}:${revision}:view`;
    setBusyKey(key);
    try {
      setRevisionDetail(
        await promptSkillsApi.getRevision(
          category,
          skill.control.name,
          revision,
        ),
      );
    } catch (cause) {
      toast.error(
        cause instanceof ApiError
          ? localizeMessage(cause.reference, t, "promptSkills.actionFailed")
          : t("promptSkills.actionFailed"),
      );
    } finally {
      setBusyKey(null);
    }
  }


  const header = (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex flex-col gap-3">
        <Button variant="outline" className="self-start" onClick={onBack}>
          <ArrowLeft data-icon="inline-start" />
          {t("promptSkills.backToSlots")}
        </Button>
        <PageHeader
          title={t("promptSkills.manageTitle", {
            category: categoryLabel(t, category),
          })}
          description={t("promptSkills.manageDescription", {
            category: categoryLabel(t, category),
          })}
        />
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        <Button
          variant="outline"
          onClick={() => void refresh()}
          disabled={loading || busyKey !== null}
        >
          <RefreshCw data-icon="inline-start" />
          {t("promptSkills.refresh")}
        </Button>
        <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
          <DialogTrigger asChild>
            <Button disabled={busyKey !== null}>
              <Plus data-icon="inline-start" />
              {t("promptSkills.uploadAction")}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {t("promptSkills.uploadTitle", {
                  category: categoryLabel(t, category),
                })}
              </DialogTitle>
              <DialogDescription>
                {t("promptSkills.uploadDescription")}
              </DialogDescription>
            </DialogHeader>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="prompt-skill-category">
                  {t("promptSkills.category")}
                </FieldLabel>
                <Select value={category} disabled>
                  <SelectTrigger id="prompt-skill-category" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {SLOT_CATEGORIES.map((slot) => (
                        <SelectItem key={slot.category} value={slot.category}>
                          {categoryLabel(t, slot.category)}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel htmlFor="prompt-skill-upload-mode">
                  {t("promptSkills.uploadMode")}
                </FieldLabel>
                <Select
                  value={uploadMode}
                  disabled={pendingUpload !== null}
                  onValueChange={(value) => {
                    setUploadMode(value as UploadMode);
                    setUploadName("");
                    setUploadSelectedName("");
                  }}
                >
                  <SelectTrigger id="prompt-skill-upload-mode" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                    <SelectItem value="new">
                      {t("promptSkills.newSkill")}
                    </SelectItem>
                    <SelectItem value="revision">
                      {t("promptSkills.newRevision")}
                    </SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              {uploadMode === "new" ? (
                <Field>
                  <FieldLabel htmlFor="prompt-skill-name">
                    {t("promptSkills.name")}
                  </FieldLabel>
                  <Input
                    id="prompt-skill-name"
                    value={uploadName}
                    disabled={pendingUpload !== null}
                    onChange={(event) => setUploadName(event.target.value)}
                    placeholder="compare-options"
                    autoComplete="off"
                  />
                  <FieldDescription>{t("promptSkills.nameHelp")}</FieldDescription>
                </Field>
              ) : (
                <Field>
                  <FieldLabel htmlFor="prompt-skill-existing">
                    {t("promptSkills.existingSkill")}
                  </FieldLabel>
                  <Select
                    value={uploadSelectedName}
                    disabled={pendingUpload !== null || skills.length === 0}
                    onValueChange={setUploadSelectedName}
                  >
                    <SelectTrigger id="prompt-skill-existing" className="w-full">
                      <SelectValue
                        placeholder={t("promptSkills.chooseExistingSkill")}
                      />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                      {skills.map((skill) => (
                        <SelectItem
                          key={skill.control.name}
                          value={skill.control.name}
                        >
                          {skill.control.name} · r{skill.control.head_revision}
                        </SelectItem>
                      ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                  <FieldDescription>
                    {t("promptSkills.revisionModeHelp")}
                  </FieldDescription>
                </Field>
              )}
              <Field>
                <FieldLabel htmlFor="prompt-skill-file">
                  {t("promptSkills.file")}
                </FieldLabel>
                <Input
                  id="prompt-skill-file"
                  type="file"
                  accept=".md,text/markdown"
                  disabled={pendingUpload !== null}
                  onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
                />
                <FieldDescription>{t("promptSkills.fileHelp")}</FieldDescription>
              </Field>
            </FieldGroup>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={busyKey === "upload"}>
                  {t("common.cancel")}
                </Button>
              </DialogClose>
              <Button
                onClick={() =>
                  void upload(uploadNeedsRetry ? pendingUpload : null)
                }
                disabled={
                  busyKey !== null ||
                  (uploadNeedsRetry
                    ? pendingUpload === null
                    : !uploadFile ||
                      uploadFile.name !== "SKILL.md" ||
                      (uploadMode === "new"
                        ? !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(uploadName.trim())
                        : !uploadSelectedName))
                }
              >
                {busyKey === "upload" ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <Upload data-icon="inline-start" />
                )}
                {t(
                  uploadNeedsRetry
                    ? "promptSkills.retryUpload"
                    : "promptSkills.upload",
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );

  if (loading && skills.length === 0) {
    return (
      <section className="flex flex-col gap-5">
        {header}
        <LoadingState title={t("promptSkills.loadingTitle")} />
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-5">
      {header}
      <Alert>
        <ShieldCheck />
        <AlertTitle>{t("promptSkills.approvalTitle")}</AlertTitle>
        <AlertDescription>{t("promptSkills.approvalDescription")}</AlertDescription>
      </Alert>
      {loadError && (
        <Alert variant="destructive">
          <AlertTitle>{t("promptSkills.loadFailedTitle")}</AlertTitle>
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      )}
      {skills.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon"><FileCode2 /></EmptyMedia>
            <EmptyTitle>{t("promptSkills.emptyTitle")}</EmptyTitle>
            <EmptyDescription>{t("promptSkills.emptyDescription")}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <Accordion type="multiple" className="rounded-lg border bg-card px-4">
          {skills.map((skill) => (
            <SkillRow
              key={`${skill.control.category}:${skill.control.name}`}
              skill={skill}
              busyKey={busyKey}
              locale={i18n.language}
              onSetEnabled={setEnabled}
              onView={viewRevision}
              t={t}
            />
          ))}
        </Accordion>
      )}
      <Dialog
        open={revisionDetail !== null}
        onOpenChange={(open) => {
          if (!open) setRevisionDetail(null);
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {revisionDetail
                ? `${revisionDetail.ref.name} r${revisionDetail.ref.revision}`
                : t("promptSkills.revisionDetail")}
            </DialogTitle>
            <DialogDescription>
              {revisionDetail?.description ?? t("promptSkills.revisionDetail")}
            </DialogDescription>
          </DialogHeader>
          {revisionDetail && (
            <div className="grid gap-4">
              <div className="grid gap-1 text-sm sm:grid-cols-2">
                <div>
                  <span className="text-muted-foreground">
                    {t("promptSkills.digest")}:
                  </span>{" "}
                  <code className="break-all text-xs">
                    {revisionDetail.ref.content_digest}
                  </code>
                </div>
                <div>
                  <span className="text-muted-foreground">
                    {t("promptSkills.created")}:
                  </span>{" "}
                  {new Intl.DateTimeFormat(i18n.language, {
                    dateStyle: "medium",
                    timeStyle: "short",
                  }).format(new Date(revisionDetail.created_at))}
                </div>
                {revisionDetail.license && (
                  <div>
                    <span className="text-muted-foreground">
                      {t("promptSkills.license")}:
                    </span>{" "}
                    {revisionDetail.license}
                  </div>
                )}
                {revisionDetail.compatibility && (
                  <div>
                    <span className="text-muted-foreground">
                      {t("promptSkills.compatibility")}:
                    </span>{" "}
                    {revisionDetail.compatibility}
                  </div>
                )}
              </div>
              <div>
                <div className="mb-2 text-sm font-medium">
                  {t("promptSkills.instructions")}
                </div>
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-4 text-xs">
                  {revisionDetail.instructions ?? revisionDetail.source}
                </pre>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}

function SkillRow({
  skill,
  busyKey,
  locale,
  onSetEnabled,
  onView,
  t,
}: {
  skill: PromptSkillSummary;
  busyKey: string | null;
  locale: string;
  onSetEnabled: SkillEnabledHandler;
  onView: RevisionViewHandler;
  t: TFunction;
}) {
  const enabledRevision = skill.control.enabled_revision;
  const revisions = [...skill.revisions].sort(
    (left, right) => right.ref.revision - left.ref.revision,
  );
  return (
    <AccordionItem value={skill.control.name}>
      <AccordionTrigger className="hover:no-underline">
        <div className="flex min-w-0 flex-1 flex-col gap-2 text-left sm:flex-row sm:items-center sm:justify-between sm:pr-3">
          <div className="min-w-0">
            <div className="truncate font-medium">{skill.control.name}</div>
            <div className="line-clamp-1 text-sm font-normal text-muted-foreground">
              {skill.head.description}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge variant="outline">
              {t("promptSkills.headRevision", { revision: skill.control.head_revision })}
            </Badge>
            <StatusBadge
              semantic={enabledRevision === null ? "inactive" : "success"}
              label={
                enabledRevision === null
                  ? t("promptSkills.disabledStatus")
                  : t("promptSkills.enabledRevision", { revision: enabledRevision })
              }
            />
          </div>
        </div>
      </AccordionTrigger>
      <AccordionContent className="pb-5">
        <Separator className="mb-4" />
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("promptSkills.revision")}</TableHead>
              <TableHead>{t("promptSkills.created")}</TableHead>
              <TableHead>{t("promptSkills.digest")}</TableHead>
              <TableHead>{t("promptSkills.status")}</TableHead>
              <TableHead className="text-right">{t("promptSkills.actions")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {revisions.map((revision) => (
              <RevisionRow
                key={revision.ref.revision}
                skill={skill}
                revision={revision}
                busyKey={busyKey}
                locale={locale}
                onView={onView}
                onSetEnabled={onSetEnabled}
                t={t}
              />
            ))}
          </TableBody>
        </Table>
      </AccordionContent>
    </AccordionItem>
  );
}

function RevisionRow({
  skill,
  revision,
  busyKey,
  locale,
  onSetEnabled,
  t,
  onView,
}: {
  skill: PromptSkillSummary;
  revision: PromptSkillRevision;
  busyKey: string | null;
  locale: string;
  onSetEnabled: SkillEnabledHandler;
  onView: RevisionViewHandler;
  t: TFunction;
}) {
  const enabled = skill.control.enabled_revision === revision.ref.revision;
  const actionKey = `${skill.control.name}:${revision.ref.revision}:${enabled ? "disable" : "enable"}`;
  return (
    <TableRow>
      <TableCell className="font-medium">r{revision.ref.revision}</TableCell>
      <TableCell>
        <div>{new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(revision.created_at))}</div>
        <div className="text-xs text-muted-foreground">{revision.created_by}</div>
      </TableCell>
      <TableCell>
        <code className="text-xs" title={revision.ref.content_digest}>
          {revision.ref.content_digest.slice(0, 12)}…
        </code>
      </TableCell>
      <TableCell>
        <StatusBadge
          semantic={enabled ? "success" : "inactive"}
          label={enabled ? t("promptSkills.enabledStatus") : t("promptSkills.disabledStatus")}
        />
      </TableCell>
      <TableCell className="text-right">
        <div className="flex justify-end gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={busyKey !== null}
            onClick={() => void onView(skill, revision.ref.revision)}
          >
            {busyKey === `${skill.control.name}:${revision.ref.revision}:view`
              ? <Spinner data-icon="inline-start" />
              : <Eye data-icon="inline-start" />}
            {t("promptSkills.view")}
          </Button>
          <Button
            size="sm"
            variant={enabled ? "outline" : "default"}
            disabled={busyKey !== null}
            onClick={() => void onSetEnabled(skill, revision.ref.revision, !enabled)}
          >
            {busyKey === actionKey && <Spinner />}
            {t(enabled ? "promptSkills.disable" : "promptSkills.enable")}
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}
