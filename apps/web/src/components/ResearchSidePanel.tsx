import { t } from "../i18n/locale";
import { Button, Tab, Tabs } from "@blueprintjs/core";
import type { ConnectedArtifact, PackRunResult } from "../types";
import { isHttpUrl, sourceCardHost, sourceCardTitle } from "../sourceDisplay";
import { ResearchDetailPane } from "./ResearchDetailPane";
import type { ResearchDetail } from "./ResearchResultCard";

export type SidePanelTab = "files" | "evidence" | "hypotheses" | "actions" | "history";

export function ResearchSidePanel({
  tab,
  onTab,
  detail,
  onClose,
  sources,
  project,
  thread,
  onOpenRecords,
  onManageResources,
}: {
  tab: SidePanelTab;
  onTab: (tab: SidePanelTab) => void;
  detail: ResearchDetail | null;
  onClose: () => void;
  sources: ConnectedArtifact[];
  project: PackRunResult["project"];
  thread: PackRunResult["thread"];
  onOpenRecords: () => void;
  onManageResources: () => void;
}) {
  if (detail) {
    return (
      <ResearchDetailPane
        key={detail.kind}
        detail={detail}
        project={project}
        thread={thread}
        onClose={onClose}
      />
    );
  }
  return (
    <aside className="research-side-panel" aria-label={t("sidePanel.title")}>
      <header>
        <Tabs
          id="research-side-tabs"
          selectedTabId={tab}
          onChange={(id) => onTab(id as SidePanelTab)}
        >
          <Tab id="files" title={t("sidePanel.sources")} />
          <Tab id="history" title={t("delta.record")} />
        </Tabs>
        <Button minimal small icon="cross" onClick={onClose} aria-label={t("sidePanel.close")} />
      </header>
      <div className="detail-scroll">
        {tab === "files" && (
          <>
            {sources.length === 0 && (
              <p className="muted">{t("sidePanel.noSources")}</p>
            )}
            {sources.map((artifact) => (
              <article className="detail-card" key={artifact.artifact_id}>
                <h3>
                  {isHttpUrl(artifact.source_uri) ? (
                    <a href={artifact.source_uri} target="_blank" rel="noreferrer">
                      {sourceCardTitle(artifact.source_uri)}
                    </a>
                  ) : (
                    sourceCardTitle(artifact.source_uri)
                  )}
                </h3>
                {sourceCardHost(artifact.source_uri) ? <p>{sourceCardHost(artifact.source_uri)}</p> : null}
                <p>{artifact.media_type}</p>
              </article>
            ))}
            <Button minimal icon="folder-open" text={t("sidePanel.manage")} onClick={onManageResources}/>
          </>
        )}
        {tab === "history" && (
          <>
            <p className="muted">{t("sidePanel.historyNotice")}</p>
            <Button minimal icon="history" text={t("sidePanel.openHistory")} onClick={onOpenRecords} />
          </>
        )}
      </div>
    </aside>
  );
}
