export function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

export function sourceCardTitle(uri: string): string {
  if (isHttpUrl(uri)) {
    return uri;
  }
  return uri.split(/[\\/]/).pop() || uri;
}

export function sourceCardHost(uri: string): string | null {
  if (!isHttpUrl(uri)) {
    return null;
  }
  return new URL(uri).host;
}
