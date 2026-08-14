export const NOTES_COLLABORATION_WEBSOCKET_PATH = "/collaboration" as const;
export const NOTES_COLLABORATION_HEALTH_PATH = "/health" as const;
export const NOTES_COLLABORATION_READINESS_PATH = "/ready" as const;

export type NotesCollaborationPublicPath =
  | typeof NOTES_COLLABORATION_WEBSOCKET_PATH
  | typeof NOTES_COLLABORATION_HEALTH_PATH
  | typeof NOTES_COLLABORATION_READINESS_PATH;
