export const objectValue = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
export const objectList = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.map(objectValue) : [];
export const textValue = (value: unknown): string => typeof value === "string" ? value : "";
export const stringValues = (value: unknown): string[] => Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];

export function hasResearchContent(result: Record<string,unknown> | null | undefined): boolean {
  return Boolean(result && (textValue(result.answer).trim() || objectList(objectValue(result.portfolio).hypotheses).length || objectList(objectValue(result.action_plan).alternatives).length || stringValues(objectValue(result.assessment).missing_items).length));
}
