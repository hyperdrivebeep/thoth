// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useOpenAtLatestTurn } from "./useOpenAtLatestTurn";

function Timeline({ threadId, turns, loaded }: { threadId: string; turns: string[]; loaded: boolean }) {
  const timeline = useOpenAtLatestTurn(threadId, loaded);
  return (
    <section ref={timeline}>
      {turns.map(turn => (
        <div className="conversation-turn" data-turn={turn} key={turn} />
      ))}
    </section>
  );
}

let root: Root;
let container: HTMLDivElement;
const scrolled: { turn: string | null; options: unknown }[] = [];

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  // jsdom has no layout; record which element a browser would have been asked to bring into view.
  Element.prototype.scrollIntoView = vi.fn(function (this: Element, options?: unknown) {
    scrolled.push({ turn: this.getAttribute("data-turn"), options });
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  scrolled.length = 0;
  Reflect.deleteProperty(Element.prototype, "scrollIntoView");
});

const show = (threadId: string, turns: string[], loaded: boolean) =>
  act(async () => root.render(<Timeline threadId={threadId} turns={turns} loaded={loaded} />));

it("brings the latest turn to the top once the turns of a conversation are on screen", async () => {
  await show("thread:a", [], false);
  expect(scrolled).toEqual([]);
  await show("thread:a", ["first", "second", "latest"], true);
  expect(scrolled).toEqual([{ turn: "latest", options: { block: "start" } }]);
});

it("leaves the position alone on later refreshes of the same conversation", async () => {
  await show("thread:a", ["first", "latest"], true);
  await show("thread:a", ["first", "latest"], true);
  await show("thread:a", ["older", "first", "latest"], true); // an older page was loaded
  expect(scrolled).toHaveLength(1);
});

it("does not jump again when the same conversation is read again from the start", async () => {
  await show("thread:a", ["first", "latest"], true);
  await show("thread:a", [], false); // the saved pages were dropped and are being read again
  await show("thread:a", ["first", "latest", "newer"], true);
  expect(scrolled.map(entry => entry.turn)).toEqual(["latest"]);
});

it("opens another conversation at its own latest turn", async () => {
  await show("thread:a", ["a-1", "a-2"], true);
  await show("thread:b", ["b-1", "b-2", "b-3"], true);
  expect(scrolled.map(entry => entry.turn)).toEqual(["a-2", "b-3"]);
});

it("does nothing for a new conversation that has no turn yet", async () => {
  await show("", [], true);
  await show("thread:a", [], true);
  expect(scrolled).toEqual([]);
});
