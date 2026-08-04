import { BrainCircuit, ChevronDown } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

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
    <ol className="flex flex-col gap-1.5" aria-label={t("workspace.reasoningTimeline")}>
      {items.map((item, index) => (
        <li
          key={item.event_id}
          data-slot="reasoning-progress"
          className="flex items-start gap-2 py-0.5 text-xs text-muted-foreground"
        >
          {live && index === items.length - 1 && item.status === "started" ? (
            <Spinner className="mt-0.5 size-3.5 opacity-60" aria-hidden="true" />
          ) : (
            <BrainCircuit className="mt-0.5 size-3.5 opacity-50" aria-hidden="true" />
          )}
          <span className="min-w-0 flex-1">
            <span>
              {t(`workspace.reasoningPhase.${item.phase}`)}
            </span>
            {item.cycle ? (
              <span className="ml-2 opacity-70">
                {t("workspace.reasoningCycle", { cycle: item.cycle })}
              </span>
            ) : null}
          </span>
        </li>
      ))}
    </ol>
  );

  if (live) {
    return (
      <section className="flex flex-col gap-1.5 opacity-80" aria-live="polite">
        <div className="text-xs text-muted-foreground">
          {t("workspace.reasoningLive")}
        </div>
        {content}
      </section>
    );
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-auto w-fit px-0 py-1 text-xs font-normal text-muted-foreground hover:bg-transparent hover:text-foreground"
        >
          <BrainCircuit data-icon="inline-start" className="opacity-60" />
          {t("workspace.reasoningTimeline")}
          <ChevronDown
            data-icon="inline-end"
            className={cn("transition-transform", open && "rotate-180")}
          />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-1 opacity-80">{content}</CollapsibleContent>
    </Collapsible>
  );
}
