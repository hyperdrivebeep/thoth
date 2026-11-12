// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, expect, it } from "vitest";
import { answerTopLayerSelectorsWithNo } from "../test/jsdomTopLayer";
import { ConfirmDialog } from "./ConfirmDialog";
import { RenameDialog } from "./RenameDialog";
import { watchFocus } from "./returnFocus";

// A confirmation window is mounted only while it is open. Cancelling it used to leave the keyboard focus on the page
// body (measured in a browser: the stop, disconnect and archive windows), so a keyboard user started again from the top.
let root: Root | undefined;
let container: HTMLDivElement;
beforeAll(() => {
  answerTopLayerSelectorsWithNo();
  watchFocus();
});
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  container?.remove();
  document.querySelectorAll(".bp6-portal").forEach((element) => element.remove());
});
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
const button = (name: string) =>
  [...document.querySelectorAll("button")].find(
    (item) => item.textContent === name,
  ) as HTMLButtonElement;

function Screen({ rename = false }: { rename?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button>다른 단추</button>
      <button onClick={() => setOpen(true)}>중단 요청 열기</button>
      {open && !rename && (
        <ConfirmDialog
          title="조사 중단"
          confirmLabel="중단 요청"
          onConfirm={() => setOpen(false)}
          onClose={() => setOpen(false)}
        >
          <p>이 조사를 중단할까요?</p>
        </ConfirmDialog>
      )}
      {open && rename && (
        <RenameDialog
          title="이름 바꾸기"
          current="옛 이름"
          onSave={() => setOpen(false)}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

async function mount(rename = false) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root!.render(<Screen rename={rename} />));
}

it("gives focus back to the button that opened a confirmation window when it is cancelled", async () => {
  await mount();
  const opener = button("중단 요청 열기");
  opener.focus();
  await act(async () => opener.click());
  await settle();
  expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  // the window takes the focus while it is open
  (document.querySelector('[role="dialog"] button') as HTMLButtonElement).focus();
  await act(async () => button("취소").click());
  await settle();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.activeElement).toBe(opener);
});

it("does the same for the rename window, and for a window opened from a menu item that is gone by then", async () => {
  await mount(true);
  const opener = button("중단 요청 열기");
  opener.focus();
  // a menu item inside a popover took the focus and opened the window; the popover closes and the item is removed
  const portal = document.createElement("div");
  portal.className = "bp6-portal";
  const item = document.createElement("button");
  item.textContent = "이름 바꾸기";
  portal.append(item);
  document.body.append(portal);
  item.focus();
  await act(async () => opener.click());
  await settle();
  portal.remove();
  await act(async () => button("취소").click());
  await settle();
  expect(document.activeElement).toBe(opener);
});

it("leaves the focus where the confirmed action or the person put it", async () => {
  await mount();
  const opener = button("중단 요청 열기");
  opener.focus();
  await act(async () => opener.click());
  await settle();
  // while the window closes, something else takes the focus (a new screen, another control)
  await act(async () => {
    button("중단 요청").click();
    button("다른 단추").focus();
  });
  await settle();
  expect(document.activeElement).toBe(button("다른 단추"));
});
