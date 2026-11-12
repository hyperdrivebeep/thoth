import type { ConnectedArtifact } from "../types";

/** One row of project/source/list "bindings": whether a source is connected to the project now. */
export type SourceBinding = { binding_id: string; artifact_id: string; state: string };

type SourcesRead = { artifacts: ConnectedArtifact[]; bindings?: SourceBinding[] } | undefined;

function bindingOf(sources: SourcesRead, artifactId: string): SourceBinding | undefined {
  return sources?.bindings?.find(binding => binding.artifact_id === artifactId);
}

/**
 * The server lists every source the project has ever had; the binding says whether it is connected.
 * A source with no binding row (a workspace from before bindings were listed) counts as connected.
 */
function isConnected(sources: SourcesRead, artifactId: string): boolean {
  const binding = bindingOf(sources, artifactId);
  return !binding || binding.state === "ACTIVE";
}

/** The sources a new question can use. */
export function connectedArtifacts(sources: SourcesRead): ConnectedArtifact[] {
  return (sources?.artifacts ?? []).filter(artifact => isConnected(sources, artifact.artifact_id));
}

/** The same file under another id: connecting a file again makes a new source row with the same bytes. */
function connectedAgain(sources: SourcesRead, artifact: ConnectedArtifact): boolean {
  return connectedArtifacts(sources).some(other => other.byte_sha256 === artifact.byte_sha256 && other.source_uri === artifact.source_uri);
}

/**
 * The sources that were disconnected: kept on disk and in old answers, not used for new questions.
 * A file that was connected again afterwards is not listed: it is in use.
 */
export function detachedArtifacts(sources: SourcesRead): ConnectedArtifact[] {
  return (sources?.artifacts ?? []).filter(artifact => !isConnected(sources, artifact.artifact_id) && !connectedAgain(sources, artifact));
}

export function detachedArtifactIds(sources: SourcesRead): ReadonlySet<string> {
  return new Set(detachedArtifacts(sources).map(artifact => artifact.artifact_id));
}

/** The binding to name when disconnecting a source; undefined when the source has none or is already off. */
export function activeBinding(sources: SourcesRead, artifactId: string): SourceBinding | undefined {
  const binding = bindingOf(sources, artifactId);
  return binding?.state === "ACTIVE" ? binding : undefined;
}
