export type MockApiRequest = {
  url: URL;
  method: string;
  init?: RequestInit;
};

export type MockApiHandler = (
  request: MockApiRequest,
) => Promise<Response> | undefined;

export function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

export async function dispatchMockApi(
  request: MockApiRequest,
  handlers: MockApiHandler[],
): Promise<Response> {
  for (const handler of handlers) {
    const response = handler(request);
    if (response !== undefined) {
      return response;
    }
  }
  return jsonResponse(
    { message_code: "common.rejected", message_params: {} },
    404,
  );
}
