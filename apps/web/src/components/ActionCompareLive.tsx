import { ActionCompare } from "./ActionCompare";
import { useHypothesisLinks } from "./useHypothesisLinks";
import { useDiscrimination } from "./useJudgmentRecords";

/** The next-actions comparison with a mark on each action that rests on a hypothesis whose trace verdict changed. */
export function ActionCompareLive({ result, projectId }: { result: Record<string, unknown>; projectId: string }) {
  const linked = useHypothesisLinks(projectId);
  const discrimination = useDiscrimination(projectId);
  return <ActionCompare result={result} projectId={projectId} links={linked.links} discrimination={discrimination.items}/>;
}
