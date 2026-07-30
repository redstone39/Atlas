import type { TFunction } from "i18next";

import catalogPayload from "../../../contracts/user-messages.json";

export type MessageParam = string | number | boolean | null;
export type MessageParams = Record<string, MessageParam>;

export interface MessageReference {
  message_code: string;
  message_params: MessageParams;
}

type ParamContract = {
  type: string | string[];
  required?: boolean;
};

type MessageContract = {
  params: Record<string, ParamContract>;
};

const catalog = catalogPayload.messages as Record<string, MessageContract>;

function matchesType(value: MessageParam, expected: string) {
  if (expected === "null") return value === null;
  if (expected === "string") return typeof value === "string";
  if (expected === "boolean") return typeof value === "boolean";
  if (expected === "integer") return typeof value === "number" && Number.isInteger(value);
  if (expected === "number") return typeof value === "number" && Number.isFinite(value);
  return false;
}

export function isValidMessageReference(value: unknown): value is MessageReference {
  if (!value || typeof value !== "object") return false;
  const reference = value as Partial<MessageReference>;
  if (typeof reference.message_code !== "string" || !reference.message_params || typeof reference.message_params !== "object" || Array.isArray(reference.message_params)) return false;
  const contract = catalog[reference.message_code];
  if (!contract) return false;
  const parameters = reference.message_params;
  const names = Object.keys(parameters);
  if (names.some((name) => !(name in contract.params))) return false;
  for (const [name, specification] of Object.entries(contract.params)) {
    if (specification.required && !(name in parameters)) return false;
    if (!(name in parameters)) continue;
    const expected = Array.isArray(specification.type) ? specification.type : [specification.type];
    if (!expected.some((item) => matchesType(parameters[name], item))) return false;
  }
  return true;
}

export function toMessageReference(value: unknown): MessageReference | null {
  if (isValidMessageReference(value)) return value;
  if (typeof value === "string" && catalog[value]) {
    const reference = { message_code: value, message_params: {} };
    return isValidMessageReference(reference) ? reference : null;
  }
  return null;
}

export function localizeMessage(
  value: unknown,
  t: TFunction,
  fallbackKey = "common.requestFailed",
) {
  const reference = toMessageReference(value);
  if (!reference) return t(fallbackKey);
  const translationKey = `messages.${reference.message_code}`;
  const translated = t(translationKey, reference.message_params);
  return translated === translationKey ? t(fallbackKey) : translated;
}

export class ApiError extends Error {
  readonly reference: MessageReference | null;
  readonly message_code: string | null;
  readonly message_params: MessageParams;
  readonly errorCode: string | null;
  readonly correlationId: string | null;
  readonly status: number | null;

  constructor(payload: unknown, status: number | null = null) {
    const body = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
    const reference = toMessageReference(body);
    super(reference?.message_code ?? "common.request_failed");
    this.name = "ApiError";
    this.reference = reference;
    this.message_code = reference?.message_code ?? null;
    this.message_params = reference?.message_params ?? {};
    this.errorCode = typeof body.error_code === "string" ? body.error_code : null;
    this.correlationId = typeof body.correlation_id === "string" ? body.correlation_id : null;
    this.status = status;
  }
}
