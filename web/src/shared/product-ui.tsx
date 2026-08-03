import { type ComponentType, type KeyboardEvent, type ReactNode, useState } from "react";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  CircleOff,
  Clock3,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription, AlertTitle } from "../components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "../components/ui/alert-dialog";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "../components/ui/collapsible";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "../components/ui/empty";
import { Field, FieldLabel } from "../components/ui/field";
import { Spinner } from "../components/ui/spinner";
import { ToggleGroup, ToggleGroupItem } from "../components/ui/toggle-group";
import { persistLanguage, type SupportedLanguage } from "../i18n";
import { cn } from "../lib/utils";
import { localizeMessage } from "./user-messages";

export const clickableSurfaceClassName =
  "cursor-pointer transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export const clickableCardClassName = cn(
  clickableSurfaceClassName,
  "rounded-md border bg-card",
);

export function activateOnEnterOrSpace<T extends HTMLElement>(
  event: KeyboardEvent<T>,
  onActivate: () => void,
) {
  if (event.currentTarget !== event.target) return;
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  onActivate();
}

export type StatusSemantic =
  | "success"
  | "progress"
  | "attention"
  | "failure"
  | "denied"
  | "refused"
  | "inactive"
  | "unknown";

const statusSemanticClassName: Record<StatusSemantic, string> = {
  success: "border-evidence/30 bg-evidence/10 text-evidence",
  progress: "border-info/30 bg-info/10 text-info",
  attention: "border-warning/30 bg-warning/10 text-warning",
  failure: "border-destructive/30 bg-destructive/10 text-destructive",
  denied: "border-destructive/30 bg-destructive/10 text-destructive",
  refused: "border-warning/30 bg-warning/10 text-warning",
  inactive: "border-border bg-muted text-muted-foreground",
  unknown: "border-border bg-muted text-muted-foreground",
};

const statusSemanticIcon: Record<StatusSemantic, ComponentType<{ className?: string }>> = {
  success: CheckCircle2,
  progress: Clock3,
  attention: AlertTriangle,
  failure: XCircle,
  denied: Ban,
  refused: Ban,
  inactive: CircleOff,
  unknown: CircleHelp,
};

export function StatusBadge({
  semantic,
  label,
  className,
}: {
  semantic: StatusSemantic;
  label: string;
  className?: string;
}) {
  const Icon = statusSemanticIcon[semantic];
  return (
    <Badge
      variant="outline"
      data-status-semantic={semantic}
      className={cn(
        "gap-1.5 border px-2 py-0.5 [&>svg]:size-3.5",
        statusSemanticClassName[semantic],
        className,
      )}
    >
      <Icon aria-hidden="true" />
      {label}
    </Badge>
  );
}

export function TargetSummary({
  label,
  title,
  description,
  children,
  className,
}: {
  label: string;
  title: string;
  description?: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-md border bg-muted/50 px-3 py-2", className)}>
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="mt-1 font-medium">{title}</div>
      {description && (
        <div className="text-sm text-muted-foreground">{description}</div>
      )}
      {children && <div className="mt-2 flex flex-wrap gap-2">{children}</div>}
    </div>
  );
}

export function serverMessage(message: unknown, t: Parameters<typeof localizeMessage>[1]) {
  return localizeMessage(message, t);
}

export function resultStatusPresentation(status: string, t: (key: string) => string) {
  if (status === "submitted" || status === "processing") {
    return { label: t(`statusValues.${status}`), semantic: "progress" as const };
  }
  if (status === "failed_closed") {
    return { label: t("status.failedClosed"), semantic: "failure" as const };
  }
  if (status === "refused") {
    return { label: t("status.refused"), semantic: "refused" as const };
  }
  if (status === "clarification") {
    return { label: t("status.clarification"), semantic: "attention" as const };
  }
  if (status === "external_unverified") {
    return { label: t("status.externalUnverified"), semantic: "attention" as const };
  }
  if (status === "mixed_answer") {
    return { label: t("status.mixedAnswer"), semantic: "attention" as const };
  }
  if (status === "verification_incomplete") {
    return { label: t("status.verificationIncomplete"), semantic: "attention" as const };
  }
  if (
    status === "completed" ||
    status === "answer" ||
    status === "dialogue" ||
    status === "grounded_answer"
  ) {
    return { label: t("status.answered"), semantic: "success" as const };
  }
  return { label: t("status.unknown"), semantic: "unknown" as const };
}

export function resultStatusLabel(status: string, t: (key: string) => string) {
  return resultStatusPresentation(status, t).label;
}

export function resultStatusSemantic(status: string): StatusSemantic {
  return resultStatusPresentation(status, (key) => key).semantic;
}

export function conversationTurnStatusPresentation(
  turn: {
    execution_status: string;
    retryable: boolean;
    validation_state: string;
    response_kind: string;
  },
  t: (key: string) => string,
) {
  if (turn.execution_status === "submitted" || turn.execution_status === "processing") {
    return resultStatusPresentation(turn.execution_status, t);
  }
  if (turn.execution_status === "failed_closed") {
    return resultStatusPresentation(turn.retryable ? "failed_closed" : "refused", t);
  }
  if (turn.validation_state === "degraded") {
    return resultStatusPresentation("verification_incomplete", t);
  }
  return resultStatusPresentation(turn.response_kind, t);
}

export function localizedStatusLabel(
  value: string | null | undefined,
  t: (key: string, options?: { defaultValue?: string }) => string,
) {
  if (!value) return t("status.unknown");
  return t(`statusValues.${value}`, { defaultValue: t("status.unknown") });
}

export function AdminAccessDenied({
  operatorAllowed = false,
}: {
  operatorAllowed?: boolean;
}) {
  const { t } = useTranslation();
  const description = operatorAllowed
    ? t("admin.operatorAccessDeniedDescription")
    : t("admin.accessDeniedDescription");
  return (
    <section className="flex flex-col gap-4">
      <PageHeader title={t("admin.accessDeniedTitle")} />
      <Empty className="border">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ShieldCheck />
          </EmptyMedia>
          <EmptyTitle>{t("admin.accessDeniedTitle")}</EmptyTitle>
          <EmptyDescription>{description}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    </section>
  );
}

export function LanguageSwitch() {
  const { i18n, t } = useTranslation();
  const language: SupportedLanguage = i18n.language === "zh-TW" ? "zh-TW" : "en";

  function changeLanguage(next: string) {
    if (next !== "en" && next !== "zh-TW") {
      return;
    }
    persistLanguage(next);
    void i18n.changeLanguage(next);
  }

  return (
    <Field orientation="horizontal" className="w-auto">
      <FieldLabel className="sr-only">{t("lang.label")}</FieldLabel>
      <ToggleGroup
        type="single"
        value={language}
        onValueChange={changeLanguage}
        variant="outline"
        size="sm"
        aria-label={t("lang.label")}
      >
        <ToggleGroupItem value="en" aria-label={t("lang.enLabel")}>
          {t("lang.en")}
        </ToggleGroupItem>
        <ToggleGroupItem value="zh-TW" aria-label={t("lang.zhTwLabel")}>
          {t("lang.zhTw")}
        </ToggleGroupItem>
      </ToggleGroup>
    </Field>
  );
}

export function LoadingShell() {
  const { t } = useTranslation();
  return (
    <main className="grid min-h-screen place-items-center bg-background text-foreground">
      <div
        role="status"
        aria-live="polite"
        aria-busy="true"
        aria-label={t("app.loading")}
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <Spinner data-icon="inline-start" aria-hidden="true" />
        {t("app.loading")}
      </div>
    </main>
  );
}

export function LoadingState({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <Empty
      className="border"
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label={title}
    >
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Spinner aria-hidden="true" />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {description && <EmptyDescription>{description}</EmptyDescription>}
      </EmptyHeader>
    </Empty>
  );
}

export function LoadErrorState({
  title,
  description,
  retryLabel,
  onRetry,
}: {
  title: string;
  description: string;
  retryLabel: string;
  onRetry: () => void;
}) {
  return (
    <Alert variant="destructive">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-col gap-3">
        <span>{description}</span>
        <Button type="button" variant="outline" size="sm" className="w-fit" onClick={onRetry}>
          {retryLabel}
        </Button>
      </AlertDescription>
    </Alert>
  );
}

export function ConfirmActionButton({
  ariaLabel,
  children,
  icon,
  disabled = false,
  size = "sm",
  confirmTitle,
  confirmDescription,
  confirmLabel,
  cancelLabel,
  onConfirm,
}: {
  ariaLabel: string;
  children: ReactNode;
  icon: ReactNode;
  disabled?: boolean;
  size?: "default" | "sm";
  confirmTitle: string;
  confirmDescription: string;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button
          variant="outline"
          size={size}
          aria-label={ariaLabel}
          disabled={disabled}
          onClick={(event) => event.stopPropagation()}
        >
          {icon}
          {children}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent onClick={(event) => event.stopPropagation()}>
        <AlertDialogHeader>
          <AlertDialogTitle>{confirmTitle}</AlertDialogTitle>
          <AlertDialogDescription>{confirmDescription}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{cancelLabel}</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={(event) => {
              event.stopPropagation();
              onConfirm();
            }}
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function PageHeader({ title, description }: { title: string; description?: string }) {
  return (
    <header className="flex flex-col gap-1">
      <h1 className="text-2xl font-semibold tracking-normal">{title}</h1>
      {description && <p className="text-sm text-muted-foreground">{description}</p>}
    </header>
  );
}

export function TechnicalDetails({
  children,
  label,
  className,
}: {
  children: ReactNode;
  label: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      data-slot="technical-details"
      className={className}
    >
      <CollapsibleTrigger asChild>
        <Button type="button" variant="ghost" size="sm" className="w-fit">
          {label}
          <ChevronDown
            data-icon="inline-end"
            className={cn("transition-transform", open && "rotate-180")}
          />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">{children}</CollapsibleContent>
    </Collapsible>
  );
}

export function StatusRow({
  label,
  value,
  good = false,
}: {
  label: string;
  value: string;
  good?: boolean;
}) {
  return (
    <div className="flex min-h-10 items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <StatusBadge semantic={good ? "success" : "attention"} label={value} />
    </div>
  );
}
