import { Button, Collapse } from "@blueprintjs/core";
import { useState } from "react";
import { cutoffLabel } from "../api/sourceTime";
import { t } from "../i18n/locale";
import { isHttpUrl, sourceCardHost, sourceCardTitle } from "../sourceDisplay";
import { RecordInspector } from "./RecordInspector";
import { SourceDisconnectButton } from "./SourceDisconnect";
import { SourceTimeAdvanced } from "./SourceTimeAdvanced";
import type { ProjectSources } from "./useProjectSources";

/** One connected source: what it is, whether it may be cited, and the actions on it. */
export function ConnectedSourceCard({ projectId, artifact, sources, hosted, onCite }: {
  projectId: string;
  artifact: ProjectSources["artifacts"][number];
  sources: ProjectSources | undefined;
  hosted: boolean;
  onCite: () => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const assessment = (sources?.source_times ?? []).find(item => item.artifact_id === artifact.artifact_id);
  const title = sourceCardTitle(artifact.source_uri);
  const host = sourceCardHost(artifact.source_uri);
  return (
    <article className="detail-card">
      <h3>{isHttpUrl(artifact.source_uri) ? <a href={artifact.source_uri} target="_blank" rel="noreferrer">{title}</a> : title}</h3>
      {host ? <p>{host}</p> : null}
      <p>{artifact.media_type}</p>
      <p>{cutoffLabel(assessment?.cutoff_state ?? artifact.cutoff_state ?? "UNKNOWN_TIME")}</p>
      <Button small onClick={onCite}>{t("workspace.cite")}</Button>
      <SourceDisconnectButton projectId={projectId} artifact={artifact} sources={sources} />
      {!hosted && <SourceTimeAdvanced projectId={projectId} artifactId={artifact.artifact_id} assessment={assessment} cutoff={sources?.cutoff_basis} />}
      {" "}
      {!hosted && (
        <>
          <Button small minimal icon={detailsOpen ? "chevron-up" : "info-sign"} aria-expanded={detailsOpen} onClick={() => setDetailsOpen(value => !value)}>
            {t("workspace.sourceDetails")}
          </Button>
          <Collapse isOpen={detailsOpen}><RecordInspector value={artifact} /></Collapse>
        </>
      )}
    </article>
  );
}

