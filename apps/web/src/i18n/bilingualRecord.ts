import { getLocale } from "./locale";

/** Code-keyed display resources read the current locale on access; keys never change. */
export function bilingualRecord<K extends string, V>(ko: Record<K, V>, en: Record<NoInfer<K>, V>): Record<K, V> {
  const result = Object.create(null) as Record<K, V>;
  for (const key of Object.keys(ko) as K[]) {
    Object.defineProperty(result, key, {enumerable: true, get: () => (getLocale() === "en" ? en : ko)[key]});
  }
  return result;
}
