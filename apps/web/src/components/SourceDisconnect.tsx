import { Button } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { RpcError, rpc } from "../api/rpcClient";
import { activeBinding, detachedArtifacts } from "../api/sourceBindings";
import { message } from "../i18n/formatMessages";
import { sourceCardTitle } from "../sourceDisplay";
import { t, useTranslation } from "../i18n/locale";
import type { ConnectedArtifact } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
import { Disclosure } from "./Disclosure";
import type { ProjectSources } from "./useProjectSources";

const REVISION_MISMATCH = -32030;

function disconnectFailure(error: unknown): string {
  if (error instanceof RpcError && error.code === REVISION_MISMATCH) return t("archive.revisionChanged");
  return t("sources.disconnectFailed");
}

/**
 * "Disconnect" on one connected source. The source stays on disk and in the answers already made; it
 * is only left out of new questions. Nothing is sent before the person confirms.
 */
export function SourceDisconnectButton({ projectId, artifact, sources }: {
  projectId: string;
  artifact: ConnectedArtifact;
  sources: ProjectSources | undefined;
}) {
  useTranslation();
  const client = useQueryClient();
  const [asking, setAsking] = useState(false);
  const binding = activeBinding(sources, artifact.artifact_id);
  const disconnect = useMutation({
    mutationFn: () =>
      rpc(
        "project/source/disconnect",
        {
          project_id: projectId,
          binding_id: binding?.binding_id,
          mode: "DETACH",
          expected_project_revision: sources?.cutoff_basis?.project_revision,
        },
        crypto.randomUUID(),
      ),
    onSuccess: () => setAsking(false),
    // Success or refusal, what is connected may have changed: read the sources, the project and its answers again.
    onSettled: async () => {
      await client.invalidateQueries({ queryKey: ["sources", projectId] });
      await client.invalidateQueries({ queryKey: ["projects"] });
      await client.invalidateQueries({ queryKey: ["research", projectId] });
    },
  });
  if (!binding) return null;
  const name = sourceCardTitle(artifact.source_uri);
  return (
    <>
      <Button
        small
        minimal
        icon="unlink"
        onClick={() => {
          disconnect.reset();
          setAsking(true);
        }}
      >
        {t("sources.disconnect")}
      </Button>
      {asking && (
        <ConfirmDialog
          title={message("disconnectSourceTitle", name)}
          confirmLabel={t("sources.disconnect")}
          intent="warning"
          icon="unlink"
          pending={disconnect.isPending}
          error={disconnect.error ? disconnectFailure(disconnect.error) : null}
          onConfirm={() => disconnect.mutate()}
          onClose={() => setAsking(false)}
        >
          <p>{t("sources.disconnectKeeps")}</p>
          <p>{t("sources.disconnectReview")}</p>
        </ConfirmDialog>
      )}
    </>
  );
}

/** The sources that were disconnected, folded away under the connected ones. */
export function DetachedSources({ sources }: { sources: ProjectSources | undefined }) {
  useTranslation();
  const detached = detachedArtifacts(sources);
  if (detached.length === 0) return null;
  return (
    <Disclosure label={message("detachedSources", detached.length)}>
      <p className="muted">{t("sources.detachedNotice")}</p>
      <ul className="detached-sources">
        {detached.map(artifact => (
          <li key={artifact.artifact_id}>{sourceCardTitle(artifact.source_uri)}</li>
        ))}
      </ul>
    </Disclosure>
  );
}
