import { afterEach, describe, expect, it, vi } from "vitest";

import { directoryAdministrationApi } from "./index";
import { projectGovernanceApi } from "../project-governance";
import { teamAdministrationApi } from "../team-administration";

function successfulFetch() {
  const mock = vi.fn().mockResolvedValue({ ok: true, text: async () => "{}" });
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => vi.unstubAllGlobals());

const config = {
  connection_id: "directory-main",
  display_name: "Main AD",
  priority: 1,
  provider_type: "active_directory" as const,
  host: "ad.example.test",
  port: 636,
  tls_mode: "ldaps" as const,
  connect_timeout_seconds: 10,
  operation_timeout_seconds: 10,
  bind_dn: "CN=Atlas,OU=Services,DC=example,DC=test",
  user_base_dn: "OU=People,DC=example,DC=test",
  user_object_filter: "(&(objectCategory=person)(objectClass=user))",
  login_attribute: "userPrincipalName",
  stable_id_attribute: "objectGUID",
  display_name_attribute: "displayName",
  email_attribute: "mail",
  groups_attribute: "memberOf",
  department_attribute: "department",
  title_attribute: "title",
  employee_id_attribute: "employeeID",
  enabled: true,
};

describe("directory administration API boundary", () => {
  it("keeps bind credentials and custom CA write-only across create and update", async () => {
    const fetchMock = successfulFetch();

    await directoryAdministrationApi.listConnections();
    const { connection_id: _createdConnectionId, ...createConfig } = config;
    await directoryAdministrationApi.createConnection({
      ...createConfig,
      bind_password: "create-secret",
      custom_ca_pem: "create-ca",
    }, "directory-create-main");
    const { connection_id: _connectionId, ...updateConfig } = config;
    await directoryAdministrationApi.updateConnection({
      connectionId: config.connection_id,
      config: updateConfig,
      bindPassword: "replacement-secret",
      customCaPem: "replacement-ca",
    });
    await directoryAdministrationApi.updateConnection({
      connectionId: config.connection_id,
      config: updateConfig,
      clearBindPassword: true,
      clearCustomCa: true,
    });

    const requests = fetchMock.mock.calls.map(([path, init = {}]) => ({
      path,
      method: init.method,
      body: init.body ? JSON.parse(String(init.body)) : null,
    }));
    expect(requests[0]).toEqual({
      path: "/api/v1/admin/directory-connections",
      method: undefined,
      body: null,
    });
    expect(requests[1].body).toMatchObject({
      bind_password: "create-secret",
      custom_ca_pem: "create-ca",
    });
    expect(requests[1].body).not.toHaveProperty("connection_id");
    expect(requests[1].body).toHaveProperty(
      "idempotency_key",
      "directory-create-main",
    );
    expect(requests[2].body).toMatchObject({
      bind_password: "replacement-secret",
      custom_ca_pem: "replacement-ca",
    });
    expect(requests[3].body).toMatchObject({
      clear_bind_password: true,
      clear_custom_ca: true,
    });
  });

  it("submits plain LDAP without implicitly clearing a configured CA", async () => {
    const fetchMock = successfulFetch();
    const { connection_id: _connectionId, ...updateConfig } = config;

    await directoryAdministrationApi.updateConnection({
      connectionId: config.connection_id,
      config: {
        ...updateConfig,
        provider_type: "ldap",
        port: 389,
        tls_mode: "plain",
      },
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      provider_type: "ldap",
      port: 389,
      tls_mode: "plain",
    });
    expect(body).not.toHaveProperty("custom_ca_pem");
    expect(body).not.toHaveProperty("clear_custom_ca");
  });

  it("uses the selected source for test, search, and all-or-nothing import", async () => {
    const fetchMock = successfulFetch();

    await directoryAdministrationApi.testConnection("directory/main");
    await directoryAdministrationApi.searchUsers("directory/main", "Ada (admin)", 50);
    await directoryAdministrationApi.importUsers("directory/main", ["subject-1", "subject-2"]);

    expect(fetchMock.mock.calls.map(([path, init = {}]) => ({
      path,
      method: init.method,
      body: init.body ? JSON.parse(String(init.body)) : null,
    }))).toEqual([
      {
        path: "/api/v1/admin/directory-connections/directory%2Fmain/test",
        method: "POST",
        body: null,
      },
      {
        path: "/api/v1/admin/directory-connections/directory%2Fmain/users/search",
        method: "POST",
        body: { query: "Ada (admin)", limit: 50 },
      },
      {
        path: "/api/v1/admin/directory-connections/directory%2Fmain/users/import",
        method: "POST",
        body: { external_subjects: ["subject-1", "subject-2"] },
      },
    ]);
  });

  it("keeps scoped Team and Project directory calls nested and batch-only", async () => {
    const fetchMock = successfulFetch();

    await teamAdministrationApi.listDirectoryConnections("team-a");
    await teamAdministrationApi.searchDirectoryUsers(
      "team-a",
      "directory/main",
      "department",
      "R&D",
    );
    await teamAdministrationApi.importDirectoryMembers(
      "team-a",
      "directory/main",
      ["subject-1", "subject-2"],
      "uploader",
      "team-import-key",
    );
    await projectGovernanceApi.listDirectoryConnections("project-a");
    await projectGovernanceApi.searchDirectoryUsers(
      "project-a",
      "directory/main",
      "member",
      "Ada",
    );
    await projectGovernanceApi.importDirectoryMembers(
      "project-a",
      "directory/main",
      ["subject-1", "subject-2"],
      "contributor",
      "project-import-key",
    );

    expect(fetchMock.mock.calls.map(([path, init = {}]) => ({
      path,
      method: init.method,
      body: init.body ? JSON.parse(String(init.body)) : null,
    }))).toEqual([
      {
        path: "/api/v1/admin/teams/team-a/directory-connections",
        method: undefined,
        body: null,
      },
      {
        path: "/api/v1/admin/teams/team-a/directory-connections/directory%2Fmain/users/search",
        method: "POST",
        body: { search_mode: "department", query: "R&D", limit: 100 },
      },
      {
        path: "/api/v1/admin/teams/team-a/directory-connections/directory%2Fmain/users/import",
        method: "POST",
        body: {
          external_subjects: ["subject-1", "subject-2"],
          role: "uploader",
          idempotency_key: "team-import-key",
        },
      },
      {
        path: "/api/v1/admin/projects/project-a/directory-connections",
        method: undefined,
        body: null,
      },
      {
        path: "/api/v1/admin/projects/project-a/directory-connections/directory%2Fmain/users/search",
        method: "POST",
        body: { search_mode: "member", query: "Ada", limit: 100 },
      },
      {
        path: "/api/v1/admin/projects/project-a/directory-connections/directory%2Fmain/users/import",
        method: "POST",
        body: {
          external_subjects: ["subject-1", "subject-2"],
          role: "contributor",
          idempotency_key: "project-import-key",
        },
      },
    ]);
  });
});
