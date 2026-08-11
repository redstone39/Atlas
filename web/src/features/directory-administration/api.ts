import { requestJson } from "../../shared/api-client";
import type {
  DirectoryConnectionCreateInput,
  DirectoryConnectionListResult,
  DirectoryConnectionStatus,
  DirectoryConnectionTestResult,
  DirectoryConnectionUpdateInput,
  DirectoryUserImportResult,
  DirectoryUserSearchResult,
} from "./types";

export const directoryAdministrationApi = {
  listConnections: () =>
    requestJson<DirectoryConnectionListResult>("/api/v1/admin/directory-connections"),
  createConnection: (input: DirectoryConnectionCreateInput) =>
    requestJson<DirectoryConnectionStatus>("/api/v1/admin/directory-connections", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateConnection: (input: DirectoryConnectionUpdateInput) => {
    const body: Record<string, unknown> = { ...input.config };
    if (input.bindPassword) body.bind_password = input.bindPassword;
    if (input.clearBindPassword) body.clear_bind_password = true;
    if (input.customCaPem) body.custom_ca_pem = input.customCaPem;
    if (input.clearCustomCa) body.clear_custom_ca = true;
    return requestJson<DirectoryConnectionStatus>(
      `/api/v1/admin/directory-connections/${encodeURIComponent(input.connectionId)}`,
      { method: "PATCH", body: JSON.stringify(body) },
    );
  },
  testConnection: (connectionId: string) =>
    requestJson<DirectoryConnectionTestResult>(
      `/api/v1/admin/directory-connections/${encodeURIComponent(connectionId)}/test`,
      { method: "POST" },
    ),
  searchUsers: (connectionId: string, query: string, limit = 50) =>
    requestJson<DirectoryUserSearchResult>(
      `/api/v1/admin/directory-connections/${encodeURIComponent(connectionId)}/users/search`,
      {
        method: "POST",
        body: JSON.stringify({ query, limit }),
      },
    ),
  importUsers: (connectionId: string, externalSubjects: string[]) =>
    requestJson<DirectoryUserImportResult>(
      `/api/v1/admin/directory-connections/${encodeURIComponent(connectionId)}/users/import`,
      {
        method: "POST",
        body: JSON.stringify({ external_subjects: externalSubjects }),
      },
    ),
};
