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
  const name = uri.split(/[\\/]/).pop() || uri;
  // Project uploads are stored as "<16 hex of the file hash>-<original name>" under web/projects/.
  return /[\\/]web[\\/]projects[\\/]/.test(uri) ? name.replace(/^[0-9a-f]{16}-(?=.)/, "") : name;
}

export function sourceCardHost(uri: string): string | null {
  if (!isHttpUrl(uri)) {
    return null;
  }
  return new URL(uri).host;
}
