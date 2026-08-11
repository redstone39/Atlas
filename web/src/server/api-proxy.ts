import type { NextRequest } from "next/server";

const DEFAULT_API_PROXY_TARGET = "http://127.0.0.1:8002";
const HOP_BY_HOP_HEADERS = [
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
] as const;

function requestHeaders(request: NextRequest): Headers {
  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP_HEADERS) headers.delete(name);
  headers.delete("host");
  headers.delete("content-length");
  return headers;
}

function responseHeaders(response: Response): Headers {
  const headers = new Headers(response.headers);
  for (const name of HOP_BY_HOP_HEADERS) headers.delete(name);
  headers.delete("content-encoding");
  headers.delete("content-length");
  headers.delete("set-cookie");

  const setCookies = response.headers.getSetCookie();
  for (const cookie of setCookies) headers.append("set-cookie", cookie);
  return headers;
}

function upstreamUrl(request: NextRequest): URL {
  const target =
    process.env.ATLAS_PRODUCTION_API_PROXY_TARGET ?? DEFAULT_API_PROXY_TARGET;
  const base = target.endsWith("/") ? target.slice(0, -1) : target;
  return new URL(`${base}${request.nextUrl.pathname}${request.nextUrl.search}`);
}

export async function proxyApiRequest(request: NextRequest): Promise<Response> {
  try {
    const method = request.method.toUpperCase();
    const hasBody = method !== "GET" && method !== "HEAD";
    const init: RequestInit & { duplex?: "half" } = {
      method,
      headers: requestHeaders(request),
      redirect: "manual",
      cache: "no-store",
    };
    if (hasBody) {
      init.body = request.body;
      init.duplex = "half";
    }

    const upstream = await fetch(upstreamUrl(request), init);
    return new Response(method === "HEAD" ? null : upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders(upstream),
    });
  } catch {
    return new Response(null, { status: 502 });
  }
}
