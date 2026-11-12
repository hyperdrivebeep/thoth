import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EvidenceRail } from "./EvidenceRail";
import { DetachedSourcesContext } from "./detachedSources";
import type { EvidenceSpan } from "../types";

const row = (locator: EvidenceSpan["locator"], text: string): EvidenceSpan => ({
  span_id: "span:1",
  artifact_id: "artifact:1",
  source_version_id: "version:1",
  exact_text: text,
  authority_state: "UNCLASSIFIED",
  cutoff_state: "ELIGIBLE",
  verification_state: "SCHEMA_VALID",
  support_state: "EXTRACTED",
  locator,
});

describe("EvidenceRail position of a table row", () => {
  it("shows the line and the row range of a CSV row, and keeps older single cells readable", () => {
    const html = renderToStaticMarkup(
      <EvidenceRail
        evidence={[
          row(
            {
              line: 4,
              cell_range: "R4C1:R4C6",
              json_pointer: "/rows/4",
            } as EvidenceSpan["locator"],
            "row_id=SYN-T-RAIN-03, detected=0",
          ),
          row({ cell_range: "R6C2" }, "SYN-T-RAIN-05"),
        ]}
      />,
    );
    expect(html).toContain("row_id=SYN-T-RAIN-03, detected=0");
    expect(html).toContain("line 4 · R4C1:R4C6");
    expect(html).toContain("R6C2");
    expect(html).not.toContain("구조 위치 없음");
  });
});

describe("EvidenceRail and a source that was disconnected later", () => {
  const evidence = [
    {
      ...row({ line: 1 }, "from the connected source"),
      span_id: "span:kept",
      artifact_id: "artifact:kept",
    },
    {
      ...row({ line: 2 }, "from the disconnected source"),
      span_id: "span:gone",
      artifact_id: "artifact:gone",
    },
  ];
  const marked = (html: string) =>
    html.split("<article").filter((part) => part.includes("지금은 연결 해제됨"));

  it("keeps the evidence and marks only what came from the disconnected source", () => {
    const html = renderToStaticMarkup(
      <DetachedSourcesContext.Provider value={new Set(["artifact:gone"])}>
        <EvidenceRail evidence={evidence} />
      </DetachedSourcesContext.Provider>,
    );
    expect(html).toContain("from the connected source");
    expect(html).toContain("from the disconnected source");
    expect(marked(html)).toHaveLength(1);
    expect(marked(html)[0]).toContain("from the disconnected source");
  });

  it("marks nothing where no source was disconnected", () => {
    expect(marked(renderToStaticMarkup(<EvidenceRail evidence={evidence} />))).toHaveLength(0);
  });
});
