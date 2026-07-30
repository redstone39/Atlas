export function generatedId(prefix: string, label: string) {
  const slug = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  const entropy =
    globalThis.crypto?.randomUUID?.().replace(/-/g, "").slice(0, 8) ??
    Date.now().toString(36);
  return [prefix, slug || "item", entropy].join("-");
}
