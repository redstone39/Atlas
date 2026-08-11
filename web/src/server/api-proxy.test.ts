import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import * as apiRoute from "@/app/api/[...path]/route";
import { proxyApiRequest } from "./api-proxy";

type ObservedRequest = {
  body: string;
  headers: IncomingMessage["headers"];
  method: string;
  url: string;
};

type TestUpstream = {
  close: () => Promise<void>;
  observed: ObservedRequest[];
  origin: string;
};

async function startUpstream(label: string): Promise<TestUpstream> {
  const observed: ObservedRequest[] = [];
  const server = createServer((request: IncomingMessage, response: ServerResponse) => {
    const chunks: Buffer[] = [];
    request.on("data", (chunk: Buffer) => chunks.push(chunk));
    request.on("end", () => {
      const entry = {
        body: Buffer.concat(chunks).toString("utf8"),
        headers: request.headers,
        method: request.method ?? "",
        url: request.url ?? "",
      };
      observed.push(entry);

      response.setHeader("x-upstream", label);
      response.setHeader("set-cookie", [
        "atlas_session=session-value; HttpOnly; SameSite=Lax; Path=/",
        "atlas_aux=aux-value; Path=/",
      ]);

      if (entry.method === "HEAD") {
        response.statusCode = 206;
        response.setHeader("content-range", "bytes 0-3/10");
        response.setHeader("content-length", "4");
        response.end();
        return;
      }
      if (entry.url.includes("/events")) {
        response.statusCode = 200;
        response.setHeader("content-type", "text/event-stream");
        response.write("data: first\n\n");
        setImmediate(() => response.end("data: second\n\n"));
        return;
      }

      response.statusCode = entry.method === "POST" ? 201 : 202;
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify({ ...entry, label }));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    close: () => new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
    observed,
    origin: `http://127.0.0.1:${port}`,
  };
}

function nextRequest(
  path: string,
  init: RequestInit & { duplex?: "half" } = {},
): NextRequest {
  return new NextRequest(
    `http://atlas.test${path}`,
    init as ConstructorParameters<typeof NextRequest>[1],
  );
}

describe("runtime API proxy", () => {
  let first: TestUpstream;
  let second: TestUpstream;

  beforeAll(async () => {
    [first, second] = await Promise.all([startUpstream("first"), startUpstream("second")]);
  });

  afterAll(async () => {
    delete process.env.ATLAS_PRODUCTION_API_PROXY_TARGET;
    await Promise.all([first.close(), second.close()]);
  });

  it("preserves raw encoded URLs, cookies, status, safe headers, and separate Set-Cookie values", async () => {
    process.env.ATLAS_PRODUCTION_API_PROXY_TARGET = first.origin;
    const response = await proxyApiRequest(
      nextRequest("/api/v1/items/%2Fencoded?query=a%2Fb&space=a+b", {
        headers: { cookie: "atlas_session=request-cookie" },
      }),
    );

    expect(response.status).toBe(202);
    expect(response.headers.get("x-upstream")).toBe("first");
    expect(response.headers.get("content-length")).toBeNull();
    expect(response.headers.getSetCookie()).toEqual([
      "atlas_session=session-value; HttpOnly; SameSite=Lax; Path=/",
      "atlas_aux=aux-value; Path=/",
    ]);
    expect(await response.json()).toMatchObject({
      method: "GET",
      url: "/api/v1/items/%2Fencoded?query=a%2Fb&space=a+b",
      headers: { cookie: "atlas_session=request-cookie" },
    });
  });

  it.each([
    ["POST", "application/json", '{"message":"hello"}'],
    ["PUT", "application/octet-stream", "put-body"],
    ["PATCH", "application/json", '{"enabled":true}'],
    ["DELETE", "application/json", '{"reason":"done"}'],
    ["POST", "multipart/form-data; boundary=atlas", "--atlas\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\nbody\r\n--atlas--\r\n"],
  ])("streams %s %s request bodies", async (method, contentType, body) => {
    process.env.ATLAS_PRODUCTION_API_PROXY_TARGET = first.origin;
    const response = await proxyApiRequest(
      nextRequest("/api/v1/body", {
        method,
        headers: { "content-type": contentType },
        body,
        duplex: "half",
      }),
    );
    const payload = await response.json();

    expect(response.status).toBe(method === "POST" ? 201 : 202);
    expect(payload).toMatchObject({ body, method });
    expect(payload.headers["content-type"]).toBe(contentType);
  });

  it("preserves HEAD range metadata without forwarding a body", async () => {
    process.env.ATLAS_PRODUCTION_API_PROXY_TARGET = first.origin;
    const response = await proxyApiRequest(
      nextRequest("/api/v1/file", {
        method: "HEAD",
        headers: { range: "bytes=0-3" },
      }),
    );

    expect(response.status).toBe(206);
    expect(response.headers.get("content-range")).toBe("bytes 0-3/10");
    expect(response.headers.get("content-length")).toBeNull();
    expect(await response.text()).toBe("");
    expect(first.observed.at(-1)).toMatchObject({ body: "", method: "HEAD" });
  });

  it("streams event chunks in order", async () => {
    process.env.ATLAS_PRODUCTION_API_PROXY_TARGET = first.origin;
    const response = await proxyApiRequest(nextRequest("/api/v1/events"));

    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(await response.text()).toBe("data: first\n\ndata: second\n\n");
  });

  it("reads the upstream target at request time without rebuilding", async () => {
    process.env.ATLAS_PRODUCTION_API_PROXY_TARGET = first.origin;
    const firstResponse = await proxyApiRequest(nextRequest("/api/v1/runtime-target"));
    process.env.ATLAS_PRODUCTION_API_PROXY_TARGET = second.origin;
    const secondResponse = await proxyApiRequest(nextRequest("/api/v1/runtime-target"));

    expect(firstResponse.headers.get("x-upstream")).toBe("first");
    expect(secondResponse.headers.get("x-upstream")).toBe("second");
  });

  it("returns an empty 502 without diagnostics when the upstream is unreachable", async () => {
    const unavailable = await startUpstream("unavailable");
    process.env.ATLAS_PRODUCTION_API_PROXY_TARGET = unavailable.origin;
    await unavailable.close();

    const response = await proxyApiRequest(nextRequest("/api/v1/unreachable"));

    expect(response.status).toBe(502);
    expect(await response.text()).toBe("");
  });

  it("does not declare an OPTIONS contract", () => {
    expect("OPTIONS" in apiRoute).toBe(false);
  });
});
