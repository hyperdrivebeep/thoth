// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, afterEach } from "vitest";
import { setLocale } from "../i18n/locale";
import { CheckedObservations } from "./CheckedObservations";
import { ResearchResultCard, type ResearchDetail } from "./ResearchResultCard";

const payload = { schema_version: "1.0.0", kind: "SOURCE_ARITHMETIC", display_rows: [
  { calculation_ref: "calc:1", label: "measurement", context_label: "Release B", sizes: ["1000", "10000"], size_label: "columns", operation: "RATIO", status: "CALCULATED", display_value: "62.280000", operand_refs: ["span:a", "span:b"] },
  { calculation_ref: "calc:2", label: "measurement", context_label: "Release B", sizes: ["1000", "10000"], operation: "LOG_LOG_SLOPE", status: "NOT_CALCULATED", operand_refs: ["span:a", "span:b"] },
], diagnostic_count: 1, not_delivered: 1 };
const render = (value: unknown) => renderToStaticMarkup(<CheckedObservations result={{ checked_observations: value }} onCite={() => {}}/>);
afterEach(() => setLocale("ko"));
describe("source arithmetic display", () => {
  it("does not invent calculations for old or unsupported payloads", () => {
    expect(render(undefined)).toBe("");
    expect(render({ ...payload, schema_version: "99.0.0" })).toBe("");
  });
  it("labels arithmetic and limitations without a cause or criterion claim", () => {
    const html = render(payload);
    expect(html).toContain("원문 숫자로 계산한 값");
    expect(html).toContain("인과·복잡도 판정 아님");
    expect(html).toContain("62.280000");
    expect(html).toContain("계산하지 않음");
    expect(html).toContain("모델 입력에 포함되지 않았습니다");
    expect(html).toContain('class="answer-citation">원문 1</button>');
  });
  it("renders English labels without translating source labels", () => {
    setLocale("en");
    const html = render(payload);
    expect(html).toContain("Values calculated from source numbers");
    expect(html).toContain("Not a causal or computational-complexity determination");
    expect(html).toContain("Release B");
    expect(html).toContain("Not calculated");
  });
  it("is consumed by the result card alongside the unmodified answer", () => {
    const html = renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={{ answer: "Original answer", checked_observations: payload }} onDetail={() => {}}/>);
    expect(html).toContain("Original answer");
    expect(html).toContain("62.280000");
  });
  it("opens the exact operand in the existing evidence detail flow", async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    const node = document.createElement("div");
    const root = createRoot(node);
    const opened: ResearchDetail[] = [];
    try {
      await act(async () => root.render(<ResearchResultCard state="SUCCEEDED" result={{ checked_observations: payload }} onDetail={detail => opened.push(detail)}/>));
      const section = node.querySelector("details.checked-observations");
      expect(section?.hasAttribute("open")).toBe(false);
      const buttons = section!.querySelectorAll<HTMLButtonElement>("button.answer-citation");
      await act(async () => buttons[1].click());
      expect(opened).toHaveLength(1);
      expect(opened[0].kind).toBe("evidence");
      expect(opened[0].focusSpanId).toBe("span:b");
    } finally {
      await act(async () => root.unmount());
    }
  });
});
