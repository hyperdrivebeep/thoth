import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it } from "vitest";
import { setLocale, t } from "../i18n/locale";
import { ExpectedResults } from "./ExpectedResults";
import { GroundingRowsNotice } from "./GroundingNotice";
import { ResearchResultCard } from "./ResearchResultCard";
import { groundingFixture } from "./groundingTestFixture";
import { hypothesisRows } from "./hypothesisView";

afterEach(() => setLocale("ko"));

it.each(["ko", "en"] as const)("shows approved %s labels without enum codes or changing source text", locale => {
  setLocale(locale);
  const before = structuredClone(groundingFixture);
  const html = renderToStaticMarkup(<ExpectedResults test={groundingFixture.portfolio.hypotheses[0].discriminating_tests[0]} rows={hypothesisRows(groundingFixture)} onOpenEvidence={() => {}}/>);
  for (const key of ["grounding.observation", "grounding.inference", "grounding.hypothesis", "grounding.unclassified", "grounding.unassessed"] as const) expect(html).toContain(t(key));
  for (const code of ["OBSERVATION_RESTATEMENT", "EVIDENCE_INFERENCE", "HYPOTHESIS_ONLY", "UNASSESSED", "UNAVAILABLE"]) expect(html).not.toContain(code);
  expect(html).toContain("Synthetic observation, not a new experiment.");
  expect(html.match(/class="expected-unassessed"/g)).toHaveLength(1);
  expect(html.match(/<button/g)).toHaveLength(2);
  expect(html).toContain(t("evidence.unknownBasis"));
  expect(groundingFixture).toEqual(before); // response language is omitted, not invented.
});

it.each(["ko", "en"] as const)("uses only the approved %s errors and omits saved-result claims when absent", locale => {
  setLocale(locale);
  const result = { terminal_reason: "GROUNDING_PUBLICATION_BINDING_MISMATCH", research_state: "PARTIAL" };
  const card = (saved: boolean) => renderToStaticMarkup(<ResearchResultCard result={result} state="SUCCEEDED" hasSavedResult={saved} onDetail={() => {}}/>);
  expect(card(false)).toContain(t("grounding.hold"));
  expect(card(false)).not.toContain(t("grounding.saved"));
  expect(card(true)).toContain(t("grounding.saved"));
  expect(card(true)).not.toContain("GROUNDING_PUBLICATION_BINDING_MISMATCH");
  expect(card(true)).not.toContain(t("result.remoteUnknown"));
  expect(renderToStaticMarkup(<GroundingRowsNotice result={groundingFixture}/>)).toContain(t("grounding.omitted"));
});

it("shows a legacy projected row only as unreviewed and offers no unavailable source action", () => {
  const legacy = groundingFixture.portfolio.hypotheses[0].discriminating_tests[0].expected_by_hypothesis[4];
  const html = renderToStaticMarkup(<ExpectedResults test={{expected_by_hypothesis: [legacy]}} rows={hypothesisRows(groundingFixture)} onOpenEvidence={() => {}}/>);
  expect(html).toContain(t("grounding.unassessed"));
  expect(html).not.toContain("expected-grounding-role");
  expect(html).not.toContain("<button");
});
