import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { ResearchStatus } from "../api/research";
import { UsageSummary } from "./ResearchProgress";

it("keeps unavailable usage and pricing unknown without replacing them with zero", () => {
  const html = renderToStaticMarkup(<UsageSummary/>);
  expect(html).toContain("사용 토큰 미확인 · 추정 비용 미확인");
  expect(html).not.toContain("$0");
});

it("shows observed zero and partial totals separately from unknown cost", () => {
  const status: Pick<ResearchStatus,"usage"> = {usage:{input_tokens:0,output_tokens:0,total_tokens:0,state:"OBSERVED",unreported_calls:0,cumulative_token_limit_enforced:false}};
  expect(renderToStaticMarkup(<UsageSummary status={status}/>)).toContain("사용 토큰 0 · 추정 비용 미확인");
  status.usage = {...status.usage!,input_tokens:12,output_tokens:null,total_tokens:12,state:"PARTIAL",unreported_calls:1};
  expect(renderToStaticMarkup(<UsageSummary status={status}/>)).toContain("사용 토큰 12 (부분 관측) · 추정 비용 미확인");
});
