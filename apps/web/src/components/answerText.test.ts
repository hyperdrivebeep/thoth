import { expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { parseAnswer } from "./answerText";

it("numbers real IRIS span citations by first appearance and reuses numbers", () => {
  const parsed = parseAnswer(excerpt.answer);
  const ids = parsed.citations.map(item => item.spanId);
  expect(new Set(ids).size).toBe(ids.length);
  expect(parsed.citations.map(item => item.n)).toEqual(ids.map((_, index) => index + 1));
  expect(ids[0]).toBe("span:1580e7b4-64bb-4eb0-99c2-5a8fc6113f70");
  // span:6cd5d0ab... is cited in two paragraphs and keeps one number
  const shared = parsed.citations.find(item => item.spanId === "span:6cd5d0ab-63e1-45ef-91dc-65e4071836e2")!;
  const uses = parsed.blocks.flatMap(block => block.kind === "p" ? block.inline : block.items.flat())
    .filter(item => item.kind === "cite" && item.spanId === shared.spanId);
  expect(uses.length).toBeGreaterThan(1);
  expect(uses.every(item => item.kind === "cite" && item.n === shared.n)).toBe(true);
});

it("never leaves an internal span id in the visible text", () => {
  const parsed = parseAnswer(excerpt.answer);
  const visible = parsed.blocks.flatMap(block => block.kind === "p" ? block.inline : block.items.flat())
    .filter(item => item.kind !== "cite").map(item => item.text).join("");
  expect(visible).not.toMatch(/span:/);
  expect(visible).toContain("RFP p.36");
});

it("splits paragraphs, reads bold and lists, and keeps HTML as plain text", () => {
  const parsed = parseAnswer("첫 **굵게** 문장\n\n- 하나 (span:aaaa1111)\n- 둘\n\n<script>alert(1)</script> **<b>x</b>**");
  expect(parsed.blocks.map(block => block.kind)).toEqual(["p", "ul", "p"]);
  const first = parsed.blocks[0];
  expect(first.kind === "p" && first.inline.some(item => item.kind === "text" && item.bold && item.text === "굵게")).toBe(true);
  const list = parsed.blocks[1];
  expect(list.kind === "ul" && list.items.length).toBe(2);
  const last = parsed.blocks[2];
  const text = last.kind === "p" ? last.inline.map(item => item.kind === "text" ? item.text : "").join("") : "";
  expect(text).toContain("<script>alert(1)</script>");
  expect(text).toContain("<b>x</b>");
});

it("handles empty and citation-free answers", () => {
  expect(parseAnswer("").blocks).toEqual([]);
  expect(parseAnswer("근거 없는 문장").citations).toEqual([]);
});
