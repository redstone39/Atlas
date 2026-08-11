import { adminAuditSectionRoute } from "@/shared/routes";
import { AuditEventsScreen } from "./screen";

export default function AuditEventsRoute() {
  return <AuditEventsScreen route={adminAuditSectionRoute("events")} />;
}
