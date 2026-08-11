import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "../ui/button";
import {
  Card,
  CardContent,
} from "../ui/card";
import {
  LoadErrorState,
  LoadingState,
  localizedStatusLabel,
  PageHeader,
  StatusBadge,
  serverMessage,
  StatusRow,
} from "../../shared/product-ui";
import {
  opsApi,
  SetupRecoveryCard,
  type ReadinessState,
} from "../../features/ops/index";
import { isAbortError, sessionQueryClient } from "../../shared/session-query-client";

export function OpsPage({
  canManageSetup,
  onNavigate,
}: {
  canManageSetup: boolean;
  onNavigate: (
    route: "/admin/projects" | "/admin/models" | "/admin/document-library"
  ) => void;
}) {
  const { t } = useTranslation();
  const [readiness, setReadiness] = useState<ReadinessState | null>(null);
  const [loadError, setLoadError] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoadError(false);
    try {
      const nextReadiness = await sessionQueryClient.query({
        key: ["ops", "readiness"],
        signal: controller.signal,
        queryFn: opsApi.readiness,
      });
      if (!controller.signal.aborted) setReadiness(nextReadiness);
    } catch (error) {
      if (!isAbortError(error)) setLoadError(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => requestRef.current?.abort();
  }, [refresh]);

  if (loadError) {
    return (
      <section className="flex flex-col gap-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <PageHeader title={t("ops.title")} />
        </div>
        <LoadErrorState
          title={t("admin.listLoadFailed")}
          description={t("ops.readinessLoadFailed")}
          retryLabel={t("admin.retry")}
          onRetry={() => void refresh()}
        />
      </section>
    );
  }

  if (!readiness) {
    return (
      <section className="flex flex-col gap-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <PageHeader title={t("ops.title")} />
        </div>
        <LoadingState
          title={t("app.readinessChecking")}
        />
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title={t("ops.title")} />
        <Button variant="outline" onClick={() => void refresh()}>
          {t("ops.refresh")}
        </Button>
      </div>
      <Card>
        <CardContent className="flex flex-col gap-4 pt-6">
          <StatusBadge
            semantic={!readiness ? "progress" : readiness.ready ? "success" : "attention"}
            label={localizedStatusLabel(readiness?.health ?? "checking", t)}
          />
          <p className="text-sm text-muted-foreground">
            {readiness?.message_code
              ? serverMessage(readiness, t)
              : t("app.readinessChecking")}
          </p>
          <div className="grid gap-2">
            {(readiness?.setup_blockers.length ?? 0) === 0 ? (
              <StatusRow label={t("ops.setup")} value={t("ops.complete")} good />
            ) : (
              readiness?.setup_blockers.map((blocker) => (
                <StatusRow
                  key={blocker}
                  label={t("ops.blocker")}
                  value={serverMessage(blocker, t)}
                />
              ))
            )}
          </div>
        </CardContent>
      </Card>
      <SetupRecoveryCard
        readiness={readiness}
        canAct={canManageSetup}
        onNavigate={onNavigate}
      />
    </section>
  );
}
