import { useQuery } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";
import type { ConnectedArtifact } from "../types";
import type { SourceBinding } from "../api/sourceBindings";

export type SourceTimeAssessment = {
  artifact_id: string;
  source_version_id: string;
  byte_sha256: string;
  cutoff_state: string;
  revision: number;
  assessment_digest: string;
  metadata_digest?: string;
  reason_code: string;
  cutoff_at: string;
  mode: string;
};
export type ProjectSources = {
  artifacts: ConnectedArtifact[];
  bindings?: SourceBinding[];
  source_times?: SourceTimeAssessment[];
  cutoff_basis?: { cutoff_at: string; project_revision: number };
};

/**
 * The project's connected sources. The conversation page and the shell's tab counts share this one query (same key),
 * so showing the count adds no server call beyond the read the page already makes.
 */
export function useProjectSources(projectId: string, enabled = true) {
  return useQuery({
    queryKey: ["sources", projectId],
    enabled: enabled && Boolean(projectId),
    queryFn: ({ signal }) =>
      rpc<ProjectSources>(
        "project/source/list",
        { project_id: projectId },
        crypto.randomUUID(),
        signal,
      ),
  });
}
