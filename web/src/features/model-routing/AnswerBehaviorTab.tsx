import { MessageSquareText, RefreshCw, Save, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Field, FieldDescription, FieldError, FieldLabel } from "../../components/ui/field";
import { Textarea } from "../../components/ui/textarea";
import { LoadErrorState, LoadingState, serverMessage } from "../../shared/product-ui";
import type { AnswerBehaviorStatus } from "./types";

export function AnswerBehaviorTab({
 answerBehavior, draft, loading, error, pendingAction, locale,
 onDraftChange, onRefresh, onUpdate,
}: {
 answerBehavior: AnswerBehaviorStatus | null; draft: string; loading: boolean; error: string;
 pendingAction: string; locale: string; onDraftChange: (value: string) => void;
 onRefresh: () => Promise<void>; onUpdate: (guidance: string | null) => Promise<void>;
}) {
 const { t } = useTranslation();
 const length = Array.from(draft).length;
 const normalizedDraft = draft.trim();
 const canSave = Boolean(answerBehavior && normalizedDraft && length <= 2000 &&
   normalizedDraft !== (answerBehavior.custom_guidance ?? ""));
 return (
    <>
{loading ? (
              <LoadingState title={t("models.answerBehaviorLoading")} />
            ) : error && !answerBehavior ? (
              <LoadErrorState
                title={t("admin.listLoadFailed")}
                description={serverMessage(error, t)}
                retryLabel={t("admin.retry")}
                onRetry={() => void onRefresh()}
              />
            ) : answerBehavior ? (
              <Card>
                <CardHeader>
                  <CardTitle>{t("models.answerBehaviorTitle")}</CardTitle>
                  <CardDescription>
                    {t("models.answerBehaviorDescription")}
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col gap-5">
                  {error ? (
                    <Alert variant="destructive">
                      <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
                      <AlertDescription>
                        {serverMessage(error, t)}
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  <Alert>
                    <MessageSquareText />
                    <AlertTitle>{t("models.answerBehaviorCoreRulesTitle")}</AlertTitle>
                    <AlertDescription>
                      {t("models.answerBehaviorCoreRulesDescription")}
                    </AlertDescription>
                  </Alert>
                  <Field>
                    <FieldLabel htmlFor="answer-behavior-guidance">
                      {t("models.answerBehaviorGuidanceLabel")}
                    </FieldLabel>
                    <Textarea
                      id="answer-behavior-guidance"
                      rows={9}
                      value={draft}
                      onChange={(event) =>
                        onDraftChange(event.target.value)
                      }
                      aria-invalid={length > 2000}
                    />
                    {length > 2000 ? (
                      <FieldError>
                        {t("models.answerBehaviorTooLong")}
                      </FieldError>
                    ) : null}
                    <FieldDescription>
                      {t("models.answerBehaviorGuidanceHelp")}
                    </FieldDescription>
                    <div className="text-right text-xs text-muted-foreground">
                      {t("models.answerBehaviorCharacterCount", {
                        count: length,
                      })}
                    </div>
                  </Field>
                  <div className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-2">
                    <div>
                      {t("models.answerBehaviorRevision", {
                        revision: answerBehavior.revision,
                      })}
                    </div>
                    <div className="break-all">
                      {t("models.answerBehaviorDigest")}:{" "}
                      {answerBehavior.guidance_digest ??
                        t("models.answerBehaviorNotConfigured")}
                    </div>
                    <div>
                      {t("models.answerBehaviorUpdatedBy")}:{" "}
                      {answerBehavior.updated_by ??
                        t("models.answerBehaviorNotConfigured")}
                    </div>
                    <div>
                      {t("models.answerBehaviorUpdatedAt")}:{" "}
                      {readableTime(
                        answerBehavior.updated_at,
                        t("models.answerBehaviorNotConfigured"),
                        locale,
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    <Button
                      variant="outline"
                      onClick={() => void onRefresh()}
                      disabled={pendingAction === "answer-behavior"}
                    >
                      <RefreshCw data-icon="inline-start" />
                      {t("models.refresh")}
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => void onUpdate(null)}
                      disabled={
                        pendingAction === "answer-behavior" ||
                        answerBehavior.custom_guidance === null
                      }
                    >
                      <Trash2 data-icon="inline-start" />
                      {t("models.answerBehaviorClear")}
                    </Button>
                    <Button
                      onClick={() =>
                        void onUpdate(normalizedDraft)
                      }
                      disabled={
                        pendingAction === "answer-behavior" ||
                        !canSave
                      }
                    >
                      <Save data-icon="inline-start" />
                      {t("models.answerBehaviorSave")}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ) : null}
    </>
 );
}

function readableTime(value: string | null, fallback: string, locale: string) {
  if (!value) return fallback;
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString(locale);
}
