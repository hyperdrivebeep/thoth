/** The name a conversation is listed under: the one a person gave it, else its first question. */
export function threadName(thread: { display_name?: string | null; problem?: string | null } | undefined): string {
  return thread?.display_name?.trim() || thread?.problem || "";
}
