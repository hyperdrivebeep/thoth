import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { EvidenceRail } from "./components/EvidenceRail";
import { StatusBadge } from "./components/StatusBadge";

describe("THOTH UI contracts", () => {
  it("renders machine states as readable badges", () => {
    const html = renderToStaticMarkup(<StatusBadge value="EXPERT_INPUT_REQUIRED" />);

    expect(html).toContain("EXPERT INPUT REQUIRED");
    expect(html).toContain("badge-expert-input-required");
  });

  it("labels bounded evidence separately from total evidence", () => {
    const html = renderToStaticMarkup(<EvidenceRail evidence={[]} totalCount={42} />);

    expect(html).toContain("활성 근거 0개");
    expect(html).toContain("전체 42개 중 선택");
  });

  it("renders a null scientific classification as unrecorded, never a fabricated status", () => {
    const html = renderToStaticMarkup(<StatusBadge value={null} />);
    expect(html).toContain("미기록");
    expect(html).not.toContain("PASS");
    expect(html).not.toContain("UNKNOWN");
  });
});
