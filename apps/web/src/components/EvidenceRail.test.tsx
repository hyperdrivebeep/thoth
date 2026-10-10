import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EvidenceRail } from "./EvidenceRail";
import type { EvidenceSpan } from "../types";

const row = (locator: EvidenceSpan["locator"], text: string): EvidenceSpan => ({
  span_id: "span:1", artifact_id: "artifact:1", source_version_id: "version:1", exact_text: text,
  authority_state: "UNCLASSIFIED", cutoff_state: "ELIGIBLE", verification_state: "SCHEMA_VALID", support_state: "EXTRACTED", locator,
});

describe("EvidenceRail position of a table row", () => {
  it("shows the line and the row range of a CSV row, and keeps older single cells readable", () => {
    const html = renderToStaticMarkup(<EvidenceRail evidence={[
      row({ line: 4, cell_range: "R4C1:R4C6", json_pointer: "/rows/4" } as EvidenceSpan["locator"], "row_id=SYN-T-RAIN-03, detected=0"),
      row({ cell_range: "R6C2" }, "SYN-T-RAIN-05"),
    ]} />);
    expect(html).toContain("row_id=SYN-T-RAIN-03, detected=0");
    expect(html).toContain("line 4 · R4C1:R4C6");
    expect(html).toContain("R6C2");
    expect(html).not.toContain("구조 위치 없음");
  });
});

