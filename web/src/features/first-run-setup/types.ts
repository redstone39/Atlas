export interface FirstAdminStatus {
  claim_available: boolean;
}

export interface FirstAdminClaimInput {
  displayName: string;
  email: string;
  password: string;
}

export type SetupStep = "admin" | "provider" | "project" | "document" | "review";
export type ReviewState = "complete" | "incomplete" | "unavailable";

export interface ReviewRow {
  key: "admin" | "provider" | "project" | "document" | "readiness";
  state: ReviewState;
  detail: string;
}
