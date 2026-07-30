import { ApiError } from "./user-messages";

export const API_BASE = import.meta.env.VITE_ATLAS_PRODUCTION_API_BASE ?? "";

export async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const isFormData = options.body instanceof FormData;
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: isFormData
      ? options.headers
      : {
          "Content-Type": "application/json",
          ...(options.headers ?? {}),
        },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(data, response.status);
  }
  return data as T;
}
