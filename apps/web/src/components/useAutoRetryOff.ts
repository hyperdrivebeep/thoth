import { useQuery } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";

/**
 * True only when the project's call settings were read and automatic retry of an interrupted model call is off.
 * It reads the same query as the Settings switch (same key and RPC); a value that cannot be read is not "off", and
 * no error is shown for it.
 */
export function useAutoRetryOff(projectId: string, enabled = true): boolean {
  const query = useQuery({
    queryKey: ["model-call-settings", projectId], enabled: enabled && projectId !== "",
    queryFn: ({ signal }) => rpc<{ auto_retry_interrupted_model_call: boolean; settings_digest: string | null }>(
      "model/callSettings/read", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  return query.data?.value.auto_retry_interrupted_model_call === false;
}

