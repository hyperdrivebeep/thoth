import { useQuery } from "@tanstack/react-query";
import { hypothesisLinksKey, listHypothesisLinks } from "../api/hypothesisLink";

/** The hypotheses of a project that came from a trace row, read against today's verdicts. A failed read shows no marks. */
export function useHypothesisLinks(projectId: string, enabled = true) {
  const query = useQuery({ queryKey: hypothesisLinksKey(projectId), enabled: enabled && Boolean(projectId), retry: false,
    queryFn: ({ signal }) => listHypothesisLinks(projectId, signal) });
  return { links: query.data?.links, distribution: query.data?.reason_distribution };
}
