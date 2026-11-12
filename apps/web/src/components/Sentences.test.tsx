import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { Sentences } from "./Sentences";

const lines = (text: string) =>
  renderToStaticMarkup(<Sentences text={text} />)
    .split("</span>")
    .filter(Boolean).length;

it("leaves a short explanation as one piece of text", () => {
  expect(renderToStaticMarkup(<Sentences text="짧은 설명입니다. 두 문장입니다." />)).toBe(
    "짧은 설명입니다. 두 문장입니다.",
  );
});

it("shows a long explanation one sentence per line", () => {
  const long =
    "순서는 점수가 아니라 규칙입니다. " +
    "남는 원인 후보가 적은 시험부터 놓습니다. ".repeat(5) +
    "한 시험을 하고 나면 그 뒤 순서는 바뀝니다(근거 상태는 따로 봅니다).";
  expect(long.length).toBeGreaterThan(120);
  const html = renderToStaticMarkup(<Sentences text={long} />);
  expect(lines(long)).toBe(7);
  expect(html).toContain('<span class="sentence-line">순서는 점수가 아니라 규칙입니다.</span>');
  expect(html.replace(/<[^>]+>/g, "")).toBe(long.replace(/([.]) /g, "$1"));
});

it("splits Japanese and Chinese sentences at the full-width full stop", () => {
  const long =
    "順序は点数ではなく規則です。" + "残る原因の候補が少ない試験から並べます。".repeat(7);
  expect(long.length).toBeGreaterThan(120);
  expect(lines(long)).toBe(8);
});

it("does not split inside a number, a file name or a parenthesis that follows a full stop", () => {
  const long =
    "모델 파일(약 1.2GB)과 logs/thoth.log 를 확인합니다. " +
    "색인이 준비되기 전에는 기존 검색 방식 그대로입니다. ".repeat(4);
  expect(lines(long)).toBe(5);
});
