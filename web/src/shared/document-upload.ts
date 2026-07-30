export function titleFromFilename(filename: string) {
  const name = filename.replace(/\\/g, "/").split("/").pop()?.trim() ?? "";
  if (!name) return "";
  const dotIndex = name.lastIndexOf(".");
  const stem = dotIndex > 0 ? name.slice(0, dotIndex).trim() : name;
  return (stem || name).replace(/\s+/g, " ").trim();
}
