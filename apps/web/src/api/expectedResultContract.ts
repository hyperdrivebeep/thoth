/** Read-view negotiation never changes a submitted request or its generation budget. */
export function expectedResultContract(ready: unknown): "v4" | "v5" | undefined {
  if (!ready || typeof ready !== "object") return undefined;
  if ("expected_result_contracts" in ready && Array.isArray(ready.expected_result_contracts)) {
    if (ready.expected_result_contracts.includes("v5")) return "v5";
    return ready.expected_result_contracts.includes("v4") ? "v4" : undefined;
  }
  return "response_language_scope" in ready && ready.response_language_scope === "REQUEST" ? "v4" : undefined;
}
