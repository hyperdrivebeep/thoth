import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ResearchLiveProgress } from "./components/ResearchLiveProgress";
import {
  TIMELINE_EXAMPLE_ASKED_AT,
  TIMELINE_EXAMPLE_SOURCES,
  timelineExampleStatus,
} from "./api/timelineExample";
import "@blueprintjs/core/lib/css/blueprint.css";
import "@blueprintjs/icons/lib/css/blueprint-icons.css";
import "./styles.css";
import "./workstation.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <main className="bp6-dark" style={{ minHeight: "100vh", background: "var(--surface-0)" }}>
      <section className="conversation-timeline" aria-label="Psyche 예시 타임라인">
        <div className="conversation-turn">
          <article className="user-message">
            <header>
              질문
              <time dateTime={TIMELINE_EXAMPLE_ASKED_AT}>
                {new Date(TIMELINE_EXAMPLE_ASKED_AT).toLocaleString()}
              </time>
            </header>
            <p>{timelineExampleStatus.request?.authored_text}</p>
          </article>
        </div>
        <ResearchLiveProgress
          status={timelineExampleStatus}
          sourceUris={TIMELINE_EXAMPLE_SOURCES}
        />
      </section>
    </main>
  </StrictMode>,
);
