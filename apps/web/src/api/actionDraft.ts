import { rpc } from "./rpcClient";

/** A draft action request made from one discriminating test of a hypothesis. What the person declares is sent as written; the server decides the risk from it and nothing about risk is sent. */
export async function draftActionFromTest(projectId: string, hypothesisId: string, testId: string, declaration: Record<string, boolean>) {
  return (await rpc<{ action: Record<string, unknown> }>("action/draft/fromTest", { project_id: projectId, hypothesis_id: hypothesisId, test_id: testId, effect_declaration: declaration }, crypto.randomUUID())).value;
}
