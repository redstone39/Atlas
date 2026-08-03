import { BrainCircuit, ChevronDown } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "../../components/ui/collapsible";
import { Spinner } from "../../components/ui/spinner";
import { cn } from "../../lib/utils";
import type { ReasoningProgress } from "./types";

export function ReasoningTimeline({
  items = [],
  live = false,
}: {
  items?: ReasoningProgress[];
  live?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;

  const content = (
    <ol className="flex flex-col gap-2" aria-label={t("workspace.reasoningTimeline")}>
      {items.map((item, index) => (
        <li
          key={item.event_id}
          className="flex items-start gap-2 rounded-md border bg-muted/30 p-2 text-sm"
        >
          {live && index === items.length - 1 && item.status === "started" ? (
            <Spinner className="mt-0.5" aria-hidden="true" />
          ) : (
            <BrainCircuit className="mt-0.5" aria-hidden="true" />
          )}
          <span className="min-w-0 flex-1">
            <span className="font-medium">
              {t(`workspace.reasoningPhase.${item.phase}`)}
            </span>
            {item.cycle ? (
              <span className="ml-2 text-xs text-muted-foreground">
                {t("workspace.reasoningCycle", { cycle: item.cycle })}
              </span>
            ) : null}
          </span>
          <Badge
            variant={item.status === "failed" ? "destructive" : "outline"}
          >
            {t(`workspace.reasoningStatus.${item.status}`)}
          </Badge>
        </li>
      ))}
    </ol>
  );

  if (live) {
    return (
      <section className="flex flex-col gap-2" aria-live="polite">
        <div className="text-xs font-medium text-muted-foreground">
          {t("workspace.reasoningLive")}
        </div>
        {content}
      </section>
    );
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="w-fit">
          <BrainCircuit data-icon="inline-start" />
          {t("workspace.reasoningTimeline")}
          <ChevronDown
            data-icon="inline-end"
            className={cn("transition-transform", open && "rotate-180")}
          />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">{content}</CollapsibleContent>
    </Collapsible>
  );
}
