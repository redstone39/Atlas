import { ProcessingPluginsFeature } from "../../features/processing-plugins";

export function AdminPluginsPage({
  initialTab,
  requestedRunId,
}: {
  initialTab: "plugins" | "runs";
  requestedRunId: string | null;
}) {
  return (
    <ProcessingPluginsFeature
      initialTab={initialTab}
      requestedRunId={requestedRunId}
    />
  );
}
