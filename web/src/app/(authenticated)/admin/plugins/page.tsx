import { PluginsScreen } from "./screen";

export default async function PluginsRoute({
  searchParams,
}: {
  searchParams: Promise<{
    run?: string | string[];
    tab?: string | string[];
  }>;
}) {
  const { run, tab } = await searchParams;
  const requestedRunId = typeof run === "string" && run.length > 0 ? run : null;
  const initialTab = requestedRunId || tab === "runs" ? "runs" : "plugins";
  return (
    <PluginsScreen
      initialTab={initialTab}
      requestedRunId={requestedRunId}
    />
  );
}
