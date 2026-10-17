import { useQuery } from "@tanstack/react-query";
import { closuresKey, discriminationKey, listClosures, listDiscrimination } from "../api/judgmentRecords";
import { lessonsKey, listLessons } from "../api/lessons";
import { listSame, sameKey } from "../api/hypothesisSame";
import { useReadAccessProtection } from "./useReadAccessProtection";

/** What people recorded about the tests of a project's hypotheses. A failed read shows nothing recorded; it never blocks the screen. */
export function useDiscrimination(projectId: string, enabled = true) {
  const query = useQuery({ queryKey: discriminationKey(projectId), enabled: enabled && Boolean(projectId), retry: false, queryFn: ({ signal }) => listDiscrimination(projectId, signal) });
  const blocked = useReadAccessProtection(discriminationKey(projectId), query.error);
  return { items: blocked ? undefined : query.data?.items };
}

/** Earlier results recalled for the rows of a project, by exact condition. A failed read shows none. */
export function useSameHypotheses(projectId: string, enabled = true) {
  const query = useQuery({ queryKey: sameKey(projectId), enabled: enabled && Boolean(projectId), retry: false, queryFn: ({ signal }) => listSame(projectId, signal) });
  const blocked = useReadAccessProtection(sameKey(projectId), query.error);
  return { same: blocked ? undefined : query.data };
}

/** Earlier results recalled for the rows of a project, by exact condition. A failed read shows none. */
export function useLessons(projectId: string, enabled = true) {
  const query = useQuery({ queryKey: lessonsKey(projectId), enabled: enabled && Boolean(projectId), retry: false, queryFn: ({ signal }) => listLessons(projectId, signal) });
  const blocked = useReadAccessProtection(lessonsKey(projectId), query.error);
  return { lessons: blocked ? undefined : query.data?.lessons };
}

/** The closures people recorded on trace rows, with the effect the rules confirmed. A failed read shows none. */
export function useTraceClosures(projectId: string, enabled = true) {
  const query = useQuery({ queryKey: closuresKey(projectId), enabled: enabled && Boolean(projectId), retry: false, queryFn: ({ signal }) => listClosures(projectId, signal) });
  const blocked = useReadAccessProtection(closuresKey(projectId), query.error);
  return { closures: blocked ? undefined : query.data?.closures };
}
