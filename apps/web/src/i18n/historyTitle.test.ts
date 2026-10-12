// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { setLocale } from "./locale";
import { historyTitle } from "./historyTitle";

afterEach(() => setLocale("ko"));
it("localizes generated history titles at render time but preserves authored questions", () => {
  const item = { kind: "REVISION", title: "가설 변경" };
  expect(historyTitle(item)).toBe("가설 변경");
  setLocale("en");
  expect(historyTitle(item)).toBe("Hypothesis updated");
  expect(historyTitle({ ...item, kind: "REQUEST" })).toBe("가설 변경");
  expect(historyTitle({ ...item, title: "Original custom title 12%" })).toBe("Original custom title 12%");
  expect(item.title).toBe("가설 변경");
  setLocale("ko");
  expect(historyTitle(item)).toBe("가설 변경");
});
