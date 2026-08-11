import { afterEach, describe, expect, it, vi } from "vitest";

import { directoryAdministrationApi } from "./index";

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
    await directoryAdministrationApi.createConnection({
      ...config,
      bind_password: "create-secret",
      custom_ca_pem: "create-ca",
    });
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
    expect(requests[2].body).toMatchObject({
      bind_password: "replacement-secret",
      custom_ca_pem: "replacement-ca",
    });
    expect(requests[3].body).toMatchObject({
      clear_bind_password: true,
      clear_custom_ca: true,
    });
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
});
