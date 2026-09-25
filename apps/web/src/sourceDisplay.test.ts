import { describe, expect, it } from "vitest";

import { isHttpUrl, sourceCardHost, sourceCardTitle } from "./sourceDisplay";

describe("sourceDisplay", () => {
  it("keeps local file names", () => {
    expect(sourceCardTitle("inbox/mobilenets.pdf")).toBe("mobilenets.pdf");
    expect(isHttpUrl("inbox/mobilenets.pdf")).toBe(false);
  });

  it("shows absolute http URLs and host", () => {
    const uri = "https://arxiv.org/pdf/1704.04861";
    expect(isHttpUrl(uri)).toBe(true);
    expect(sourceCardTitle(uri)).toBe(uri);
    expect(sourceCardHost(uri)).toBe("arxiv.org");
  });
});
