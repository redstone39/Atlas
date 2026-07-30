import type { MessageReference } from "../../shared/user-messages";

export interface ReadinessState extends MessageReference {
  ready: boolean;
  health: "ok" | "degraded";
  setup_blockers: string[];
  evidence_ready_projects: string[];
}
