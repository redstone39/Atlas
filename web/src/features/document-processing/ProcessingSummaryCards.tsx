import { CircleCheckBig, CircleStop, Clock3, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Card, CardContent } from "../../components/ui/card";
import { ACTIVE_PROCESSING_STATUSES, type ProcessingJobStatus } from "./types";

export function ProcessingSummaryCards({ jobs }: { jobs: ProcessingJobStatus[] }) {
  const { t } = useTranslation();
  const current = jobs.filter((job) => job.is_current);
  const values = [
    {
      key: "active",
      count: current.filter((job) => ACTIVE_PROCESSING_STATUSES.has(job.status)).length,
      icon: Clock3,
    },
    {
      key: "ready",
      count: current.filter((job) => ["ready", "ready_with_warnings"].includes(job.status)).length,
      icon: CircleCheckBig,
    },
    {
      key: "failed",
      count: current.filter((job) => job.status === "failed").length,
      icon: TriangleAlert,
    },
    {
      key: "cancelled",
      count: current.filter((job) => job.status === "cancelled").length,
      icon: CircleStop,
    },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label={t("processing.summaryLabel")}>
      {values.map(({ key, count, icon: Icon }) => (
        <Card key={key}>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <div className="text-2xl font-semibold tabular-nums">{count}</div>
              <div className="text-sm text-muted-foreground">
                {t(`processing.summary.${key}`)}
              </div>
            </div>
            <Icon className="size-5 text-muted-foreground" aria-hidden="true" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
