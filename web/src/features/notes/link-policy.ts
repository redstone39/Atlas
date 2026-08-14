export function isAllowedNoteLink(value: unknown) {
  return typeof value === "string" && /^(https?:\/\/|mailto:)/i.test(value.trim());
}
