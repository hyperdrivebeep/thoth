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
    <aside className="research-side-panel" aria-label="연구 작업 패널">
      <header>
        <Tabs
          id="research-side-tabs"
          selectedTabId={tab}
          onChange={(id) => onTab(id as SidePanelTab)}
        >
          <Tab id="files" title="자료" />
          <Tab id="history" title="기록" />
        </Tabs>
        <Button minimal small icon="cross" onClick={onClose} aria-label="패널 닫기" />
      </header>
      <div className="detail-scroll">
        {tab === "files" && (
          <>
            {sources.length === 0 && (
              <p className="muted">연결된 자료가 없습니다. 입력창의 자료 연결로 원문을 붙입니다.</p>
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
            <Button minimal icon="folder-open" text="파일·웹 출처 관리" onClick={onManageResources}/>
          </>
        )}
        {tab === "history" && (
          <>
            <p className="muted">당시의 답변과 바뀐 판단을 확인하고, 지원되는 항목의 복원 내용을 미리 살펴볼 수 있습니다.</p>
            <Button minimal icon="history" text="연구 이력 열기" onClick={onOpenRecords} />
          </>
        )}
      </div>
    </aside>
  );
}
