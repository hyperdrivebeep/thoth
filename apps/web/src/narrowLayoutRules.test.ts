import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

// Rules of the narrow layout that have no behaviour jsdom could run. The arrangement itself is measured
// in a browser (outputs: qa_b3.py); these lines keep the stylesheet from sliding back into the old one.

const css = readFileSync(new URL("./workstation.css", import.meta.url), "utf8");

type Rule = { media: string | null; selector: string; body: string };

/** Every rule of the stylesheet with the media query it sits in (one level of nesting is all the file uses). */
function rules(source: string): Rule[] {
  const text = source.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Rule[] = [];
  let media: string | null = null;
  let at = 0;
  while (at < text.length) {
    const open = text.indexOf("{", at);
    const close = text.indexOf("}", at);
    if (close !== -1 && (open === -1 || close < open)) {
      media = null;
      at = close + 1;
      continue;
    }
    if (open === -1) break;
    const head = text.slice(at, open).trim();
    if (head.startsWith("@media")) {
      media = head.replace(/\s+/g, "");
      at = open + 1;
      continue;
    }
    const end = text.indexOf("}", open);
    out.push({
      media,
      selector: head.replace(/\s+/g, " "),
      body: text
        .slice(open + 1, end)
        .replace(/\s+/g, " ")
        .trim(),
    });
    at = end + 1;
  }
  return out;
}

const all = rules(css);
const maxWidth = (rule: Rule) => {
  const found = rule.media?.match(/max-width:(\d+)px/);
  return found ? Number(found[1]) : null;
};
const narrow = all.filter((rule) => (maxWidth(rule) ?? Infinity) <= 760);
const has = (rule: Rule, selector: string) =>
  rule.selector.split(",").some((part) => part.trim() === selector);
const declares = (rule: Rule, property: string) =>
  new RegExp("(^|;|\\s)" + property + "\\s*:").test(rule.body);

it("takes the project list out of the page at 760px and below, in one place", () => {
  const list = narrow.filter(
    (rule) =>
      has(rule, ".workstation.conversation-workstation > .navigation-rail") &&
      declares(rule, "position"),
  );
  expect(list).toHaveLength(1);
  expect(list[0].body).toContain("position: fixed");
  // Closed, it is not drawn: it cannot be focused or read out while it is off screen.
  expect(list[0].body).toContain("visibility: hidden");
  const open = narrow.filter(
    (rule) =>
      has(rule, '.workstation.conversation-workstation > .navigation-rail[data-open="true"]') &&
      declares(rule, "visibility"),
  );
  expect(open).toHaveLength(1);
  expect(open[0].body).toContain("visibility: visible");
  // No narrow rule gives the list a column of its own again.
  const columns = narrow.filter(
    (rule) =>
      rule.selector.includes(".conversation-workstation") &&
      declares(rule, "grid-template-columns"),
  );
  expect(columns.map((rule) => rule.body)).toEqual(["grid-template-columns: minmax(0, 1fr);"]);
});

it("shows the list button only where the list is a drawer", () => {
  const button = all.filter(
    (rule) => has(rule, ".workstation-topbar .nav-drawer-button") && declares(rule, "display"),
  );
  expect(
    button.map((rule) => [maxWidth(rule), /display:\s*([a-z-]+)/.exec(rule.body)![1]]),
  ).toEqual([
    [null, "none"],
    [760, "inline-flex"],
  ]);
});

it("does not cut project and conversation names to a fixed width in a narrow window", () => {
  const names = all.filter(
    (rule) =>
      maxWidth(rule) !== null &&
      /(project|thread)-button strong/.test(rule.selector) &&
      declares(rule, "max-width"),
  );
  expect(names.map((rule) => /max-width:\s*([^;]+)/.exec(rule.body)![1].trim())).toEqual(["none"]);
});

it("folds top bar tags to their icons without removing their words", () => {
  const folded = all.filter(
    (rule) =>
      maxWidth(rule) !== null &&
      (has(rule, ".workstation-topbar .header-cutoff > span:not(.bp6-icon)") ||
        has(rule, ".workstation-topbar .setup-needed-button > .bp6-button-text")),
  );
  expect(folded).toHaveLength(2);
  for (const rule of folded) {
    expect(rule.body).toContain("clip-path: inset(50%)");
    expect(declares(rule, "display")).toBe(false);
  }
  // A failed connection keeps its tag in the narrow bar.
  const health = narrow.filter(
    (rule) => rule.selector.includes(".header-health") && declares(rule, "display"),
  );
  expect(health.map((rule) => rule.selector)).toEqual([
    '.workstation-topbar .header-health:not([data-health="failed"])',
  ]);
});

it("covers the conversation with the detail panel where there is no room for both, and hides what is under it", () => {
  const cover = all.filter(
    (rule) =>
      maxWidth(rule) === 1100 &&
      has(rule, ".research-detail-pane") &&
      has(rule, ".research-side-panel"),
  );
  expect(cover).toHaveLength(1);
  expect(cover[0].body).toContain("position: absolute");
  expect(cover[0].body).toContain("inset: 0");
  const under = all.filter(
    (rule) =>
      maxWidth(rule) === 1100 &&
      has(rule, ".conversation-layout.detail-open > .conversation-center"),
  );
  expect(under.map((rule) => rule.body)).toEqual(["visibility: hidden;"]);
});

it("breaks Korean lines between words, and leaves text marked as another language alone", () => {
  const korean = all.filter((rule) => has(rule, "html:lang(ko)"));
  expect(korean).toHaveLength(1);
  expect(korean[0].media).toBeNull();
  expect(korean[0].body).toContain("word-break: keep-all");
  // "anywhere" would let a flex item shrink to one letter, which is how the top bar came to stack its letters.
  expect(korean[0].body).toContain("overflow-wrap: break-word");
  const other = all.filter((rule) => has(rule, "[lang]:not(:lang(ko))"));
  expect(other.map((rule) => rule.body)).toEqual(["word-break: normal;"]);
});

it("never shortens the state tag of a provider on the first-run screen", () => {
  const tag = all.filter((rule) => has(rule, ".first-run-provider-head > .bp6-tag"));
  expect(tag.map((rule) => rule.body)).toEqual(["flex: 0 0 auto;"]);
  const head = all.filter((rule) => has(rule, ".first-run-provider-head"));
  expect(head[0].body).toContain("flex-wrap: wrap");
});

it("never shortens the label of where this copy runs: it takes a row of its own when the name leaves no room", () => {
  // English and Japanese do not fit next to the name in the project list's head; the label used to be cut to "THIS CO…".
  const brand = all.filter((rule) => has(rule, ".workstation .brand"));
  expect(brand).toHaveLength(1);
  expect(brand[0].body).toContain("flex-wrap: wrap");
  const tag = all.filter(
    (rule) => has(rule, ".workstation .brand .bp6-tag") && declares(rule, "flex"),
  );
  expect(tag.map((rule) => rule.body.match(/flex:\s*([^;]+);/)?.[1])).toEqual(["none"]);
  // nothing pushes the label off its row or gives it a width to be cut to
  expect(
    all.filter(
      (rule) =>
        has(rule, ".workstation .brand .bp6-tag") &&
        (declares(rule, "max-width") || declares(rule, "width") || declares(rule, "overflow")),
    ),
  ).toEqual([]);
});
