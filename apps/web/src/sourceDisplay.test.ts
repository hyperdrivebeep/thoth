import { describe, expect, it } from "vitest";

import { isHttpUrl, sourceCardHost, sourceCardTitle } from "./sourceDisplay";

describe("sourceDisplay", () => {
  it("keeps local file names", () => {
    expect(sourceCardTitle("inbox/mobilenets.pdf")).toBe("mobilenets.pdf");
    expect(isHttpUrl("inbox/mobilenets.pdf")).toBe(false);
  });

  it("drops the upload hash prefix from project-uploaded files only", () => {
    expect(sourceCardTitle("file://C:/ws/inbox/web/projects/c55d00cc9f1d0b905825a326/e9ac587f7cd94944-01_IRIS_2026_smart_port_RFP.pdf")).toBe("01_IRIS_2026_smart_port_RFP.pdf");
    expect(sourceCardTitle("inbox/e9ac587f7cd94944-notes.pdf")).toBe("e9ac587f7cd94944-notes.pdf");
    expect(sourceCardTitle("inbox/web/projects/c55d00cc9f1d0b905825a326/report.pdf")).toBe("report.pdf");
  });

  it("shows absolute http URLs and host", () => {
    const uri = "https://arxiv.org/pdf/1704.04861";
    expect(isHttpUrl(uri)).toBe(true);
    expect(sourceCardTitle(uri)).toBe(uri);
    expect(sourceCardHost(uri)).toBe("arxiv.org");
  });
});
