import { renderToStaticMarkup } from "react-dom/server";
import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect, it } from "vitest";
import { Select } from "./Select";

it("draws a select whose arrow has no English tooltip and is hidden from a screen reader", () => {
  const html = renderToStaticMarkup(<Select aria-label="자료 성격" value="a" onChange={() => {}} options={[{ value: "a", label: "공식" }]}/>);
  expect(html).toContain('aria-label="자료 성격"');
  expect(html).toContain("공식");
  expect(html).not.toContain("<title");
  expect(html).not.toContain("Open dropdown");
  expect(html).toMatch(/<span aria-hidden="true" class="bp6-icon[^"]*">/);
});

function screenSources(folder: string): string[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap(entry => {
    const path = join(folder, entry.name);
    if (entry.isDirectory()) return screenSources(path);
    return /\.tsx?$/.test(entry.name) && !entry.name.includes(".test.") ? [path] : [];
  });
}

it("is the only place that uses the library's select", () => {
  const found = screenSources(resolve(import.meta.dirname, "..")).filter(path => !path.endsWith("Select.tsx") && /\bHTMLSelect\b/.test(readFileSync(path, "utf8")));
  expect(found.map(path => path.split(/[\\/]/).pop())).toEqual([]);
});
