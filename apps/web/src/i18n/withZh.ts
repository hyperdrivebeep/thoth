/** A column of Chinese text for a catalog of { ko, en } messages: tsc rejects a missing or extra key. */
export type ZhOf<T> = { readonly [K in keyof T]: string };

type Pair = { readonly ko: string; readonly en: string };

/** The catalog with its Simplified Chinese column added; every language is then read as message[locale]. */
export function withZh<T extends Record<string, Pair>>(base: T, zh: ZhOf<T>): { readonly [K in keyof T]: T[K] & { readonly "zh-Hans": string } } {
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(base)) result[key] = { ...base[key], "zh-Hans": zh[key as keyof T] };
  return result as { readonly [K in keyof T]: T[K] & { readonly "zh-Hans": string } };
}
