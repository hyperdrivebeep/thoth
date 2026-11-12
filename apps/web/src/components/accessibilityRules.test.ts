import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect, it } from "vitest";

function screenSources(folder: string): string[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
    const path = join(folder, entry.name);
    if (entry.isDirectory()) return screenSources(path);
    return entry.name.endsWith(".tsx") && !entry.name.includes(".test.") ? [path] : [];
  });
}
const sources = screenSources(resolve(import.meta.dirname, "..")).map((path) => ({
  name: path.split(/[\\/]/).pop() ?? path,
  text: readFileSync(path, "utf8"),
}));
const lineOf = (text: string, index: number) => text.slice(0, index).split("\n").length;

// An opening tag with its attributes; a "{...}" value may hold ">" (arrow functions, comparisons).
const tag = (names: string) =>
  new RegExp("<(" + names + ")\\b((?:[^<>{}]|\\{(?:[^{}]|\\{[^{}]*\\})*\\})*)>", "g");

it("names only elements that have a role (a name on a plain div is not read out)", () => {
  // WCAG 4.1.2; axe rule aria-prohibited-attr. section, nav, ol and ul have a role of their own.
  const found: string[] = [];
  for (const { name, text } of sources) {
    for (const match of text.matchAll(tag("div|span|p|small|strong|em"))) {
      const attributes = match[2];
      if (attributes.includes("aria-label=") && !attributes.includes("role="))
        found.push(name + ":" + lineOf(text, match.index));
    }
  }
  expect(found).toEqual([]);
});

it("announces every failure and every success notice (role alert or status)", () => {
  // WCAG 4.1.3: a message that appears after an action is announced without moving the focus.
  const found: string[] = [];
  for (const { name, text } of sources) {
    for (const match of text.matchAll(tag("Callout"))) {
      const attributes = match[2];
      const outcome =
        attributes.includes('intent="danger"') || attributes.includes('intent="success"');
      if (outcome && !attributes.includes("role="))
        found.push(name + ":" + lineOf(text, match.index));
    }
  }
  expect(found).toEqual([]);
});

it("gives every progress bar a name", () => {
  // axe rule aria-progressbar-name: a bar without a name is read as "progress bar" and nothing else.
  const found: string[] = [];
  for (const { name, text } of sources) {
    for (const match of text.matchAll(tag("ProgressBar"))) {
      if (!match[2].includes("aria-label=")) found.push(name + ":" + lineOf(text, match.index));
    }
  }
  expect(found).toEqual([]);
});

it("puts only header or data cells directly in a table row", () => {
  // axe rule aria-required-children: a row whose children have no cell role is not read as a row.
  const found: string[] = [];
  for (const { name, text } of sources) {
    for (const match of text.matchAll(/role="row"[^>]*>\s*<(\w+)\b([^>]*)>/g)) {
      if (!/role="(cell|columnheader|rowheader|gridcell)"/.test(match[2]))
        found.push(name + ":" + lineOf(text, match.index));
    }
  }
  expect(found).toEqual([]);
});

/** WCAG 2.x relative luminance and contrast ratio of two "#rrggbb" colours. */
function luminance(hex: string): number {
  const channel = (index: number) => {
    const value = parseInt(hex.slice(1 + index * 2, 3 + index * 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}
function contrast(text: string, background: string): number {
  const [light, dark] = [luminance(text), luminance(background)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

it("keeps the two grey text colours readable on the surfaces they are written on", () => {
  // WCAG 1.4.3 (4.5:1). The surfaces: project list, top bars, question bubble and cards.
  const css = readFileSync(resolve(import.meta.dirname, "..", "workstation.css"), "utf8");
  const colour = (name: string) =>
    new RegExp("--" + name + ":\\s*(#[0-9a-fA-F]{6})").exec(css)?.[1] ?? "";
  const surfaces = ["#182029", "#202831", "#252d37"];
  for (const name of ["faint", "muted"]) {
    expect(colour(name), name).toMatch(/^#[0-9a-f]{6}$/i);
    for (const surface of surfaces)
      expect(contrast(colour(name), surface), name + " on " + surface).toBeGreaterThanOrEqual(4.5);
  }
  // The check can tell a failing pair: the colour used before this rule was 3.6:1 on the bubble.
  expect(contrast("#738497", "#252d37")).toBeLessThan(4.5);
});
