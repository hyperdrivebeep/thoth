import { useEffect } from "react";

// A window (dialog) that is drawn only while it is open is taken out of the page when it closes, so the component
// library never gets to give the keyboard focus back: focus fell to the page body and a person using the keyboard
// had to start again from the top. The last element that had focus outside any window is remembered here, and the
// window gives focus back to it when it goes away.
let lastOutside: HTMLElement | null = null;
let watching = false;
const inWindow = (element: Element) => element.closest(".bp6-portal, .bp6-overlay") !== null;

/** Starts remembering where focus is. Called once when the app starts; calling it again does nothing. */
export function watchFocus(): void {
  if (watching) return;
  watching = true;
  document.addEventListener("focusin", (event) => {
    if (event.target instanceof HTMLElement && !inWindow(event.target)) lastOutside = event.target;
  });
}

/** For a window that is mounted only while open: when it is taken away, focus returns to where it was before. */
export function useReturnFocus(): void {
  useEffect(() => {
    watchFocus();
    const active = document.activeElement;
    const before =
      active instanceof HTMLElement && active !== document.body && !inWindow(active)
        ? active
        : lastOutside;
    return () => {
      // After the window is gone, and only when nothing else has taken the focus (a confirmed action may move it).
      setTimeout(() => {
        const lost = document.activeElement === null || document.activeElement === document.body;
        if (lost && before?.isConnected) before.focus();
      }, 0);
    };
  }, []);
}
