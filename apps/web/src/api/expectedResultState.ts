/** Legacy consumers intentionally retain their different historical whitespace rules. */
export function expectedUnknown(label: string, state: unknown, consumer: "ranking" | "annotation"): boolean {
  if (state !== undefined) return state === "UNKNOWN";
  const normalized = consumer === "ranking" ? label.trim().replace(/\s+/g, " ").toLowerCase() : label.trim();
  return normalized === "모름";
}
