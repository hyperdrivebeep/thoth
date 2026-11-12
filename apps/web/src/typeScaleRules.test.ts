import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect, it } from "vitest";

// No text is drawn under 12px (04-ideal I-23). The smallest size is one variable; a stylesheet rule
// that names a smaller size, or a size relative to its parent with no floor, would bring 8 to 11px back.

const SOURCE = resolve(import.meta.dirname);
function files(folder: string, extension: string): string[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap(entry => {
    const path = join(folder, entry.name);
    if (entry.isDirectory()) return files(path, extension);
    return entry.name.endsWith(extension) && !entry.name.includes(".test.") ? [path] : [];
  });
}
const sheets = files(SOURCE, ".css").map(path => ({ path, text: readFileSync(path, "utf8").replace(/\/\*[\s\S]*?\*\//g, "") }));
const declared = sheets.flatMap(sheet => [...sheet.text.matchAll(/font-size:\s*([^;}]+)/g)].map(found => found[1].trim()));

it("declares no font size under 12px", () => {
  expect(declared.length).toBeGreaterThan(300);
  const pixels = declared.filter(value => /^[\d.]+px$/.test(value)).map(value => parseFloat(value));
  expect(pixels.filter(size => size < 12)).toEqual([]);
  const smallest = sheets.map(sheet => /--font-small:\s*([\d.]+)px/.exec(sheet.text)?.[1]).filter(Boolean);
  expect(smallest).toEqual(["12"]);
});

it("gives every size that depends on its parent a floor", () => {
  const relative = declared.filter(value => /(em|rem|%)\b/.test(value) || /^(smaller|small|x-small|xx-small)$/.test(value));
  expect(relative.length).toBeGreaterThan(0);
  for (const value of relative) expect(value, value).toMatch(/^max\([\d.]+em, var\(--font-small\)\)$/);
});

it("sets no font size in a component's own style", () => {
  const found = files(SOURCE, ".tsx").filter(path => /fontSize\s*:/.test(readFileSync(path, "utf8")));
  expect(found).toEqual([]);
});

