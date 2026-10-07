import { useQuery } from "@tanstack/react-query";
import { closuresKey, discriminationKey, listClosures, listDiscrimination } from "../api/judgmentRecords";
import { lessonsKey, listLessons } from "../api/lessons";
import { listSame, sameKey } from "../api/hypothesisSame";

/** What people recorded about the tests of a project's hypotheses. A failed read shows nothing recorded; it never blocks the screen. */
export function useDiscrimination(projectId: string, enabled = true) {
  const query = useQuery({ queryKey: discriminationKey(projectId), enabled: enabled && Boolean(projectId), retry: false, queryFn: ({ signal }) => listDiscrimination(projectId, signal) });
  return { items: query.data?.items };
}

/** Earlier results recalled for the rows of a project, by exact condition. A failed read shows none. */
export function useSameHypotheses(projectId: string, enabled = true) {
  const query = useQuery({ queryKey: sameKey(projectId), enabled: enabled && Boolean(projectId), retry: false, queryFn: ({ signal }) => listSame(projectId, signal) });
  return { same: query.data };
}

/** Earlier results recalled for the rows of a project, by exact condition. A failed read shows none. */
export function useLessons(projectId: string, enabled = true) {
  const query = useQuery({ queryKey: lessonsKey(projectId), enabled: enabled && Boolean(projectId), retry: false, queryFn: ({ signal }) => listLessons(projectId, signal) });
  return { lessons: query.data?.lessons };
}

/** The closures people recorded on trace rows, with the effect the rules confirmed. A failed read shows none. */
export function useTraceClosures(projectId: string, enabled = true) {
  const query = useQuery({ queryKey: closuresKey(projectId), enabled: enabled && Boolean(projectId), retry: false, queryFn: ({ signal }) => listClosures(projectId, signal) });
  return { closures: query.data?.closures };
}
